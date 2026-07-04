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
