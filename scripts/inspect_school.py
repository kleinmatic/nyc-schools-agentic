"""Print everything we have on a single DBN across all cached loaders.

Usage:
    uv run scripts/inspect_school.py 15K321
    uv run scripts/inspect_school.py 15K321 --year 2024
"""
import argparse
import os
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NYC_SCHOOLS_DATA_DIR", str(REPO_ROOT / "school-data"))

from nycschools import schools, snapshot, exams, class_size, geo

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 60)


def section(title):
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def slice_dbn(df, dbn):
    if "dbn" not in df.columns:
        return df.iloc[0:0]
    return df[df["dbn"] == dbn]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dbn", help="e.g. 15K321")
    ap.add_argument("--year", type=int, help="Filter year-keyed datasets to this academic year (column 'ay')")
    args = ap.parse_args()
    dbn = args.dbn

    demo = schools.load_school_demographics()
    snap = snapshot.load_snapshots()
    cs = class_size.load_class_size()
    ptr = class_size.load_ptr()
    ela = exams.load_ela()
    math = exams.load_math()
    locs = geo.load_school_locations()

    def maybe_year(df):
        if args.year is not None and "ay" in df.columns:
            return df[df["ay"] == args.year]
        return df

    section(f"DEMOGRAPHICS  {dbn}")
    d = maybe_year(slice_dbn(demo, dbn)).sort_values("ay") if "ay" in demo.columns else slice_dbn(demo, dbn)
    if d.empty:
        print("(no rows)")
    else:
        print(d.T.to_string() if args.year is not None else d.to_string(index=False))

    section(f"SNAPSHOT (DOE official)  {dbn}")
    s = slice_dbn(snap, dbn)
    print("(no rows)" if s.empty else s.T.to_string())

    section(f"LOCATION  {dbn}")
    l = slice_dbn(locs, dbn)
    print("(no rows)" if l.empty else l.T.to_string())

    section(f"CLASS SIZE  {dbn}")
    c = maybe_year(slice_dbn(cs, dbn))
    print("(no rows)" if c.empty else c.to_string(index=False))

    section(f"PUPIL:TEACHER RATIO  {dbn}")
    p = slice_dbn(ptr, dbn)
    print("(no rows)" if p.empty else p.to_string(index=False))

    section(f"ELA 3-8 (all-students rows)  {dbn}")
    e = slice_dbn(ela, dbn)
    if "category" in e.columns:
        e = e[e["category"].fillna("All Students").str.contains("All", case=False, na=False)]
    e = maybe_year(e).sort_values([c for c in ("ay", "grade") if c in e.columns])
    print("(no rows)" if e.empty else e.to_string(index=False))

    section(f"MATH 3-8 (all-students rows)  {dbn}")
    m = slice_dbn(math, dbn)
    if "category" in m.columns:
        m = m[m["category"].fillna("All Students").str.contains("All", case=False, na=False)]
    m = maybe_year(m).sort_values([c for c in ("ay", "grade") if c in m.columns])
    print("(no rows)" if m.empty else m.to_string(index=False))


if __name__ == "__main__":
    main()
