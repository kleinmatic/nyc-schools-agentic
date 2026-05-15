"""HTML routes. Thin adapters over services/schools.py — no business logic here."""
import itertools

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from .. import config
from ..services.analytics import (
    get_neighborhood,
    homepage_borough_grid,
    homepage_leaderboards,
    homepage_neighborhood_leaderboards,
    school_peers,
)
from ..services.schools import get_school, search_schools
from ..services.zoning import find_zoned_schools, geocode
from .charts import (
    citywide_level_breakdown,
    exam_grade_year_levels,
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


@router.get("/find", response_class=HTMLResponse)
async def find_by_address(request: Request, address: str = ""):
    geo = await geocode(address) if address.strip() else None
    result = find_zoned_schools(geo.lat, geo.lon) if geo else None
    return templates.TemplateResponse(
        request,
        "find.html",
        {
            "address": address,
            "geo": geo,
            "result": result,
            "uid": _make_uid(),
        },
    )


@router.get("/school/{dbn}", response_class=HTMLResponse)
async def school_page(request: Request, dbn: str):
    detail = get_school(dbn)
    if detail is None:
        return HTMLResponse(
            content=f"<h1>School not found</h1><p>No school with DBN <code>{dbn}</code>.</p>",
            status_code=404,
        )
    return templates.TemplateResponse(
        request, "school.html",
        {
            "school": detail,
            "uid": _make_uid(),
            "peer_neighborhood": school_peers(dbn, scope="neighborhood"),
            # District peers are most meaningful for ES/MS — HS is city-wide
            # choice. Non-HS get the second cohort; HS just shows the NTA peers.
            "peer_district": (
                school_peers(dbn, scope="district")
                if detail.summary.school_level not in ("high",) else None
            ),
            "ela_grade_year": exam_grade_year_levels(detail.ela),
            "math_grade_year": exam_grade_year_levels(detail.math),
            "ela_citywide_levels": citywide_level_breakdown("ela"),
            "math_citywide_levels": citywide_level_breakdown("math"),
        },
    )


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
