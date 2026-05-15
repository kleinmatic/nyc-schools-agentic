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


