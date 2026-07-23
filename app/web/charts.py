"""View-layer chart data shaping. Pure presentation — converts service-layer
Pydantic models into chart-ready dicts. Lives here (not in services/) because
the shape is specific to a visual encoding, not to the data contract."""
from functools import lru_cache

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient

from .. import data
from ..services.analytics import aggregate_by_neighborhood
from ..services.models import (
    ChronicAbsenteeismRow,
    DemographicsYear,
    ExamRow,
    GraduationRow,
    RegentsRow,
)


@lru_cache(maxsize=2)
def citywide_level_breakdown(subject: str) -> list[dict]:
    """Weighted citywide pct at each proficiency level (1-4) per (ay × grade).
    Each output row is one (grade × ay × level). Used to render the NYC
    cohort comparator column on the school-page stacked-horizon chart."""
    if subject not in ("ela", "math"):
        raise ValueError(f"subject must be 'ela' or 'math', got {subject!r}")
    df = data.get_store().ela if subject == "ela" else data.get_store().math
    df = df[(df["grade"] != "All Grades") & (df["number_tested"].fillna(0) > 0)].copy()
    for lvl in (1, 2, 3, 4):
        df[f"_c{lvl}"] = df[f"level_{lvl}_pct"].fillna(0) * df["number_tested"]
    g = df.groupby(["ay", "grade"], as_index=False).agg(
        n=("number_tested", "sum"),
        c1=("_c1", "sum"), c2=("_c2", "sum"),
        c3=("_c3", "sum"), c4=("_c4", "sum"),
    )
    out: list[dict] = []
    for r in g.itertuples():
        n = float(r.n)
        if n == 0:
            continue
        for lvl, col in ((1, "c1"), (2, "c2"), (3, "c3"), (4, "c4")):
            out.append({
                "ay": int(r.ay), "grade": str(r.grade),
                "level": lvl, "pct": float(getattr(r, col)) / n,
            })
    return out


@lru_cache(maxsize=1)
def homepage_citywide() -> dict:
    """City-wide context package for the homepage: headline stats, a
    12-year enrollment series, the ELA/Math proficiency trend, and the
    ENI distribution across schools. All computed from the in-memory
    store; cached per-process (data is immutable per deploy).

    The proficiency series includes explicit null-pct rows for the
    COVID-cancelled test years (ay 2019 = spring 2020, ay 2020 = spring
    2021) so Observable Plot renders a gap instead of bridging it."""
    store = data.get_store()
    demo = store.demographics
    latest_ay = int(demo["ay"].max())
    latest = demo[demo["ay"] == latest_ay]

    enr = (
        demo[demo["ay"] >= latest_ay - 11]
        .groupby("ay")["total_enrollment"].sum().sort_index()
    )
    enrollment = [
        {"ay": int(ay), "year": _ay_label(int(ay)), "students": int(v)}
        for ay, v in enr.items()
    ]

    proficiency: list[dict] = []
    for subject, df in (("ELA", store.ela), ("Math", store.math)):
        df = df[(df["grade"] != "All Grades") & (df["number_tested"].fillna(0) > 0)].copy()
        df["_prof"] = df["level_3_4_pct"].fillna(0) * df["number_tested"]
        g = df.groupby("ay", as_index=False).agg(
            n=("number_tested", "sum"), p=("_prof", "sum"),
        )
        by_ay = {
            int(r.ay): {"pct": float(r.p / r.n), "n_tested": int(r.n)}
            for r in g.itertuples() if r.n > 0
        }
        for ay in range(min(by_ay), max(by_ay) + 1):
            row = by_ay.get(ay)
            proficiency.append({
                "subject": subject, "ay": ay, "year": _ay_label(ay),
                # era splits the trend line at the 2023 Common-Core →
                # Next-Generation standards divide so the client never draws a
                # single line segment across the incomparable boundary.
                "era": "old" if ay <= LAST_COMMON_CORE_AY else "new",
                "pct": row["pct"] if row else None,
                "n_tested": row["n_tested"] if row else None,
            })

    eni = latest["eni"].dropna()
    prior = enr.iloc[-2] if len(enr) >= 2 else None
    return {
        "stats": {
            "latest_ay": latest_ay,
            "latest_year": _ay_label(latest_ay),
            "prior_year": _ay_label(latest_ay - 1),
            "n_schools": int(latest["dbn"].nunique()),
            "total_enrollment": int(latest["total_enrollment"].sum()),
            "enrollment_delta": int(latest["total_enrollment"].sum() - prior) if prior is not None else None,
            "median_eni": float(eni.median()) if len(eni) else None,
        },
        "enrollment": enrollment,
        "proficiency": proficiency,
    }


# (metric key in feature properties, service metric name). All three run
# level=None: the per-school metric itself scopes the cohort — ENI exists
# for every school, ELA/Math only for schools with grades 3-8 test rows —
# and _aggregate_metric_by_group drops nulls before counting the cohort.
_NTA_MAP_METRICS = (
    ("eni", "eni"),
    ("ela", "ela_pct_proficient"),
    ("math", "math_pct_proficient"),
)
# ~50m tolerance: enough to keep NTA shapes recognizable at ~270px wide
# while holding the inlined FeatureCollection near 100KB.
_NTA_SIMPLIFY_TOLERANCE = 0.0005
_NTA_COORD_DECIMALS = 4


@lru_cache(maxsize=1)
def homepage_nta_map() -> dict:
    """GeoJSON FeatureCollection for the homepage NTA choropleth series:
    every 2010 NTA polygon (simplified + coordinate-rounded for inline
    payload weight) with per-NTA school-average ENI / ELA / Math values
    in properties. NTAs below the service's min-cohort rule (or with no
    schools at all) carry nulls — the map renders them as no-data."""
    store = data.get_store()
    vals: dict[str, dict] = {}
    for key, metric in _NTA_MAP_METRICS:
        for agg in aggregate_by_neighborhood(metric=metric, level=None, limit=500):
            d = vals.setdefault(agg.name, {})
            d[key] = agg.value
            d[f"{key}_n"] = agg.n_schools

    g = store.nta_polygons[["NTAName", "BoroName", "geometry"]].copy()
    g["geometry"] = g.geometry.simplify(_NTA_SIMPLIFY_TOLERANCE).apply(_orient_for_d3)
    features = []
    for r in g.itertuples():
        props = {"nta": r.NTAName, "boro": r.BoroName}
        for key, _ in _NTA_MAP_METRICS:
            props[key] = None
            props[f"{key}_n"] = None
        props.update(vals.get(r.NTAName, {}))
        gj = r.geometry.__geo_interface__
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": gj["type"], "coordinates": _round_coords(gj["coordinates"])},
        })
    return {"type": "FeatureCollection", "features": features}


def _orient_for_d3(geom):
    """d3-geo interprets polygons spherically: an exterior ring wound
    counterclockwise in lon-lat (the RFC 7946 convention this file uses)
    is read as enclosing the entire globe minus the shape, which renders
    as a full-frame fill. Rewind exteriors clockwise for the client."""
    if isinstance(geom, Polygon):
        return orient(geom, -1.0)
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([orient(p, -1.0) for p in geom.geoms])
    return geom


def _round_coords(obj):
    if isinstance(obj, (list, tuple)):
        return [_round_coords(x) for x in obj]
    if isinstance(obj, float):
        return round(obj, _NTA_COORD_DECIMALS)
    return obj


def _ay_label(ay: int) -> str:
    """2024 → '2024-25' (ay is the fall year, matching demographics.year)."""
    return f"{ay}-{(ay + 1) % 100:02d}"


def school_demographics_trend(rows: list[DemographicsYear]) -> list[dict]:
    """Per-year demographics series for the school-page small-multiples
    figure (enrollment, race/ethnicity shares, ELL/SWD shares, ENI).
    One output dict per academic year, values left as fractions/None so
    the client decides formatting. Requires ≥ 3 years to be worth a
    trend chart — returns [] below that and the template falls back to
    the table alone."""
    if not rows or len(rows) < 3:
        return []
    return [
        {
            "ay": r.ay,
            "year": _ay_label(r.ay),
            "enrollment": r.total_enrollment,
            "asian": r.asian_pct,
            "black": r.black_pct,
            "hispanic": r.hispanic_pct,
            "white": r.white_pct,
            "ell": r.ell_pct,
            "swd": r.swd_pct,
            "eni": r.eni,
        }
        for r in sorted(rows, key=lambda r: r.ay)
    ]


def chronic_subgroup_dots(rows: list[ChronicAbsenteeismRow]) -> dict:
    """Latest-year chronic-absenteeism-by-subgroup series for the
    school-page dot plot. NYSED only publishes two years, so the
    journalistic read here is within-school disparity, not trend: one
    dot per subgroup, with the All-Students rate carried separately as
    a reference line. Suppressed subgroups (rate None) are dropped —
    the demoted full table keeps them. Schools spanning EM + HS levels
    keep both, tagged for faceting. Returns {} when fewer than two
    non-suppressed subgroups exist (a plot would add nothing)."""
    if not rows:
        return {}
    latest = max(r.year for r in rows)
    dots: list[dict] = []
    all_students: dict[str, float] = {}
    for r in rows:
        if r.year != latest or r.absent_rate is None:
            continue
        level = r.level or ""
        if r.subgroup == "All Students":
            all_students[level] = r.absent_rate
        else:
            dots.append({
                "level": level,
                "subgroup": r.subgroup,
                "rate": r.absent_rate,
                "enrollment": r.enrollment,
            })
    if len(dots) < 2:
        return {}
    return {
        "year": latest,
        "dots": dots,
        "all_students": [
            {"level": lvl, "rate": rate} for lvl, rate in sorted(all_students.items())
        ],
    }


def grad_subgroup_dots(rows: list[GraduationRow]) -> dict:
    """Latest-year 4-Year-cohort graduation rate by subgroup for the
    school-page dot plot — same within-school-disparity grammar as
    chronic_subgroup_dots (NYSED publishes two years; a trend line
    would be two points). All Students rides separately as the
    reference rule and feeds the Quick Stats card. Returns {} when
    fewer than two non-suppressed subgroups exist."""
    if not rows:
        return {}
    four_year = [r for r in rows if r.cohort == "4-Year" and r.grad_rate is not None]
    if not four_year:
        return {}
    latest = max(r.year for r in four_year)
    dots: list[dict] = []
    all_students = None
    for r in four_year:
        if r.year != latest:
            continue
        if r.subgroup == "All Students":
            all_students = r.grad_rate
        else:
            dots.append({
                "subgroup": r.subgroup,
                "rate": r.grad_rate,
                "cohort_count": r.cohort_count,
                "grad_count": r.grad_count,
            })
    if len(dots) < 2:
        return {}
    return {
        "year": latest,
        "year_label": _ay_label(latest - 1),
        "all_students": all_students,
        "dots": dots,
    }


def regents_exam_dumbbells(rows: list[RegentsRow]) -> dict:
    """Latest-year per-exam Regents results for the school-page dumbbell
    plot: % scoring ≥65 (passing) and ≥80 (mastery) per exam, sorted by
    passing rate. Returns {} when the school has no scored exams in its
    latest year."""
    if not rows:
        return {}
    scored = [r for r in rows if r.pct_above_64 is not None]
    if not scored:
        return {}
    latest = max(r.ay for r in scored)
    exams = [
        {
            "exam": r.regents_exam,
            "pass65": r.pct_above_64,
            "mastery80": r.pct_above_79,
            "n_tested": r.number_tested,
        }
        for r in scored if r.ay == latest
    ]
    if not exams:
        return {}
    exams.sort(key=lambda e: e["pass65"], reverse=True)
    return {"ay": latest, "year_label": _ay_label(latest), "exams": exams}


# New York reset its grades-3-8 testing standards in spring 2023: from the
# Common Core Learning Standards to the Next Generation Learning Standards.
# Scale scores and proficiency percentages are NOT comparable across the divide,
# so the exam displays are physically split into an "old" block (ay <= this) and
# a "new" block (ay > this). The boundary is the fall-year `ay`: the last Common
# Core administration was spring 2022 = ay 2021, the first Next Gen was spring
# 2023 = ay 2022. Split by year boundary, never by hardcoded present-years.
LAST_COMMON_CORE_AY = 2021


def _partition_by_standard(items: list, ay_of) -> dict:
    """Split a list of exam-era items into old / new blocks at the 2023
    standards divide. `ay_of` extracts the academic year from an item."""
    old, new = [], []
    for it in items:
        (old if ay_of(it) <= LAST_COMMON_CORE_AY else new).append(it)
    return {"old": old, "new": new}


def partition_exam_rows(rows: list[ExamRow]) -> dict:
    """Split raw per-grade exam rows (for the full-history tables) into the
    old-standard and new-standard blocks. Returns {"old": [...], "new": [...]}
    of ExamRow models, each preserving the service-layer row order."""
    if not rows:
        return {"old": [], "new": []}
    return _partition_by_standard(rows, lambda r: r.ay)


def exam_grade_year_levels(rows: list[ExamRow]) -> dict:
    """Per-grade time series of proficiency-level breakdown — input for
    the grade-faceted stacked-area chart. Each output entry is one
    (grade × academic-year × level) with raw pct + n_tested. Excludes
    'All Grades' and any row with no students tested.

    Returns {"old": [...], "new": [...]}: the results are partitioned at the
    2023 Common-Core → Next-Generation standards divide (see
    LAST_COMMON_CORE_AY) so the template renders two separate charts — a
    reader is never invited to compare scores across the incomparable divide.

    COVID-cancelled years (AY 2019, AY 2020) are simply absent from the
    output — the chart consumer is expected to handle the gap visually
    (Plot's linear curve will draw a straight segment across it). Both
    cancelled years sit in the old block."""
    if not rows:
        return {"old": [], "new": []}
    out: list[dict] = []
    for r in rows:
        if r.grade == "All Grades" or not r.number_tested:
            continue
        levels = [
            (1, r.pct_level_1),
            (2, r.pct_level_2),
            (3, r.pct_level_3),
            (4, r.pct_level_4),
        ]
        for level, pct in levels:
            out.append({
                "grade": r.grade,
                "ay": r.ay,
                "level": level,
                "pct": pct or 0.0,
                "n_tested": r.number_tested,
            })
    return _partition_by_standard(out, lambda d: d["ay"])


