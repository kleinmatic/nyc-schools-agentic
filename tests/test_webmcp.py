"""WebMCP declarative-form annotations on /zoned and /search.

Substring-level guard against accidentally dropping the WebMCP attributes
during a template refactor. Does NOT validate the spec — that's what the
Chrome DevTools "agentic browsing" Lighthouse audit is for.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    "path, expected_tool",
    [
        ("/zoned", "find-zoned-schools-by-address"),
        ("/search", "search-schools-by-name"),
        ("/search", "find-zoned-schools-by-address"),
        ("/", "search-schools-by-name"),
        ("/", "find-zoned-schools-by-address"),
    ],
)
def test_webmcp_toolname_present(client, path, expected_tool):
    r = client.get(path)
    assert r.status_code == 200
    assert f'toolname="{expected_tool}"' in r.text


@pytest.mark.parametrize("path", ["/", "/zoned", "/search"])
def test_webmcp_form_carries_description_and_autosubmit(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "tooldescription=" in r.text
    assert "toolautosubmit" in r.text


@pytest.mark.parametrize("path", ["/", "/zoned", "/search"])
def test_webmcp_input_has_param_description(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "toolparamdescription=" in r.text


def test_find_legacy_redirects_to_zoned(client):
    r = client.get("/find", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"].startswith("/zoned")


def test_find_legacy_redirect_preserves_address(client):
    r = client.get("/find", params={"address": "180 7th Ave Brooklyn"}, follow_redirects=False)
    assert r.status_code == 301
    assert "address=" in r.headers["location"]
    assert r.headers["location"].startswith("/zoned")
