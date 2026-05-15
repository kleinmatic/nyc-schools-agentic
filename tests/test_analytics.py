"""Service tests for cross-school analytics: top_schools, bulk_metrics,
list_high_schools. Hits real loaded data — same fixture pattern as
test_services.py / test_zoning.py."""
import pytest

from app.services.analytics import (
    METRIC_NAMES,
    aggregate_by_neighborhood,
    borough_summary,
    bulk_metrics,
    get_neighborhood,
    homepage_borough_grid,
    homepage_leaderboards,
    homepage_neighborhood_leaderboards,
    list_high_schools,
    school_peers,
    schools_in_neighborhood,
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


# ----- homepage_leaderboards -----


# ----- Geographic aggregations: NTAs and boroughs -----


def test_aggregate_by_neighborhood_excludes_small_cohorts():
    """Single-school NTAs produce noisy averages; min_schools enforces a
    floor. Default is 5."""
    rows = aggregate_by_neighborhood("regents_pct_above_64", level="high", limit=20)
    assert rows
    assert all(r.n_schools >= 5 for r in rows), [r.n_schools for r in rows]
    # Sorted descending.
    values = [r.value for r in rows]
    assert values == sorted(values, reverse=True)
    # Each NTA gets a borough.
    assert all(r.boro in {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"} for r in rows)


def test_aggregate_by_neighborhood_park_slope_appears_in_es_ela_top():
    """Park Slope is reliably high-performing on elementary ELA — useful
    sanity check that NTA names match between locations and demographics
    join."""
    rows = aggregate_by_neighborhood("ela_pct_proficient", level="elementary", limit=20)
    names = [r.name for r in rows]
    assert any("Park Slope" in n for n in names), f"Park Slope not in top 20: {names}"


def test_borough_summary_returns_all_five_boroughs_in_canonical_order():
    g = borough_summary(metrics=["eni", "regents_pct_above_64"], level="high")
    assert [r.name for r in g.rows] == ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
    # Bronx has highest ENI of the five — common sanity check.
    bronx = next(r for r in g.rows if r.name == "Bronx")
    si = next(r for r in g.rows if r.name == "Staten Island")
    assert bronx.metrics["eni"] > si.metrics["eni"], (
        f"expected Bronx ENI > Staten Island ENI, got {bronx.metrics['eni']} vs {si.metrics['eni']}"
    )


def test_homepage_borough_grid_includes_4_metrics():
    g = homepage_borough_grid()
    assert g.metric_names == ["eni", "attendance_rate", "regents_pct_above_64", "graduation_rate_4yr"]
    assert len(g.rows) == 5


def test_homepage_neighborhood_leaderboards_returns_two_tables():
    tables = homepage_neighborhood_leaderboards(per_table=5)
    assert len(tables) == 2
    for t in tables:
        assert len(t.rows) == 5
        # Each row is a real NTA (string), not None.
        assert all(isinstance(r.name, str) and r.name for r in t.rows)


# ----- school_peers -----


def test_school_peers_neighborhood_includes_focal_school():
    """The focal school must be in its own peer cohort with is_self=True;
    otherwise the template can't highlight it."""
    cohort = school_peers("15K321", "neighborhood")
    assert cohort is not None
    assert cohort.scope == "neighborhood"
    assert cohort.label  # NTA name string
    selves = [r for r in cohort.rows if r.is_self]
    assert len(selves) == 1
    assert selves[0].dbn == "15K321"
    # Peers are same-level (elementary in this case).
    assert len(cohort.rows) >= 2  # at least focal + one peer


def test_school_peers_district_returns_district_label():
    cohort = school_peers("15K321", "district")
    assert cohort is not None
    assert cohort.scope == "district"
    assert cohort.label.startswith("District ")


def test_school_peers_unknown_dbn_returns_none():
    assert school_peers("99Z999", "neighborhood") is None


def test_school_peers_invalid_scope_raises():
    with pytest.raises(ValueError, match="scope must be"):
        school_peers("15K321", "borough")


def test_schools_in_neighborhood_park_slope_returns_canonical_nta():
    """Common case: a colloquial query resolves to the canonical NTA name."""
    r = schools_in_neighborhood("park slope", limit=10)
    assert r is not None
    assert r.nta_name == "Park Slope-Gowanus"
    assert r.boro == "Brooklyn"
    assert r.schools  # at least some Park Slope schools
    # Single-match query — no other strong candidates.
    assert r.other_candidates == []


def test_schools_in_neighborhood_harlem_surfaces_alternatives():
    """A query like 'harlem' matches multiple NTAs; the runner-up names
    must come back in other_candidates so the caller can disambiguate."""
    r = schools_in_neighborhood("harlem", limit=3)
    assert r is not None
    assert "Harlem" in r.nta_name
    assert len(r.other_candidates) >= 2
    # All candidates should reference Harlem.
    assert all("Harlem" in n for n in r.other_candidates)


def test_schools_in_neighborhood_unknown_returns_none():
    assert schools_in_neighborhood("xyzzy fake neighborhood") is None
    assert schools_in_neighborhood("") is None
    assert schools_in_neighborhood("   ") is None


def test_get_neighborhood_park_slope_full_report():
    """The full neighborhood report: peer ranks vs other NTAs, denormalized
    school list with lat/lon + per-school metrics, and a GeoJSON boundary."""
    r = get_neighborhood("park slope")
    assert r is not None
    assert r.nta_name == "Park Slope-Gowanus"
    assert r.n_schools == len(r.schools) > 0

    # Each school carries lat/lon (for the map) and a value for every
    # advertised metric (table contract — keys must match metric_names).
    sample = r.schools[0]
    assert sample.latitude is not None and sample.longitude is not None
    assert set(sample.metrics) == set(r.metric_names)

    # Peer-rank cards are sane: rank in [1, total], cohort extremes present.
    assert r.peer_ranks
    for rank in r.peer_ranks:
        assert 1 <= rank.rank <= rank.total
        assert rank.extreme_high is not None and rank.extreme_low is not None

    # Boundary is GeoJSON-shaped — the /neighborhood/{nta} map consumes it.
    assert r.boundary is not None
    assert r.boundary["type"] in ("Polygon", "MultiPolygon")


def test_get_neighborhood_unknown_query_returns_none():
    assert get_neighborhood("xyzzy fake neighborhood") is None
    assert get_neighborhood("") is None


def test_get_neighborhood_metric_set_matches_dominant_level():
    """Park Slope-Gowanus is dominated by elementary schools — the page
    should default to the ES peer metric set (ELA + math, no Regents)."""
    r = get_neighborhood("park slope")
    assert r is not None
    assert "ela_pct_proficient" in r.metric_names
    assert "math_pct_proficient" in r.metric_names
    assert "regents_pct_above_64" not in r.metric_names


def test_get_neighborhood_with_explicit_high_level_switches_metric_set():
    """Forcing level='high' selects the HS peer metric set even in an
    NTA where another level dominates."""
    r = get_neighborhood("park slope", level="high")
    assert r is not None
    assert "regents_pct_above_64" in r.metric_names
    assert "graduation_rate_4yr" in r.metric_names
    assert "ela_pct_proficient" not in r.metric_names


def test_get_neighborhood_harlem_surfaces_alternatives():
    """'harlem' fuzzy-matches multiple NTAs — runners-up surface so the
    caller can disambiguate."""
    r = get_neighborhood("harlem")
    assert r is not None
    assert "Harlem" in r.nta_name
    assert len(r.other_candidates) >= 2


def test_schools_in_neighborhood_level_filter():
    """level filter should narrow to one school type."""
    r = schools_in_neighborhood("park slope", level="elementary", limit=20)
    assert r is not None
    assert all(s.school_level == "elementary" for s in r.schools), (
        [s.school_level for s in r.schools]
    )


def test_school_peers_metric_set_matches_level():
    """ES peer cohort should include ela/math metrics; HS cohort should
    include Regents/grad. Confirms _PEER_METRICS_BY_LEVEL is wired right."""
    es = school_peers("15K321", "neighborhood")
    assert "ela_pct_proficient" in es.metric_names
    assert "math_pct_proficient" in es.metric_names
    hs = school_peers("02M475", "neighborhood")  # Stuyvesant
    if hs is not None:  # Stuy might be the only HS in its NTA — guard.
        assert "regents_pct_above_64" in hs.metric_names
        assert "graduation_rate_4yr" in hs.metric_names


def test_homepage_leaderboards_returns_curated_set():
    """Homepage should render the same fixed set of tables every time;
    never produce an empty dashboard."""
    lb = homepage_leaderboards(per_table=5)
    assert lb.tables, "expected at least one leaderboard table"
    assert len(lb.tables) == 4, f"expected 4 curated tables, got {len(lb.tables)}"
    for t in lb.tables:
        assert t.title and t.description and t.year_label
        assert t.metric in METRIC_NAMES
        assert t.metric_format in {"pct", "currency", "ratio"}
        assert len(t.rows) == 5, f"{t.title!r} has {len(t.rows)} rows, expected 5"
        # Rows must be in the configured sort direction.
        values = [r.value for r in t.rows]
        assert values == sorted(values, reverse=True), (
            f"{t.title!r} not in descending order: {values}"
        )
