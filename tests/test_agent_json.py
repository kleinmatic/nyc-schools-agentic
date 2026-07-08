"""The agent-facing JSON surface behind the WebMCP declarative forms.

/search and /zoned accept format=json and return the same service-layer
payloads the HTML path renders — this is what base.html's respondWith
handler fetches when Chrome's declarative WebMCP API invokes a
toolautosubmit form, so the page answers the agent in place instead of
navigating. Pins: JSON shapes, structured (non-HTML) errors, the HTML
path staying untouched for format-less requests, and the respondWith /
toolcancel script shipping on every base-rendered page.
"""
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from app.services.zoning import GEOSEARCH_URL


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ----- /search?format=json -----

def test_search_json_returns_results_with_known_dbn(client):
    r = client.get("/search", params={"q": "bronx science", "format": "json"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["query"] == "bronx science"
    assert isinstance(body["results"], list) and body["results"]
    dbns = [s["dbn"] for s in body["results"]]
    assert "10X445" in dbns, f"Bronx Science (10X445) not in {dbns}"
    # Raw internal codes, not display labels — `level`/`pretty` filters
    # are display-only and must not leak into the JSON contract.
    bx_sci = next(s for s in body["results"] if s["dbn"] == "10X445")
    assert bx_sci["school_level"] == "high"


def test_search_json_missing_q_is_structured_error_not_html(client):
    r = client.get("/search", params={"format": "json"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/json")
    assert "q" in r.json()["error"]


# ----- /zoned?format=json -----

@respx.mock
def test_zoned_json_returns_service_payload(client):
    # Same mock pattern as the demo-chain regression test in
    # tests/test_services.py — PS 33 Chelsea Prep's own coordinates,
    # guaranteed inside its own ES attendance-zone polygon.
    ps33_lat, ps33_lon = 40.7490139997319, -74.00023799963121
    respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"coordinates": [ps33_lon, ps33_lat]},
                        "properties": {
                            "label": "428 W 26 STREET, Manhattan, NY, USA",
                            "borough": "Manhattan",
                        },
                    }
                ]
            },
        )
    )

    r = client.get(
        "/zoned",
        params={"address": "428 W 26th St, Manhattan", "format": "json"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["address"] == "428 W 26th St, Manhattan"
    assert body["geocoded"]["borough"] == "Manhattan"

    result = body["result"]
    assert "02M033" in [s["dbn"] for s in result["elementary"]]
    assert result["es_district"] == 2
    # MS is district-based choice; the JSON must carry the interpretive
    # fields verbatim so a consuming agent doesn't misread a
    # zone-priority hit as a placement.
    assert result["ms_admission_type"] == "zone_priority_choice"
    assert "02M297" in [s["dbn"] for s in result["middle"]]
    assert "choice" in result["ms_admission_note"].lower()


def test_zoned_json_missing_address_is_structured_error_not_html(client):
    r = client.get("/zoned", params={"format": "json"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/json")
    assert "address" in r.json()["error"]


# ----- HTML path unchanged without format=json -----

def test_search_html_path_unchanged_without_format(client):
    r = client.get("/search", params={"q": "bronx science"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '/school/10X445' in r.text


# ----- respondWith progressive enhancement ships on every page -----

def test_base_page_carries_respondwith_enhancement(client):
    r = client.get("/sources")
    assert r.status_code == 200
    assert "respondWith" in r.text
    assert "agentInvoked" in r.text
    assert "toolcancel" in r.text
