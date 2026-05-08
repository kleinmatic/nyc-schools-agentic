"""FastAPI app entry. Loads data on startup, mounts web + MCP routes."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastmcp.utilities.lifespan import combine_lifespans

from . import config  # noqa: F401  -- ensures NYC_SCHOOLS_DATA_DIR is set
from . import data
from .mcp_server import mcp
from .web import routes as web_routes

log = logging.getLogger("nyc_schools_agentic")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def data_lifespan(app: FastAPI):
    log.info("Loading committed data from %s ...", config.DB_PATH)
    data.load()
    log.info("Data loaded: %s", data.summary())
    yield
    log.info("Shutting down")


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
app.include_router(web_routes.router)
app.mount("/mcp", mcp_app)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok", "data_loaded": data.is_loaded()}
