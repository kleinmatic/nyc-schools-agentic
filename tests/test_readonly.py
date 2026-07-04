"""Read-only lock-in (issue #4). The public surface is read-only by
construction: SQLite opens mode=ro, every HTTP route is GET-semantics,
and every MCP tool wraps a read-only service function. These tests turn
that property into a regression gate — if a future change mounts a
mutating route or a write-capable tool, it fails here first and forces
a conscious decision.

The MCP half lives in test_mcp_server.py: the tool-name allowlist in
test_list_tools_returns_all_registered_tools means any new tool has to
be added there deliberately (new tools must stay read-only — services/
never writes). The /mcp mount itself accepts POST because that's the
Streamable HTTP *transport* envelope, not a mutation surface: every
message inside dispatches to one of the allowlisted read-only tools.
"""
from starlette.routing import Mount, Route

from app.main import app

# HEAD rides along with GET in Starlette; OPTIONS never carries app logic.
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def test_every_http_route_is_read_only():
    routes = [r for r in app.routes if isinstance(r, Route)]
    assert routes, "no routes mounted — app wiring broke"
    offenders = {
        r.path: sorted(r.methods - _READ_METHODS)
        for r in routes
        if r.methods and (r.methods - _READ_METHODS)
    }
    assert not offenders, f"non-GET routes mounted: {offenders}"


def test_security_headers_on_every_response():
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        for path in ("/", "/healthz", "/robots.txt"):
            r = client.get(path)
            assert r.headers["x-content-type-options"] == "nosniff", path
            assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin", path
            assert r.headers["x-frame-options"] == "SAMEORIGIN", path


def test_the_only_mount_is_the_mcp_transport():
    mounts = [r.path for r in app.routes if isinstance(r, Mount)]
    assert mounts == ["/mcp"], (
        f"unexpected mounts: {mounts} — anything beyond /mcp must be "
        "reviewed for read-only semantics and added here deliberately"
    )
