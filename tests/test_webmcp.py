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
        # Pages that host toolnames inline in their primary content pick
        # the rest up via the webmcp_exclude re-include of the global
        # partial — every page must expose all three tools.
        ("/zoned", "find-zoned-schools-by-address"),
        ("/zoned", "search-schools-by-name"),
        ("/zoned", "find-schools-in-neighborhood"),
        ("/search", "search-schools-by-name"),
        ("/search", "find-zoned-schools-by-address"),
        ("/search", "find-schools-in-neighborhood"),
        ("/", "search-schools-by-name"),
        ("/", "find-zoned-schools-by-address"),
        ("/", "find-schools-in-neighborhood"),
        # Pages that pick up all toolnames via the base.html global block.
        ("/school/15K321", "search-schools-by-name"),
        ("/school/15K321", "find-zoned-schools-by-address"),
        ("/school/15K321", "find-schools-in-neighborhood"),
        ("/neighborhood/Park-Slope-Gowanus", "search-schools-by-name"),
        ("/neighborhood/Park-Slope-Gowanus", "find-zoned-schools-by-address"),
        ("/neighborhood/Park-Slope-Gowanus", "find-schools-in-neighborhood"),
        ("/sources", "search-schools-by-name"),
        ("/sources", "find-zoned-schools-by-address"),
        ("/sources", "find-schools-in-neighborhood"),
    ],
)
def test_webmcp_toolname_present(client, path, expected_tool):
    r = client.get(path)
    assert r.status_code == 200
    assert f'toolname="{expected_tool}"' in r.text


@pytest.mark.parametrize(
    "path",
    ["/", "/zoned", "/search", "/school/15K321", "/neighborhood/Park-Slope-Gowanus", "/sources"],
)
def test_webmcp_form_carries_description_and_autosubmit(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "tooldescription=" in r.text
    assert "toolautosubmit" in r.text


@pytest.mark.parametrize(
    "path",
    ["/", "/zoned", "/search", "/school/15K321", "/neighborhood/Park-Slope-Gowanus", "/sources"],
)
def test_webmcp_input_has_param_description(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "toolparamdescription=" in r.text


@pytest.mark.parametrize(
    "path, toolname",
    [
        # Spec compliance: each toolname must appear exactly once per page.
        # Pages with inline forms (/, /zoned) exclude those names from the
        # global-partial re-include; pages without inline forms pick up the
        # single global instance. We match ` toolname="..."` (leading space)
        # to exclude `data-toolname="..."` on the agent-active-pill <button>
        # — that's a JS-side selector hook, not a WebMCP form annotation.
        ("/", "search-schools-by-name"),
        ("/", "find-zoned-schools-by-address"),
        ("/", "find-schools-in-neighborhood"),
        ("/zoned", "find-zoned-schools-by-address"),
        ("/zoned", "search-schools-by-name"),
        ("/zoned", "find-schools-in-neighborhood"),
        ("/school/15K321", "search-schools-by-name"),
        ("/school/15K321", "find-zoned-schools-by-address"),
        ("/school/15K321", "find-schools-in-neighborhood"),
        ("/neighborhood/Park-Slope-Gowanus", "search-schools-by-name"),
        ("/sources", "search-schools-by-name"),
    ],
)
def test_webmcp_toolname_is_unique_per_page(client, path, toolname):
    r = client.get(path)
    assert r.status_code == 200
    needle = f' toolname="{toolname}"'
    assert r.text.count(needle) == 1, (
        f"{path}: expected exactly one form-level toolname={toolname!r}, "
        f"got {r.text.count(needle)}"
    )


# The two imperative-tool descriptions, verbatim from school.html. Pinned
# here so the length caps (Chrome WebMCP security guidance: lean tool
# descriptions) fail loudly if someone fattens them back up.
SCHOOL_TOOL_DESCRIPTION = (
    "Call this first on any question about the school on this page. Always "
    "returns identity and headline stats; pass sections to add comparison "
    "blocks. Pair every stat with its cohort position, in neutral positional "
    "language — highest/lowest, above/below the median — never "
    "best/worst/better/worse. Sections: peer_ranks = citywide rank vs "
    "same-level NYC schools; peer_neighborhood = same-NTA peers; "
    "peer_district = same-district peers (null for HS — city-wide choice); "
    "co_located_schools = schools sharing this building; staffing = "
    "counselor/social-worker FTE; ms_admission = how the school admits — "
    "quote the published priority strings verbatim; all = everything."
)
SWD_TOOL_DESCRIPTION = (
    "Returns this school's Students-With-Disabilities (SWD) subgroup "
    "outcomes — graduation, chronic absenteeism, CCCR, ESSA — each with "
    "cohort_context: rank vs the same-level NYC SWD cohort, citywide median, "
    "and extremes. Quote cohort_context.narrative verbatim; it uses neutral "
    "positional language (highest/lowest quartile, above/below the median). "
    "Call this only for special-education questions: IEPs, learning "
    "disabilities, autism, speech delays, or how the school serves students "
    "with disabilities."
)


@pytest.mark.parametrize("dbn", ["15K321", "75K004", "02M475"])
def test_school_page_registers_imperative_current_school_tool(client, dbn):
    """School pages register a WebMCP imperative tool that returns the
    current school's identity + selectable comparison blocks so an
    in-browser agent has page context without DOM access."""
    r = client.get(f"/school/{dbn}")
    assert r.status_code == 200
    # Chrome 150+ API surface, with the pre-150 fallback still present.
    assert "document.modelContext" in r.text
    assert "navigator.modelContext" in r.text
    assert 'registerTool' in r.text
    assert 'name: "get_current_school_details"' in r.text
    # DBN must appear in the embedded JSON context payload.
    assert f'"dbn": "{dbn}"' in r.text
    # All four comparison dimensions must travel in the embedded context
    # (execute() selects from it client-side via `sections`).
    assert '"peer_ranks":' in r.text
    assert '"peer_neighborhood":' in r.text
    assert '"peer_district":' in r.text
    assert '"co_located_schools":' in r.text
    # Composability: the `sections` inputSchema enum must be declared.
    assert (
        'enum: ["peer_ranks", "peer_neighborhood", "peer_district", '
        '"co_located_schools", "staffing", "ms_admission", "all"]'
    ) in r.text


def test_school_page_registers_swd_specific_tool_when_outcomes_exist(client):
    """Second WebMCP tool — only registered when there's SWD data to
    return. Description steers the agent to call it for IEP/special-ed
    questions specifically. PS 321 has SWD data, so it should register
    the tool."""
    r = client.get("/school/15K321")
    assert r.status_code == 200
    assert 'name: "get_swd_outcomes_for_current_school"' in r.text
    # Tool description must name the IEP / special-ed trigger conditions
    # so the agent knows when this tool applies.
    assert "IEP" in r.text
    # cohort_context must travel in the SWD tool's payload.
    assert '"cohort_context":' in r.text


def test_school_page_imperative_tool_descriptions_are_lean(client):
    """Chrome WebMCP guidance: tool descriptions ≤ 500 chars (our hard cap
    700), lean outputs. Pin the rendered descriptions verbatim and cap
    their length so drift or bloat fails here, not in an agent session."""
    r = client.get("/school/15K321")
    assert r.status_code == 200
    assert SCHOOL_TOOL_DESCRIPTION in r.text
    assert SWD_TOOL_DESCRIPTION in r.text
    assert len(SCHOOL_TOOL_DESCRIPTION) <= 700
    assert len(SWD_TOOL_DESCRIPTION) <= 500
    # Positive-language rule: descriptions say when to call, never "Do NOT".
    assert "Do NOT" not in SCHOOL_TOOL_DESCRIPTION
    assert "Do NOT" not in SWD_TOOL_DESCRIPTION
    # execute() must return a string, not a raw object (Chrome guidance).
    assert "return JSON.stringify(payload);" in r.text
    assert "return JSON.stringify(SWD_CONTEXT);" in r.text


def test_school_page_renders_swd_cohort_narrative(client):
    """The 'How This School Compares' section surfaces the pre-computed
    narrative strings — the journalism layer that pairs every SWD number
    with a cohort verdict."""
    r = client.get("/school/15K321")  # PS 321 — strong SWD chronic numbers
    assert r.status_code == 200
    assert "How This School Compares" in r.text
    # narrative string format: "...quartile (lowest/highest) of NYC ... schools
    # — this school: X.X%; cohort median: X.X%; ranked N of M"
    assert "quartile" in r.text
    assert "cohort median:" in r.text


def test_find_legacy_redirects_to_zoned(client):
    r = client.get("/find", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"].startswith("/zoned")


def test_find_legacy_redirect_preserves_address(client):
    r = client.get("/find", params={"address": "180 7th Ave Brooklyn"}, follow_redirects=False)
    assert r.status_code == 301
    assert "address=" in r.headers["location"]
    assert r.headers["location"].startswith("/zoned")


# ----- /llms.txt + /.well-known/webmcp -----
#
# Two emerging-convention discovery endpoints. /llms.txt orients agents to
# the site (llmstxt.org). /.well-known/webmcp declares the in-page WebMCP
# tools for pre-visit discovery (Chrome team future-work, not specced).
# Neither is required; both are additive signals for agent-first sites.

def test_llms_txt_returns_200_with_orientation_block(client):
    r = client.get("/llms.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    # The MCP endpoint must be discoverable — that's the whole point.
    assert "https://nycschools.datatribune.io/mcp/" in text
    # The launch posture: MCP requires a token, and llms.txt must say so.
    assert "X-Schools-Token" in text
    # Pointer to the WebMCP manifest for the in-page tool surface.
    assert "/.well-known/webmcp" in text
    # H1 + summary blockquote per llms.txt convention.
    assert text.startswith("# NYC Schools")
    assert "\n> " in text


def test_webmcp_manifest_returns_valid_json_with_all_tools(client):
    r = client.get("/.well-known/webmcp")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    manifest = r.json()
    assert manifest["name"] == "nyc-schools"
    tool_names = {t["name"] for t in manifest["tools"]}
    assert tool_names == {
        "search-schools-by-name",
        "find-zoned-schools-by-address",
        "find-schools-in-neighborhood",
    }
    # Each tool carries its endpoint + method so a pre-visit agent knows
    # how to invoke without parsing the HTML form.
    for t in manifest["tools"]:
        assert t["endpoint"].startswith("/")
        assert t["method"] in ("GET", "POST")
        assert t["inputSchema"]["type"] == "object"
        assert t["inputSchema"]["required"]  # at least one required field


def test_webmcp_manifest_strings_match_form_partial(client):
    """Drift guard: every tool name and description in the manifest must
    appear verbatim in the rendered form partial. If the two are
    edited independently, the manifest stops being an honest
    declaration. Catches this in one place rather than discovering
    drift via agent confusion."""
    manifest = client.get("/.well-known/webmcp").json()
    # /school/15K321 picks up the partial via base.html's
    # global_webmcp_forms block — convenient page to inspect.
    page = client.get("/school/15K321").text
    for tool in manifest["tools"]:
        assert f'toolname="{tool["name"]}"' in page, (
            f'manifest declares {tool["name"]!r} but the form partial does not'
        )
        assert tool["description"] in page, (
            f'manifest description for {tool["name"]!r} does not match the form'
        )


# ----- WebMCP origin trial token -----

def test_origin_trial_meta_tag_on_every_page(client):
    """The Chrome WebMCP origin trial (149+) is enabled via an
    `origin-trial` meta tag carrying a token scoped to the canonical
    origin. Without it, only flag-enabled Chromes expose our tools.
    Token expires 2026-11-17 — when this test starts failing after a
    token swap, update the substring."""
    for path in ("/", "/school/15K321", "/sources"):
        page = client.get(path).text
        assert 'http-equiv="origin-trial"' in page, f"{path} missing origin-trial meta"
        # Spot-check it's the WebMCP token (payload tail encodes the feature).
        assert "WnStlCKsaZUoeHdo9WhPsw8" in page, f"{path} has wrong/missing OT token"
