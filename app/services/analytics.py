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
from collections import Counter
from functools import lru_cache
from typing import Optional

import pandas as pd

from .. import data
from rapidfuzz import fuzz

from .models import (
    BoroughGrid,
    BoroughRow,
    HomepageLeaderboards,
    HsListing,
    LeaderboardTable,
    MetricRow,
    NeighborhoodAggregate,
    NeighborhoodDetail,
    NeighborhoodLeaderboard,
    NeighborhoodPeerExtreme,
    NeighborhoodPeerRank,
    NeighborhoodSchool,
    NeighborhoodSchoolsResult,
    PeerCohort,
    PeerSchool,
    RankedSchool,
    SchoolSummary,
)
from .schools import _to_summary


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
    """One row per active school: dbn, school_name, school_level, beds, boro,
    total_enrollment. `beds` is normalized to the 12-char string form NYSED
    tables key on, or None when missing. Filtered to active schools (latest
    demographics >= 2022) plus the optional level / borough filters."""
    df = store.demographics
    cols = ["dbn", "school_name", "school_level", "beds", "boro", "ay", "total_enrollment"]
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


@lru_cache(maxsize=256)
def aggregate_by_neighborhood(
    metric: str,
    level: Optional[str] = "high",
    limit: int = 10,
    ascending: bool = False,
    min_schools: int = _MIN_NTA_SCHOOLS,
) -> list[NeighborhoodAggregate]:
    """Top NTAs by the mean of `metric` across their schools (within the
    given `level`). NTAs with fewer than `min_schools` schools are
    excluded — single-school NTAs produce noisy "averages."

    Cached: runtime data is static after the lifespan load, so identical
    (metric × level × limit × ascending × min_schools) call tuples return
    the same list. The first hit pays ~1s of pandas iteration; every hit
    after is instant.\""""
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
        "title": "Top Neighborhoods — High Schools",
        "description": "Average Regents passing rate (≥65) across high schools in each NTA. NTAs with fewer than 5 high schools excluded.",
        "metric": "regents_pct_above_64",
        "level": "high",
        "year_label": "2022",
    },
    {
        "title": "Top Neighborhoods — Elementary Schools",
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


# -------- Neighborhood lookup by colloquial name --------

# Below this fuzzy-match score, we treat the query as not finding any NTA.
# rapidfuzz partial_ratio scores 0..100. 85 is empirically the right cutoff:
# legitimate colloquial queries score ~100 (the user's term is a substring
# of the NTA name); fragment-level false positives like "xyzzy fake
# neighborhood" matching 'Norwood' on 'ood' score ~77.
_NTA_FUZZY_MIN_SCORE = 85
# Other candidates that scored within this many points of the top match
# get surfaced for the caller to consider.
_NTA_OTHER_CANDIDATE_BAND = 15


def _ntas_with_boros(store) -> list[tuple[str, Optional[str]]]:
    """Distinct (nta_name, boro) pairs across all schools. Boroughs come
    from demographics.boro joined on DBN; we take the most-common borough
    per NTA in case a single NTA spans a borough boundary."""
    loc = store.locations[["dbn", "nta_name"]].dropna(subset=["nta_name"])
    dem = store.demographics[["dbn", "boro"]].drop_duplicates("dbn")
    j = loc.merge(dem, on="dbn", how="left")
    pairs: dict[str, str] = {}
    for nta, group in j.groupby("nta_name"):
        b = group["boro"].mode()
        pairs[str(nta)] = str(b.iloc[0]) if not b.empty else None
    return sorted(pairs.items())


def _fuzzy_match_ntas(query: str, store) -> list[tuple[str, Optional[str], int]]:
    """Score every NTA against the query and return ranked candidates."""
    q = query.strip()
    if not q:
        return []
    pairs = _ntas_with_boros(store)
    scored: list[tuple[str, Optional[str], int]] = []
    for nta, boro in pairs:
        score = int(fuzz.partial_ratio(q.lower(), nta.lower()))
        if score >= _NTA_FUZZY_MIN_SCORE:
            scored.append((nta, boro, score))
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored


def _schools_in_nta(
    nta_name: str,
    level: Optional[str],
    store,
    limit: Optional[int] = None,
) -> tuple[list[SchoolSummary], int]:
    """Schools whose canonical NTA matches `nta_name`. Returns (slice,
    total_count) — total is informational so the caller can show "12 of
    30 schools" when limited."""
    loc = store.locations
    dbns = loc.loc[loc["nta_name"] == nta_name, "dbn"].tolist()
    if not dbns:
        return [], 0
    cands = _candidate_schools(level=level, borough=None, store=store)
    rows = cands[cands["dbn"].isin(dbns)].copy()
    if rows.empty:
        return [], 0
    # Reuse search_schools' per-row Pydantic builder for consistency.
    summaries = [_to_summary(r) for _, r in rows.iterrows()]
    total = len(summaries)
    summaries.sort(key=lambda s: s.school_name)
    if limit is not None:
        summaries = summaries[:limit]
    return summaries, total


def schools_in_neighborhood(
    query: str,
    level: Optional[str] = None,
    limit: int = 50,
) -> Optional[NeighborhoodSchoolsResult]:
    """Look up an NTA by colloquial name (fuzzy) and return its schools.

    Useful for "tell me about the schools in park slope" — the caller
    doesn't have to know the canonical NTA name ('Park Slope-Gowanus').
    Multi-match queries ("Harlem" matches 4 NTAs) get the best match in
    `nta_name` plus runners-up in `other_candidates`; the caller can
    disambiguate or follow up.

    Returns None if no NTA fuzzy-matches the query above a low threshold."""
    if not query or not query.strip():
        return None
    store = data.get_store()
    candidates = _fuzzy_match_ntas(query, store)
    if not candidates:
        return None
    top_name, top_boro, top_score = candidates[0]
    cutoff = max(_NTA_FUZZY_MIN_SCORE, top_score - _NTA_OTHER_CANDIDATE_BAND)
    others = [name for name, _, score in candidates[1:6] if score >= cutoff]
    schools, total = _schools_in_nta(top_name, level, store, limit=limit)
    return NeighborhoodSchoolsResult(
        nta_name=top_name,
        boro=top_boro,
        n_schools_total=total,
        other_candidates=others,
        schools=schools,
    )


def warm_caches() -> None:
    """Pre-compute the heaviest cached aggregates so the first user request
    doesn't pay the 5-second cost. Called from the FastAPI lifespan after
    `data.load()`. With Fly's rolling-deploy strategy the new machine only
    accepts traffic once /healthz passes, so the warm-up time is hidden
    behind the old machine continuing to serve.

    What's primed here:
    - `aggregate_by_neighborhood` for the 5 default neighborhood-page
      metrics (the slow part — 5 × ~1s of pandas iteration)
    - The three homepage curated sets, which internally hit
      `aggregate_by_neighborhood` with different (metric × level ×
      ascending) tuples
    """
    # Neighborhood-page peer ranks: union of every metric any NTA might
    # surface (default set + per-level peer metric sets), all sorted
    # descending against all NTAs. Signature must match what
    # `_neighborhood_peer_rank` passes exactly — lru_cache keys on
    # (args, kwargs) as given, NOT on resolved defaults.
    metrics_to_warm = set(_NEIGHBORHOOD_METRICS)
    for ms in _PEER_METRICS_BY_LEVEL.values():
        metrics_to_warm.update(ms)
    for metric in metrics_to_warm:
        aggregate_by_neighborhood(
            metric=metric, level=None, limit=10_000, ascending=False,
        )
    # Homepage sets — touch each so their internal aggregator calls cache.
    homepage_leaderboards()
    homepage_neighborhood_leaderboards()
    homepage_borough_grid()


# -------- Neighborhood page --------

# Default metric set surfaced on the neighborhood page. Mixed-level NTAs are
# common, so we include both 3-8 (ELA / math) and HS (Regents) signals — the
# table renders "—" where a metric doesn't apply to a given school's grade
# band. ENI is the lead column (NYC's equity proxy).
_NEIGHBORHOOD_METRICS: tuple[str, ...] = (
    "eni",
    "attendance_rate",
    "ela_pct_proficient",
    "math_pct_proficient",
    "regents_pct_above_64",
)


def _peer_rank_metrics_for_neighborhood(
    schools: list[SchoolSummary], level: Optional[str]
) -> tuple[str, ...]:
    """The metric set used for the NTA's *peer-rank cards* — a single
    tuple, chosen for the NTA's dominant school level (or the explicit
    `level` filter if passed). One coherent set of metrics ranks the
    neighborhood against other neighborhoods.

    Falls back to `_NEIGHBORHOOD_METRICS` if no clear winner."""
    if level and level in _PEER_METRICS_BY_LEVEL:
        return _PEER_METRICS_BY_LEVEL[level]
    counts = Counter(s.school_level for s in schools if s.school_level)
    if counts:
        dominant, _ = counts.most_common(1)[0]
        if dominant in _PEER_METRICS_BY_LEVEL:
            return _PEER_METRICS_BY_LEVEL[dominant]
    return _NEIGHBORHOOD_METRICS


def _table_metrics_for_neighborhood(
    schools: list[SchoolSummary], level: Optional[str]
) -> tuple[str, ...]:
    """The metric set used for the *per-school table* — UNION of every
    level's peer metric set that's actually represented in the NTA.

    So a mixed ES + HS neighborhood shows BOTH ELA/math AND Regents/grad
    columns; each school fills the ones that apply to its level, and the
    rest render as the (now-muted) N/A spans. The previous "use the
    dominant level's set only" logic dropped data for non-dominant
    schools — a HS in a mostly-ES NTA appeared empty even though the
    school's own page showed full data."""
    if level and level in _PEER_METRICS_BY_LEVEL:
        return _PEER_METRICS_BY_LEVEL[level]
    seen: set[str] = set()
    out: list[str] = []
    for s in schools:
        for m in _PEER_METRICS_BY_LEVEL.get(s.school_level or "", ()):
            if m not in seen:
                out.append(m)
                seen.add(m)
    return tuple(out) if out else _NEIGHBORHOOD_METRICS


def _format_metric_value(value: float, fmt: str) -> str:
    if fmt == "pct":
        return f"{value * 100:.1f}%"
    if fmt == "currency":
        return f"${value:,.0f}"
    if fmt == "ratio":
        return f"{value:.2f}"
    return f"{value:.2f}"


def _neighborhood_peer_rank(
    nta_name: str, metric: str, level: Optional[str]
) -> Optional[NeighborhoodPeerRank]:
    """Rank `nta_name` among all NYC NTAs by `metric`. Returns None if the
    NTA has fewer than _MIN_NTA_SCHOOLS contributing schools at this level
    (the aggregate is dropped) or if the metric can't be computed."""
    all_aggs = aggregate_by_neighborhood(
        metric=metric, level=level, limit=10_000, ascending=False,
    )
    if len(all_aggs) < 2:
        return None
    idx = next((i for i, a in enumerate(all_aggs) if a.name == nta_name), None)
    if idx is None:
        return None
    fmt = METRIC_FORMATS.get(metric, "pct")
    return NeighborhoodPeerRank(
        metric=metric,
        metric_label=METRIC_LABELS.get(metric, metric),
        metric_format=fmt,
        value=all_aggs[idx].value,
        value_display=_format_metric_value(all_aggs[idx].value, fmt),
        caption=f"vs {len(all_aggs)} NYC neighborhoods",
        rank=idx + 1,
        total=len(all_aggs),
        cohort_label="NYC neighborhoods",
        extreme_high=NeighborhoodPeerExtreme(
            nta_name=all_aggs[0].name,
            boro=all_aggs[0].boro,
            value_display=_format_metric_value(all_aggs[0].value, fmt),
        ),
        extreme_low=NeighborhoodPeerExtreme(
            nta_name=all_aggs[-1].name,
            boro=all_aggs[-1].boro,
            value_display=_format_metric_value(all_aggs[-1].value, fmt),
        ),
    )


def get_neighborhood(
    query: str, level: Optional[str] = None
) -> Optional[NeighborhoodDetail]:
    """Full neighborhood report: how this NTA ranks vs others on key
    metrics, plus a denormalized roster of its schools (with lat/lon for
    the map and per-school metric values for the table).

    Fuzzy-matches `query` against NTA names — "park slope" → "Park Slope-
    Gowanus" — and surfaces runner-up matches in `other_candidates` so
    the caller can disambiguate (e.g. "Harlem" has 4 NTAs). Returns None
    if no NTA scores above the fuzzy threshold or no schools live there."""
    if not query or not query.strip():
        return None
    store = data.get_store()
    candidates = _fuzzy_match_ntas(query, store)
    if not candidates:
        return None
    top_name, top_boro, top_score = candidates[0]
    cutoff = max(_NTA_FUZZY_MIN_SCORE, top_score - _NTA_OTHER_CANDIDATE_BAND)
    others = [name for name, _, score in candidates[1:6] if score >= cutoff]

    schools, _ = _schools_in_nta(top_name, level, store, limit=None)
    if not schools:
        return None

    # Two metric sets: one for the peer-rank cards (chosen by dominant
    # level so the NTA gets ranked on one coherent set of metrics), and
    # the UNION of all relevant metric sets for the per-school table
    # (so a HS in a mostly-ES neighborhood doesn't appear empty just
    # because the dominant ES set excludes Regents / grad).
    peer_rank_metrics = _peer_rank_metrics_for_neighborhood(schools, level)
    table_metrics = _table_metrics_for_neighborhood(schools, level)

    # Focal NTA polygon for the map. None if no boundary on file (a handful
    # of "park-cemetery-etc-*" NTAs don't have a single contiguous polygon).
    boundary = None
    nta_polys = store.nta_polygons
    if not nta_polys.empty:
        match = nta_polys[nta_polys["NTAName"] == top_name]
        if not match.empty:
            geom = match.iloc[0].geometry
            if geom is not None and not geom.is_empty:
                boundary = geom.__geo_interface__

    # Per-school metric values for the table. Join location for lat/lon.
    cands = _candidate_schools(level=level, borough=None, store=store)
    cands_by_dbn = {row["dbn"]: row for _, row in cands.iterrows()}
    loc_by_dbn = {row["dbn"]: row for _, row in store.locations.iterrows()}

    rows: list[NeighborhoodSchool] = []
    for s in schools:
        cand = cands_by_dbn.get(s.dbn)
        beds = _beds_to_str(cand["beds"]) if cand is not None else None
        loc = loc_by_dbn.get(s.dbn)
        lat = float(loc["latitude"]) if loc is not None and pd.notna(loc.get("latitude")) else None
        lon = float(loc["longitude"]) if loc is not None and pd.notna(loc.get("longitude")) else None
        metrics = {
            m: _compute_metric(m, s.dbn, beds, store) for m in table_metrics
        }
        rows.append(NeighborhoodSchool(
            dbn=s.dbn, school_name=s.school_name, school_level=s.school_level,
            total_enrollment=s.total_enrollment,
            latitude=lat, longitude=lon, metrics=metrics,
        ))

    # Peer ranks vs other NTAs. Drop any metric where this NTA doesn't
    # appear in the ranked aggregate (e.g. <5 schools contributing).
    peer_ranks: list[NeighborhoodPeerRank] = []
    for m in peer_rank_metrics:
        rank = _neighborhood_peer_rank(top_name, m, level)
        if rank:
            peer_ranks.append(rank)

    return NeighborhoodDetail(
        nta_name=top_name,
        boro=top_boro,
        n_schools=len(rows),
        other_candidates=others,
        peer_ranks=peer_ranks,
        metric_names=list(table_metrics),
        metric_labels={m: METRIC_LABELS.get(m, m) for m in table_metrics},
        metric_formats={m: METRIC_FORMATS.get(m, "pct") for m in table_metrics},
        schools=rows,
        boundary=boundary,
    )


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
        "title": "Top High Schools by Regents Passing Rate",
        "description": "Mean share of students scoring ≥65 across all Regents exams.",
        "metric": "regents_pct_above_64",
        "level": "high",
        "ascending": False,
        "metric_label": "Passing rate (≥65)",
        "metric_format": "pct",
        "year_label": "2022",
    },
    {
        "title": "High Schools With the Most Chronic Absenteeism",
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
        "title": "Highest-Need High Schools",
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
        "title": "Top Elementary Schools by ELA Proficiency",
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
