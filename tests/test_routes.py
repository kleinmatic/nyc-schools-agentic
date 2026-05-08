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


def test_home_renders_accountability_dashboard(client):
    """Homepage should surface the curated leaderboards by default,
    with all 4 table titles visible."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Accountability dashboard" in r.text
    for fragment in (
        "Top high schools by Regents passing rate",
        "most chronic absenteeism",
        "Highest-need high schools",
        "Top elementary schools by ELA proficiency",
    ):
        assert fragment in r.text, f"missing leaderboard: {fragment!r}"
    # Top of the Regents leaderboard reliably includes one of the
    # specialized HS — at minimum Stuyvesant's DBN as a row link.
    assert 'href="/school/02M475"' in r.text


def test_home_renders_place_based_leaderboards(client):
    """Borough grid + 2 NTA leaderboards under the 'By place' section."""
    r = client.get("/")
    for fragment in (
        "By place",
        "Boroughs at a glance",
        "Top neighborhoods — high schools",
        "Top neighborhoods — elementary schools",
    ):
        assert fragment in r.text, f"missing place section: {fragment!r}"
    # Borough grid: 5 boroughs, each as a <td>.
    for boro in ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"):
        assert f"<td class=\"px-4 py-2 text-slate-900\">{boro}</td>" in r.text


def test_school_page_includes_neighborhood_peers(client):
    """ES school page should show the 'Schools nearby' section with both
    NTA and district cohorts, and highlight the focal school."""
    r = client.get("/school/15K321")
    assert "Schools nearby" in r.text
    assert "Park Slope-Gowanus" in r.text  # NTA label
    assert "District 15" in r.text
    # Focal-school highlight appears once per cohort.
    assert r.text.count("this school</span>") == 2


def test_high_school_page_omits_district_peer_cohort(client):
    """HS aren't district-zoned (city-wide choice), so school page should
    skip the district cohort but still show the NTA cohort."""
    r = client.get("/school/02M475")  # Stuyvesant
    assert "Schools nearby" in r.text
    # Exactly one peer-cohort heading (the NTA one); no "District N" label.
    import re
    district_labels = re.findall(r"District \d+", r.text)
    # The Location & neighborhood section already prints district numbers
    # — exclude that. The peer-cohort district label sits inside an h3
    # under "Schools nearby"; assert no peer-cohort h3 with District.
    assert "<h3 class=\"text-sm font-semibold text-slate-700 mb-1\">\n    \n      Schools in District" not in r.text


def test_search_with_query_hides_dashboard(client):
    """When the user has searched, leaderboards step aside — search
    results take focus."""
    r = client.get("/search", params={"q": "stuyvesant"})
    assert r.status_code == 200
    assert "Accountability dashboard" not in r.text


def test_search_with_empty_query_keeps_dashboard(client):
    """Clearing the search input shouldn't lose the dashboard; the user
    is back to "browse mode."""
    r = client.get("/search", params={"q": ""})
    assert r.status_code == 200
    assert "Accountability dashboard" in r.text


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
    # Distinctive substrings rather than full headers — heading text is now
    # broken up by inline term() popover triggers (e.g. "NYS <btn>ESSA</btn>
    # accountability").
    for fragment in (
        "Quick stats",
        ">ESSA</button>",
        "Chronic absenteeism",
        "Spending",
        "staffing",
        "School info",
        "Location",
        "ELA exam",
        "Math exam",
        "Class size",
        "Galaxy budget",
        "Demographics by year",
    ):
        assert fragment in r.text, f"missing section fragment: {fragment}"


def test_high_school_page_includes_hs_only_sections(client):
    r = client.get("/school/22K405")  # Midwood
    assert r.status_code == 200
    for fragment in (
        "High school directory",
        "Performance",
        "Admissions programs",
        "Academic offerings",
        "Athletics",
        "Regents exams",
        "HS graduation rate",
        "Civic Readiness",
    ):
        assert fragment in r.text, f"missing HS-only section fragment: {fragment}"


def test_insideschools_link_present(client):
    r = client.get("/school/15K321")
    assert r.status_code == 200
    assert 'href="https://insideschools.org/school/15K321"' in r.text


def test_school_page_404(client):
    r = client.get("/school/99Z999")
    assert r.status_code == 404


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "data_loaded": True}
