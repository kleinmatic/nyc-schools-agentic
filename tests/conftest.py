"""Shared pytest fixtures."""
import os

# Every TestClient request shares one client IP, so the production
# rate limit would trip mid-suite. Neutralize it before any test module
# imports app.main (conftest loads first). Middleware behavior itself is
# tested in test_ratelimit.py with explicitly-constructed instances.
os.environ.setdefault("RATE_LIMIT_RATE", "1000000")
os.environ.setdefault("RATE_LIMIT_BURST", "1000000")

import pytest

from app import data


@pytest.fixture(scope="session", autouse=True)
def loaded_data():
    """Load the dataframes once for the entire test session."""
    data.load()


@pytest.fixture(autouse=True)
def fresh_geocode_cache():
    """Geocode results are TTL-cached in-process; respx-mocked tests that
    reuse an address must not see each other's entries."""
    from app.services.zoning import clear_geocode_cache
    clear_geocode_cache()
    yield
