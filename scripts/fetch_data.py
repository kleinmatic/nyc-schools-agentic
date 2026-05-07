"""Pre-warm the local nycschools data cache from data.mixi.nyc.

The upstream package's `dataloader.load()` lazily fetches each file from
data.mixi.nyc on first call and caches it under NYC_SCHOOLS_DATA_DIR. This
script just kicks off the loaders we know we'll need so the cache is full
and offline-ready. Re-run any time; cached files are reused.

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

from nycschools import schools, snapshot, exams, class_size, geo

LOADERS = [
    ("demographics", schools.load_school_demographics),
    ("snapshots", snapshot.load_snapshots),
    ("class_size", class_size.load_class_size),
    ("ptr", class_size.load_ptr),
    ("ela_3_8", exams.load_ela),
    ("math_3_8", exams.load_math),
    ("school_locations", geo.load_school_locations),
    ("zipcodes", geo.load_zipcodes),
    ("neighborhoods", geo.load_neighborhoods),
]

failures = []
for name, fn in LOADERS:
    try:
        df = fn()
        print(f"  ok  {name:20s} rows={len(df):>7,}  cols={len(df.columns)}")
    except Exception as e:
        failures.append((name, e))
        print(f"  FAIL {name:20s} {type(e).__name__}: {e}")

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
