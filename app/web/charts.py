"""View-layer chart data shaping. Pure presentation — converts service-layer
Pydantic models into chart-ready dicts. Lives here (not in services/) because
the shape is specific to a visual encoding, not to the data contract."""
from functools import lru_cache

from .. import data
from ..services.models import ExamRow


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
                "pct": row["pct"] if row else None,
                "n_tested": row["n_tested"] if row else None,
            })

    eni = latest["eni"].dropna()
    n_bins = 20
    eni_bins = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        # Last bin closed on the right so eni == 1.0 isn't dropped.
        mask = (eni >= lo) & ((eni < hi) if i < n_bins - 1 else (eni <= hi))
        eni_bins.append({"x0": lo, "x1": hi, "count": int(mask.sum())})

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
        "eni_bins": eni_bins,
    }


def _ay_label(ay: int) -> str:
    """2024 → '2024-25' (ay is the fall year, matching demographics.year)."""
    return f"{ay}-{(ay + 1) % 100:02d}"


def exam_grade_year_levels(rows: list[ExamRow]) -> list[dict]:
    """Per-grade time series of proficiency-level breakdown — input for
    the grade-faceted stacked-area chart. Each output entry is one
    (grade × academic-year × level) with raw pct + n_tested. Excludes
    'All Grades' and any row with no students tested.

    COVID-cancelled years (AY 2019, AY 2020) are simply absent from the
    output — the chart consumer is expected to handle the gap visually
    (Plot's linear curve will draw a straight segment across it)."""
    if not rows:
        return []
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
    return out


