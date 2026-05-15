"""Process-wide data store. Reads from the committed SQLite + geo files in
data/, loads tables into pandas dataframes at FastAPI startup. The running
app has zero runtime dependency on the upstream nycschools package or on
the `school-data/` cache — both are build-time concerns only.

To rebuild the committed data after an upstream refresh:
    uv run scripts/fetch_data.py    # pull raw upstream into school-data/
    uv run scripts/build_db.py      # filter + write to data/
"""
from dataclasses import dataclass, field
import sqlite3
from typing import Optional

import geopandas as gpd
import pandas as pd

from . import config


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
    hs_directory: pd.DataFrame
    nysed_essa_status: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_essa_subgroup: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_chronic: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_expenditures: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_inexp_teachers: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_out_of_cert: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_hs_grad: pd.DataFrame = field(default_factory=pd.DataFrame)
    nysed_hs_cccr: pd.DataFrame = field(default_factory=pd.DataFrame)
    es_zones: gpd.GeoDataFrame = field(default_factory=lambda: gpd.GeoDataFrame())
    ms_zones: gpd.GeoDataFrame = field(default_factory=lambda: gpd.GeoDataFrame())
    nta_polygons: gpd.GeoDataFrame = field(default_factory=lambda: gpd.GeoDataFrame())


_store: Optional[DataStore] = None


def get_store() -> DataStore:
    if _store is None:
        raise RuntimeError("Data not loaded — did the FastAPI lifespan hook run?")
    return _store


def is_loaded() -> bool:
    return _store is not None


_TABLES = (
    "demographics", "snapshots", "exams_ela", "exams_math", "regents",
    "class_size", "ptr", "shsat", "budgets",
    "nysed_essa_status", "nysed_essa_subgroup", "nysed_chronic",
    "nysed_expenditures", "nysed_inexp_teachers", "nysed_out_of_cert",
    "nysed_hs_grad", "nysed_hs_cccr",
)


def load() -> DataStore:
    """Read every table from the committed SQLite + geo files. Idempotent."""
    global _store
    if _store is not None:
        return _store

    if not config.DB_PATH.exists():
        raise RuntimeError(
            f"{config.DB_PATH} is missing — run `uv run scripts/build_db.py` "
            "to build it from school-data/."
        )

    with sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True) as conn:
        tables = {t: pd.read_sql_query(f"SELECT * FROM {t}", conn) for t in _TABLES}

    cdd = config.COMMITTED_DATA_DIR
    _store = DataStore(
        demographics=tables["demographics"],
        snapshots=tables["snapshots"],
        ela=tables["exams_ela"],
        math=tables["exams_math"],
        regents=tables["regents"],
        class_size=tables["class_size"],
        ptr=tables["ptr"],
        locations=gpd.read_file(cdd / "school-locations.geojson"),
        shsat=tables["shsat"],
        budgets=tables["budgets"],
        hs_directory=pd.read_feather(cdd / "hs-directory.feather"),
        nysed_essa_status=tables["nysed_essa_status"],
        nysed_essa_subgroup=tables["nysed_essa_subgroup"],
        nysed_chronic=tables["nysed_chronic"],
        nysed_expenditures=tables["nysed_expenditures"],
        nysed_inexp_teachers=tables["nysed_inexp_teachers"],
        nysed_out_of_cert=tables["nysed_out_of_cert"],
        nysed_hs_grad=tables["nysed_hs_grad"],
        nysed_hs_cccr=tables["nysed_hs_cccr"],
        es_zones=gpd.read_file(cdd / "school-zones-es.geojson"),
        ms_zones=gpd.read_file(cdd / "school-zones-ms.geojson"),
        nta_polygons=gpd.read_file(cdd / "nta-2010.geojson"),
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
        f"grad={len(_store.nysed_hs_grad):,} cccr={len(_store.nysed_hs_cccr):,} "
        f"es_zones={len(_store.es_zones):,} ms_zones={len(_store.ms_zones):,} "
        f"ntas={len(_store.nta_polygons):,}"
    )
