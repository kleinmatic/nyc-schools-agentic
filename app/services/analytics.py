"""Cross-school queries: ranking, filtering, bulk metric extraction.

Distinct from `schools.py` (which is one-school-at-a-time detail). These
power both agentic use cases ("top high schools by Regents passing rate",
"correlate ENI with graduation rate") and a future accountability-
dashboard homepage. Service-layer-only: no transport leakage.

Convention: percentages return as 0..1 fractions, matching the rest of
the app. Source data is mixed (Regents pct cols are 0..100; NYSED pct
cols are 0..100; demographics ENI is already 0..1; ELA/math
level_3_4_pct is already 0..1) — we normalize at this boundary.
"""
from typing import Optional

import pandas as pd

from .. import data
from .models import (
    BoroughGrid,
    BoroughRow,
    HomepageLeaderboards,
    HsListing,
    LeaderboardTable,
    MetricRow,
    NeighborhoodAggregate,
    NeighborhoodLeaderboard,
    PeerCohort,
    PeerSchool,
    RankedSchool,
)


# Metric vocabulary surfaced to MCP. Each entry: name, one-line description,
# applicable school_level set ("*" = any), source-table provenance string.
# Order is the discovery order an agent sees in tool descriptions.

METRIC_DESCRIPTIONS: dict[str, str] = {
    "eni": "Economic Need Index, 0..1 (latest year). NYC DOE's poverty composite. Use for equity ranking.",
    "poverty_pct": "Share of students directly certified via HRA/SNAP/Medicaid (0..1, latest year). Stricter than the older FRPL definition.",
    "attendance_rate": "Daily attendance rate, 0..1, from DOE school snapshot (mostly AY 2016 vintage).",
    "chronic_absent_rate": "Share of students absent ≥18 days (0..1, NYSED latest year, All Students). Lower is better.",
    "ela_pct_proficient": "Share of students at NYS ELA Level 3-4 (0..1, All Grades, latest year). ES/MS/K-8/6-12 only.",
    "math_pct_proficient": "Share of students at NYS Math Level 3-4 (0..1, All Grades, latest year). ES/MS/K-8/6-12 only.",
    "regents_pct_above_64": "Mean Regents passing rate (≥65) across all exams (0..1, latest year). HS / 6-12 only.",
    "regents_pct_above_79": "Mean Regents mastery rate (≥80) across all exams (0..1, latest year). HS / 6-12 only.",
    "graduation_rate_4yr": "4-year cohort graduation rate (NYSED, latest year, All Students, 0..1). HS / 6-12 only.",
    "pupil_teacher_ratio": "Pupils per teacher (latest year). Lower is generally seen as better.",
    "pct_inexperienced_teachers": "Share of teachers with <4 years experience (NYSED, latest year, 0..1).",
    "pct_out_of_cert_teachers": "Share of teachers teaching outside their certification area (NYSED, latest year, 0..1).",
    "per_pupil_expenditure": "Per-pupil expenditure, federal + state + local combined ($/year, NYSED, latest year).",
}

METRIC_NAMES = tuple(METRIC_DESCRIPTIONS.keys())


# -------- Per-school metric computation --------

def _eni_for(dbn: str, store) -> Optional[float]:
    df = store.demographics
    rows = df.loc[df["dbn"] == dbn, ["ay", "eni"]].dropna(subset=["eni"])
    if rows.empty:
        return None
    return float(rows.sort_values("ay").iloc[-1]["eni"])


def _poverty_for(dbn: str, store) -> Optional[float]:
    df = store.demographics
    rows = df.loc[df["dbn"] == dbn, ["ay", "poverty_pct"]].dropna(subset=["poverty_pct"])
    if rows.empty:
        return None
    return float(rows.sort_values("ay").iloc[-1]["poverty_pct"])


def _attendance_for(dbn: str, store) -> Optional[float]:
    df = store.snapshots
    rows = df.loc[df["dbn"] == dbn, ["ay", "attendance_rate"]].dropna(subset=["attendance_rate"])
    if rows.empty:
        return None
    return float(rows.sort_values("ay").iloc[-1]["attendance_rate"])


def _ela_proficient_for(dbn: str, store) -> Optional[float]:
    return _exam_proficient_for(dbn, store.ela)


def _math_proficient_for(dbn: str, store) -> Optional[float]:
    return _exam_proficient_for(dbn, store.math)


def _exam_proficient_for(dbn: str, df: pd.DataFrame) -> Optional[float]:
    """ELA/math `level_3_4_pct` for the latest year, "All Grades" row."""
    rows = df[(df["dbn"] == dbn) & (df["grade"] == "All Grades")]
    rows = rows.dropna(subset=["level_3_4_pct"])
    if rows.empty:
        return None
    latest = rows.sort_values("ay").iloc[-1]
    return float(latest["level_3_4_pct"])


def _regents_pct_for(dbn: str, store, col: str) -> Optional[float]:
    """Mean of a Regents pct column across all exams in the school's
    latest year. `col` is 'above_64_pct' or 'above_79_pct'. Source 0..100."""
    df = store.regents
    school = df[df["dbn"] == dbn]
    if school.empty:
        return None
    latest = school[school["ay"] == school["ay"].max()]
    vals = latest[col].dropna()
    if vals.empty:
        return None
    return float(vals.mean()) / 100.0


def _grad_rate_4yr_for(beds: Optional[str], store) -> Optional[float]:
    if not beds:
        return None
    df = store.nysed_hs_grad
    rows = df[
        (df["ENTITY_CD"] == beds)
        & (df["COHORT"] == "4-Year")
        & (df["SUBGROUP_NAME"] == "All Students")
    ]
    if rows.empty:
        return None
    latest = rows.loc[rows["YEAR"] == rows["YEAR"].max()].iloc[0]
    rate = latest["GRAD_RATE"]
    if pd.isna(rate):
        return None
    return float(rate) / 100.0


def _chronic_rate_for(beds: Optional[str], store) -> Optional[float]:
    if not beds:
        return None
    df = store.nysed_chronic
    rows = df[(df["ENTITY_CD"] == beds) & (df["SUBGROUP_NAME"] == "All Students")]
    if rows.empty:
        return None
    latest = rows.loc[rows["YEAR"] == rows["YEAR"].max()].iloc[0]
    rate = latest["ABSENT_RATE"]
    if pd.isna(rate):
        return None
    return float(rate) / 100.0


def _ptr_for(dbn: str, store) -> Optional[float]:
    df = store.ptr
    rows = df.loc[df["dbn"] == dbn, ["ay", "ptr"]].dropna(subset=["ptr"])
    if rows.empty:
        return None
    return float(rows.sort_values("ay").iloc[-1]["ptr"])


def _nysed_pct_for(beds: Optional[str], df: pd.DataFrame, col: str) -> Optional[float]:
    """Generic latest-year pct lookup against a NYSED school×year table.
    Source values are stored as strings (Text columns in Access); empty
    strings or 'NA' come back as NaN after coercion."""
    if not beds:
        return None
    rows = df[df["ENTITY_CD"] == beds].copy()
    if rows.empty:
        return None
    latest = rows.loc[rows["YEAR"] == rows["YEAR"].max()].iloc[0]
    val = pd.to_numeric(latest[col], errors="coerce")
    if pd.isna(val):
        return None
    return float(val) / 100.0


def _per_pupil_for(beds: Optional[str], store) -> Optional[float]:
    """Per-pupil combined expenditure ($/year). NOT a percent; not divided."""
    if not beds:
        return None
    df = store.nysed_expenditures
    rows = df[df["ENTITY_CD"] == beds]
    if rows.empty:
        return None
    latest = rows.loc[rows["YEAR"] == rows["YEAR"].max()].iloc[0]
    val = pd.to_numeric(latest["PER_FED_STATE_LOCAL_EXP"], errors="coerce")
    if pd.isna(val):
        return None
    return float(val)


def _compute_metric(name: str, dbn: str, beds: Optional[str], store) -> Optional[float]:
    if name == "eni":
        return _eni_for(dbn, store)
    if name == "poverty_pct":
        return _poverty_for(dbn, store)
    if name == "attendance_rate":
        return _attendance_for(dbn, store)
    if name == "chronic_absent_rate":
        return _chronic_rate_for(beds, store)
    if name == "ela_pct_proficient":
        return _ela_proficient_for(dbn, store)
    if name == "math_pct_proficient":
        return _math_proficient_for(dbn, store)
    if name == "regents_pct_above_64":
        return _regents_pct_for(dbn, store, "above_64_pct")
    if name == "regents_pct_above_79":
        return _regents_pct_for(dbn, store, "above_79_pct")
    if name == "graduation_rate_4yr":
        return _grad_rate_4yr_for(beds, store)
    if name == "pupil_teacher_ratio":
        return _ptr_for(dbn, store)
    if name == "pct_inexperienced_teachers":
        return _nysed_pct_for(beds, store.nysed_inexp_teachers, "PER_TEACH_INEXP")
    if name == "pct_out_of_cert_teachers":
        return _nysed_pct_for(beds, store.nysed_out_of_cert, "PER_OUT_CERT")
    if name == "per_pupil_expenditure":
        return _per_pupil_for(beds, store)
    raise ValueError(f"unknown metric: {name!r}. Valid: {METRIC_NAMES}")


# -------- Helper: build the candidate-school table for a level + filters --------

VALID_LEVELS = ("elementary", "middle", "high", "K-8", "6-12")
BORO_NAME_BY_LETTER = {"M": "Manhattan", "X": "Bronx", "K": "Brooklyn", "Q": "Queens", "R": "Staten Island"}
BORO_LETTER_BY_NAME = {v.upper(): k for k, v in BORO_NAME_BY_LETTER.items()}

# Schools whose latest demographics row is older than this are closed /
# inactive — exclude from rankings to avoid stale ENI=0 sentinels and
# zombie rows polluting the leaderboards.
_ACTIVE_SCHOOL_MIN_AY = 2022


def _beds_to_str(b) -> Optional[str]:
    """demographics.beds is int64; nysed_*.ENTITY_CD is a 12-char string.
    0 / NaN means missing (school predates BEDS or is otherwise unmapped)."""
    if pd.isna(b):
        return None
    bi = int(b)
    if bi == 0:
        return None
    return f"{bi:012d}"


def _candidate_schools(level: Optional[str], borough: Optional[str], store) -> pd.DataFrame:
    """One row per active school: dbn, school_name, school_level, beds, boro.
    `beds` is normalized to the 12-char string form NYSED tables key on,
    or None when missing. Filtered to active schools (latest demographics
    >= 2022) plus the optional level / borough filters."""
    df = store.demographics
    cols = ["dbn", "school_name", "school_level", "beds", "boro", "ay"]
    df = df[[c for c in cols if c in df.columns]].copy()
    df = df.sort_values("ay").drop_duplicates("dbn", keep="last")
    df = df[df["ay"] >= _ACTIVE_SCHOOL_MIN_AY]
    df["beds"] = df["beds"].apply(_beds_to_str)
    if level:
        if level not in VALID_LEVELS:
            raise ValueError(f"unknown level: {level!r}. Valid: {VALID_LEVELS}")
        df = df[df["school_level"] == level]
    if borough:
        b = borough.strip().upper()
        full = BORO_NAME_BY_LETTER.get(b) or BORO_NAME_BY_LETTER.get(BORO_LETTER_BY_NAME.get(b, ""))
        if not full:
            raise ValueError(f"unknown borough: {borough!r}")
        df = df[df["boro"] == full]
    return df


# -------- Public service functions --------

def top_schools(
    metric: str,
    level: Optional[str] = "high",
    limit: int = 20,
    borough: Optional[str] = None,
    ascending: bool = False,
) -> list[RankedSchool]:
    """Top N schools by a named metric. Default sort: descending (highest
    first). Pass `ascending=True` for "lowest values first" — useful when
    the metric is one where lower is better (chronic absence) or when
    the question is e.g. "lowest-ENI schools." See METRIC_DESCRIPTIONS
    for the metric vocabulary."""
    if metric not in METRIC_DESCRIPTIONS:
        raise ValueError(f"unknown metric: {metric!r}. Valid: {METRIC_NAMES}")
    store = data.get_store()
    candidates = _candidate_schools(level, borough, store)
    rows: list[tuple[str, str, str, float]] = []
    for _, c in candidates.iterrows():
        v = _compute_metric(metric, c["dbn"], c.get("beds"), store)
        if v is None:
            continue
        rows.append((c["dbn"], c["school_name"], c.get("school_level") or "", v))
    rows.sort(key=lambda r: r[3], reverse=not ascending)
    return [
        RankedSchool(
            rank=i + 1, dbn=dbn, school_name=name,
            school_level=lvl or None, metric=metric, value=val,
        )
        for i, (dbn, name, lvl, val) in enumerate(rows[:limit])
    ]


def bulk_metrics(
    level: Optional[str] = "high",
    metrics: Optional[list[str]] = None,
    borough: Optional[str] = None,
) -> list[MetricRow]:
    """One row per school with the requested metrics. For correlations
    and cross-school analytics. Missing values are None — never coerce
    to 0, since that breaks downstream stats. Default `metrics` is all
    of them (~13 fields × N schools — ~10K tokens for HS-level full)."""
    if not metrics:
        metrics = list(METRIC_NAMES)
    unknown = [m for m in metrics if m not in METRIC_DESCRIPTIONS]
    if unknown:
        raise ValueError(f"unknown metric(s): {unknown}. Valid: {METRIC_NAMES}")
    store = data.get_store()
    candidates = _candidate_schools(level, borough, store)
    out: list[MetricRow] = []
    for _, c in candidates.iterrows():
        vals: dict[str, Optional[float]] = {
            m: _compute_metric(m, c["dbn"], c.get("beds"), store) for m in metrics
        }
        out.append(MetricRow(
            dbn=c["dbn"],
            school_name=c["school_name"],
            school_level=c.get("school_level") or None,
            metrics=vals,
        ))
    return out


# -------- Geographic aggregations: NTAs and boroughs --------

# Minimum schools per NTA required to be included in a leaderboard.
# Single-school neighborhoods produce noisy "averages" that overstate /
# understate the area's true distribution.
_MIN_NTA_SCHOOLS = 5


def _candidate_schools_with_geo(level: Optional[str], store) -> pd.DataFrame:
    """Active candidate schools joined with NTA + district from locations.
    Schools without an NTA assigned are dropped (~7% of schools)."""
    cands = _candidate_schools(level=level, borough=None, store=store)
    loc = store.locations[["dbn", "nta_name", "district"]].copy()
    loc = loc.rename(columns={"district": "geo_district"})
    df = cands.merge(loc, on="dbn", how="left")
    return df[df["nta_name"].notna()]


def _aggregate_metric_by_group(
    df: pd.DataFrame,
    group_col: str,
    metric: str,
    store,
    min_schools: int,
) -> list[tuple[str, Optional[str], int, float]]:
    """Compute the per-school metric, group by `group_col`, return (name,
    boro, n, mean) tuples. Cohorts smaller than min_schools are dropped."""
    df = df.copy()
    df["_value"] = df.apply(
        lambda r: _compute_metric(metric, r["dbn"], r.get("beds"), store),
        axis=1,
    )
    df = df.dropna(subset=["_value"])
    if df.empty:
        return []
    grouped = df.groupby(group_col, dropna=True)
    out: list[tuple[str, Optional[str], int, float]] = []
    for name, sub in grouped:
        if len(sub) < min_schools:
            continue
        boro = sub["boro"].iloc[0] if "boro" in sub.columns else None
        out.append((str(name), boro, len(sub), float(sub["_value"].mean())))
    return out


def aggregate_by_neighborhood(
    metric: str,
    level: Optional[str] = "high",
    limit: int = 10,
    ascending: bool = False,
    min_schools: int = _MIN_NTA_SCHOOLS,
) -> list[NeighborhoodAggregate]:
    """Top NTAs by the mean of `metric` across their schools (within the
    given `level`). NTAs with fewer than `min_schools` schools are
    excluded — single-school NTAs produce noisy "averages.\""""
    if metric not in METRIC_DESCRIPTIONS:
        raise ValueError(f"unknown metric: {metric!r}. Valid: {METRIC_NAMES}")
    store = data.get_store()
    df = _candidate_schools_with_geo(level, store)
    rows = _aggregate_metric_by_group(df, "nta_name", metric, store, min_schools)
    rows.sort(key=lambda r: r[3], reverse=not ascending)
    return [
        NeighborhoodAggregate(
            name=name, boro=boro, n_schools=n, metric=metric, value=val,
        )
        for name, boro, n, val in rows[:limit]
    ]


def borough_summary(metrics: list[str], level: Optional[str] = None) -> BoroughGrid:
    """5-borough × N-metric overview grid. Each cell = mean of metric
    across schools in that borough (filtered to the given level if any)."""
    unknown = [m for m in metrics if m not in METRIC_DESCRIPTIONS]
    if unknown:
        raise ValueError(f"unknown metric(s): {unknown}. Valid: {METRIC_NAMES}")
    store = data.get_store()
    cands = _candidate_schools(level=level, borough=None, store=store)

    # Compute per-school metric values once per metric (so we don't iterate
    # the candidate set N times).
    rows_by_boro: dict[str, BoroughRow] = {}
    for boro in BORO_NAME_BY_LETTER.values():
        sub = cands[cands["boro"] == boro]
        agg: dict[str, Optional[float]] = {}
        for m in metrics:
            vals = [
                _compute_metric(m, r["dbn"], r.get("beds"), store)
                for _, r in sub.iterrows()
            ]
            non_null = [v for v in vals if v is not None]
            agg[m] = float(sum(non_null) / len(non_null)) if non_null else None
        rows_by_boro[boro] = BoroughRow(name=boro, n_schools=len(sub), metrics=agg)

    return BoroughGrid(
        metric_names=metrics,
        metric_labels={m: METRIC_LABELS.get(m, m) for m in metrics},
        metric_formats={m: METRIC_FORMATS.get(m, "ratio") for m in metrics},
        rows=[rows_by_boro[b] for b in ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island")],
    )


# Display defaults for metrics — used by tools that surface metric
# values directly to users (peer comparison, borough grid). Compact
# labels for table headers.
METRIC_LABELS: dict[str, str] = {
    "eni": "ENI",
    "poverty_pct": "Poverty",
    "attendance_rate": "Attendance",
    "chronic_absent_rate": "Chronic absent",
    "ela_pct_proficient": "ELA proficient",
    "math_pct_proficient": "Math proficient",
    "regents_pct_above_64": "Regents pass",
    "regents_pct_above_79": "Regents mastery",
    "graduation_rate_4yr": "4yr grad rate",
    "pupil_teacher_ratio": "Pupil:teacher",
    "pct_inexperienced_teachers": "Inexp teachers",
    "pct_out_of_cert_teachers": "Out-of-cert",
    "per_pupil_expenditure": "$/pupil",
}

METRIC_FORMATS: dict[str, str] = {
    "per_pupil_expenditure": "currency",
    "pupil_teacher_ratio": "ratio",
    # default = "pct"
}
for _m in METRIC_NAMES:
    METRIC_FORMATS.setdefault(_m, "pct")


# -------- Homepage neighborhood + borough leaderboards --------

_HOMEPAGE_NTA_LEADERBOARDS = (
    {
        "title": "Top neighborhoods — high schools",
        "description": "Average Regents passing rate (≥65) across high schools in each NTA. NTAs with fewer than 5 high schools excluded.",
        "metric": "regents_pct_above_64",
        "level": "high",
        "year_label": "2022",
    },
    {
        "title": "Top neighborhoods — elementary schools",
        "description": "Average ELA proficiency (Level 3-4) across elementary schools in each NTA.",
        "metric": "ela_pct_proficient",
        "level": "elementary",
        "year_label": "2022",
    },
)

_HOMEPAGE_BOROUGH_METRICS = ("eni", "attendance_rate", "regents_pct_above_64", "graduation_rate_4yr")


def homepage_neighborhood_leaderboards(per_table: int = 5) -> list[NeighborhoodLeaderboard]:
    out: list[NeighborhoodLeaderboard] = []
    for cfg in _HOMEPAGE_NTA_LEADERBOARDS:
        rows = aggregate_by_neighborhood(
            metric=cfg["metric"], level=cfg["level"], limit=per_table,
        )
        out.append(NeighborhoodLeaderboard(
            title=cfg["title"],
            description=cfg["description"],
            metric=cfg["metric"],
            metric_label=METRIC_LABELS.get(cfg["metric"], cfg["metric"]),
            metric_format=METRIC_FORMATS.get(cfg["metric"], "pct"),
            year_label=cfg["year_label"],
            rows=rows,
        ))
    return out


def homepage_borough_grid() -> BoroughGrid:
    """Single 5-borough overview across HS-level outcomes + equity."""
    return borough_summary(metrics=list(_HOMEPAGE_BOROUGH_METRICS), level="high")


# -------- School-page peer comparison --------

# Default metrics shown in peer-comparison tables, by school level.
# HS gets graduation/Regents; ES/MS gets ELA/math; everything gets ENI
# + attendance.
_PEER_METRICS_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "high": ("eni", "attendance_rate", "regents_pct_above_64", "graduation_rate_4yr"),
    "elementary": ("eni", "attendance_rate", "ela_pct_proficient", "math_pct_proficient"),
    "middle": ("eni", "attendance_rate", "ela_pct_proficient", "math_pct_proficient"),
    "K-8": ("eni", "attendance_rate", "ela_pct_proficient", "math_pct_proficient"),
    "6-12": ("eni", "attendance_rate", "ela_pct_proficient", "regents_pct_above_64"),
}


def _make_peer_school(dbn: str, name: str, beds: Optional[str],
                      metrics: tuple[str, ...], is_self: bool, store) -> PeerSchool:
    return PeerSchool(
        dbn=dbn,
        school_name=name,
        is_self=is_self,
        metrics={m: _compute_metric(m, dbn, beds, store) for m in metrics},
    )


def school_peers(dbn: str, scope: str, limit: int = 20) -> Optional[PeerCohort]:
    """Schools in the same NTA (`scope="neighborhood"`) or district
    (`scope="district"`) as `dbn`, with comparable metrics. Returns None
    if the focal school can't be looked up. The focal school is included
    and flagged via `is_self=True` so the template can highlight it."""
    if scope not in ("neighborhood", "district"):
        raise ValueError(f"scope must be 'neighborhood' or 'district', got {scope!r}")
    store = data.get_store()

    # Look up the focal school's NTA and district.
    loc = store.locations
    self_loc = loc[loc["dbn"] == dbn]
    if self_loc.empty:
        return None
    self_loc_row = self_loc.iloc[0]
    nta = self_loc_row.get("nta_name")
    geo_district = self_loc_row.get("district")

    # Need school_level (and beds for NYSED-derived metrics) from demographics.
    cands = _candidate_schools(level=None, borough=None, store=store)
    self_row = cands[cands["dbn"] == dbn]
    if self_row.empty:
        return None
    self_row = self_row.iloc[0]
    self_level = self_row["school_level"]
    metrics = _PEER_METRICS_BY_LEVEL.get(self_level)
    if not metrics:
        return None

    # Filter peers to the same level so we're comparing apples to apples.
    cands = cands[cands["school_level"] == self_level]
    if scope == "neighborhood":
        if not isinstance(nta, str) or not nta:
            return None
        peer_dbns = (
            loc.loc[(loc["nta_name"] == nta) & (loc["dbn"].isin(cands["dbn"])), "dbn"]
               .tolist()
        )
        label = nta
    else:  # district
        if pd.isna(geo_district):
            return None
        peer_dbns = (
            loc.loc[(loc["district"] == geo_district) & (loc["dbn"].isin(cands["dbn"])), "dbn"]
               .tolist()
        )
        label = f"District {int(geo_district)}"

    if not peer_dbns:
        return None

    peer_rows = cands[cands["dbn"].isin(peer_dbns)].copy()
    rows: list[PeerSchool] = []
    for _, r in peer_rows.iterrows():
        rows.append(_make_peer_school(
            dbn=r["dbn"], name=r["school_name"], beds=r.get("beds"),
            metrics=metrics, is_self=(r["dbn"] == dbn), store=store,
        ))
    rows.sort(key=lambda p: (not p.is_self, p.school_name))
    rows = rows[:limit]

    return PeerCohort(
        label=label,
        scope=scope,
        metric_names=list(metrics),
        metric_labels={m: METRIC_LABELS.get(m, m) for m in metrics},
        metric_formats={m: METRIC_FORMATS.get(m, "pct") for m in metrics},
        rows=rows,
    )


# -------- HS directory listing --------

def _truncate_overview(s: object) -> Optional[str]:
    if not isinstance(s, str):
        return None
    if len(s) <= 400:
        return s
    return s[:400].rsplit(" ", 1)[0] + "…"


def _hs_summary(row, dem_row) -> HsListing:
    enroll = None
    if dem_row is not None:
        e = dem_row.get("total_enrollment")
        if pd.notna(e):
            enroll = int(e)
    return HsListing(
        dbn=row["dbn"],
        school_name=row["school_name"],
        boro=BORO_NAME_BY_LETTER.get(row.get("borocode"), row.get("borocode")),
        neighborhood=row.get("neighborhood") if isinstance(row.get("neighborhood"), str) else None,
        accessibility=row.get("school_accessibility") if isinstance(row.get("school_accessibility"), str) else None,
        total_enrollment=enroll,
        overview=_truncate_overview(row.get("overview_paragraph")),
    )


_HS_PROGRAM_TEXT_FIELDS = (
    "overview_paragraph",
    "academicopportunities1", "academicopportunities2", "academicopportunities3",
    "academicopportunities4", "academicopportunities5", "academicopportunities6",
    "language_classes", "advancedplacement_courses",
    "ell_programs", "diplomaendorsements",
)


def _matches_program_keyword(row, keyword: str) -> bool:
    needle = keyword.lower()
    for f in _HS_PROGRAM_TEXT_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and needle in v.lower():
            return True
    return False


VALID_ACCESSIBILITY = ("Fully Accessible", "Partially Accessible", "Not Accessible")


# -------- Homepage leaderboards --------

# Curated set surfaced on /. Each entry: title, description, (metric,
# level, ascending), metric_label/format, year_label. Year labels are
# hard-coded against the current data vintage; update when the data
# is refreshed (see README "Refreshing data").
_HOMEPAGE_LEADERBOARDS = (
    {
        "title": "Top high schools by Regents passing rate",
        "description": "Mean share of students scoring ≥65 across all Regents exams.",
        "metric": "regents_pct_above_64",
        "level": "high",
        "ascending": False,
        "metric_label": "Passing rate (≥65)",
        "metric_format": "pct",
        "year_label": "2022",
    },
    {
        "title": "High schools with the most chronic absenteeism",
        "description": (
            "Share of students absent ≥18 days. Higher is worse — but the top of "
            "this list is dominated by transfer / alternative schools (D79, charter "
            "transfer schools) whose admissions design selects for students already "
            "disengaged from school."
        ),
        "metric": "chronic_absent_rate",
        "level": "high",
        "ascending": False,
        "metric_label": "Chronic absent",
        "metric_format": "pct",
        "year_label": "2024-25",
    },
    {
        "title": "Highest-need high schools",
        "description": (
            "Top of the Economic Need Index — NYC DOE's composite poverty / "
            "disadvantage measure. Transfer schools rank high here for the same "
            "reason they appear on the chronic-absence list."
        ),
        "metric": "eni",
        "level": "high",
        "ascending": False,
        "metric_label": "ENI",
        "metric_format": "pct",
        "year_label": "2024-25",
    },
    {
        "title": "Top elementary schools by ELA proficiency",
        "description": "NYS 3-8 ELA — share of students at Level 3 or 4 across all grades.",
        "metric": "ela_pct_proficient",
        "level": "elementary",
        "ascending": False,
        "metric_label": "% proficient",
        "metric_format": "pct",
        "year_label": "2022",
    },
)


def homepage_leaderboards(per_table: int = 5) -> HomepageLeaderboards:
    """Curated set of accountability tables for the homepage dashboard.
    Same shape every render — leaderboards aren't filterable here; for
    that, hit the per-metric API once those routes exist."""
    tables: list[LeaderboardTable] = []
    for cfg in _HOMEPAGE_LEADERBOARDS:
        rows = top_schools(
            metric=cfg["metric"], level=cfg["level"],
            limit=per_table, ascending=cfg["ascending"],
        )
        tables.append(LeaderboardTable(
            title=cfg["title"],
            description=cfg["description"],
            metric=cfg["metric"],
            metric_label=cfg["metric_label"],
            metric_format=cfg["metric_format"],
            year_label=cfg["year_label"],
            rows=rows,
        ))
    return HomepageLeaderboards(tables=tables)


def list_high_schools(
    borough: Optional[str] = None,
    accessibility: Optional[str] = None,
    program_keyword: Optional[str] = None,
    limit: int = 50,
) -> list[HsListing]:
    """Browse / filter NYC high schools from the HS Directory (AY 2021).

    Filters compose AND. `borough` accepts 'M'/'X'/'K'/'Q'/'R' or full
    names. `accessibility` matches one of the three values in
    VALID_ACCESSIBILITY. `program_keyword` is a case-insensitive substring
    search over the school overview, academic opportunities, language
    classes, and AP courses fields."""
    store = data.get_store()
    df = store.hs_directory.copy()

    if borough:
        b = borough.strip().upper()
        letter = b if b in {"M", "X", "K", "Q", "R"} else BORO_LETTER_BY_NAME.get(b)
        if not letter:
            raise ValueError(f"unknown borough: {borough!r}")
        df = df[df["borocode"] == letter]

    if accessibility:
        if accessibility not in VALID_ACCESSIBILITY:
            raise ValueError(f"unknown accessibility: {accessibility!r}. Valid: {VALID_ACCESSIBILITY}")
        df = df[df["school_accessibility"] == accessibility]

    if program_keyword:
        mask = df.apply(lambda r: _matches_program_keyword(r, program_keyword), axis=1)
        df = df[mask]

    dem = store.demographics.sort_values("ay").drop_duplicates("dbn", keep="last")
    dem_by_dbn = dem.set_index("dbn")

    out: list[HsListing] = []
    for _, row in df.head(limit).iterrows():
        dem_row = dem_by_dbn.loc[row["dbn"]] if row["dbn"] in dem_by_dbn.index else None
        out.append(_hs_summary(row, dem_row))
    return out
