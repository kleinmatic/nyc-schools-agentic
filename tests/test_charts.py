"""Tests for the view-layer chart shaping in app/web/charts.py."""
from app.web.charts import homepage_citywide


def test_homepage_citywide_stats_sanity():
    cw = homepage_citywide()
    stats = cw["stats"]
    assert stats["n_schools"] > 1500
    assert stats["total_enrollment"] > 900_000
    assert 0.5 < stats["median_eni"] < 1.0
    # ay is the fall year; label mirrors demographics.year.
    assert stats["latest_year"] == f"{stats['latest_ay']}-{(stats['latest_ay'] + 1) % 100:02d}"


def test_homepage_citywide_enrollment_series():
    cw = homepage_citywide()
    enr = cw["enrollment"]
    assert len(enr) == 12
    ays = [r["ay"] for r in enr]
    assert ays == sorted(ays)
    assert all(r["students"] > 0 for r in enr)
    assert enr[-1]["students"] == cw["stats"]["total_enrollment"]


def test_homepage_citywide_proficiency_has_covid_gap():
    """AY 2019 (spring 2020) and AY 2020 (spring 2021) tests were
    cancelled — the series must carry explicit null rows so the chart
    renders a gap instead of bridging it."""
    cw = homepage_citywide()
    for subject in ("ELA", "Math"):
        rows = {r["ay"]: r for r in cw["proficiency"] if r["subject"] == subject}
        # Contiguous year coverage from first to last test year.
        assert set(rows) == set(range(min(rows), max(rows) + 1))
        for covid_ay in (2019, 2020):
            assert rows[covid_ay]["pct"] is None
            assert rows[covid_ay]["n_tested"] is None
        # Real years carry weighted 0..1 fractions and test counts.
        assert 0 < rows[2022]["pct"] < 1
        assert rows[2022]["n_tested"] > 100_000


def test_homepage_citywide_eni_bins_cover_all_schools():
    cw = homepage_citywide()
    bins = cw["eni_bins"]
    assert len(bins) == 20
    assert bins[0]["x0"] == 0.0
    assert bins[-1]["x1"] == 1.0
    # Every school with a reported ENI lands in exactly one bin.
    assert sum(b["count"] for b in bins) <= cw["stats"]["n_schools"]
    assert sum(b["count"] for b in bins) > 1500
