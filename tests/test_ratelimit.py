"""Tests for the per-IP token-bucket middleware (issue #4). Built on a
throwaway ASGI app so the production app's env-neutralized limits (see
conftest.py) stay out of the way."""
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.ratelimit import RateLimitMiddleware


def _make_client(**kwargs) -> TestClient:
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[
        Route("/", ok),
        Route("/healthz", ok),
    ])
    return TestClient(RateLimitMiddleware(app, **kwargs))


def test_burst_then_429_with_retry_after():
    # rate ~0 so the bucket can't refill mid-test.
    client = _make_client(rate=0.001, burst=3)
    for _ in range(3):
        assert client.get("/").status_code == 200
    r = client.get("/")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert "burst=3" in r.headers["x-ratelimit-policy"]
    assert "Rate limit exceeded" in r.json()["detail"]


def test_healthz_is_exempt():
    client = _make_client(rate=0.001, burst=1)
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 429  # bucket empty…
    for _ in range(5):
        assert client.get("/healthz").status_code == 200  # …healthz unaffected


def test_buckets_key_on_forwarded_client_ip():
    client = _make_client(rate=0.001, burst=1)
    assert client.get("/", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    # A different caller has its own bucket.
    assert client.get("/", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_fly_client_ip_wins_over_x_forwarded_for():
    client = _make_client(rate=0.001, burst=1)
    h1 = {"Fly-Client-IP": "9.9.9.9", "X-Forwarded-For": "1.1.1.1"}
    h2 = {"Fly-Client-IP": "9.9.9.9", "X-Forwarded-For": "8.8.8.8"}
    assert client.get("/", headers=h1).status_code == 200
    # Same Fly-Client-IP → same bucket, despite the differing XFF.
    assert client.get("/", headers=h2).status_code == 429


def test_cf_connecting_ip_trusted_only_with_valid_edge_token(monkeypatch):
    """Behind Cloudflare the real client is in CF-Connecting-IP — but the
    header is only trustworthy when the request also carries the edge
    token the CF Transform Rule stamps (else direct-to-origin callers
    could spoof it to rotate buckets)."""
    monkeypatch.setenv("EDGE_TOKEN", "edge-secret")
    client = _make_client(rate=0.001, burst=1)
    trusted = {"X-Edge-Token": "edge-secret", "CF-Connecting-IP": "1.1.1.1"}
    # Trusted: two CF end-clients get separate buckets.
    assert client.get("/", headers=trusted).status_code == 200
    assert client.get("/", headers=trusted).status_code == 429
    assert client.get("/", headers={**trusted, "CF-Connecting-IP": "2.2.2.2"}).status_code == 200
    # Untrusted: no edge token → CF header ignored, falls back to XFF;
    # rotating CF-Connecting-IP does NOT rotate buckets.
    spoof = {"CF-Connecting-IP": "3.3.3.3", "X-Forwarded-For": "5.5.5.5"}
    assert client.get("/", headers=spoof).status_code == 200
    assert client.get("/", headers={**spoof, "CF-Connecting-IP": "4.4.4.4"}).status_code == 429


def test_bucket_refills_over_time(monkeypatch):
    import app.ratelimit as rl
    t = {"now": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: t["now"])
    client = _make_client(rate=1.0, burst=1)
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 429
    t["now"] += 2.0  # 2s at 1 token/s → bucket full again (capped at burst)
    assert client.get("/").status_code == 200


def test_prune_drops_fully_refilled_buckets(monkeypatch):
    import app.ratelimit as rl
    t = {"now": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: t["now"])
    mw = RateLimitMiddleware(lambda s, r, sd: None, rate=1.0, burst=2, max_buckets=10)
    mw._buckets = {f"ip{i}": (0.0, 900.0) for i in range(11)}  # idle 100s > 2s refill
    mw._prune(t["now"])
    assert mw._buckets == {}
