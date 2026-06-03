"""Dynamic capability discovery — registry-driven metric access.

Per Dan Shipper's "agent-native architectures" piece, the agent-facing
surface is a `discover + access` pair rather than a tool per metric.
`list_school_metrics()` / `list_neighborhood_metrics()` enumerate every
metric the system can answer; `get_school_metric()` /
`get_neighborhood_metric()` return values for a specific entity. When a
new column lands in scripts/build_db.py, surfacing it to agents is a
matter of adding one registry entry here — no new MCP tool needed.

Additive layer. The existing curated tools (`top_schools`,
`bulk_metrics`, `aggregate_by_neighborhood`, …) still source their
vocabulary from `analytics.METRIC_DESCRIPTIONS`; folding those tools
onto this registry is a separate follow-up. The registry below is
designed to be authoritative once that fold happens — every id matches
a key in METRIC_DESCRIPTIONS, the descriptions are richer here, and the
computation is delegated to `analytics._compute_metric` so the numbers
are guaranteed identical.

School and neighborhood are distinct data universes — Scott's call.
They don't share a single dispatcher. The neighborhood registry entries
explicitly name their `underlying_school_metric`, so the relationship
is recoverable when needed.
"""

from typing import Optional

import pandas as pd

from .. import data
from . import analytics
from .analytics import (
    METRIC_NAMES,
    _aggregate_metric_by_group,
    _beds_to_str,
    _candidate_schools,
    _candidate_schools_with_geo,
    _compute_metric,
    _fuzzy_match_ntas,
    _MIN_NTA_SCHOOLS,
    _NTA_FUZZY_MIN_SCORE,
    _NTA_OTHER_CANDIDATE_BAND,
)
from .models import (
    NeighborhoodMetricDef,
    NeighborhoodMetricValue,
    SchoolMetricDef,
    SchoolMetricValue,
)


# Levels named individually rather than "*" so the agent can filter
# concretely. "*" reads as "wildcard" but agents tend to apply it
# literally — explicit list avoids that failure mode.
_ALL_LEVELS = ["elementary", "middle", "high", "K-8", "6-12"]
_NON_HS = ["elementary", "middle", "K-8", "6-12"]
_HS_ONLY = ["high", "6-12"]


# ---------- School-scope registry ----------

SCHOOL_METRICS: dict[str, SchoolMetricDef] = {
    "eni": SchoolMetricDef(
        id="eni",
        label="Economic Need Index",
        description=(
            "NYC DOE's composite poverty index, 0..1. Higher values mean greater "
            "economic need. The equity-proxy of choice for ranking and peer "
            "comparison — `poverty_pct` is for direct interpretability only "
            "because the 2017 CEP transition broke its longitudinal continuity."
        ),
        unit="pct",
        source_table="demographics",
        applicable_levels=_ALL_LEVELS,
        vintage_note="NYC DOE annual demographics export, latest available academic year.",
    ),
    "poverty_pct": SchoolMetricDef(
        id="poverty_pct",
        label="Direct-Certified Poverty Share",
        description=(
            "Share of students directly certified via HRA/SNAP/Medicaid, 0..1. "
            "Stricter than the older FRPL definition. Use `eni` rather than this "
            "for ranking — the 2017 CEP transition broke continuity for "
            "poverty_pct."
        ),
        unit="pct",
        source_table="demographics",
        applicable_levels=_ALL_LEVELS,
        vintage_note="NYC DOE annual demographics export, latest available academic year.",
    ),
    "attendance_rate": SchoolMetricDef(
        id="attendance_rate",
        label="Daily Attendance Rate",
        description=(
            "Average daily attendance rate, 0..1. Higher values mean more "
            "students present per day."
        ),
        unit="pct",
        source_table="snapshots",
        applicable_levels=_ALL_LEVELS,
        vintage_note="DOE School Snapshot, mostly AY 2016 vintage — this dataset has not been refreshed by DOE in recent years.",
    ),
    "chronic_absent_rate": SchoolMetricDef(
        id="chronic_absent_rate",
        label="Chronic Absenteeism Rate (All Students)",
        description=(
            "Share of students absent 18 or more days, 0..1. Higher values "
            "mean more chronically-absent students. The All-Students subgroup; "
            "use `school_swd_outcomes` for the Students-With-Disabilities cut."
        ),
        unit="pct",
        source_table="nysed_chronic",
        applicable_levels=_ALL_LEVELS,
        vintage_note="NYSED School Report Card, All Students subgroup, latest reporting year.",
    ),
    "ela_pct_proficient": SchoolMetricDef(
        id="ela_pct_proficient",
        label="ELA Proficiency Rate (Grades 3–8)",
        description=(
            "Share of students at NY State ELA Level 3 or 4 across all tested "
            "grades, 0..1. ES / MS / K-8 / 6-12 only — high schools take "
            "Regents, not state ELA."
        ),
        unit="pct",
        source_table="ela",
        applicable_levels=_NON_HS,
        vintage_note="NYS Grades 3–8 ELA Assessment, latest year, 'All Grades' row.",
    ),
    "math_pct_proficient": SchoolMetricDef(
        id="math_pct_proficient",
        label="Math Proficiency Rate (Grades 3–8)",
        description=(
            "Share of students at NY State Math Level 3 or 4 across all tested "
            "grades, 0..1. ES / MS / K-8 / 6-12 only — high schools take "
            "Regents, not state math."
        ),
        unit="pct",
        source_table="math",
        applicable_levels=_NON_HS,
        vintage_note="NYS Grades 3–8 Math Assessment, latest year, 'All Grades' row.",
    ),
    "regents_pct_above_64": SchoolMetricDef(
        id="regents_pct_above_64",
        label="Regents Passing Rate (≥65, All Exams)",
        description=(
            "Mean Regents passing rate across all exams the school administers, "
            "0..1. Passing = score 65 or higher. HS / 6-12 only."
        ),
        unit="pct",
        source_table="regents",
        applicable_levels=_HS_ONLY,
        vintage_note="NYS Regents exam results, latest year, averaged across exams.",
    ),
    "regents_pct_above_79": SchoolMetricDef(
        id="regents_pct_above_79",
        label="Regents Mastery Rate (≥80, All Exams)",
        description=(
            "Mean Regents mastery rate across all exams the school administers, "
            "0..1. Mastery = score 80 or higher. HS / 6-12 only."
        ),
        unit="pct",
        source_table="regents",
        applicable_levels=_HS_ONLY,
        vintage_note="NYS Regents exam results, latest year, averaged across exams.",
    ),
    "graduation_rate_4yr": SchoolMetricDef(
        id="graduation_rate_4yr",
        label="4-Year Graduation Rate (All Students)",
        description=(
            "Share of the 4-year cohort that graduated, 0..1. NYSED official "
            "rate, All Students subgroup. HS / 6-12 only."
        ),
        unit="pct",
        source_table="nysed_hs_grad",
        applicable_levels=_HS_ONLY,
        vintage_note="NYSED School Report Card 4-Year Cohort, All Students, latest year.",
    ),
    "pupil_teacher_ratio": SchoolMetricDef(
        id="pupil_teacher_ratio",
        label="Pupil-to-Teacher Ratio",
        description=(
            "Pupils per teacher. Higher values mean more students per teacher; "
            "lower values mean fewer."
        ),
        unit="ratio",
        source_table="ptr",
        applicable_levels=_ALL_LEVELS,
        vintage_note="NYC DOE pupil-to-teacher ratio, latest available year.",
    ),
    "pct_inexperienced_teachers": SchoolMetricDef(
        id="pct_inexperienced_teachers",
        label="Share of Teachers With <4 Years Experience",
        description=(
            "Share of the school's teachers with fewer than four years of "
            "teaching experience, 0..1. Higher values mean a less experienced "
            "teaching corps."
        ),
        unit="pct",
        source_table="nysed_inexp_teachers",
        applicable_levels=_ALL_LEVELS,
        vintage_note="NYSED School Report Card teacher quality table, latest reporting year.",
    ),
    "pct_out_of_cert_teachers": SchoolMetricDef(
        id="pct_out_of_cert_teachers",
        label="Share of Teachers Teaching Out of Certification",
        description=(
            "Share of the school's teachers teaching outside their certified "
            "subject area, 0..1. Higher values mean more out-of-cert teaching."
        ),
        unit="pct",
        source_table="nysed_out_of_cert",
        applicable_levels=_ALL_LEVELS,
        vintage_note="NYSED School Report Card teacher quality table, latest reporting year.",
    ),
    "per_pupil_expenditure": SchoolMetricDef(
        id="per_pupil_expenditure",
        label="Per-Pupil Expenditure (Federal + State + Local)",
        description=(
            "Dollars per pupil per year, combined federal + state + local "
            "spending. Higher values mean more spent per pupil."
        ),
        unit="dollars",
        source_table="nysed_expenditures",
        applicable_levels=_ALL_LEVELS,
        vintage_note="NYSED School Report Card per-pupil expenditures, latest reporting year.",
    ),
}


# ---------- Neighborhood-scope registry ----------
#
# Every entry is a mean of the underlying school metric across the active
# schools in an NTA (filtered to a school level when the underlying metric
# is level-specific). Distinct data universe from SCHOOL_METRICS — the
# agent should ask `list_neighborhood_metrics()` separately when ranking
# NTAs, not infer from the school catalog.

NEIGHBORHOOD_METRICS: dict[str, NeighborhoodMetricDef] = {
    k: NeighborhoodMetricDef(
        id=v.id,
        label=v.label + " (NTA mean)",
        description=(
            f"Mean of `{v.id}` across schools in the NTA. "
            + v.description
        ),
        unit=v.unit,
        underlying_school_metric=v.id,
        aggregation="mean",
        applicable_levels=list(v.applicable_levels),
        vintage_note=v.vintage_note,
    )
    for k, v in SCHOOL_METRICS.items()
}


# ---------- Discovery ----------

def list_school_metrics() -> list[SchoolMetricDef]:
    """Every metric the agent can ask `get_school_metric` for. Order is the
    registry order — designed so an agent scanning sees high-leverage
    equity and outcome metrics first."""
    return list(SCHOOL_METRICS.values())


def list_neighborhood_metrics() -> list[NeighborhoodMetricDef]:
    """Every metric the agent can ask `get_neighborhood_metric` for. Each
    entry names its `underlying_school_metric`, since the neighborhood
    value is computed as an aggregation over schools."""
    return list(NEIGHBORHOOD_METRICS.values())


# ---------- Access ----------

def _school_identity(dbn: str, store) -> Optional[tuple[str, Optional[str]]]:
    """Return (school_name, beds_str) for a DBN, or None if not present
    in demographics. Looks at every demographics row, not just active —
    discovery should answer about historical schools too."""
    df = store.demographics
    rows = df.loc[df["dbn"] == dbn, ["ay", "school_name", "beds"]]
    if rows.empty:
        return None
    latest = rows.sort_values("ay").iloc[-1]
    return str(latest["school_name"]), _beds_to_str(latest["beds"])


def get_school_metric(dbn: str, metric: str) -> Optional[SchoolMetricValue]:
    """Look up one metric for one school. Returns None for an unknown
    DBN (school not in our data); raises ValueError for an unknown
    metric (the agent should have called `list_school_metrics` first).

    `value` is None when the school has no data for this metric — that's
    distinct from an unknown DBN. For metrics with restricted level
    applicability (e.g. Regents on an elementary school), `value` is
    None and `note` carries the reason."""
    if metric not in SCHOOL_METRICS:
        raise ValueError(
            f"unknown metric: {metric!r}. Call list_school_metrics() to "
            f"discover available metrics. Valid ids: {tuple(SCHOOL_METRICS)}"
        )
    spec = SCHOOL_METRICS[metric]
    store = data.get_store()
    identity = _school_identity(dbn, store)
    if identity is None:
        return None
    school_name, beds = identity

    # Level-applicability gate: surface the reason cleanly instead of
    # letting _compute_metric return a silent None.
    level = _school_level_for(dbn, store)
    note: Optional[str] = None
    if level and level not in spec.applicable_levels:
        note = (
            f"{spec.label} is not reported for {level} schools; applicable "
            f"levels are {spec.applicable_levels}."
        )
        return SchoolMetricValue(
            dbn=dbn,
            school_name=school_name,
            metric=metric,
            label=spec.label,
            value=None,
            unit=spec.unit,
            vintage_note=spec.vintage_note,
            note=note,
        )

    value = _compute_metric(metric, dbn, beds, store)
    return SchoolMetricValue(
        dbn=dbn,
        school_name=school_name,
        metric=metric,
        label=spec.label,
        value=value,
        unit=spec.unit,
        vintage_note=spec.vintage_note,
    )


def _school_level_for(dbn: str, store) -> Optional[str]:
    df = store.demographics
    rows = df.loc[df["dbn"] == dbn, ["ay", "school_level"]].dropna(subset=["school_level"])
    if rows.empty:
        return None
    return str(rows.sort_values("ay").iloc[-1]["school_level"])


def get_neighborhood_metric(
    nta: str,
    metric: str,
    level: Optional[str] = None,
) -> Optional[NeighborhoodMetricValue]:
    """Mean of `metric`'s underlying school metric across schools in the
    NTA. Fuzzy-matches the NTA name — pass "Park Slope" and get
    "Park Slope-Gowanus."

    Returns None when no NTA fuzzy-matches above the minimum score
    threshold. Raises ValueError for an unknown metric. `value` is None
    when the NTA has data but no schools at the requested level (e.g.
    asking for a HS-only metric in an NTA without HS schools)."""
    if metric not in NEIGHBORHOOD_METRICS:
        raise ValueError(
            f"unknown metric: {metric!r}. Call list_neighborhood_metrics() to "
            f"discover available metrics. Valid ids: {tuple(NEIGHBORHOOD_METRICS)}"
        )
    if not nta or not nta.strip():
        return None
    spec = NEIGHBORHOOD_METRICS[metric]
    underlying = spec.underlying_school_metric

    store = data.get_store()
    candidates = _fuzzy_match_ntas(nta, store)
    if not candidates:
        return None
    top_name, top_boro, top_score = candidates[0]
    cutoff = max(_NTA_FUZZY_MIN_SCORE, top_score - _NTA_OTHER_CANDIDATE_BAND)
    others = [name for name, _, score in candidates[1:6] if score >= cutoff]

    # Build the candidate-school table (with NTA + geo joined) and
    # restrict to this NTA. Then aggregate.
    df = _candidate_schools_with_geo(level, store)
    df = df[df["nta_name"] == top_name]
    if df.empty:
        return NeighborhoodMetricValue(
            nta_name=top_name,
            boro=top_boro,
            metric=metric,
            label=spec.label,
            value=None,
            n_schools=0,
            school_level=level,
            unit=spec.unit,
            aggregation=spec.aggregation,
            vintage_note=spec.vintage_note,
            other_candidates=others,
        )

    rows = _aggregate_metric_by_group(df, "nta_name", underlying, store, min_schools=1)
    if not rows:
        return NeighborhoodMetricValue(
            nta_name=top_name,
            boro=top_boro,
            metric=metric,
            label=spec.label,
            value=None,
            n_schools=0,
            school_level=level,
            unit=spec.unit,
            aggregation=spec.aggregation,
            vintage_note=spec.vintage_note,
            other_candidates=others,
        )
    _name, _boro, n, val = rows[0]
    return NeighborhoodMetricValue(
        nta_name=top_name,
        boro=top_boro,
        metric=metric,
        label=spec.label,
        value=val,
        n_schools=n,
        school_level=level,
        unit=spec.unit,
        aggregation=spec.aggregation,
        vintage_note=spec.vintage_note,
        other_candidates=others,
    )


# ---------- Internal consistency check ----------
#
# Pinned at import time: every registry id must correspond to an
# analytics._compute_metric path. If a metric is added here without a
# matching computation, fail fast at module load — not at the first
# agent call.
_unknown = set(SCHOOL_METRICS) - set(METRIC_NAMES)
if _unknown:
    raise RuntimeError(
        f"metrics.py registry has ids with no analytics._compute_metric "
        f"path: {sorted(_unknown)}. Add the computation in analytics.py "
        f"or remove the entry from SCHOOL_METRICS."
    )
del _unknown
