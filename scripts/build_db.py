"""Build the committed SQLite + geo files that the app reads at startup.

Two-phase data flow:

  upstream sources   →   school-data/   →    data/
                  (fetch_data.py)     (build_db.py)
                    [gitignored]      [committed]

scripts/fetch_data.py pulls raw data from upstream (data.mixi.nyc, NYSED's
SRC zip, NYC Open Data, etc.) into school-data/. That's a heavy operation
we run rarely — about once a year when NYSED publishes a new SRC.

This script reads from that cache, filters down to the columns and rows
the app actually surfaces, and writes a single SQLite plus a few small
GeoJSON / feather files into data/. Those committed files are what the
running app and CI both read — no upstream dependency at runtime.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "school-data"   # gitignored upstream cache
DEST = REPO_ROOT / "data"            # committed working set
DEST.mkdir(exist_ok=True)
DB_PATH = DEST / "data.sqlite"

os.environ.setdefault("NYC_SCHOOLS_DATA_DIR", str(SOURCE))

import pandas as pd  # noqa: E402

# --- Tabular tables ---

# demographics — keep only columns we surface anywhere.
DEMO_KEEP = [
    "dbn", "ay", "year", "school_name", "short_name", "clean_name",
    "district", "geo_district", "boro", "school_level",
    "total_enrollment", "beds", "zip",
    "asian_pct", "black_pct", "hispanic_pct", "white_pct",
    "multi_racial_pct", "native_american_pct",
    "female_pct", "male_pct",
    "ell_pct", "swd_pct", "poverty_pct", "eni",
]

# Exam columns to keep; we filter rows to "All Students" category.
EXAM_KEEP = [
    "dbn", "ay", "grade", "number_tested", "mean_scale_score",
    "level_1_pct", "level_2_pct", "level_3_pct", "level_4_pct", "level_3_4_pct",
]

REGENTS_KEEP = [
    "dbn", "ay", "regents_exam", "number_tested", "mean_score",
    "below_65_pct", "above_64_pct", "above_79_pct", "college_ready_pct",
]

CLASS_SIZE_KEEP = [
    "dbn", "ay", "grade", "program_type", "subject",
    "students_n", "classes_n", "avg_class_size", "min_class_size", "max_class_size",
]


def _filtered_columns(df, keep):
    """Subset to columns in `keep` that actually exist in the source df."""
    cols = [c for c in keep if c in df.columns]
    return df[cols].copy()


def build_demographics():
    from nycschools import schools
    df = schools.load_school_demographics()
    return _filtered_columns(df, DEMO_KEEP)


def build_snapshots():
    from nycschools import snapshot
    df = snapshot.load_snapshots()
    keep = [
        "dbn", "school_name", "ay", "address", "city_state_zip",
        "principal_name", "principal_phone_number", "principal_years",
        "attendance_rate", "student_chronic_absent",
        "teacher_3yr_exp_pct",
        "quality_review_year", "quality_review_url", "dates_of_review",
        "all_es_admissionsmethods", "all_ms_admissionsmethods",
        "co_located", "colocated_n",
        "website_es", "website_ms", "grades_text",
    ]
    return _filtered_columns(df, keep)


def build_exam(loader_name):
    """Filter to All Students rows + needed columns."""
    from nycschools import exams
    df = getattr(exams, loader_name)()
    if "category" in df.columns:
        df = df[df["category"].fillna("All Students") == "All Students"]
    return _filtered_columns(df, EXAM_KEEP)


def build_regents():
    from nycschools import exams
    df = exams.load_regents()
    if "category" in df.columns:
        df = df[df["category"].fillna("All Students") == "All Students"]
    return _filtered_columns(df, REGENTS_KEEP)


def build_class_size():
    from nycschools import class_size
    return _filtered_columns(class_size.load_class_size(), CLASS_SIZE_KEEP)


def build_ptr():
    from nycschools import class_size
    return class_size.load_ptr()[["dbn", "ay", "ptr"]]


def build_shsat():
    from nycschools import shsat
    df = shsat.load_admission_offers()
    return df[["dbn", "ay", "hs_applicants_n", "testers_n", "offers_n", "offers_pct"]]


def build_budgets():
    """Galaxy budgets ship as currency strings — parse to floats here so the
    runtime app never has to."""
    import re
    from nycschools import budgets
    df = budgets.load_galaxy_budgets()
    cleaner = re.compile(r"[^0-9.\-]")

    def parse(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip()
        cleaned = cleaner.sub("", s) or None
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    df = df.copy()
    df["budget_num"] = df["budget"].apply(parse)
    return df[["dbn", "ay", "category", "item", "positions", "budget_num"]].rename(
        columns={"budget_num": "budget"}
    )


# --- NYSED tables (already pre-filtered to NYC by nysed_src) ---

def build_nysed(slug, columns=None, level=None):
    """Load a NYSED table and (optionally) restrict to columns we surface."""
    from nycschools import nysed_src
    df = nysed_src.load_table(slug, year=2025, nyc_only=True)
    if level == "ALL" and slug in ("acc-em-chronic-absenteeism", "acc-hs-chronic-absenteeism"):
        # Caller wants combined level=EM/HS; handle as part of build_chronic.
        pass
    if columns:
        cols = [c for c in columns if c in df.columns]
        df = df[cols].copy()
    return df


def build_chronic():
    """Combine EM + HS chronic absenteeism into one table with a LEVEL column."""
    from nycschools import nysed_src
    em = nysed_src.load_chronic_absenteeism(2025, level="EM", nyc_only=True).assign(LEVEL="EM")
    hs = nysed_src.load_chronic_absenteeism(2025, level="HS", nyc_only=True).assign(LEVEL="HS")
    cols = [
        "INSTITUTION_ID", "ENTITY_CD", "ENTITY_NAME", "YEAR", "LEVEL",
        "SUBGROUP_NAME", "ENROLLMENT", "ABSENT_COUNT", "ABSENT_RATE",
    ]
    out = pd.concat([em[[c for c in cols if c in em.columns]],
                     hs[[c for c in cols if c in hs.columns]]], ignore_index=True)
    return out


# --- Geo files (kept as separate file artifacts, not in SQLite) ---

GEO_COPIES = [
    ("school_locations.geojson", "school-locations.geojson"),
    ("nyc-school-zones-es-2024.geojson", "school-zones-es.geojson"),
    ("nyc-school-zones-ms-2024.geojson", "school-zones-ms.geojson"),
    ("hs-directory-2021.feather", "hs-directory.feather"),
]


# --- Indexes — created post-load for clean errors -----

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_demo_dbn ON demographics(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_demo_dbn_ay ON demographics(dbn, ay);",
    "CREATE INDEX IF NOT EXISTS idx_demo_level ON demographics(school_level);",
    "CREATE INDEX IF NOT EXISTS idx_snap_dbn ON snapshots(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_ela_dbn ON exams_ela(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_math_dbn ON exams_math(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_regents_dbn ON regents(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_class_dbn ON class_size(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_ptr_dbn ON ptr(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_shsat_dbn ON shsat(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_budgets_dbn ON budgets(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_essa_cd ON nysed_essa_status(ENTITY_CD);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_essa_sg_cd ON nysed_essa_subgroup(ENTITY_CD);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_chronic_cd ON nysed_chronic(ENTITY_CD);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_exp_cd ON nysed_expenditures(ENTITY_CD);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_inexp_cd ON nysed_inexp_teachers(ENTITY_CD);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_oct_cd ON nysed_out_of_cert(ENTITY_CD);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_grad_cd ON nysed_hs_grad(ENTITY_CD);",
    "CREATE INDEX IF NOT EXISTS idx_nysed_cccr_cd ON nysed_hs_cccr(ENTITY_CD);",
]


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    print(f"Building {DB_PATH} from {SOURCE}/ ...")

    tables = {
        "demographics": build_demographics(),
        "snapshots": build_snapshots(),
        "exams_ela": build_exam("load_ela"),
        "exams_math": build_exam("load_math"),
        "regents": build_regents(),
        "class_size": build_class_size(),
        "ptr": build_ptr(),
        "shsat": build_shsat(),
        "budgets": build_budgets(),
    }

    # NYSED tables (already NYC-filtered).
    nysed_tables = {
        "nysed_essa_status": ("accountability-status", None),
        "nysed_essa_subgroup": ("accountability-status-by-subgroup", None),
        "nysed_expenditures": ("expenditures-per-pupil", None),
        "nysed_inexp_teachers": ("inexperienced-teachers-and-principals", [
            "INSTITUTION_ID", "ENTITY_CD", "ENTITY_NAME", "YEAR",
            "NUM_TEACH", "NUM_TEACH_INEXP", "PER_TEACH_INEXP",
            "NUM_PRINC", "NUM_PRINC_INEXP", "PER_PRINC_INEXP",
        ]),
        "nysed_out_of_cert": ("teachers-teaching-out-of-certification", [
            "INSTITUTION_ID", "ENTITY_CD", "ENTITY_NAME", "YEAR",
            "NUM_TEACH_OC", "NUM_OUT_CERT", "PER_OUT_CERT",
        ]),
        "nysed_hs_grad": ("acc-hs-graduation-rate", [
            "INSTITUTION_ID", "ENTITY_CD", "ENTITY_NAME", "YEAR",
            "SUBGROUP_NAME", "COHORT", "COHORT_COUNT", "GRAD_COUNT", "GRAD_RATE",
        ]),
        "nysed_hs_cccr": ("acc-hs-cccr", [
            "INSTITUTION_ID", "ENTITY_CD", "ENTITY_NAME", "YEAR",
            "SUBGROUP_NAME", "COHORT", "INDEX", "LEVEL",
        ]),
    }
    for name, (slug, cols) in nysed_tables.items():
        tables[name] = build_nysed(slug, columns=cols)
    tables["nysed_chronic"] = build_chronic()

    conn = sqlite3.connect(DB_PATH)
    try:
        for name, df in tables.items():
            df.to_sql(name, conn, if_exists="replace", index=False)
            print(f"  {name:24s} rows={len(df):>7,}  cols={len(df.columns)}")
        for stmt in INDEX_SQL:
            conn.execute(stmt)
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    print()
    print("Copying geo / wide-format files into data/ ...")
    for src_name, dest_name in GEO_COPIES:
        src = SOURCE / src_name
        dest = DEST / dest_name
        if src.exists():
            shutil.copyfile(src, dest)
            print(f"  {src_name}  →  data/{dest_name}  ({dest.stat().st_size / 1_000_000:.1f} MB)")
        else:
            print(f"  SKIP {src_name} (not found in {SOURCE}/)")

    db_mb = DB_PATH.stat().st_size / 1_000_000
    print()
    print(f"DB size: {db_mb:.1f} MB")
    print(f"Total committed data/: {sum(p.stat().st_size for p in DEST.iterdir()) / 1_000_000:.1f} MB")


if __name__ == "__main__":
    main()
