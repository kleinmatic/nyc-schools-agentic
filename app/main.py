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


# Streamable HTTP ASGI sub-app. path="/" makes it serve at the mount point
# itself, so the canonical URL is /mcp/ (with trailing slash).
mcp_app = mcp.http_app(path="/")

app = FastAPI(
    title="NYC Schools Agentic",
    lifespan=combine_lifespans(data_lifespan, mcp_app.lifespan),
)
app.include_router(web_routes.router)
app.mount("/mcp", mcp_app)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok", "data_loaded": data.is_loaded()}
