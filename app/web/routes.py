"""HTML routes. Thin adapters over services/schools.py — no business logic here."""
import itertools

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import config
from ..services.analytics import (
    homepage_borough_grid,
    homepage_leaderboards,
    homepage_neighborhood_leaderboards,
    school_peers,
)
from ..services.schools import get_school, search_schools
from ..services.zoning import find_zoned_schools, geocode

router = APIRouter()
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


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
        },
    )
