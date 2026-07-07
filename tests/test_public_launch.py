"""Regression matrix for the public-launch access gates (app/gates.py).

Both gates read env per-request, so one module-scoped client serves every
case with monkeypatch flipping the env. The invariants that matter:
dormant-by-default (unset env changes nothing), 401-before-redirect for
MCP callers, and the health check always answering.
"""
import pytest
from fastapi.testclient import TestClient

from app.gates import CANONICAL_HOST
from app.main import app

MCP_TOKEN = "test-mcp-token"
EDGE_TOKEN = "test-edge-token"

# Minimal Streamable HTTP handshake — enough to prove dispatch works.
_INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "launch-test", "version": "0"}},
}
_MCP_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _post_mcp(client, extra_headers=None):
    return client.post("/mcp/", json=_INITIALIZE,
                       headers={**_MCP_HEADERS, **(extra_headers or {})})


# ----- dormant by default -----

def test_gates_are_noops_when_env_unset(client):
    assert client.get("/").status_code == 200
    assert _post_mcp(client).status_code == 200


# ----- MCP token gate -----

def test_mcp_gate_401_without_token(client, monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    r = _post_mcp(client)
    assert r.status_code == 401
    assert "X-Schools-Token" in r.json()["detail"]


def test_mcp_gate_401_with_wrong_token(client, monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    assert _post_mcp(client, {"X-Schools-Token": "wrong"}).status_code == 401


def test_mcp_gate_passes_with_token(client, monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    assert _post_mcp(client, {"X-Schools-Token": MCP_TOKEN}).status_code == 200


def test_mcp_gate_covers_the_no_slash_alias(client, monkeypatch):
    """/mcp (rewritten to /mcp/ downstream) must be gated too."""
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    r = client.post("/mcp", json=_INITIALIZE, headers=_MCP_HEADERS)
    assert r.status_code == 401


def test_mcp_gate_does_not_touch_html_routes(client, monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    assert client.get("/").status_code == 200


# ----- edge lockdown -----

def test_edge_lockdown_redirects_direct_get(client, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    r = client.get("/school/15K321", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == f"https://{CANONICAL_HOST}/school/15K321"


def test_edge_lockdown_redirect_preserves_query(client, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    r = client.get("/search?q=stuyvesant", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == f"https://{CANONICAL_HOST}/search?q=stuyvesant"


def test_edge_lockdown_403s_direct_post(client, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    assert _post_mcp(client).status_code == 403


def test_edge_lockdown_passes_edge_token(client, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    r = client.get("/", headers={"X-Edge-Token": EDGE_TOKEN})
    assert r.status_code == 200


def test_edge_lockdown_wrong_edge_token_redirects(client, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    r = client.get("/", headers={"X-Edge-Token": "wrong"}, follow_redirects=False)
    assert r.status_code == 301


def test_edge_lockdown_non_ascii_token_rejected_not_500(client, monkeypatch):
    """hmac.compare_digest raises TypeError on non-ASCII str input, so a
    garbage token header (any bytes — header values decode via latin-1)
    500'd the origin instead of being rejected. Regression pin: it must
    behave exactly like a wrong token."""
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    r = client.get("/", headers={"X-Edge-Token": "café-token"}, follow_redirects=False)
    assert r.status_code == 301
    r = _post_mcp(client, {"X-Schools-Token": "café-token"})
    assert r.status_code == 401


def test_edge_lockdown_health_check_always_open(client, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    assert client.get("/healthz").status_code == 200


def test_direct_mcp_with_schools_token_bypasses_edge(client, monkeypatch):
    """The publisher and the owner's local clients hit the origin directly
    with X-Schools-Token — the edge gate must not force them through
    Cloudflare."""
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    assert _post_mcp(client, {"X-Schools-Token": MCP_TOKEN}).status_code == 200


def test_both_gates_armed_bad_mcp_token_gets_401_not_redirect(client, monkeypatch):
    """The 401-before-redirect precedence: a machine client with a bad
    token needs a status it can act on, not a 301 to an HTML page."""
    monkeypatch.setenv("EDGE_TOKEN", EDGE_TOKEN)
    monkeypatch.setenv("MCP_ACCESS_TOKEN", MCP_TOKEN)
    r = client.post("/mcp/", json=_INITIALIZE,
                    headers={**_MCP_HEADERS, "X-Schools-Token": "wrong"})
    assert r.status_code == 401
