"""School data-access functions. Transport-agnostic: take primitives, return
Pydantic models. These are the operations every surface (HTML, JSON, MCP,
A2A, ACP) shares.
"""
import re
from typing import Optional

from .. import config  # noqa: F401  -- must precede nycschools import (sets data-dir env var)

import pandas as pd

from nycschools import schools as ns_schools

from .. import data
from .models import (
    ClassSizeRow,
    DemographicsYear,
    ExamRow,
    LocationInfo,
    PtrInfo,
    SchoolDetail,
    SchoolSummary,
    SnapshotInfo,
)

DBN_RE = re.compile(r"^\d{0,2}[MXKQR]\d{1,4}$", re.IGNORECASE)


def _opt_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _opt_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_str(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float):
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
    s = str(v).strip()
    return s or None


def _to_summary(row) -> SchoolSummary:
    return SchoolSummary(
        dbn=row["dbn"],
        school_name=row["school_name"],
        short_name=_opt_str(row.get("short_name")),
        district=_opt_int(row.get("district")),
        boro=_opt_str(row.get("boro")),
        school_level=_opt_str(row.get("school_level")),
        total_enrollment=_opt_int(row.get("total_enrollment")),
        zip=_opt_str(row.get("zip")),
    )


def search_schools(query: str, limit: int = 10) -> list[SchoolSummary]:
    """Find schools by name, short name, or partial DBN.

    Latest academic year only — one row per school.
    """
    q = (query or "").strip()
    if not q:
        return []

    df = data.get_store().demographics
    latest = df[df["ay"] == df["ay"].max()]

    if DBN_RE.match(q):
        results = latest[latest["dbn"].str.contains(q, case=False, na=False)]
    else:
        results = ns_schools.search(latest, q)

    return [_to_summary(row) for _, row in results.head(limit).iterrows()]


def _demographics_for(dbn: str) -> list[DemographicsYear]:
    df = data.get_store().demographics
    rows = df[df["dbn"] == dbn].sort_values("ay")
    return [
        DemographicsYear(
            ay=_opt_int(row["ay"]) or 0,
            total_enrollment=_opt_int(row.get("total_enrollment")),
            poverty_pct=_opt_float(row.get("poverty_pct")),
            eni=_opt_float(row.get("eni")),
            ell_pct=_opt_float(row.get("ell_pct")),
            swd_pct=_opt_float(row.get("swd_pct")),
            asian_pct=_opt_float(row.get("asian_pct")),
            black_pct=_opt_float(row.get("black_pct")),
            hispanic_pct=_opt_float(row.get("hispanic_pct")),
            white_pct=_opt_float(row.get("white_pct")),
        )
        for _, row in rows.iterrows()
    ]


def _snapshot_for(dbn: str) -> Optional[SnapshotInfo]:
    df = data.get_store().snapshots
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return SnapshotInfo(
        ay=_opt_int(r.get("ay")),
        address=_opt_str(r.get("address")),
        city_state_zip=_opt_str(r.get("city_state_zip")),
        principal_name=_opt_str(r.get("principal_name")),
        principal_phone=_opt_str(r.get("principal_phone_number")),
        principal_years=_opt_float(r.get("principal_years")),
        attendance_rate=_opt_float(r.get("attendance_rate")),
        student_chronic_absent=_opt_str(r.get("student_chronic_absent")),
        teacher_3yr_exp_pct=_opt_float(r.get("teacher_3yr_exp_pct")),
        quality_review_year=_opt_int(r.get("quality_review_year")),
        quality_review_url=_opt_str(r.get("quality_review_url")),
        es_admissions=_opt_str(r.get("all_es_admissionsmethods")),
        ms_admissions=_opt_str(r.get("all_ms_admissionsmethods")),
        co_located=_opt_str(r.get("co_located")),
        co_located_n=_opt_int(r.get("colocated_n")),
        website=_opt_str(r.get("website_es")) or _opt_str(r.get("website_ms")),
        grades_text=_opt_str(r.get("grades_text")),
        dates_of_review=_opt_str(r.get("dates_of_review")),
    )


def _location_for(dbn: str) -> Optional[LocationInfo]:
    df = data.get_store().locations
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return LocationInfo(
        latitude=_opt_float(r.get("latitude")),
        longitude=_opt_float(r.get("longitude")),
        nta_name=_opt_str(r.get("nta_name")),
        council_district=_opt_int(r.get("council_district")),
        community_district=_opt_int(r.get("community_district")),
        census_tract=_opt_float(r.get("census_tract")),
        open_year=_opt_int(r.get("open_year")),
        grades_text=_opt_str(r.get("grades_final_text")) or _opt_str(r.get("grades_text")),
        status=_opt_str(r.get("status_descriptions")),
        location_category=_opt_str(r.get("location_category_description")),
        managed_by=_opt_str(r.get("managed_by_name")),
    )


def _exam_rows_for(dbn: str, df) -> list[ExamRow]:
    rows = df[(df["dbn"] == dbn) & (df["category"].fillna("All Students") == "All Students")]
    out: list[ExamRow] = []
    for _, r in rows.iterrows():
        out.append(
            ExamRow(
                ay=_opt_int(r.get("ay")) or 0,
                grade=str(r.get("grade", "")),
                number_tested=_opt_int(r.get("number_tested")),
                mean_scale_score=_opt_float(r.get("mean_scale_score")),
                pct_level_1=_opt_float(r.get("level_1_pct")),
                pct_level_2=_opt_float(r.get("level_2_pct")),
                pct_level_3=_opt_float(r.get("level_3_pct")),
                pct_level_4=_opt_float(r.get("level_4_pct")),
                pct_proficient=_opt_float(r.get("level_3_4_pct")),
            )
        )

    def sort_key(x: ExamRow):
        # Year desc, then "All Grades" first within each year, then numeric grade asc.
        grade_key = -1 if x.grade == "All Grades" else _opt_int(x.grade) or 99
        return (-x.ay, grade_key)

    out.sort(key=sort_key)
    return out


def _class_size_for(dbn: str) -> tuple[list[ClassSizeRow], Optional[int]]:
    df = data.get_store().class_size
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        return [], None
    latest_year = _opt_int(rows["ay"].max())
    rows = rows[rows["ay"] == rows["ay"].max()]
    out = [
        ClassSizeRow(
            grade=str(r.get("grade", "")),
            program_type=_opt_str(r.get("program_type")),
            subject=_opt_str(r.get("subject")),
            students_n=_opt_int(r.get("students_n")),
            classes_n=_opt_int(r.get("classes_n")),
            avg_class_size=_opt_float(r.get("avg_class_size")),
            min_class_size=_opt_int(r.get("min_class_size")),
            max_class_size=_opt_int(r.get("max_class_size")),
        )
        for _, r in rows.iterrows()
    ]
    return out, latest_year


def _ptr_for(dbn: str) -> Optional[PtrInfo]:
    df = data.get_store().ptr
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        return None
    latest = rows.sort_values("ay").iloc[-1]
    return PtrInfo(ay=_opt_int(latest.get("ay")) or 0, ratio=_opt_float(latest.get("ptr")))


def get_school(dbn: str) -> Optional[SchoolDetail]:
    """Return the full report card for a DBN, or None if not found.

    Pulls from every dataframe we have. Sections that have no data for this
    DBN come back as None / empty list.
    """
    store = data.get_store()
    rows = store.demographics[store.demographics["dbn"] == dbn]
    if rows.empty:
        return None

    latest_row = rows.sort_values("ay").iloc[-1]
    summary = _to_summary(latest_row)

    class_size_rows, class_size_year = _class_size_for(dbn)

    return SchoolDetail(
        summary=summary,
        demographics_by_year=_demographics_for(dbn),
        snapshot=_snapshot_for(dbn),
        location=_location_for(dbn),
        ela=_exam_rows_for(dbn, store.ela),
        math=_exam_rows_for(dbn, store.math),
        class_size=class_size_rows,
        class_size_year=class_size_year,
        ptr=_ptr_for(dbn),
    )
