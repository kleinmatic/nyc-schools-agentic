"""Route smoke tests via FastAPI's TestClient."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    # TestClient triggers lifespan, so data loads here too.
    with TestClient(app) as c:
        yield c


def test_home_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Find a school" in r.text


def test_search_html_returns_results(client):
    r = client.get("/search", params={"q": "PS 321"})
    assert r.status_code == 200
    assert "15K321" in r.text


def test_search_htmx_returns_partial(client):
    r = client.get("/search", params={"q": "PS 321"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    # Partial does NOT include the page chrome.
    assert "<html" not in r.text.lower()
    assert "15K321" in r.text


def test_school_page_renders(client):
    r = client.get("/school/15K321")
    assert r.status_code == 200
    assert "15K321" in r.text
    assert "Demographics by year" in r.text


def test_school_page_includes_all_sections(client):
    r = client.get("/school/15K321")
    assert r.status_code == 200
    # Each major section header expected for an elementary school like PS 321.
    for section in (
        "Quick stats",
        "School info",
        "Location",
        "3&#8211;8 ELA exam",
        "3&#8211;8 Math exam",
        "Class size",
        "Galaxy budget",
        "Demographics by year",
    ):
        assert section in r.text or section.replace("&#8211;", "–") in r.text, f"missing section: {section}"


def test_high_school_page_includes_hs_only_sections(client):
    r = client.get("/school/22K405")  # Midwood
    assert r.status_code == 200
    for section in (
        "High school directory",
        "Performance",
        "Admissions programs",
        "Academic offerings",
        "Athletics",
        "Regents exams",
    ):
        assert section in r.text, f"missing HS-only section: {section}"


def test_school_page_404(client):
    r = client.get("/school/99Z999")
    assert r.status_code == 404


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "data_loaded": True}
