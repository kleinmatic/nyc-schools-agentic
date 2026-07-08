"""Public-launch access gates (tmp/todo-cloudflare.md; coordinates with
the Data Tribune launch at datatribune.io).

Two pure-ASGI middlewares, both dormant until their env var is set — so
local dev, tests, and CI are unchanged until `fly secrets set` arms them:

- McpAccessGateMiddleware: with a token configured, every /mcp/*
  request must carry `X-Schools-Token: <value>` or gets a clean 401.
  Tokens come from the legacy single MCP_ACCESS_TOKEN and/or the
  per-consumer MCP_ACCESS_TOKENS set (comma-separated id:secret) — the
  gate accepts any of them, so one consumer can be revoked without
  disturbing the rest, and both env vars together give a dual-valid
  migration window. Owner decision 2026-07-06: hard token wall, not
  throttled-public — "keep proprietary data proprietary."
- EdgeLockdownMiddleware: with EDGE_TOKEN set, non-MCP traffic must
  arrive through Cloudflare (which stamps `X-Edge-Token` via a Transform
  Rule on the canonical host). Direct-to-origin browsers get a 301 to
  the canonical host; non-GET gets 403. Valid-token MCP callers and the
  health check are never redirected.

Ordering (see main.py): the MCP gate runs OUTSIDE the edge gate so a
bad-token /mcp/* request 401s — a machine client can act on a 401; a
301 to an HTML page is noise.

Tokens compare via hmac.compare_digest. Env is read per-request (a dict
lookup) so tests can monkeypatch and a future secrets re-stage doesn't
need a code path.
"""
import hmac
import os
from urllib.parse import quote

CANONICAL_HOST = "nycschools.datatribune.io"

_MCP_TOKEN_ENV = "MCP_ACCESS_TOKEN"    # legacy single shared token
_MCP_TOKENS_ENV = "MCP_ACCESS_TOKENS"  # per-consumer set: comma-separated id:secret
_EDGE_TOKEN_ENV = "EDGE_TOKEN"
_MCP_HEADER = b"x-schools-token"
_EDGE_HEADER = b"x-edge-token"
_HEALTH_PATH = "/healthz"  # matches fly.toml [[http_service.checks]]


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k.lower() == name:
            return v.decode("latin-1")
    return None


def _is_mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _token_matches(supplied: str | None, expected: str | None) -> bool:
    if not supplied or not expected:
        return False
    # Compare as bytes: hmac.compare_digest raises TypeError on str args
    # containing non-ASCII, so a request with a garbage token header
    # (latin-1 decodes anything) would 500 the origin instead of being
    # rejected. Encoding both sides first makes any byte junk just fail.
    return hmac.compare_digest(
        supplied.encode("utf-8", "surrogateescape"),
        expected.encode("utf-8", "surrogateescape"),
    )


def _accepted_mcp_tokens() -> list[tuple[str, str]]:
    """(id, secret) pairs the MCP gate accepts. Reads the legacy single
    MCP_ACCESS_TOKEN plus the per-consumer MCP_ACCESS_TOKENS set
    (comma-separated, each "id:secret", or a bare secret → id "default").
    Named per-consumer tokens let us attribute and revoke one caller
    without disturbing the others; the legacy var keeps the wall up and,
    set alongside the new set, gives a dual-valid window so a consumer can
    migrate to its own token before the shared one is retired."""
    pairs: list[tuple[str, str]] = []
    single = os.environ.get(_MCP_TOKEN_ENV)
    if single:
        pairs.append(("shared", single))
    for item in os.environ.get(_MCP_TOKENS_ENV, "").split(","):
        item = item.strip()
        if not item:
            continue
        cid, sep, secret = item.partition(":")
        if sep:
            pairs.append((cid.strip() or "default", secret.strip()))
        else:
            pairs.append(("default", item))
    return pairs


def mcp_gate_armed() -> bool:
    return bool(_accepted_mcp_tokens())


def matched_mcp_token_id(scope) -> str | None:
    """The consumer id whose token the request carries, or None. Returned
    separately from the boolean so a future audit-log line can attribute a
    call to a consumer."""
    supplied = _header(scope, _MCP_HEADER)
    if not supplied:
        return None
    for cid, secret in _accepted_mcp_tokens():
        if _token_matches(supplied, secret):
            return cid
    return None


def has_valid_mcp_token(scope) -> bool:
    return matched_mcp_token_id(scope) is not None


def has_valid_edge_token(scope) -> bool:
    return _token_matches(_header(scope, _EDGE_HEADER), os.environ.get(_EDGE_TOKEN_ENV))


async def _send_response(send, status: int, body: bytes,
                         content_type: bytes = b"text/plain",
                         extra_headers: list | None = None):
    headers = [
        (b"content-type", content_type),
        (b"content-length", str(len(body)).encode()),
    ] + (extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class McpAccessGateMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not _is_mcp_path(scope["path"])
            or not mcp_gate_armed()
            or has_valid_mcp_token(scope)
        ):
            return await self.app(scope, receive, send)
        await _send_response(
            send, 401,
            b'{"detail":"This MCP endpoint requires an access token: send an '
            b'X-Schools-Token header. Contact the site owner for access."}',
            content_type=b"application/json",
        )


class EdgeLockdownMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not os.environ.get(_EDGE_TOKEN_ENV):
            return await self.app(scope, receive, send)
        path = scope["path"]
        if (
            path == _HEALTH_PATH
            or has_valid_edge_token(scope)
            or (_is_mcp_path(path) and has_valid_mcp_token(scope))
        ):
            return await self.app(scope, receive, send)
        if scope.get("method") in ("GET", "HEAD"):
            # uvicorn delivers `path` percent-DECODED, so a request like
            # /%0d%0aSet-Cookie:x would inject CR/LF into the Location header
            # (response splitting) and a non-latin-1 path would crash the
            # .encode("latin-1") below. quote() re-encodes it to pure,
            # CRLF-free ASCII. query_string stays raw wire bytes — it can't
            # carry a literal CR/LF (that would have broken the request line
            # the server already parsed).
            location = f"https://{CANONICAL_HOST}{quote(path, safe='/')}"
            if scope.get("query_string"):
                location += "?" + scope["query_string"].decode("latin-1")
            await _send_response(
                send, 301, b"",
                extra_headers=[(b"location", location.encode("latin-1"))],
            )
            return
        await _send_response(send, 403, b"Forbidden: use https://" + CANONICAL_HOST.encode())
