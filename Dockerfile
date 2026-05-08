# Production runtime image for the FastAPI app.
#
# Single-stage build: simpler to reason about and image size is fine for a
# demo (~600 MB; geopandas system libs are most of it). Multi-stage with a
# slim runtime would shave that to ~300 MB if needed later.
#
# Data files in data/ (LFS-tracked) are expected to be materialized in the
# build context — the Fly remote builder gets them via `flyctl deploy`, and
# CI gets them via `actions/checkout@v4` with `lfs: true`.

FROM python:3.12-slim-bookworm

# System libs geopandas needs at runtime (gdal/geos/proj). The "*-dev"
# variants are heavier than strictly necessary but include the .so files
# at predictable paths; trimming to runtime-only libs is a future optimization.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# uv: copy the static binary from the official image rather than running an
# installer script — faster, no curl/network needed.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install runtime deps from the lock file. --no-dev skips the dev group;
# the build group (which includes nycschools, only used by build_db.py) is
# also excluded since it's not in the default install set. --no-install-project
# keeps uv from trying to install the app itself as a package — we run from
# /app via WORKDIR + PYTHONPATH.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

# Application code + committed data
COPY app/ ./app/
COPY data/ ./data/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
