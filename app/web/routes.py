"""HTML routes. Thin adapters over services/schools.py — no business logic here."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import config
from ..services.schools import get_school, search_schools

router = APIRouter()
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "search.html", {"results": [], "query": ""})


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    results = search_schools(q)
    template = "partials/results.html" if _is_htmx(request) else "search.html"
    return templates.TemplateResponse(request, template, {"results": results, "query": q})


@router.get("/school/{dbn}", response_class=HTMLResponse)
async def school_page(request: Request, dbn: str):
    detail = get_school(dbn)
    if detail is None:
        return HTMLResponse(
            content=f"<h1>School not found</h1><p>No school with DBN <code>{dbn}</code>.</p>",
            status_code=404,
        )
    return templates.TemplateResponse(request, "school.html", {"school": detail})
