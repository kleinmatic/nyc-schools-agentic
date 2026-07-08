"""Per-IP token-bucket rate limiting (issue #4).

Pure ASGI middleware — deliberately not BaseHTTPMiddleware, which wraps
response streaming and can interfere with the MCP Streamable HTTP (SSE)
mount. On an allowed request it passes straight through; over the limit
it answers 429 with Retry-After and never touches the wrapped app.

Keying: CF-Connecting-IP (only with a valid X-Edge-Token — the CF
Transform Rule stamps it; without that gate a direct-to-origin caller
could spoof CF-Connecting-IP to rotate buckets) → Fly-Client-IP →
transport peer. Fly-Client-IP is set by the Fly proxy on every request
it forwards and is NOT client-settable (Fly overwrites any supplied
value), so it's the trustworthy per-client key for direct-to-origin
traffic; behind our own Cloudflare it holds the CF edge IP, which is
why CF-Connecting-IP takes precedence. We deliberately do NOT fall back
to X-Forwarded-For: Fly appends to it (a client controls the leftmost
hop), so trusting it — as the pre-fix code did — let a direct caller
rotate the header per request to mint fresh buckets and evade the
limiter. Off-Fly (local dev) there's no Fly-Client-IP, so keying falls
to the transport peer. (Note: this diverges from the audit's suggested
fix of gating Fly-Client-IP behind a token — that would send anonymous
Fly traffic to the uvicorn scope['client'], which under
--forwarded-allow-ips='*' is itself XFF-derived and spoofable. Trusting
the Fly-set header directly is both simpler and strictly safer, and
avoids narrowing --forwarded-allow-ips, which the app still needs at
'*' for X-Forwarded-Proto scheme detection.) State is per-process and
resets on deploy — fine for a single-machine app; move to a shared
store before scaling out.

Limits are generous by design (the agentic-newsroom MCP consumer points
at this server): sustained RATE_LIMIT_RATE tokens/sec with a
RATE_LIMIT_BURST bucket, published in /llms.txt and the README.
"""
import json
import math
import os
import time
from collections import OrderedDict


class RateLimitMiddleware:
    def __init__(
        self,
        app,
        rate: float | None = None,
        burst: float | None = None,
        exempt_paths: tuple[str, ...] = ("/healthz",),
        max_buckets: int = 10_000,
    ):
        self.app = app
        self.rate = float(rate if rate is not None else os.environ.get("RATE_LIMIT_RATE", 2.0))
        self.burst = float(burst if burst is not None else os.environ.get("RATE_LIMIT_BURST", 120))
        self.exempt_paths = exempt_paths
        self.max_buckets = max_buckets
        # ip -> (tokens, monotonic timestamp of last update). OrderedDict so
        # eviction can drop least-recently-used entries (move_to_end on every
        # touch keeps insertion order == recency order).
        self._buckets: "OrderedDict[str, tuple[float, float]]" = OrderedDict()

    def _client_ip(self, scope) -> str:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        # CF-Connecting-IP is the real client behind Cloudflare, but only
        # trustworthy with a valid edge token (else a direct caller spoofs
        # it to rotate buckets). Otherwise Fly-Client-IP — set and overwritten
        # by the Fly proxy, so it's the unspoofable per-client key for
        # direct-to-origin traffic (incl. the agentic-newsroom MCP consumer
        # and Scott's local client). We do NOT trust X-Forwarded-For: Fly
        # appends to it, so a client controls its leftmost hop, and trusting
        # it was the C1/C2 limiter-bypass. Off-Fly there's no Fly-Client-IP,
        # so keying falls to the transport peer.
        from .gates import has_valid_edge_token
        if headers.get("cf-connecting-ip") and has_valid_edge_token(scope):
            return headers["cf-connecting-ip"]
        if headers.get("fly-client-ip"):
            return headers["fly-client-ip"]
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _prune(self, now: float) -> None:
        """Drop buckets idle long enough to have refilled completely —
        forgetting them is lossless. Deletes in place so the OrderedDict's
        recency ordering survives."""
        full_after = self.burst / self.rate if self.rate > 0 else 60.0
        stale = [ip for ip, (_, ts) in self._buckets.items()
                 if now - ts >= full_after]
        for ip in stale:
            del self._buckets[ip]

    def _evict(self, now: float) -> None:
        """Bound memory. First drop idle-refilled buckets (lossless). If a
        live flood keeps every bucket fresh so nothing is idle, hard-evict
        the least-recently-used entries down to the cap — otherwise the map
        grows unbounded AND the prune scan re-runs on every request (O(n)
        CPU amplification that scales with the attack). LRU order is kept by
        move_to_end on every touch."""
        self._prune(now)
        while len(self._buckets) > self.max_buckets:
            self._buckets.popitem(last=False)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt_paths:
            return await self.app(scope, receive, send)

        ip = self._client_ip(scope)
        now = time.monotonic()
        tokens, last = self._buckets.get(ip, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)

        allowed = tokens >= 1.0
        self._buckets[ip] = (tokens - 1.0 if allowed else tokens, now)
        self._buckets.move_to_end(ip)
        if len(self._buckets) > self.max_buckets:
            self._evict(now)

        if allowed:
            return await self.app(scope, receive, send)
        retry_after = max(1, math.ceil((1.0 - tokens) / self.rate)) if self.rate > 0 else 60
        body = json.dumps({
            "detail": "Rate limit exceeded — this is a shared public server. "
                      f"Sustained {self.rate:g} requests/sec per IP with a "
                      f"{self.burst:g}-request burst. Retry after "
                      f"{retry_after}s.",
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(retry_after).encode()),
                (b"x-ratelimit-policy", f"{self.rate:g}/s; burst={self.burst:g}".encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
