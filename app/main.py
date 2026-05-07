"""FastAPI app entry. Loads data on startup, mounts web routes."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config  # noqa: F401  -- ensures NYC_SCHOOLS_DATA_DIR is set
from . import data
from .web import routes as web_routes

log = logging.getLogger("nyc_schools_agentic")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Loading nycschools data from %s ...", config.DATA_DIR)
    data.load()
    log.info("Data loaded: %s", data.summary())
    yield
    log.info("Shutting down")


app = FastAPI(title="NYC Schools Agentic", lifespan=lifespan)
app.include_router(web_routes.router)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok", "data_loaded": data.is_loaded()}
