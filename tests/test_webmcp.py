"""WebMCP declarative-form annotations on /find and /search.

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
        ("/find", "find-zoned-schools-by-address"),
        ("/search", "search-schools-by-name"),
        ("/search", "find-zoned-schools-by-address"),
    ],
)
def test_webmcp_toolname_present(client, path, expected_tool):
    r = client.get(path)
    assert r.status_code == 200
    assert f'toolname="{expected_tool}"' in r.text


@pytest.mark.parametrize("path", ["/find", "/search"])
def test_webmcp_form_carries_description_and_autosubmit(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "tooldescription=" in r.text
    assert "toolautosubmit" in r.text


@pytest.mark.parametrize("path", ["/find", "/search"])
def test_webmcp_input_has_param_description(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "toolparamdescription=" in r.text
