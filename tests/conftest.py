"""Shared pytest fixtures."""
import pytest

from app import data


@pytest.fixture(scope="session", autouse=True)
def loaded_data():
    """Load the dataframes once for the entire test session."""
    data.load()
