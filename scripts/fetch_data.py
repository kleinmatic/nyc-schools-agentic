"""Pre-warm the local nycschools data cache.

The upstream package's `dataloader.load()` lazily fetches each file from
data.mixi.nyc on first call and caches it under NYC_SCHOOLS_DATA_DIR. This
script kicks off the loaders we know we'll need so the cache is full and
offline-ready. Re-run any time; cached files are reused.

Includes the NYSED School Report Card Database loader (nysed_src) which
downloads SRC{year}.zip (~370 MB), extracts the Group3 .mdb (~1.5 GB), and
materializes one feather per accountability/assessment table. Requires
mdbtools to be installed (`brew install mdbtools` on macOS).

The bulk Google Drive .7z archive (referenced in nycschools/datasets.py) is
404 as of 2026 — we deliberately bypass it.
"""
import os
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "school-data"
DATA_DIR.mkdir(exist_ok=True)
os.environ["NYC_SCHOOLS_DATA_DIR"] = str(DATA_DIR)

from nycschools import schools, snapshot, exams, class_size, geo, budgets, shsat, nysed_src


def fetch_hs_directory(ay: int = 2021):
    """HS directory has no data.mixi.nyc cache — pull from NYC Open Data once
    and persist to feather under school-data/."""
    cache_path = DATA_DIR / f"hs-directory-{ay}.feather"
    if cache_path.exists():
        import pandas as pd
        return pd.read_feather(cache_path)
    df = schools.load_hs_directory(ay=ay)
    df.reset_index(drop=True).to_feather(cache_path)
    return df


def fetch_nysed_src(year: int = 2025):
    """Download the NYSED SRC database and extract every NYC-only feather we use."""
    nysed_src.warm_cache(year=year)
    # Return a representative table so the LOADERS log line has a row count.
    return nysed_src.load_essa_status(year=year, nyc_only=True)


# NYC Open Data Socrata resources for school zone polygons (2024-25).
ZONE_RESOURCES = {
    "es": "cmjf-yawu",  # School Zones 2024-2025 (Elementary School)
    "ms": "t26j-jbq7",  # School Zones 2024-2025 (Middle School)
}


def fetch_zone_polygons(level: str, year: int = 2024):
    """Pull the appropriate ES or MS zone GeoJSON if not already cached."""
    import requests
    cache_path = DATA_DIR / f"nyc-school-zones-{level}-{year}.geojson"
    if not cache_path.exists():
        rid = ZONE_RESOURCES[level]
        url = f"https://data.cityofnewyork.us/api/geospatial/{rid}?method=export&format=GeoJSON"
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        cache_path.write_bytes(r.content)
    import geopandas as gpd
    return gpd.read_file(cache_path)


LOADERS = [
    ("demographics", schools.load_school_demographics),
    ("snapshots", snapshot.load_snapshots),
    ("class_size", class_size.load_class_size),
    ("ptr", class_size.load_ptr),
    ("ela_3_8", exams.load_ela),
    ("math_3_8", exams.load_math),
    ("regents", exams.load_regents),
    ("school_locations", geo.load_school_locations),
    ("zipcodes", geo.load_zipcodes),
    ("neighborhoods", geo.load_neighborhoods),
    ("shsat_offers", shsat.load_admission_offers),
    ("galaxy_budgets", budgets.load_galaxy_budgets),
    ("hs_directory_2021", fetch_hs_directory),
    ("nysed_src_2025", fetch_nysed_src),
    ("es_zones_2024", lambda: fetch_zone_polygons("es", 2024)),
    ("ms_zones_2024", lambda: fetch_zone_polygons("ms", 2024)),
]

failures = []
for name, fn in LOADERS:
    try:
        df = fn()
        print(f"  ok  {name:22s} rows={len(df):>7,}  cols={len(df.columns)}")
    except Exception as e:
        failures.append((name, e))
        print(f"  FAIL {name:22s} {type(e).__name__}: {e}")

print()
print(f"Cached files in {DATA_DIR}:")
for p in sorted(DATA_DIR.iterdir()):
    print(f"  {p.name}  ({p.stat().st_size / 1_000_000:.1f} MB)")

if failures:
    print(f"\n{len(failures)} loader(s) failed. Details:")
    for name, e in failures:
        print(f"\n--- {name} ---")
        traceback.print_exception(type(e), e, e.__traceback__)
    sys.exit(1)

print()
print("Tip: after the NYSED feathers are extracted, you can reclaim ~1.5 GB by")
print("deleting school-data/SRC2025_Group3.mdb (re-extracts from the zip if needed).")
