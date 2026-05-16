"""School data-access functions. Transport-agnostic: take primitives, return
Pydantic models. These are the operations every surface (HTML, JSON, MCP,
A2A, ACP) shares.
"""
import re
from typing import Optional

from .. import config  # noqa: F401

import pandas as pd
from rapidfuzz import fuzz

from .. import data
from .models import (
    BudgetCategory,
    BudgetSummary,
    CccrRow,
    ChronicAbsenteeismRow,
    ClassSizeRow,
    CoLocatedSchool,
    DemographicsYear,
    EssaStatus,
    ExamRow,
    ExpenditureYear,
    GraduationRow,
    HsDirectoryInfo,
    HsProgram,
    LocationInfo,
    NysedReport,
    OutOfCertYear,
    PeerExtreme,
    PeerRank,
    PtrInfo,
    RegentsRow,
    SchoolDetail,
    SchoolSummary,
    ShsatYear,
    SnapshotInfo,
    StaffingInfo,
    SubgroupStatus,
    TeacherQualityYear,
)

DBN_RE = re.compile(r"^\d{0,2}[MXKQR]\d{1,4}$", re.IGNORECASE)
_BUDGET_RE = re.compile(r"[^0-9.\-]")
_CLEAN_NAME_RE = re.compile(r"[^a-z0-9 ]")
_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_ZEROS_RE = re.compile(r"\b0+(\d)")


def _clean_name(name: str) -> str:
    """Normalize a school name for matching: lowercase, strip non-alphanumerics,
    collapse whitespace. Mirrors the column upstream nycschools writes into
    `school-demographics.csv` so we can do O(1) exact-match lookups."""
    if not name:
        return ""
    s = _CLEAN_NAME_RE.sub("", name.lower())
    return _WHITESPACE_RE.sub(" ", s).strip()


def _search_normalize(name: str) -> str:
    """Normalize a school name for fuzzy SEARCH (slightly more aggressive
    than `_clean_name`). On top of clean_name's lowercase + punctuation
    strip — so 'P.S. 321' becomes 'ps 321' — also strips leading zeros
    from any number, so 'PS 039 Henry Bristow' matches a user typing
    'PS 39'. Common when users half-remember the school number."""
    if not name:
        return ""
    return _LEADING_ZEROS_RE.sub(r"\1", _clean_name(name))


def _fuzzy_search(df: pd.DataFrame, qry: str, limit: int) -> pd.DataFrame:
    """Score every school against the query, return the best matches.

    Scoring:
      - Primary: rapidfuzz.partial_ratio — finds the best alignment of
        the query within the school name. Handles partial-name queries
        ("LaGuardia" → full name match) AND extra-word queries
        ("art and design high school" → "Art and Design High School")
        without the over-permissiveness of partial_token_set_ratio
        (which scores 100 for any token overlap, including "and").
      - Boost +10 for exact clean_name match.
      - Boost +5 for exact short_name match (e.g. "PS 321" → 15K321).
      - Secondary tie-breaker: full-string fuzz.ratio against the
        school name. Penalizes length difference, so for queries with
        many partial-100 matches ("Stuyvesant" — Stuyvesant HS,
        Bedford Stuyvesant Charter, etc.) the school whose name is
        closest in length to the query ranks first.

    We deliberately do NOT short-circuit on exact match: when the user
    types "Fiorello LaGuardia", PS 205's clean_name happens to be exactly
    "fiorello laguardia", but they probably also want LaGuardia HS in
    the results. Both schools end up in the list; user picks.
    """
    if df.empty:
        return df

    q_clean = _clean_name(qry)
    q_search = _search_normalize(qry)
    df = df.copy()

    # Match against the search-normalized name — strips "P.S." → "ps" and
    # leading zeros from any number — so "PS 321" finds "P.S. 321 William
    # Penn" and "PS 39" finds "P.S. 039 Henry Bristow."
    df["_search_name"] = df["school_name"].fillna("").apply(_search_normalize)

    # max(partial_ratio, token_set_ratio):
    # - partial_ratio handles substring/abbreviation queries
    #   ("Stuyvesant" → "Stuyvesant High School").
    # - token_set_ratio handles non-contiguous-token queries where the
    #   target inserts words between the query's tokens
    #   ("Bronx Science" → "The Bronx High School of Science").
    # Either alone misses cases the other catches.
    df["_match"] = df["_search_name"].apply(
        lambda sn: max(
            float(fuzz.partial_ratio(q_search, sn)),
            float(fuzz.token_set_ratio(q_search, sn)),
        )
    )

    if q_clean and "clean_name" in df.columns:
        df.loc[df["clean_name"].fillna("") == q_clean, "_match"] += 10

    if "short_name" in df.columns:
        df.loc[
            df["short_name"].fillna("").apply(_search_normalize) == q_search,
            "_match",
        ] += 5

    df = df[df["_match"] >= 80]
    if df.empty:
        return df

    df["_tiebreak"] = df["school_name"].fillna("").apply(
        lambda sn: float(fuzz.ratio(qry, sn))
    )
    return df.sort_values(["_match", "_tiebreak"], ascending=[False, False]).head(limit)


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
    if s.lower() in ("nan", "none", "<na>"):
        return None
    return s or None


def _parse_budget(v) -> Optional[float]:
    """Galaxy budgets ship as '$ 187,530'-style strings. Normalize to float.
    TODO: this currency parsing logically belongs upstream in nycschools."""
    s = _opt_str(v)
    if s is None:
        return None
    cleaned = _BUDGET_RE.sub("", s)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _opt_pct(v) -> Optional[float]:
    """NYSED stores percentages in 0–100 units; the rest of the app treats
    fractions in 0–1. Convert by dividing by 100."""
    f = _opt_float(v)
    return f / 100 if f is not None else None


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
        results = latest[latest["dbn"].str.contains(q, case=False, na=False)].head(limit)
    else:
        results = _fuzzy_search(latest, q, limit)

    return [_to_summary(row) for _, row in results.iterrows()]


# ----- per-section helpers -----

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
    # All-Students filtering already happened at build time (build_db.py).
    rows = df[df["dbn"] == dbn]
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
        grade_key = -1 if x.grade == "All Grades" else _opt_int(x.grade) or 99
        return (-x.ay, grade_key)

    out.sort(key=sort_key)
    return out


def _regents_for(dbn: str) -> list[RegentsRow]:
    df = data.get_store().regents
    # All-Students filtering already happened at build time (build_db.py).
    rows = df[df["dbn"] == dbn]
    out: list[RegentsRow] = []
    for _, r in rows.iterrows():
        out.append(
            RegentsRow(
                ay=_opt_int(r.get("ay")) or 0,
                regents_exam=str(r.get("regents_exam", "")),
                number_tested=_opt_int(r.get("number_tested")),
                mean_score=_opt_float(r.get("mean_score")),
                pct_below_65=_opt_float(r.get("below_65_pct")),
                pct_above_64=_opt_float(r.get("above_64_pct")),
                pct_above_79=_opt_float(r.get("above_79_pct")),
                pct_college_ready=_opt_float(r.get("college_ready_pct")),
            )
        )
    out.sort(key=lambda x: (-x.ay, x.regents_exam))
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


def _shsat_for(dbn: str) -> list[ShsatYear]:
    df = data.get_store().shsat
    rows = df[df["dbn"] == dbn].sort_values("ay", ascending=False)
    return [
        ShsatYear(
            ay=_opt_int(r.get("ay")) or 0,
            applicants_n=_opt_int(r.get("hs_applicants_n")),
            testers_n=_opt_int(r.get("testers_n")),
            offers_n=_opt_int(r.get("offers_n")),
            offers_pct=_opt_float(r.get("offers_pct")),
        )
        for _, r in rows.iterrows()
    ]


def _budget_for(dbn: str) -> Optional[BudgetSummary]:
    df = data.get_store().budgets
    rows = df[df["dbn"] == dbn].copy()
    if rows.empty:
        return None
    rows["budget_num"] = rows["budget"].apply(_parse_budget)
    rows = rows.dropna(subset=["budget_num"])
    if rows.empty:
        return None
    latest_ay = _opt_int(rows["ay"].max()) or 0
    rows = rows[rows["ay"] == rows["ay"].max()]

    grouped = (
        rows.groupby("category", dropna=False)
        .agg(total=("budget_num", "sum"), positions=("positions", "sum"))
        .reset_index()
        .sort_values("total", ascending=False)
    )

    by_category = [
        BudgetCategory(
            category=_opt_str(r["category"]) or "(uncategorized)",
            total=float(r["total"]),
            positions=_opt_float(r.get("positions")),
        )
        for _, r in grouped.iterrows()
    ]

    return BudgetSummary(
        ay=latest_ay,
        total=float(rows["budget_num"].sum()),
        total_positions=_opt_float(rows["positions"].sum() if "positions" in rows.columns else None),
        by_category=by_category,
    )


def _hs_programs(row) -> list[HsProgram]:
    """Reshape the wide HS-directory row's program{N} / interest{N} / etc.
    columns into a list of HsProgram models."""
    out: list[HsProgram] = []
    for i in range(1, 13):
        name = _opt_str(row.get(f"program{i}"))
        if not name:
            continue
        requirements = []
        for j in range(1, 8):
            req = _opt_str(row.get(f"requirement_{j}_{i}"))
            if req:
                requirements.append(req)
        out.append(
            HsProgram(
                code=_opt_str(row.get(f"code{i}")),
                name=name,
                interest=_opt_str(row.get(f"interest{i}")),
                description=_opt_str(row.get(f"prgdesc{i}")),
                method=_opt_str(row.get(f"method{i}")),
                eligibility=_opt_str(row.get(f"eligibility{i}")),
                seats_9th=_opt_int(row.get(f"seats9ge{i}")),
                applicants_9th=_opt_int(row.get(f"grade9geapplicants{i}")),
                applicants_per_seat_9th=_opt_float(row.get(f"grade9geapplicantsperseat{i}")),
                requirements=requirements,
                admissions_priority=_opt_str(
                    row.get(f"admissionspriority1{i}") or row.get(f"admissionspriority2{i}")
                ),
            )
        )
    return out


def _hs_directory_for(dbn: str) -> Optional[HsDirectoryInfo]:
    df = data.get_store().hs_directory
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        return None
    r = rows.iloc[0]

    address = _opt_str(r.get("primary_address_line_1"))
    city = _opt_str(r.get("city"))
    postcode = _opt_str(r.get("postcode"))
    full_address = ", ".join(filter(None, [address, f"{city} NY {postcode}".strip() if city or postcode else None]))

    opps = [_opt_str(r.get(f"academicopportunities{i}")) for i in range(1, 7)]
    opps = [o for o in opps if o]

    div_in_adm = r.get("diversity_in_admissions")
    if pd.isna(div_in_adm) or div_in_adm is None:
        div_bool = None
    else:
        try:
            div_bool = bool(float(div_in_adm))
        except (TypeError, ValueError):
            div_bool = None

    return HsDirectoryInfo(
        ay=_opt_int(r.get("ay")) or 2021,
        overview=_opt_str(r.get("overview_paragraph")),
        total_students=_opt_int(r.get("total_students")),
        attendance_rate=_opt_float(r.get("attendance_rate")),
        graduation_rate=_opt_float(r.get("graduation_rate")),
        college_career_rate=_opt_float(r.get("college_career_rate")),
        pct_students_safe=_opt_float(r.get("pct_stu_safe")),
        pct_students_enough_variety=_opt_float(r.get("pct_stu_enough_variety")),
        school_accessibility=_opt_str(r.get("school_accessibility")),
        neighborhood=_opt_str(r.get("neighborhood")),
        address=full_address or None,
        phone=_opt_str(r.get("phone_number")),
        fax=_opt_str(r.get("fax_number")),
        email=_opt_str(r.get("school_email")),
        website=_opt_str(r.get("website")),
        sqr_website=_opt_str(r.get("sqr_website")),
        recruitment_website=_opt_str(r.get("recruitment_website")),
        subway=_opt_str(r.get("subway")),
        bus=_opt_str(r.get("bus")),
        start_time=_opt_str(r.get("start_time")),
        end_time=_opt_str(r.get("end_time")),
        advanced_placement_courses=_opt_str(r.get("advancedplacement_courses")),
        language_classes=_opt_str(r.get("language_classes")),
        psal_sports_boys=_opt_str(r.get("psal_sports_boys")),
        psal_sports_girls=_opt_str(r.get("psal_sports_girls")),
        psal_sports_coed=_opt_str(r.get("psal_sports_coed")),
        diploma_endorsements=_opt_str(r.get("diplomaendorsements")),
        diversity_in_admissions=div_bool,
        diversity_details=_opt_str(r.get("diadetails")),
        school_10th_seats=_opt_str(r.get("school_10th_seats")),
        ell_programs=_opt_str(r.get("ell_programs")),
        school_sports=_opt_str(r.get("school_sports")),
        online_ap_courses=_opt_str(r.get("online_ap_courses")),
        online_language_courses=_opt_str(r.get("online_language_courses")),
        summer_session=_opt_str(r.get("summer_session")),
        extracurricular_activities=_opt_str(r.get("extracurricular_activities")),
        addtl_info=_opt_str(r.get("addtl_info1")),
        grades_served=_opt_str(r.get("finalgrades")) or _opt_str(r.get("gradespan")),
        campus_name=_opt_str(r.get("campus_name")),
        building_code=_opt_str(r.get("building_code")),
        academic_opportunities=opps,
        programs=_hs_programs(r),
    )


# ----- NYSED helpers (joined on BEDS code) -----

def _beds_for(dbn: str) -> Optional[str]:
    """Resolve the 12-digit BEDS code from our demographics table for a DBN.
    NYSED's ENTITY_CD is the same code stored as a 12-char string."""
    df = data.get_store().demographics
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        return None
    beds = rows.iloc[-1].get("beds")
    if beds is None or pd.isna(beds):
        return None
    try:
        return f"{int(beds):012d}"
    except (TypeError, ValueError):
        s = str(beds).strip()
        return s if s else None


def _essa_status_for(beds: str) -> list[EssaStatus]:
    df = data.get_store().nysed_essa_status
    rows = df[df["ENTITY_CD"] == beds].sort_values("YEAR")
    return [
        EssaStatus(
            year=_opt_int(r.get("YEAR")) or 0,
            overall_status=_opt_str(r.get("OVERALL_STATUS")) or "Unknown",
        )
        for _, r in rows.iterrows()
        if _opt_str(r.get("OVERALL_STATUS"))
    ]


def _essa_subgroup_for(beds: str) -> list[SubgroupStatus]:
    df = data.get_store().nysed_essa_subgroup
    rows = df[df["ENTITY_CD"] == beds].sort_values(["YEAR", "SUBGROUP_NAME"])
    return [
        SubgroupStatus(
            year=_opt_int(r.get("YEAR")) or 0,
            school_type=_opt_str(r.get("SCHOOL_TYPE")),
            subgroup=_opt_str(r.get("SUBGROUP_NAME")) or "Unknown",
            overall_status=_opt_str(r.get("OVERALL_STATUS")) or "Unknown",
        )
        for _, r in rows.iterrows()
    ]


def _chronic_for(beds: str) -> list[ChronicAbsenteeismRow]:
    df = data.get_store().nysed_chronic
    rows = df[df["ENTITY_CD"] == beds].sort_values(["YEAR", "LEVEL", "SUBGROUP_NAME"])
    return [
        ChronicAbsenteeismRow(
            year=_opt_int(r.get("YEAR")) or 0,
            level=_opt_str(r.get("LEVEL")),
            subgroup=_opt_str(r.get("SUBGROUP_NAME")) or "Unknown",
            enrollment=_opt_int(r.get("ENROLLMENT")),
            absent_count=_opt_int(r.get("ABSENT_COUNT")),
            absent_rate=_opt_pct(r.get("ABSENT_RATE")),
        )
        for _, r in rows.iterrows()
        if _opt_str(r.get("SUBGROUP_NAME"))
    ]


def _expenditures_for(beds: str) -> list[ExpenditureYear]:
    df = data.get_store().nysed_expenditures
    rows = df[df["ENTITY_CD"] == beds].sort_values("YEAR")
    return [
        ExpenditureYear(
            year=_opt_int(r.get("YEAR")) or 0,
            pupil_count=_opt_int(r.get("PUPIL_COUNT_TOT")),
            federal_total=_opt_float(r.get("FEDERAL_EXP")),
            state_local_total=_opt_float(r.get("STATE_LOCAL_EXP")),
            combined_total=_opt_float(r.get("FED_STATE_LOCAL_EXP")),
            per_pupil_federal=_opt_float(r.get("PER_FEDERAL_EXP")),
            per_pupil_state_local=_opt_float(r.get("PER_STATE_LOCAL_EXP")),
            per_pupil_combined=_opt_float(r.get("PER_FED_STATE_LOCAL_EXP")),
        )
        for _, r in rows.iterrows()
    ]


def _teacher_quality_for(beds: str) -> list[TeacherQualityYear]:
    df = data.get_store().nysed_inexp_teachers
    rows = df[df["ENTITY_CD"] == beds].sort_values("YEAR")
    return [
        TeacherQualityYear(
            year=_opt_int(r.get("YEAR")) or 0,
            num_teachers=_opt_int(r.get("NUM_TEACH")),
            num_inexp_teachers=_opt_int(r.get("NUM_TEACH_INEXP")),
            pct_inexp_teachers=_opt_pct(r.get("PER_TEACH_INEXP")),
            num_principals=_opt_int(r.get("NUM_PRINC")),
            num_inexp_principals=_opt_int(r.get("NUM_PRINC_INEXP")),
            pct_inexp_principals=_opt_pct(r.get("PER_PRINC_INEXP")),
        )
        for _, r in rows.iterrows()
    ]


def _out_of_cert_for(beds: str) -> list[OutOfCertYear]:
    df = data.get_store().nysed_out_of_cert
    rows = df[df["ENTITY_CD"] == beds].sort_values("YEAR")
    return [
        OutOfCertYear(
            year=_opt_int(r.get("YEAR")) or 0,
            num_teachers=_opt_int(r.get("NUM_TEACH_OC")),
            num_out_of_cert=_opt_int(r.get("NUM_OUT_CERT")),
            pct_out_of_cert=_opt_pct(r.get("PER_OUT_CERT")),
        )
        for _, r in rows.iterrows()
    ]


def _hs_grad_for(beds: str) -> list[GraduationRow]:
    df = data.get_store().nysed_hs_grad
    rows = df[df["ENTITY_CD"] == beds].sort_values(["YEAR", "SUBGROUP_NAME", "COHORT"])
    return [
        GraduationRow(
            year=_opt_int(r.get("YEAR")) or 0,
            subgroup=_opt_str(r.get("SUBGROUP_NAME")) or "Unknown",
            cohort=_opt_str(r.get("COHORT")) or "Unknown",
            cohort_count=_opt_int(r.get("COHORT_COUNT")),
            grad_count=_opt_int(r.get("GRAD_COUNT")),
            grad_rate=_opt_pct(r.get("GRAD_RATE")),
        )
        for _, r in rows.iterrows()
    ]


def _hs_cccr_for(beds: str) -> list[CccrRow]:
    df = data.get_store().nysed_hs_cccr
    rows = df[df["ENTITY_CD"] == beds].sort_values(["YEAR", "SUBGROUP_NAME"])
    return [
        CccrRow(
            year=_opt_int(r.get("YEAR")) or 0,
            subgroup=_opt_str(r.get("SUBGROUP_NAME")) or "Unknown",
            cohort_size=_opt_int(r.get("COHORT")),
            index_score=_opt_float(r.get("INDEX")),
            level=_opt_int(r.get("LEVEL")),
        )
        for _, r in rows.iterrows()
    ]


def _nysed_for(dbn: str) -> Optional[NysedReport]:
    beds = _beds_for(dbn)
    if not beds:
        return None
    report = NysedReport(
        essa_status=_essa_status_for(beds),
        essa_status_by_subgroup=_essa_subgroup_for(beds),
        chronic_absenteeism=_chronic_for(beds),
        expenditures=_expenditures_for(beds),
        teacher_quality=_teacher_quality_for(beds),
        out_of_cert=_out_of_cert_for(beds),
        hs_graduation=_hs_grad_for(beds),
        hs_cccr=_hs_cccr_for(beds),
    )
    # If every section is empty, signal "no NYSED data" by returning None.
    if not any((
        report.essa_status, report.essa_status_by_subgroup, report.chronic_absenteeism,
        report.expenditures, report.teacher_quality, report.out_of_cert,
        report.hs_graduation, report.hs_cccr,
    )):
        return None
    return report


# ----- peer comparison -----

def _school_level_for(dbn: str) -> Optional[str]:
    """Latest known school_level for this DBN, or None if missing."""
    df = data.get_store().demographics
    latest = df[df["ay"] == df["ay"].max()]
    rows = latest[latest["dbn"] == dbn]
    if rows.empty:
        return None
    sl = rows.iloc[0].get("school_level")
    if sl is None or pd.isna(sl):
        return None
    return str(sl)


def _nysed_level_for(school_level: str) -> Optional[str]:
    """Map our school_level to NYSED's EM/HS distinction.

    NYSED tables split into Elementary/Middle ("EM") vs High School ("HS")
    indicators. Schools spanning both (K-12) get None — skip ranking.
    """
    sl = (school_level or "").lower()
    if any(k in sl for k in ("elementary", "middle", "k-8")):
        return "EM"
    if "high" in sl or "secondary" in sl:
        return "HS"
    return None


def _rank_in_cohort(
    cohort: pd.DataFrame,
    key_col: str,
    key_val,
    value_col: str,
    ascending: bool = False,
) -> Optional[tuple[int, int, float, "pd.Series", "pd.Series"]]:
    """Find the row where `cohort[key_col] == key_val`, then rank it within
    `cohort` by `value_col`. Returns (rank, total, value, top_row, bottom_row).

    By default sorts descending — rank #1 = highest value, top_row = highest,
    bottom_row = lowest.
    """
    matches = cohort[cohort[key_col] == key_val]
    if matches.empty:
        return None
    val = matches.iloc[0].get(value_col)
    if val is None or pd.isna(val):
        return None
    cohort = cohort.dropna(subset=[value_col])
    if len(cohort) < 2:
        return None
    sorted_ = cohort.sort_values(value_col, ascending=ascending).reset_index(drop=True)
    idx = sorted_.index[sorted_[key_col] == key_val].tolist()
    if not idx:
        return None
    return idx[0] + 1, len(sorted_), float(val), sorted_.iloc[0], sorted_.iloc[-1]


def _extreme_from_row(row, name_col: str, dbn_col: str, format_value) -> PeerExtreme:
    return PeerExtreme(
        school_name=str(row.get(name_col, "")),
        dbn=str(row.get(dbn_col, "")),
        value_display=format_value(row),
    )


def _peer_rank_eni(dbn: str) -> Optional[PeerRank]:
    """ENI rank vs same-school-level peers. ENI is NYC DOE's preferred
    equity proxy (used for Fair Student Funding) — see README "ENI vs
    poverty" for why it beats raw poverty_pct as a ranking signal."""
    school_level = _school_level_for(dbn)
    if school_level is None:
        return None
    df = data.get_store().demographics
    latest = df[df["ay"] == df["ay"].max()]
    cohort = latest[latest["school_level"] == school_level]
    info = _rank_in_cohort(cohort, "dbn", dbn, "eni", ascending=False)
    if info is None:
        return None
    rank, total, value, top, bottom = info
    fmt = lambda r: f"{r['eni']:.2f}"
    return PeerRank(
        metric_label="Economic Need Index",
        value_display=f"{value:.2f}",
        caption="0–1 scale; 1 = highest need",
        rank=rank,
        total=total,
        cohort_label=f"{school_level} schools",
        extreme_high=_extreme_from_row(top, "school_name", "dbn", fmt),
        extreme_low=_extreme_from_row(bottom, "school_name", "dbn", fmt),
    )


def _peer_rank_exam_proficient(
    dbn: str, exam_df: pd.DataFrame, metric_label: str
) -> Optional[PeerRank]:
    """Latest-year 'All Grades' rollup of level_3_4_pct, ranked against
    same-school-level peers. Returns None for high schools (3-8 state
    tests don't apply) or schools without exam data."""
    school_level = _school_level_for(dbn)
    if school_level is None or "high" in school_level.lower():
        return None
    store = data.get_store()
    demo_latest = store.demographics[
        store.demographics["ay"] == store.demographics["ay"].max()
    ]
    same_level = demo_latest[demo_latest["school_level"] == school_level][
        ["dbn", "school_name"]
    ]
    latest_year = exam_df["ay"].max()
    cohort = exam_df[
        (exam_df["ay"] == latest_year) & (exam_df["grade"] == "All Grades")
    ].merge(same_level, on="dbn", how="inner")
    info = _rank_in_cohort(cohort, "dbn", dbn, "level_3_4_pct", ascending=False)
    if info is None:
        return None
    rank, total, value, top, bottom = info
    fmt = lambda r: f"{float(r['level_3_4_pct']) * 100:.1f}%"
    return PeerRank(
        metric_label=metric_label,
        value_display=f"{value * 100:.1f}%",
        caption="scored proficient (L3 + L4)",
        rank=rank,
        total=total,
        cohort_label=f"{school_level} schools",
        extreme_high=_extreme_from_row(top, "school_name", "dbn", fmt),
        extreme_low=_extreme_from_row(bottom, "school_name", "dbn", fmt),
    )


def _peer_rank_ela_proficient(dbn: str) -> Optional[PeerRank]:
    return _peer_rank_exam_proficient(dbn, data.get_store().ela, "ELA proficient")


def _peer_rank_math_proficient(dbn: str) -> Optional[PeerRank]:
    return _peer_rank_exam_proficient(dbn, data.get_store().math, "Math proficient")


def _peer_rank_ptr(dbn: str) -> Optional[PeerRank]:
    school_level = _school_level_for(dbn)
    if school_level is None:
        return None
    store = data.get_store()
    same_level_dbns = set(
        store.demographics[
            (store.demographics["ay"] == store.demographics["ay"].max())
            & (store.demographics["school_level"] == school_level)
        ]["dbn"]
    )
    ptr = store.ptr
    latest_year = ptr["ay"].max()
    cohort = ptr[(ptr["ay"] == latest_year) & (ptr["dbn"].isin(same_level_dbns))]
    info = _rank_in_cohort(cohort, "dbn", dbn, "ptr", ascending=False)
    if info is None:
        return None
    rank, total, value, top, bottom = info
    fmt = lambda r: f"{r['ptr']:.1f}"
    return PeerRank(
        metric_label="Pupil:teacher ratio",
        value_display=f"{value:.1f}",
        caption="students per teacher",
        rank=rank,
        total=total,
        cohort_label=f"{school_level} schools",
        extreme_high=_extreme_from_row(top, "school_name", "dbn", fmt),
        extreme_low=_extreme_from_row(bottom, "school_name", "dbn", fmt),
    )


def _peer_rank_chronic(dbn: str) -> Optional[PeerRank]:
    """Chronic-absenteeism rank, All Students subgroup, latest year, against
    same-school-level NYSED-level peers."""
    school_level = _school_level_for(dbn)
    if school_level is None:
        return None
    nysed_level = _nysed_level_for(school_level)
    if nysed_level is None:
        return None
    beds = _beds_for(dbn)
    if beds is None:
        return None

    store = data.get_store()
    same_level = store.demographics[
        (store.demographics["ay"] == store.demographics["ay"].max())
        & (store.demographics["school_level"] == school_level)
    ]
    same_level_beds = {
        f"{int(b):012d}" for b in same_level["beds"].dropna()
    }

    chronic = store.nysed_chronic
    latest_year = chronic["YEAR"].max()
    cohort = chronic[
        (chronic["YEAR"] == latest_year)
        & (chronic["LEVEL"] == nysed_level)
        & (chronic["SUBGROUP_NAME"] == "All Students")
        & (chronic["ENTITY_CD"].isin(same_level_beds))
    ]
    info = _rank_in_cohort(cohort, "ENTITY_CD", beds, "ABSENT_RATE", ascending=False)
    if info is None:
        return None
    rank, total, value, top, bottom = info
    fmt = lambda r: f"{float(r['ABSENT_RATE']):.1f}%"
    return PeerRank(
        metric_label="Chronic absenteeism",
        value_display=f"{value:.1f}%",
        caption="absent ≥10% of enrolled days",
        rank=rank,
        total=total,
        cohort_label=f"{school_level} schools",
        extreme_high=_extreme_from_row(top, "ENTITY_NAME", "ENTITY_CD", fmt),
        extreme_low=_extreme_from_row(bottom, "ENTITY_NAME", "ENTITY_CD", fmt),
    )


def _peer_ranks_for(dbn: str) -> dict[str, PeerRank]:
    """Build the peer_ranks dict. Each metric we add becomes one key."""
    out: dict[str, PeerRank] = {}
    for key, fn in [
        ("eni", _peer_rank_eni),
        ("ela_proficient", _peer_rank_ela_proficient),
        ("math_proficient", _peer_rank_math_proficient),
        ("ptr", _peer_rank_ptr),
        ("chronic_absent", _peer_rank_chronic),
    ]:
        rank = fn(dbn)
        if rank:
            out[key] = rank
    return out


def _staffing_for(dbn: str) -> Optional[StaffingInfo]:
    """Latest-year Guidance Counselor + Social Worker FTE counts plus
    DOE-computed pupil ratios."""
    df = data.get_store().staffing
    rows = df[df["dbn"].str.upper() == dbn.upper()]
    if rows.empty:
        return None
    latest = rows.sort_values("ay").iloc[-1]
    return StaffingInfo(
        ay=int(latest["ay"]),
        total_gc=_opt_float(latest.get("total_gc")),
        total_sw=_opt_float(latest.get("total_sw")),
        total_gc_sw=_opt_float(latest.get("total_gc_sw")),
        full_time_gc=_opt_float(latest.get("full_time_gc")),
        full_time_sw=_opt_float(latest.get("full_time_sw")),
        part_time_gc=_opt_float(latest.get("part_time_gc")),
        part_time_sw=_opt_float(latest.get("part_time_sw")),
        enrollment=_opt_int(latest.get("enrollment")),
        ratio_gc_sw=_opt_float(latest.get("ratio_gc_sw")),
        ratio_gc_only=_opt_float(latest.get("ratio_gc_only")),
        ratio_sw_only=_opt_float(latest.get("ratio_sw_only")),
        school_psychologist_mandated=_opt_str(latest.get("school_psychologist_mandated")),
        cbo_partner_mental_health=_opt_str(latest.get("cbo_partner_mental_health")),
    )


def _co_located_for(dbn: str) -> list[CoLocatedSchool]:
    """Schools sharing a building with `dbn`. NYC DOE's Co-Location Reports
    list each school's building IDs (e.g. PS 372 occupies K113 + K834).
    Schools with any overlapping building ID are co-located. Excludes the
    focal school itself."""
    df = data.get_store().co_locations
    if df.empty:
        return []
    self_row = df[df["dbn"].str.upper() == dbn.upper()]
    if self_row.empty:
        return []
    self_buildings = {
        b.strip() for b in (self_row.iloc[0]["building_ids"] or "").split(",") if b.strip()
    }
    if not self_buildings:
        return []
    out: list[CoLocatedSchool] = []
    for _, r in df.iterrows():
        if r["dbn"].upper() == dbn.upper():
            continue
        peer_buildings = {
            b.strip() for b in (r["building_ids"] or "").split(",") if b.strip()
        }
        shared = sorted(self_buildings & peer_buildings)
        if shared:
            out.append(CoLocatedSchool(
                dbn=r["dbn"], school_name=r["school_name"],
                building_ids=shared,
            ))
    out.sort(key=lambda s: s.school_name)
    return out


# ----- top-level -----

def school_staffing(dbn: str) -> Optional[StaffingInfo]:
    """Guidance Counselor + Social Worker FTE breakdown + pupil ratios for
    a single school. Latest year on file. Returns None if no row matches."""
    return _staffing_for(dbn)


def co_located_schools(dbn: str) -> list[CoLocatedSchool]:
    """Schools sharing a building with `dbn`. Empty list if none or if
    the school isn't in the DOE Co-Location Report."""
    return _co_located_for(dbn)


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
        co_located=_co_located_for(dbn),
        staffing=_staffing_for(dbn),
        ela=_exam_rows_for(dbn, store.ela),
        math=_exam_rows_for(dbn, store.math),
        regents=_regents_for(dbn),
        class_size=class_size_rows,
        class_size_year=class_size_year,
        ptr=_ptr_for(dbn),
        shsat=_shsat_for(dbn),
        budget=_budget_for(dbn),
        hs_directory=_hs_directory_for(dbn),
        nysed=_nysed_for(dbn),
        peer_ranks=_peer_ranks_for(dbn),
    )
