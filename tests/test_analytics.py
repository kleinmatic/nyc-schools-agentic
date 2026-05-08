"""Service tests for cross-school analytics: top_schools, bulk_metrics,
list_high_schools. Hits real loaded data — same fixture pattern as
test_services.py / test_zoning.py."""
import pytest

from app.services.analytics import (
    METRIC_NAMES,
    bulk_metrics,
    list_high_schools,
    top_schools,
)


# ----- top_schools -----


def test_top_schools_regents_passing_returns_known_specialized_hs():
    """The five 'specialized' HS that take SHSAT (Stuy, Bronx Sci, etc.)
    consistently top the Regents passing-rate ranking. Sanity-check that
    the metric+sort produces a plausible result without locking down the
    exact ordering (which can drift year over year)."""
    top = top_schools("regents_pct_above_64", level="high", limit=10)
    assert len(top) == 10
    dbns = {s.dbn for s in top}
    # At least three specialized HS should be in the top 10.
    specialized = {"02M475", "10X445", "13K430", "14K449", "31R605", "05M692"}
    assert len(dbns & specialized) >= 3, f"got {dbns}"
    # Values are 0..1 fractions, descending.
    values = [s.value for s in top]
    assert values == sorted(values, reverse=True)
    assert all(0 <= v <= 1 for v in values)


def test_top_schools_chronic_absent_ascending_returns_lowest_rates():
    """ascending=True flips the order; useful for 'lowest chronic absence'."""
    top = top_schools("chronic_absent_rate", level="high", limit=5, ascending=True)
    values = [s.value for s in top]
    assert values == sorted(values), f"expected ascending, got {values}"
    assert all(0 <= v <= 1 for v in values)


def test_top_schools_borough_filter_constrains_results():
    bx = top_schools("eni", level="high", limit=10, borough="X")
    assert all(s.dbn[2] == "X" for s in bx), [s.dbn for s in bx]
    # Same query with full borough name should match.
    bx_full = top_schools("eni", level="high", limit=10, borough="Bronx")
    assert {s.dbn for s in bx} == {s.dbn for s in bx_full}


def test_top_schools_unknown_metric_raises():
    with pytest.raises(ValueError, match="unknown metric"):
        top_schools("not_a_real_metric", level="high")


def test_top_schools_unknown_level_raises():
    with pytest.raises(ValueError, match="unknown level"):
        top_schools("eni", level="kindergarten")


def test_top_schools_each_metric_returns_data():
    """Smoke: every metric in the vocabulary should produce at least
    *some* ranked results when paired with a level it applies to."""
    by_metric = {
        "eni": "high",
        "poverty_pct": "high",
        "attendance_rate": "high",
        "chronic_absent_rate": "high",
        "ela_pct_proficient": "elementary",
        "math_pct_proficient": "elementary",
        "regents_pct_above_64": "high",
        "regents_pct_above_79": "high",
        "graduation_rate_4yr": "high",
        "pupil_teacher_ratio": "high",
        "pct_inexperienced_teachers": "high",
        "pct_out_of_cert_teachers": "high",
        "per_pupil_expenditure": "high",
    }
    assert set(by_metric) == set(METRIC_NAMES)
    for metric, level in by_metric.items():
        result = top_schools(metric, level=level, limit=3)
        assert result, f"{metric} ({level}) returned no rows"


# ----- bulk_metrics -----


def test_bulk_metrics_returns_one_row_per_active_high_school():
    rows = bulk_metrics(level="high")
    # There are ~440 HS in the directory; bulk uses demographics so the
    # count includes all with recent data. Just confirm a plausible range.
    assert 200 < len(rows) < 700, f"got {len(rows)} HS rows"
    # Default is all metrics.
    assert set(rows[0].metrics.keys()) == set(METRIC_NAMES)


def test_bulk_metrics_subset_returns_only_requested():
    rows = bulk_metrics(level="high", metrics=["eni", "regents_pct_above_64"])
    assert rows
    for r in rows:
        assert set(r.metrics.keys()) == {"eni", "regents_pct_above_64"}


def test_bulk_metrics_missing_data_is_none_not_zero():
    """A metric that doesn't apply to a school must come back as None,
    not 0. Coercing to 0 would silently bias downstream correlations.
    Use 4-year graduation rate at elementary level — graduation is by
    construction a HS-cohort metric, so almost every row should be None."""
    rows = bulk_metrics(level="elementary", metrics=["graduation_rate_4yr"])
    assert rows
    none_count = sum(1 for r in rows if r.metrics["graduation_rate_4yr"] is None)
    # The vast majority should be None.
    assert none_count > 0.95 * len(rows), (
        f"only {none_count}/{len(rows)} elementary schools had None graduation_rate"
    )


def test_bulk_metrics_unknown_metric_raises():
    with pytest.raises(ValueError, match="unknown metric"):
        bulk_metrics(level="high", metrics=["bogus_metric"])


# ----- list_high_schools -----


def test_list_high_schools_unfiltered_returns_listings():
    schools = list_high_schools(limit=10)
    assert len(schools) == 10
    for s in schools:
        assert s.dbn
        assert s.school_name
        assert s.boro in {"Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island", None}


def test_list_high_schools_borough_filter():
    bk = list_high_schools(borough="Brooklyn", limit=5)
    assert bk
    assert all(s.boro == "Brooklyn" for s in bk)


def test_list_high_schools_accessibility_filter():
    fa = list_high_schools(accessibility="Fully Accessible", limit=10)
    assert fa
    assert all(s.accessibility == "Fully Accessible" for s in fa)


def test_list_high_schools_program_keyword_match():
    """A keyword that maps to a real program (performing arts) should
    surface schools where the term appears in the directory text."""
    hits = list_high_schools(program_keyword="performing arts", limit=20)
    assert hits, "expected at least one performing-arts HS"
    # The legacy Frank Sinatra School of the Arts (30Q501) or Professional
    # Performing Arts (02M408) are reliable hits.
    dbns = {s.dbn for s in hits}
    assert dbns & {"30Q501", "02M408"}, f"expected canonical PA schools, got {dbns}"


def test_list_high_schools_invalid_borough_raises():
    with pytest.raises(ValueError, match="unknown borough"):
        list_high_schools(borough="Jersey")


def test_list_high_schools_invalid_accessibility_raises():
    with pytest.raises(ValueError, match="unknown accessibility"):
        list_high_schools(accessibility="Wheelchair OK")
