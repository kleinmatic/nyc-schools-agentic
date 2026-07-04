"""HTML routes. Thin adapters over services/schools.py — no business logic here."""
import itertools
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config

# Build-time SHA stamped into the Docker image by the CI deploy step
# (--build-arg COMMIT_SHA=${{ github.sha }}). The 7-char short form is
# what GitHub/Fly UIs show, so the footer matches at a glance. Falls
# back to "dev" for local uvicorn runs where the env var isn't set.
COMMIT_SHA_FULL = os.environ.get("GIT_COMMIT_SHA", "dev")
COMMIT_SHA_SHORT = COMMIT_SHA_FULL[:7] if COMMIT_SHA_FULL != "dev" else "dev"
from ..services.analytics import (
    get_neighborhood,
    homepage_borough_grid,
    homepage_leaderboards,
    homepage_neighborhood_leaderboards,
    school_peers,
    schools_in_district,
)
from ..services.schools import get_school, school_swd_outcomes, search_schools
from ..services.zoning import find_zoned_schools, geocode
from .charts import (
    citywide_level_breakdown,
    exam_grade_year_levels,
    homepage_citywide,
    homepage_nta_map,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


# Display labels for internal school-level codes. Stay aware: MCP tools and
# services keep the raw values ("high", "middle", etc.); this mapping is
# template-layer only.
LEVEL_LABELS = {
    "elementary": "Elementary School",
    "middle":     "Middle School",
    "high":       "High School",
    "K-8":        "K-8",
    "6-12":       "6-12",
    "K-12":       "K-12",
}


def _level_label(value):
    if value is None:
        return ""
    return LEVEL_LABELS.get(value, value)


def _pretty(value):
    """Replace straight ASCII apostrophes with curly U+2019. Safe for
    every school + NTA name in our data — surveyed, all uses are
    possessive (Children's, Mariner's, etc.), no leading-quote or
    contraction-ambiguity cases. Display-only; raw data unchanged for
    DBN keys / fuzzy search input / MCP contracts."""
    if value is None:
        return ""
    return str(value).replace("'", "’")


templates.env.filters["level"] = _level_label
templates.env.filters["pretty"] = _pretty
templates.env.globals["commit_sha_short"] = COMMIT_SHA_SHORT
templates.env.globals["commit_sha_full"] = COMMIT_SHA_FULL


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _make_uid():
    """Return a per-request callable that produces unique element IDs.

    Templates use it to create stable popovertarget references without
    needing a hash filter or page-wide stateful counter.
    """
    counter = itertools.count()
    return lambda prefix="id": f"{prefix}-{next(counter)}"


def _dashboard_context() -> dict:
    """The cluster of leaderboards / aggregates that make up the homepage
    accountability dashboard. Pulled into one place so /` and empty-query
    /search render the same thing."""
    return {
        "leaderboards": homepage_leaderboards(),
        "nta_leaderboards": homepage_neighborhood_leaderboards(),
        "borough_grid": homepage_borough_grid(),
        "citywide": homepage_citywide(),
        "nta_map": homepage_nta_map(),
    }


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    ctx = {"results": [], "query": ""}
    ctx.update(_dashboard_context())
    return templates.TemplateResponse(request, "search.html", ctx)


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    results = search_schools(q)
    template = "partials/results.html" if _is_htmx(request) else "search.html"
    # No dashboard on /search?q=… — search-result focus. Empty-query
    # /search behaves like the homepage and gets it too, so a user who
    # clears the input doesn't lose the dashboard.
    ctx = {"results": results, "query": q}
    if not _is_htmx(request) and not q.strip():
        ctx.update(_dashboard_context())
    return templates.TemplateResponse(request, template, ctx)


@router.get("/zoned", response_class=HTMLResponse)
async def zoned_page(request: Request, address: str = ""):
    geo = await geocode(address) if address.strip() else None
    result = find_zoned_schools(geo.lat, geo.lon) if geo else None
    # When the address falls in a district that runs MS by choice, pull
    # the district MS cohort so the template can render the full set the
    # family can rank, each with its admission methods. Pure MS only —
    # there isn't an equivalent ES/HS cohort tool here.
    ms_district_cohort = None
    if result and result.ms_district and result.ms_admission_type:
        ms_district_cohort = schools_in_district(result.ms_district, "middle")
    return templates.TemplateResponse(
        request,
        "zoned.html",
        {
            "address": address,
            "geo": geo,
            "result": result,
            "ms_district_cohort": ms_district_cohort,
            "uid": _make_uid(),
        },
    )


@router.get("/find")
async def find_legacy_redirect(address: str = ""):
    """Old URL — kept as a permanent redirect so any inbound links and
    bookmarks continue to work after the /find → /zoned rename."""
    target = f"/zoned?address={address}" if address else "/zoned"
    return RedirectResponse(url=target, status_code=301)


def _agent_context_for_school(detail, swd, peer_neighborhood, peer_district):
    """Generic per-school context for the WebMCP imperative tool that's
    "always called first" on a school page. Identity + demographics +
    staffing + the FOUR comparison dimensions an agent might need:

      1) `peer_ranks` — citywide rank against same-level NYC schools on
         each headline metric (ENI, ELA, math, PTR, chronic absent).
         Use for "how does this school rank in NYC?"
      2) `peer_neighborhood` — every school in the same NTA at the same
         level, with metrics, so the agent can hand-compute "vs other
         Park Slope ES." Use for neighborhood-scoped questions.
      3) `peer_district` — same for the same geographic district. Use
         for "in District 15." Null for HS (city-wide choice).
      4) `co_located_schools` — schools sharing this building. Use for
         "what other schools are here?" — especially the D75 case.

    SWD-specific outcomes go in a separate tool (see
    `_agent_swd_context_for_school`) so agents only pull the heavier
    SWD-with-comparisons payload on IEP / special-ed questions."""
    s = detail.summary
    loc = detail.location
    st = detail.staffing
    return {
        "dbn": s.dbn,
        "school_name": s.school_name,
        "short_name": s.short_name,
        "school_level": s.school_level,
        "level_label": _level_label(s.school_level),
        "borough": s.boro,
        "district": s.district,
        "is_d75": s.is_d75,
        "neighborhood_nta": loc.nta_name if loc else None,
        "url_path": f"/school/{s.dbn}",
        "latest_year": detail.demographics_by_year[-1].ay if detail.demographics_by_year else None,
        "total_enrollment": s.total_enrollment,
        "swd_enrollment_pct": swd.swd_enrollment_pct if swd else None,
        "peer_ranks": {k: v.model_dump() for k, v in detail.peer_ranks.items()},
        "peer_neighborhood": peer_neighborhood.model_dump() if peer_neighborhood else None,
        "peer_district": peer_district.model_dump() if peer_district else None,
        "staffing": st.model_dump() if st else None,
        "co_located_schools": [
            {
                "dbn": c.dbn,
                "school_name": c.school_name,
                "is_d75": c.dbn.startswith("75"),
                "shared_building_ids": c.building_ids,
            }
            for c in detail.co_located
        ],
        # MS admission methods + per-program priority cascades from the
        # NYC DOE Middle School Directory. Populated for any school in
        # the directory (pure MS, K-8, 6-12); None otherwise. Lets an
        # in-browser agent answer "how does this school admit?" without
        # round-tripping back to the MCP server.
        "ms_admission": detail.ms_admission.model_dump() if detail.ms_admission else None,
    }


def _agent_swd_context_for_school(swd):
    """SWD-specific payload for the second WebMCP tool, registered with a
    description that tells the agent to call it only when the user
    identifies as needing IEP / special-education context. Includes
    `cohort_context` per metric — the journalism layer for SWD numbers
    (rank vs same-level NYC SWD cohort, citywide median, extremes,
    pre-computed narrative string). Returns None if the school has no
    SWD outcomes payload at all."""
    if swd is None:
        return None
    return swd.model_dump()


@router.get("/school/{dbn}", response_class=HTMLResponse)
async def school_page(request: Request, dbn: str):
    detail = get_school(dbn)
    if detail is None:
        return HTMLResponse(
            content=f"<h1>School not found</h1><p>No school with DBN <code>{dbn}</code>.</p>",
            status_code=404,
        )
    swd = school_swd_outcomes(dbn)
    peer_neighborhood = school_peers(dbn, scope="neighborhood")
    # District peers are most meaningful for ES/MS — HS is city-wide
    # choice. Non-HS get the second cohort; HS just shows the NTA peers.
    peer_district = (
        school_peers(dbn, scope="district")
        if detail.summary.school_level not in ("high",) else None
    )
    return templates.TemplateResponse(
        request, "school.html",
        {
            "school": detail,
            "uid": _make_uid(),
            "peer_neighborhood": peer_neighborhood,
            "peer_district": peer_district,
            "swd": swd,
            "agent_context": _agent_context_for_school(detail, swd, peer_neighborhood, peer_district),
            "agent_swd_context": _agent_swd_context_for_school(swd),
            "ela_grade_year": exam_grade_year_levels(detail.ela),
            "math_grade_year": exam_grade_year_levels(detail.math),
            "ela_citywide_levels": citywide_level_breakdown("ela"),
            "math_citywide_levels": citywide_level_breakdown("math"),
        },
    )


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    """Data sources page: every dataset, the agency that publishes it,
    the vintage, and a link to the original source."""
    return templates.TemplateResponse(request, "sources.html", {})


@router.get("/neighborhood/{query:path}", response_class=HTMLResponse)
async def neighborhood_page(request: Request, query: str):
    """Neighborhood (NTA) report. `query` is fuzzy-matched, so colloquial
    names like 'park slope' or URL-encoded canonical names both work."""
    detail = get_neighborhood(query)
    if detail is None:
        return HTMLResponse(
            content=f"<h1>Neighborhood not found</h1><p>No NTA matched <code>{query}</code>.</p>",
            status_code=404,
        )
    return templates.TemplateResponse(
        request, "neighborhood.html",
        {
            "nbh": detail,
            # tojson can't serialize Pydantic models directly — pass a
            # plain-dict slim for the inline map script.
            "schools_geo": [
                {
                    "dbn": s.dbn, "school_name": s.school_name,
                    "school_level": s.school_level,
                    "total_enrollment": s.total_enrollment,
                    "latitude": s.latitude, "longitude": s.longitude,
                }
                for s in detail.schools
            ],
            "boundary": detail.boundary,
            "uid": _make_uid(),
        },
    )


# Block named AI training crawlers from harvesting the HTML surface, while
# leaving conventional search indexers (Googlebot, Bingbot, DuckDuckBot,
# Applebot) untouched — school pages should still be findable. Agents that
# want structured access should use /mcp/ (Streamable HTTP); /a2a/ and /acp/
# will be siblings. On-demand fetchers (ChatGPT-User, Claude-Web) are not
# blocked: those are single-page user-initiated retrievals, not the
# bulk-training crawl this is trying to deter.
_ROBOTS_TXT = """\
# AI training crawlers: disallowed.
# Agents wanting structured access should use /mcp/ (Streamable HTTP).
# Source: https://github.com/kleinmatic/nyc-schools-agentic

User-agent: GPTBot
Disallow: /

User-agent: ClaudeBot
Disallow: /

User-agent: anthropic-ai
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: Applebot-Extended
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: PerplexityBot
Disallow: /

User-agent: Bytespider
Disallow: /

User-agent: Meta-ExternalAgent
Disallow: /

User-agent: FacebookBot
Disallow: /

User-agent: Amazonbot
Disallow: /

User-agent: Diffbot
Disallow: /

User-agent: cohere-ai
Disallow: /

User-agent: Omgilibot
Disallow: /

User-agent: Timpibot
Disallow: /

User-agent: ImagesiftBot
Disallow: /

User-agent: YouBot
Disallow: /

# Everyone else (Googlebot, Bingbot, DuckDuckBot, Applebot, on-demand
# agent fetchers like ChatGPT-User / Claude-Web, and any A2A/MCP traffic):
# the site is open.
User-agent: *
Allow: /
"""


@router.get("/robots.txt", include_in_schema=False, response_class=PlainTextResponse)
async def robots_txt() -> str:
    return _ROBOTS_TXT


# ----- llms.txt + WebMCP manifest ------------------------------------------
#
# `/llms.txt` follows the proposed llmstxt.org convention (Jeremy Howard,
# Answer.AI): a root-level markdown file that gives an agent fetching the
# site a fast orientation — what it is, where the canonical URLs are, which
# endpoint serves the MCP tools. Not a spec; an early-adopter convention
# with real uptake (Stripe, Mintlify, others). For an explicitly
# agent-first site like this one, the alignment is obvious.
#
# `/.well-known/webmcp` follows an emerging — but not-yet-specced — WebMCP
# manifest convention. The Chrome team has discussed it as future work for
# pre-visit tool discovery; the freeCodeCamp WebMCP guide documents the
# shape; early adopters publish it. Our manifest declares the two
# declarative WebMCP forms exposed on every page (search + zoned), with
# their input schemas and HTTP endpoints. It is NOT a mirror of the 19
# server-MCP tools at /mcp/ — those are reachable via the standard MCP
# tools/list RPC, not via this manifest.

# Source of truth for the WebMCP manifest. Must mirror the actual
# declarative-form annotations in `partials/_webmcp_global_forms.html`
# and the inline forms on /search and /zoned. Drift is caught by
# `test_webmcp_manifest_matches_form_strings` in tests/test_webmcp.py.
_WEB_MCP_TOOLS = [
    {
        "name": "search-schools-by-name",
        "description": "Search NYC public schools by name or DBN. Returns a ranked list of matching schools with summary info.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Full or partial school name, common short name, or 6-character DBN. Examples: 'Bronx Science', 'PS 321', '15K321'.",
                },
            },
            "required": ["q"],
        },
        "endpoint": "/search",
        "method": "GET",
    },
    {
        "name": "find-zoned-schools-by-address",
        "description": "Find the NYC public schools an address is zoned for. Returns the zoned elementary school (where one exists) plus, for middle school, any school-specific zone-priority polygon hits and the district admission mechanic — NYC middle school is district-based choice, not strict zoning.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "A street address in any of NYC's five boroughs. Include the borough name. Example: '180 7th Avenue, Brooklyn'.",
                },
            },
            "required": ["address"],
        },
        "endpoint": "/zoned",
        "method": "GET",
    },
]


_LLMS_TXT = """# NYC Schools

> Interactive site and MCP server for NYC public school data. Agents are first-class consumers — the same service layer powers HTML pages for humans and an MCP server for AI agents.

Journalism-style accountability and equity data for every NYC public school, keyed by DBN (e.g. 15K321). The data refresh cycle is annual (NYSED School Report Card + DOE demographics); refreshes are committed via git so every version is reviewable.

## MCP server

- [Streamable HTTP endpoint](https://nycschools.fly.dev/mcp/) — 19 tools, no auth. Includes a dynamic-capability-discovery pair (`list_school_metrics` / `list_neighborhood_metrics` + `get_school_metric` / `get_neighborhood_metric`) alongside 15 curated tools for common patterns.
- Tool descriptions are oriented to *when an agent should use this*.

## Pages

- [Homepage](https://nycschools.fly.dev/) — leaderboards by metric and by neighborhood
- [Search](https://nycschools.fly.dev/search) — fuzzy school search by name or DBN
- [Zoned schools](https://nycschools.fly.dev/zoned) — address → zoned ES + MS
- [Sources](https://nycschools.fly.dev/sources) — every dataset and its vintage

## Reference

- [Repository](https://github.com/kleinmatic/nyc-schools-agentic) — AGPL-3.0; corresponding source per §13
- [WebMCP manifest](https://nycschools.fly.dev/.well-known/webmcp) — in-page declarative tool surface
- [llms.txt convention](https://llmstxt.org) — this file's format
"""


@router.get("/llms.txt", include_in_schema=False, response_class=PlainTextResponse)
async def llms_txt() -> str:
    return _LLMS_TXT


@router.get("/.well-known/webmcp", include_in_schema=False)
async def webmcp_manifest() -> JSONResponse:
    return JSONResponse(
        {
            "name": "nyc-schools",
            "version": "0.1.0",
            "description": "Interactive site and MCP server for NYC public school data. This manifest declares the in-page declarative WebMCP tools (form-based) exposed on every page. The server's full MCP catalog (19 tools) lives at /mcp/ over Streamable HTTP.",
            "tools": _WEB_MCP_TOOLS,
        }
    )
