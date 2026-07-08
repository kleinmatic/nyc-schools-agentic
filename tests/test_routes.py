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
    """Structural-only — exact copy in the masthead and hero is free to
    change. We check that the page renders, has an h1, and surfaces the
    structural commitments: the masthead wordmark and the hero block."""
    r = client.get("/")
    assert r.status_code == 200
    assert "<h1" in r.text
    assert 'class="masthead-wordmark"' in r.text
    assert 'class="display-headline"' in r.text


def test_search_with_query_drops_homepage_hero(client):
    """When the user has searched, the eyebrow + display headline give
    way to a plain section header so search results take focus. Page
    still has *some* h1 (the smaller fallback heading)."""
    r = client.get("/search", params={"q": "stuyvesant"})
    assert r.status_code == 200
    assert "<h1" in r.text
    assert 'class="display-headline"' not in r.text


def test_home_renders_accountability_dashboard(client):
    """Homepage should surface the curated leaderboards by default,
    with all 4 table titles visible."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Accountability Dashboard" in r.text
    for fragment in (
        "Top High Schools by Regents Passing Rate",
        "Most Chronic Absenteeism",
        "Highest-Need High Schools",
        "Top Elementary Schools by ELA Proficiency",
    ):
        assert fragment in r.text, f"missing leaderboard: {fragment!r}"
    # Top of the Regents leaderboard reliably includes one of the
    # specialized HS — at minimum Stuyvesant's DBN as a row link.
    assert 'href="/school/02M475"' in r.text


def test_home_renders_citywide_picture(client):
    """The city-wide context section: stat tiles + the two chart figures,
    each with its no-JS data table and per-chart source line."""
    r = client.get("/")
    assert r.status_code == 200
    for fragment in (
        "The Citywide Picture",
        "Public Schools",
        "Students Enrolled",
        "Median Economic Need",
        "Proficiency on State Tests, Grades 3–8",
        "Need and Proficiency by Neighborhood",
        'id="citywide-proficiency"',
        'id="citywide-map-eni"',
        'id="citywide-map-ela"',
        'id="citywide-map-math"',
        'id="citywide-spark"',
    ):
        assert fragment in r.text, f"missing citywide fragment: {fragment!r}"
    # Chart data rides inline as JSON for the Plot scripts — including
    # the NTA FeatureCollection for the choropleth series.
    assert '"FeatureCollection"' in r.text
    # Source lines under both figures, per house style.
    assert "NYS grades 3–8 test results" in r.text
    assert "NYC DOE demographic snapshot" in r.text


def test_search_with_query_drops_citywide_picture(client):
    r = client.get("/search", params={"q": "stuyvesant"})
    assert "The Citywide Picture" not in r.text


def test_home_renders_place_based_leaderboards(client):
    """Borough grid + 2 NTA leaderboards under the 'By place' section."""
    r = client.get("/")
    for fragment in (
        "By Place",
        "Boroughs at a Glance",
        "Top Neighborhoods — High Schools",
        "Top Neighborhoods — Elementary Schools",
    ):
        assert fragment in r.text, f"missing place section: {fragment!r}"
    # Borough grid: 5 boroughs, each as a <td>.
    for boro in ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"):
        assert f"<td class=\"px-4 py-2 text-slate-900\">{boro}</td>" in r.text


def test_school_page_includes_neighborhood_peers(client):
    """ES school page should show the 'Schools nearby' section with both
    NTA and district cohorts, and highlight the focal school."""
    r = client.get("/school/15K321")
    assert "Schools Nearby" in r.text
    assert "Park Slope-Gowanus" in r.text  # NTA label
    assert "District 15" in r.text
    # Focal-school highlight appears once per cohort.
    assert r.text.count("this school</span>") == 2


def test_high_school_page_omits_district_peer_cohort(client):
    """HS aren't district-zoned (city-wide choice), so school page should
    skip the district cohort but still show the NTA cohort."""
    r = client.get("/school/02M475")  # Stuyvesant
    assert "Schools Nearby" in r.text
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
    assert "Accountability Dashboard" not in r.text


def test_search_with_empty_query_keeps_dashboard(client):
    """Clearing the search input shouldn't lose the dashboard; the user
    is back to "browse mode."""
    r = client.get("/search", params={"q": ""})
    assert r.status_code == 200
    assert "Accountability Dashboard" in r.text


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
    assert "Demographics by Year" in r.text


def test_school_page_includes_all_sections(client):
    r = client.get("/school/15K321")
    assert r.status_code == 200
    # Distinctive substrings rather than full headers — heading text is now
    # broken up by inline term() popover triggers (e.g. "NYS <btn>ESSA</btn>
    # Accountability").
    for fragment in (
        "Quick Stats",
        ">ESSA</button>",
        "Chronic Absenteeism",
        "Spending",
        "Staffing",
        "School Info",
        "Location",
        "ELA Exam",
        "Math Exam",
        "Class Size",
        "Galaxy Budget",
        "Demographics by Year",
    ):
        assert fragment in r.text, f"missing section fragment: {fragment}"


def test_high_school_page_includes_hs_only_sections(client):
    r = client.get("/school/22K405")  # Midwood
    assert r.status_code == 200
    for fragment in (
        "High School Directory",
        "Performance",
        "Admissions Programs",
        "Academic Offerings",
        "Athletics",
        "Regents Exams",
        "HS Graduation Rate",
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


def test_school_page_404_escapes_dbn(client):
    """Issue #3: the hand-built 404 body must escape the user-controlled
    path param — it bypasses Jinja autoescape. Payload is slash-free so
    it actually matches the {dbn} segment and reaches the handler."""
    r = client.get("/school/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E")
    assert r.status_code == 404
    assert "<img" not in r.text
    assert "&lt;img" in r.text


def test_neighborhood_page_404_escapes_query(client):
    """Issue #3, second site: the {query:path} converter accepts arbitrary
    payloads, including slashes."""
    r = client.get("/neighborhood/x%2F%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E")
    assert r.status_code == 404
    assert "<img" not in r.text
    assert "&lt;img" in r.text


def test_school_page_renders_swd_outcomes_section(client):
    """Regular HS with SWD enrollment gets the new SWD-outcomes section."""
    r = client.get("/school/15K462")
    assert r.status_code == 200
    assert "Outcomes for Students With Disabilities" in r.text
    # The 4/5/6-year cohort table appears for any HS with grad data.
    assert "SWD Graduation Rate" in r.text
    # Suppression rendering uses the slate-300 "Suppressed" treatment.
    assert "Suppressed" in r.text


def test_d75_school_shows_badge_and_swd_section(client):
    """D75 school shows the amber District-75 badge in the page header
    and the SWD-outcomes section with the placement-system caveat."""
    r = client.get("/school/75K004")
    assert r.status_code == 200
    # Badge text — the term() popover wraps it, so the literal "District 75"
    # substring appears as the trigger label.
    assert "District 75" in r.text
    assert "Outcomes for Students With Disabilities" in r.text


def test_non_d75_school_omits_d75_badge(client):
    """Non-D75 schools should never show the District-75 badge. Match the
    badge container class combo rather than the literal "District 75"
    string — the latter legitimately appears in the WebMCP imperative
    tool's description text on every school page."""
    r = client.get("/school/15K321")
    assert r.status_code == 200
    assert 'rounded-full text-xs font-medium bg-amber-50 text-amber-900' not in r.text


@pytest.mark.parametrize("path", ["/", "/school/15K321", "/sources"])
def test_footer_shows_deploy_marker(client, path):
    """Every full HTML page footer surfaces the deploy version so we can
    tell at a glance which commit is live. Local TestClient runs have no
    GIT_COMMIT_SHA env set, so the marker reads `dev`."""
    r = client.get(path)
    assert r.status_code == 200
    assert "Deploy" in r.text
    # Local runs render the literal "dev" since no CI build-arg threaded
    # a real SHA in. In prod the same template renders a 7-char SHA.
    assert ">dev<" in r.text or "/commit/" in r.text


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data_loaded"] is True
    # caches_warming may be true (background task still running) or false
    # (warm completed before this check fires) — both are healthy states.
    assert isinstance(body["caches_warming"], bool)
    # 0.0 is legal: a second lifespan in the same process (another test
    # module's TestClient) finds every lru_cache primed and warms in <5ms.
    assert body["caches_warm_s"] is None or body["caches_warm_s"] >= 0


def test_robots_txt_blocks_training_crawlers_and_allows_search(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # Named training crawlers we explicitly disallow.
    for ua in ("GPTBot", "ClaudeBot", "Google-Extended", "CCBot", "PerplexityBot"):
        assert f"User-agent: {ua}\nDisallow: /" in body, ua
    # Everyone else is allowed — Googlebot/Bingbot index normally, MCP
    # clients aren't impeded.
    assert "User-agent: *\nAllow: /" in body


def test_mcp_endpoint_accepts_bare_path_without_redirect(client):
    """`/mcp` (no trailing slash) must respond with the MCP initialize
    handshake directly, not a 307 redirect to `/mcp/`. Some clients
    (Claude Code's `claude mcp add`) normalize the trailing slash off
    URLs they store, and Starlette's auto-redirect on the bare mount
    path was breaking them. The _McpTrailingSlashMiddleware in main.py
    rewrites the scope path to `/mcp/` before routing — pin that
    behavior here."""
    init_payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    # `/mcp` and `/mcp/` should behave identically.
    for url in ("/mcp", "/mcp/"):
        r = client.post(url, json=init_payload, headers=headers)
        assert r.status_code == 200, f"{url}: expected 200, got {r.status_code} (body: {r.text[:200]})"
        assert "Mcp-Session-Id" in {k.title() for k in r.headers}
        assert "text/event-stream" in r.headers.get("content-type", "")


# ----- MS admission UI: school page + /zoned reframe ---------------------

def test_ms_school_page_renders_admits_section_with_programs(client):
    """MS 297 has both a Screened program and a Zone Priority program.
    The "How This School Admits" section should render both, the
    intro callout should name the multi-program reality, and the
    priority cascade strings from the directory should appear verbatim."""
    r = client.get("/school/02M297")
    assert r.status_code == 200
    text = r.text
    assert "How This School Admits" in text
    assert "district-based choice" in text
    assert "Screened" in text and "Zone Priority" in text
    # MS 297 carries 2 programs — intro callout should say so.
    assert "2 programs" in text
    # Verbatim cascade strings from the directory.
    assert "Priority to applicants whose sibling" in text
    assert "schools_in_district(2" in text


def test_es_school_page_does_not_render_ms_admits_section(client):
    r = client.get("/school/15K321")
    assert r.status_code == 200
    assert "How This School Admits" not in r.text


def test_hs_school_page_does_not_render_ms_admits_section(client):
    r = client.get("/school/02M475")  # Stuyvesant
    assert r.status_code == 200
    assert "How This School Admits" not in r.text


def test_zoned_page_reframes_ms_as_district_choice(client):
    """The zoned page for an address in a district with MS choice
    should render the new framing: the ms_admission_note callout,
    the "Your Zone-Priority School" heading (when there's a match),
    and the "Other District N Middle Schools You Can Rank" cohort
    section with admission-method chips."""
    import httpx
    import respx
    from app.services.zoning import GEOSEARCH_URL
    with respx.mock(assert_all_called=False) as rmock:
        rmock.get(GEOSEARCH_URL).mock(return_value=httpx.Response(200, json={
            "features": [{
                "geometry": {"coordinates": [-74.000238, 40.749014]},
                "properties": {"label": "428 W 26 STREET, Manhattan, NY, USA", "borough": "Manhattan"},
            }]
        }))
        r = client.get("/zoned", params={"address": "428 W 26th St Manhattan"})
    assert r.status_code == 200
    text = r.text
    assert "Choice-Based Admission" in text
    assert "Your Zone-Priority School" in text
    assert "Other District 2 Middle Schools You Can Rank" in text
    # Method chips render across the cohort.
    assert "Screened" in text
    assert "Zone Priority" in text
    assert "Open" in text


def test_zoned_page_district_choice_branch_renders_cohort_without_zone_callout(client):
    """For an address in a D15 fallback-only zone (no school-specific
    zone match), the page should NOT show the zone-priority callout
    and SHOULD show the district cohort + the district_choice note."""
    import httpx
    import respx
    from app.services.zoning import GEOSEARCH_URL
    with respx.mock(assert_all_called=False) as rmock:
        rmock.get(GEOSEARCH_URL).mock(return_value=httpx.Response(200, json={
            "features": [{
                "geometry": {"coordinates": [-73.997, 40.6693]},
                "properties": {"label": "D15 fallback point", "borough": "Brooklyn"},
            }]
        }))
        r = client.get("/zoned", params={"address": "anywhere D15"})
    assert r.status_code == 200
    text = r.text
    assert "Your Zone-Priority School" not in text
    assert "District 15 Middle Schools You Can Rank" in text
    assert "no zone-priority school for this address" in text


def test_ms_school_page_imperative_tool_includes_admission_methods(client):
    """The in-browser get_current_school_details tool should carry
    ms_admission on its return payload for any school in the MS
    Directory, so a chat-panel agent can answer "how does this school
    admit?" without an MCP round-trip."""
    r = client.get("/school/02M297")
    assert r.status_code == 200
    text = r.text
    # The agent_context JSON is dumped inline as `const CONTEXT = {...}`.
    assert '"ms_admission":' in text
    assert '"admission_methods":' in text
    # And the tool description should mention the section so the agent
    # knows when to request it — priority strings surfaced verbatim.
    assert "ms_admission = how the school admits" in text
    assert "quote the published priority strings verbatim" in text
