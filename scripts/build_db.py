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


# NYSED SRC annual grades-3-8 assessment feathers → DOE-shaped exam rows for the
# years the upstream DOE workbooks don't cover. Built HERE (not upstream): the DOE
# loaders (exams.load_ela / load_math) take no year param and read a static mirror
# that stops early, so newer years come from the NYSED School Report Card database
# (the same pipeline that keeps the nysed_* tables current). We read the cached SRC
# feather, filter to All-Students per-grade rows, join BEDS→DBN at build time, and
# APPEND to the DOE rows so every downstream consumer keeps the same
# dbn/ay/grade/level_*_pct schema and simply sees new AY rows appear.
#
# YEAR CONVENTION (critical): the DOE `ay` is the FALL/start year (upstream computes
# ay = test_year - 1, e.g. ay 2021 = tests given spring 2022). NYSED's `YEAR` is the
# SPRING/end year (YEAR 2025 = SY2024-25 = spring 2025). `_reshape_nysed_exam_feather`
# subtracts 1 so NYSED conforms to the DOE fall-year axis; without it the NYSED years
# render one school-year too high and misfile across the standards divide.
#
# STANDARDS DIVIDE = SOURCE BOUNDARY. NY replaced Common Core with the Next Generation
# Learning Standards; the LAST Common Core administration was spring 2022 (ay 2021)
# and the FIRST Next Gen was spring 2023 (ay 2022). Scores/scale are NOT comparable
# across it. We source each side from the standard that owns it: DOE supplies Common
# Core (ay ≤ 2021), NYSED supplies Next Gen (ay ≥ 2022, from YEAR 2023/2024/2025).
# DOE's own last year (ay 2022 = spring 2023) is actually Next Gen and would DUPLICATE
# NYSED's ay 2022 — build_exam drops DOE ay ≥ 2022 so NYSED is the single Next-Gen
# source (uniform ~1.3k-school coverage across the whole new-standard block).
# Resulting ay series: 2012..2021 (DOE, Common Core; COVID gap 2019-20), then
# 2022, 2023, 2024 (NYSED, Next Gen). Newest ay 2024 = SY2024-25.
NYSED_EXAM_FEATHER = {
    "load_ela": [
        ("nysed-src-2024-annual-em-ela.feather", (2023,)),
        ("nysed-src-2025-annual-em-ela.feather", (2024, 2025)),
    ],
    "load_math": [
        ("nysed-src-2024-annual-em-math.feather", (2023,)),
        ("nysed-src-2025-annual-em-math.feather", (2024, 2025)),
    ],
}


def _beds_to_dbn_crosswalk():
    """Most-recent non-zero BEDS per DBN → {12-char NYSED ENTITY_CD: DBN}.

    Mirrors the service-layer join (analytics._beds_to_str over demographics.beds)
    so build-time coverage of the NYSED-sourced exam rows matches how the app
    already links its other NYSED tables to DBNs."""
    from nycschools import schools
    demo = schools.load_school_demographics()[["dbn", "ay", "beds"]].copy()
    demo["beds"] = pd.to_numeric(demo["beds"], errors="coerce").fillna(0).astype("int64")
    demo = demo[demo["beds"] > 0].sort_values("ay").drop_duplicates("dbn", keep="last")
    entity = demo["beds"].astype(str).str.zfill(12)
    return dict(zip(entity, demo["dbn"]))


def _reshape_nysed_exam_feather(df, crosswalk):
    """Reshape one NYSED SRC annual grades-3-8 feather into the DOE exam schema."""
    df = df[df["SUBGROUP_NAME"] == "All Students"].copy()
    # Keep the per-grade rows (ELA3..ELA8 / MATH3..MATH8) AND the all-grades
    # aggregate (ELA3_8 / MATH3_8), which becomes the DOE-schema "All Grades" row
    # that the analytics layer (leaderboards, peer ranks, metric registry) selects on.
    # Anchor on ELA|MATH explicitly: the ELA/math feathers only carry ELA*/MATH*
    # assessments today, but NYSED also ships all-caps-free names (Combined7Math,
    # RegentsMath8, Science8). The subject anchor makes the intent defensive-by-design
    # rather than leaning on NYSED's casing convention — a stray all-caps non-ELA/math
    # assessment ending 3-8 would otherwise be silently reshaped in as a grade row.
    df = df[df["ASSESSMENT_NAME"].str.fullmatch(r"(ELA|MATH)(3_8|[3-8])", na=False)]
    df["dbn"] = df["ENTITY_CD"].map(crosswalk)
    df = df.dropna(subset=["dbn"])
    # NYSED YEAR is the spring/end year; conform to the DOE fall-year `ay` axis
    # (ay = spring_year - 1) so labels and the standards-divide boundary line up.
    df["ay"] = pd.to_numeric(df["YEAR"], errors="coerce").astype("Int64") - 1
    # grade stored as TEXT to match the DOE tables: "3".."8" or "All Grades".
    is_agg = df["ASSESSMENT_NAME"].str.endswith("3_8")
    df["grade"] = df["ASSESSMENT_NAME"].str[-1].mask(is_agg, "All Grades")
    df["number_tested"] = pd.to_numeric(df["NUM_TESTED"], errors="coerce")
    df["mean_scale_score"] = pd.to_numeric(df["MEAN_SCORE"], errors="coerce")
    for i in (1, 2, 3, 4):
        df[f"level_{i}_pct"] = pd.to_numeric(df.get(f"LEVEL{i}_%TESTED"), errors="coerce") / 100.0
    df["level_3_4_pct"] = pd.to_numeric(df["PER_PROF"], errors="coerce") / 100.0
    df = df[df["number_tested"].fillna(0) > 0]
    return _filtered_columns(df, EXAM_KEEP)


def _nysed_exam_rows(loader_name, crosswalk):
    """Concatenate DOE-schema exam rows across every SRC feather for this loader,
    taking from each feather only the year(s) it is authoritative for."""
    frames = []
    for filename, years in NYSED_EXAM_FEATHER[loader_name]:
        path = SOURCE / filename
        if not path.exists():
            continue
        df = pd.read_feather(path)
        keep = pd.to_numeric(df["YEAR"], errors="coerce").isin(years)
        df = df[keep]
        if len(df):
            frames.append(_reshape_nysed_exam_feather(df, crosswalk))
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


LAST_COMMON_CORE_AY = 2021  # last Common Core administration (spring 2022)


def build_exam(loader_name, crosswalk=None):
    """Common Core exam rows from the DOE loader (ay ≤ 2021) plus Next Generation
    rows from NYSED SRC (ay ≥ 2022). The DOE loader's own last year (ay 2022 =
    spring 2023) is already Next Gen and would duplicate NYSED's ay 2022, so when
    NYSED is supplying this subject we bound the DOE side at the Common Core years
    and let NYSED be the single Next-Gen source (see the module comment above)."""
    from nycschools import exams
    df = getattr(exams, loader_name)()
    if "category" in df.columns:
        df = df[df["category"].fillna("All Students") == "All Students"]
    doe = _filtered_columns(df, EXAM_KEEP)
    if crosswalk is not None and loader_name in NYSED_EXAM_FEATHER:
        doe = doe[pd.to_numeric(doe["ay"], errors="coerce") <= LAST_COMMON_CORE_AY]
        nysed = _nysed_exam_rows(loader_name, crosswalk)
        if nysed is not None and len(nysed):
            doe = pd.concat([doe, nysed], ignore_index=True)
    return doe


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


def build_staffing(ay: int = 2025):
    """GC + SW FTE counts plus DOE-computed pupil ratios. Reads the
    feather built by scripts/fetch_data.py and coerces ratio columns to
    numeric (the DOE spreadsheet leaves them as 'N/A' strings when a
    school has zero of that staff type)."""
    import pandas as pd
    df = pd.read_feather(SOURCE / f"staffing-{ay}.feather")
    keep = [
        "dbn", "school_name", "location_type", "ay",
        "total_gc", "total_sw", "total_gc_sw",
        "full_time_gc", "full_time_sw", "part_time_gc", "part_time_sw",
        "bilingual_gc", "bilingual_sw",
        "school_psychologist_mandated", "cbo_partner_mental_health",
        "enrollment", "ratio_gc_sw", "ratio_gc_only", "ratio_sw_only",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()
    # Coerce ratio columns to numeric — "N/A" strings become NaN.
    for col in ("ratio_gc_sw", "ratio_gc_only", "ratio_sw_only", "enrollment"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def build_ms_directory(ay: int = 2025):
    """MS Directory long-format: one row per (DBN, program) with admission
    method + priority strings. 793 program-rows across 477 schools in
    Fall 2025. Reads the feather built by scripts/fetch_data.py."""
    import pandas as pd
    df = pd.read_feather(SOURCE / f"ms-directory-{ay}.feather")
    keep = [
        "dbn", "school_name", "district", "boro", "address", "neighborhood",
        "gradespan", "ay",
        "program_index", "program_name", "program_code", "admission_method",
        "priority1", "priority2", "priority3", "priority4", "priority5", "priority6",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()
    if "district" in df.columns:
        df["district"] = pd.to_numeric(df["district"], errors="coerce").astype("Int64")
    if "program_index" in df.columns:
        df["program_index"] = df["program_index"].astype(int)
    return df


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
    ("nyc-nta-2010.geojson", "nta-2010.geojson"),
    ("nyc-co-location-2020-21.csv", "co-locations.csv"),
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
    "CREATE INDEX IF NOT EXISTS idx_staffing_dbn ON staffing(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_ms_directory_dbn ON ms_directory(dbn);",
    "CREATE INDEX IF NOT EXISTS idx_ms_directory_district ON ms_directory(district);",
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

    crosswalk = _beds_to_dbn_crosswalk()
    tables = {
        "demographics": build_demographics(),
        "snapshots": build_snapshots(),
        "exams_ela": build_exam("load_ela", crosswalk),
        "exams_math": build_exam("load_math", crosswalk),
        "regents": build_regents(),
        "class_size": build_class_size(),
        "ptr": build_ptr(),
        "shsat": build_shsat(),
        "budgets": build_budgets(),
        "staffing": build_staffing(),
        "ms_directory": build_ms_directory(),
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
