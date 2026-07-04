"""FastAPI app entry. Loads data on startup, mounts web + MCP routes."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastmcp.utilities.lifespan import combine_lifespans

from . import config  # noqa: F401  -- ensures NYC_SCHOOLS_DATA_DIR is set
from . import data
from .mcp_server import mcp
from .ratelimit import RateLimitMiddleware
from .services.analytics import warm_caches
from .web import routes as web_routes

log = logging.getLogger("nyc_schools_agentic")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# Surface warm-up progress on /healthz so a deploy / monitor can tell
# warm-vs-cold from outside. State is process-global because warm_caches
# uses lru_cache (also process-global); there's no shared-state issue.
_warm_state: dict = {"running": False, "elapsed_s": None}


def _run_warm_caches() -> None:
    """Sync helper that runs in a worker thread so it doesn't block the
    event loop. pandas releases the GIL inside C extensions, so other
    requests proceed even while this is grinding."""
    t = time.monotonic()
    try:
        warm_caches()
        # Homepage chart payloads live in the web layer (main.py already
        # imports web, so this direction is layering-clean). The NTA map
        # runs three uncached level=None aggregations — several seconds
        # each cold, so warm them here rather than on the first user hit.
        from .web.charts import homepage_citywide, homepage_nta_map
        homepage_citywide()
        homepage_nta_map()
        _warm_state["elapsed_s"] = round(time.monotonic() - t, 2)
        log.info("Caches warm in %.1fs (background)", _warm_state["elapsed_s"])
    except Exception:
        log.exception("warm_caches failed in background")
    finally:
        _warm_state["running"] = False


@asynccontextmanager
async def data_lifespan(app: FastAPI):
    log.info("Loading committed data from %s ...", config.DB_PATH)
    data.load()
    log.info("Data loaded: %s", data.summary())
    # warm_caches() runs in the background so /healthz responds the moment
    # uvicorn binds. First user hit to /, /neighborhood/*, etc. may pay an
    # un-amortized cost; everything after is fast. This trades a single
    # cold-request stall for resilience against shared-CPU throttling — at
    # shared-cpu-1x, blocking warm-up takes ~230s and flaps healthchecks.
    _warm_state["running"] = True
    warm_task: Optional[asyncio.Task] = asyncio.create_task(
        asyncio.to_thread(_run_warm_caches)
    )
    try:
        yield
    finally:
        if warm_task and not warm_task.done():
            warm_task.cancel()
        log.info("Shutting down")


class _SecurityHeadersMiddleware:
    """Baseline hardening headers on every response (issue #4). No CSP on
    purpose: the site's inline scripts (Plot charts, GA bootstrap, WebMCP
    imperative tools) would require 'unsafe-inline', which forfeits CSP's
    protection against inline reflection — the vector issue #3 fixed at
    the root. Revisit with nonces if the calculus changes."""

    _HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"x-frame-options", b"SAMEORIGIN"),
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + list(self._HEADERS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _McpTrailingSlashMiddleware:
    """Rewrite incoming `/mcp` to `/mcp/` at the ASGI layer before Starlette
    routes the request.

    The MCP app is mounted at `/mcp` with internal path `/`, making `/mcp/`
    the canonical URL. Some MCP HTTP clients normalize trailing slashes off
    URLs they store (Claude Code's `claude mcp add` does this — registers
    `https://host/mcp/` as `https://host/mcp`), and Starlette / FastAPI's
    auto-307-redirect from `/mcp` to `/mcp/` is brittle behind a
    TLS-terminating proxy: without --proxy-headers, the redirect URL is
    `http://...`, which the client either follows insecurely or rejects.

    Rewriting at the ASGI scope level avoids the redirect entirely — the
    request hits the mount with path `/mcp/`, scheme is preserved end-to-
    end, and clients don't have to follow POST redirects."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
            scope["raw_path"] = b"/mcp/"
        await self.app(scope, receive, send)


# Streamable HTTP ASGI sub-app. path="/" makes it serve at the mount point
# itself, so the canonical URL is /mcp/ (with trailing slash).
mcp_app = mcp.http_app(path="/")

app = FastAPI(
    title="NYC Schools Agentic",
    lifespan=combine_lifespans(data_lifespan, mcp_app.lifespan),
)
app.add_middleware(_McpTrailingSlashMiddleware)
app.add_middleware(_SecurityHeadersMiddleware)
# Outermost: per-IP token bucket over site + /mcp/ alike (/healthz exempt
# so Fly healthchecks never trip it). Limits via RATE_LIMIT_RATE /
# RATE_LIMIT_BURST env; published in /llms.txt and README.
app.add_middleware(RateLimitMiddleware)
app.include_router(web_routes.router)
app.mount("/mcp", mcp_app)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {
        "status": "ok",
        "data_loaded": data.is_loaded(),
        "caches_warming": _warm_state["running"],
        "caches_warm_s": _warm_state["elapsed_s"],
    }
