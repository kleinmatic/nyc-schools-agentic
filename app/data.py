"""Process-wide data store. Holds upstream nycschools dataframes in memory.

Loaded once at FastAPI startup via lifespan; queried by services/schools.py.
This is the only module that imports nycschools loaders directly.
"""
from dataclasses import dataclass, field
from pathlib import Path
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
    regents: pd.DataFrame
    class_size: pd.DataFrame
    ptr: pd.DataFrame
    locations: gpd.GeoDataFrame
    shsat: pd.DataFrame
    budgets: pd.DataFrame
    hs_directory: pd.DataFrame  # academic year 2021
    # NYSED School Report Card Database (NYC-only views, year 2025).
    nysed_essa_status: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_essa_subgroup: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_chronic: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_expenditures: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_inexp_teachers: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_out_of_cert: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_hs_grad: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_hs_cccr: pd.DataFrame = field(default_factory=pd.DataFrame)


_store: Optional[DataStore] = None


def get_store() -> DataStore:
    if _store is None:
        raise RuntimeError("Data not loaded — did the FastAPI lifespan hook run?")
    return _store


def is_loaded() -> bool:
    return _store is not None


def _load_hs_directory(ay: int = 2021) -> pd.DataFrame:
    """HS directory isn't in data.mixi.nyc, so we cache it locally to feather
    after the first NYC-Open-Data fetch. scripts/fetch_data.py also primes
    this cache, so a freshly-bootstrapped repo never hits the network here."""
    cache_path: Path = config.DATA_DIR / f"hs-directory-{ay}.feather"
    if cache_path.exists():
        return pd.read_feather(cache_path)
    from nycschools import schools as ns_schools

    df = ns_schools.load_hs_directory(ay=ay).reset_index(drop=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_feather(cache_path)
    return df


def load() -> DataStore:
    """Load all dataframes from the on-disk cache. Idempotent."""
    global _store
    if _store is not None:
        return _store

    from nycschools import (
        schools,
        snapshot,
        exams,
        class_size,
        geo,
        shsat,
        budgets,
        nysed_src,
    )

    _store = DataStore(
        demographics=schools.load_school_demographics(),
        snapshots=snapshot.load_snapshots(),
        ela=exams.load_ela(),
        math=exams.load_math(),
        regents=exams.load_regents(),
        class_size=class_size.load_class_size(),
        ptr=class_size.load_ptr(),
        locations=geo.load_school_locations(),
        shsat=shsat.load_admission_offers(),
        budgets=budgets.load_galaxy_budgets(),
        hs_directory=_load_hs_directory(2021),
        nysed_essa_status=nysed_src.load_essa_status(2025, nyc_only=True),
        nysed_essa_subgroup=nysed_src.load_essa_status_by_subgroup(2025, nyc_only=True),
        nysed_chronic=nysed_src.load_chronic_absenteeism(2025, level="ALL", nyc_only=True),
        nysed_expenditures=nysed_src.load_expenditures_per_pupil(2025, nyc_only=True),
        nysed_inexp_teachers=nysed_src.load_inexperienced_teachers(2025, nyc_only=True),
        nysed_out_of_cert=nysed_src.load_out_of_certification(2025, nyc_only=True),
        nysed_hs_grad=nysed_src.load_hs_graduation_rate(2025, nyc_only=True),
        nysed_hs_cccr=nysed_src.load_hs_cccr(2025, nyc_only=True),
    )
    return _store


def summary() -> str:
    if _store is None:
        return "(unloaded)"
    return (
        f"demo={len(_store.demographics):,} snap={len(_store.snapshots):,} "
        f"ela={len(_store.ela):,} math={len(_store.math):,} reg={len(_store.regents):,} "
        f"cs={len(_store.class_size):,} ptr={len(_store.ptr):,} loc={len(_store.locations):,} "
        f"shsat={len(_store.shsat):,} budgets={len(_store.budgets):,} hs_dir={len(_store.hs_directory):,} "
        f"nysed: essa={len(_store.nysed_essa_status):,} essa_sg={len(_store.nysed_essa_subgroup):,} "
        f"chronic={len(_store.nysed_chronic):,} exp={len(_store.nysed_expenditures):,} "
        f"inexp={len(_store.nysed_inexp_teachers):,} oct={len(_store.nysed_out_of_cert):,} "
        f"grad={len(_store.nysed_hs_grad):,} cccr={len(_store.nysed_hs_cccr):,}"
    )
