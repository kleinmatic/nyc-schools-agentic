"""Public-launch access gates (tmp/todo-cloudflare.md; coordinates with
the Data Tribune launch at datatribune.io).

Two pure-ASGI middlewares, both dormant until their env var is set — so
local dev, tests, and CI are unchanged until `fly secrets set` arms them:

- McpAccessGateMiddleware: with MCP_ACCESS_TOKEN set, every /mcp/*
  request must carry `X-Schools-Token: <value>` or gets a clean 401.
  Owner decision 2026-07-06: hard token wall, not throttled-public —
  "keep proprietary data proprietary."
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

CANONICAL_HOST = "nycschools.datatribune.io"

_MCP_TOKEN_ENV = "MCP_ACCESS_TOKEN"
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
    return hmac.compare_digest(supplied, expected)


def has_valid_mcp_token(scope) -> bool:
    return _token_matches(_header(scope, _MCP_HEADER), os.environ.get(_MCP_TOKEN_ENV))


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
            or not os.environ.get(_MCP_TOKEN_ENV)
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
            location = f"https://{CANONICAL_HOST}{path}"
            if scope.get("query_string"):
                location += "?" + scope["query_string"].decode("latin-1")
            await _send_response(
                send, 301, b"",
                extra_headers=[(b"location", location.encode("latin-1"))],
            )
            return
        await _send_response(send, 403, b"Forbidden: use https://" + CANONICAL_HOST.encode())
