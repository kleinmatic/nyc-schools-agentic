"""Per-IP token-bucket rate limiting (issue #4).

Pure ASGI middleware — deliberately not BaseHTTPMiddleware, which wraps
response streaming and can interfere with the MCP Streamable HTTP (SSE)
mount. On an allowed request it passes straight through; over the limit
it answers 429 with Retry-After and never touches the wrapped app.

Keying: CF-Connecting-IP (trusted ONLY on requests bearing a valid
X-Edge-Token — otherwise a direct-to-origin caller could spoof it to
rotate buckets) → Fly-Client-IP (set by Fly's proxy; behind Cloudflare
this is a CF edge address, hence the CF header taking precedence) →
first X-Forwarded-For hop → transport peer address. The Fly proxy is
the trust boundary in production, and proxy-level concurrency limits
(fly.toml) backstop header games. State is per-process and resets on
deploy — fine for a single-machine app; move to a shared store before
scaling out.

Limits are generous by design (the agentic-newsroom MCP consumer points
at this server): sustained RATE_LIMIT_RATE tokens/sec with a
RATE_LIMIT_BURST bucket, published in /llms.txt and the README.
"""
import json
import math
import os
import time


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
        # ip -> (tokens, monotonic timestamp of last update)
        self._buckets: dict[str, tuple[float, float]] = {}

    def _client_ip(self, scope) -> str:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        ip = None
        if headers.get("cf-connecting-ip"):
            from .gates import has_valid_edge_token
            if has_valid_edge_token(scope):
                ip = headers["cf-connecting-ip"]
        if not ip:
            ip = headers.get("fly-client-ip")
        if not ip and (xff := headers.get("x-forwarded-for")):
            ip = xff.split(",")[0].strip()
        if not ip:
            client = scope.get("client")
            ip = client[0] if client else "unknown"
        return ip

    def _prune(self, now: float) -> None:
        """Drop buckets idle long enough to have refilled completely —
        forgetting them is lossless."""
        full_after = self.burst / self.rate if self.rate > 0 else 60.0
        self._buckets = {
            ip: (tokens, ts)
            for ip, (tokens, ts) in self._buckets.items()
            if now - ts < full_after
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt_paths:
            return await self.app(scope, receive, send)

        ip = self._client_ip(scope)
        now = time.monotonic()
        tokens, last = self._buckets.get(ip, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)

        if tokens >= 1.0:
            self._buckets[ip] = (tokens - 1.0, now)
            if len(self._buckets) > self.max_buckets:
                self._prune(now)
            return await self.app(scope, receive, send)

        self._buckets[ip] = (tokens, now)
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
