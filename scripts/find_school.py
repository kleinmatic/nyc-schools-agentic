"""Find NYC schools by name, short-name, or partial DBN.

Usage:
    uv run scripts/find_school.py "Midwood High School"
    uv run scripts/find_school.py "PS 321"
    uv run scripts/find_school.py K405          # partial DBN
"""
import argparse
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NYC_SCHOOLS_DATA_DIR", str(REPO_ROOT / "school-data"))

from nycschools import schools

DBN_RE = re.compile(r"^\d{0,2}[MXKQR]\d{1,4}$", re.IGNORECASE)
COLS = ["dbn", "school_name", "short_name", "district", "boro", "school_level", "total_enrollment", "zip"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="school name, short name (e.g. 'PS 321'), or partial/full DBN (e.g. K405, 21K405)")
    ap.add_argument("-n", "--limit", type=int, default=10, help="max results to show (default 10)")
    args = ap.parse_args()

    df = schools.load_school_demographics()
    latest = df[df["ay"] == df["ay"].max()].copy()

    q = args.query.strip()

    if DBN_RE.match(q):
        results = latest[latest["dbn"].str.contains(q, case=False, na=False)]
        mode = "DBN substring"
    else:
        results = schools.search(latest, q)
        mode = "fuzzy name/short-name"

    cols = [c for c in COLS if c in results.columns]
    print(f"[{mode}]  query={q!r}  matches={len(results)}")
    if results.empty:
        return
    print(results[cols].head(args.limit).to_string(index=False))


if __name__ == "__main__":
    main()
