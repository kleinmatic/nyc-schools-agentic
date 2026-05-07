"""Process-wide data store. Holds upstream nycschools dataframes in memory.

Loaded once at FastAPI startup via lifespan; queried by services/schools.py.
This is the only module that imports nycschools loaders directly.
"""
from dataclasses import dataclass
from typing import Optional

import geopandas as gpd
import pandas as pd

from . import config  # noqa: F401  -- side effect: sets NYC_SCHOOLS_DATA_DIR


@dataclass
class DataStore:
    demographics: pd.DataFrame
    snapshots: pd.DataFrame
    ela: pd.DataFrame
    math: pd.DataFrame
    class_size: pd.DataFrame
    ptr: pd.DataFrame
    locations: gpd.GeoDataFrame


_store: Optional[DataStore] = None


def get_store() -> DataStore:
    if _store is None:
        raise RuntimeError("Data not loaded — did the FastAPI lifespan hook run?")
    return _store


def is_loaded() -> bool:
    return _store is not None


def load() -> DataStore:
    """Load all dataframes from the on-disk cache. Idempotent."""
    global _store
    if _store is not None:
        return _store

    from nycschools import schools, snapshot, exams, class_size, geo

    _store = DataStore(
        demographics=schools.load_school_demographics(),
        snapshots=snapshot.load_snapshots(),
        ela=exams.load_ela(),
        math=exams.load_math(),
        class_size=class_size.load_class_size(),
        ptr=class_size.load_ptr(),
        locations=geo.load_school_locations(),
    )
    return _store


def summary() -> str:
    if _store is None:
        return "(unloaded)"
    return (
        f"demo={len(_store.demographics):,} snap={len(_store.snapshots):,} "
        f"ela={len(_store.ela):,} math={len(_store.math):,} "
        f"cs={len(_store.class_size):,} ptr={len(_store.ptr):,} "
        f"loc={len(_store.locations):,}"
    )
