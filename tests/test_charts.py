"""Tests for the view-layer chart shaping in app/web/charts.py."""
import json

from app.web.charts import homepage_citywide, homepage_nta_map


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


def test_homepage_nta_map_covers_every_nta():
    fc = homepage_nta_map()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 195  # every 2010 NTA renders, data or not
    for f in fc["features"]:
        p = f["properties"]
        assert p["nta"] and p["boro"]
        for key in ("eni", "ela", "math"):
            assert key in p and f"{key}_n" in p


def test_homepage_nta_map_values_respect_min_cohort():
    fc = homepage_nta_map()
    props = [f["properties"] for f in fc["features"]]
    with_eni = [p for p in props if p["eni"] is not None]
    no_data = [p for p in props if p["eni"] is None]
    # Most NTAs carry values; park/cemetery/small-cohort NTAs don't.
    assert len(with_eni) > 100
    assert len(no_data) > 0
    for p in with_eni:
        assert 0 < p["eni"] <= 1
        assert p["eni_n"] >= 5  # the service's min-cohort rule
    for p in props:
        if p["ela"] is not None:
            assert 0 < p["ela"] < 1 and p["ela_n"] >= 5


def test_homepage_nta_map_exterior_rings_wind_clockwise():
    """d3-geo reads a counterclockwise exterior ring (in lon-lat) as
    enclosing the whole globe — the map renders as a solid block. Pin
    the clockwise rewind. Shoelace sum > 0 = clockwise with y up."""
    def shoelace(ring):
        return sum(
            (x2 - x1) * (y2 + y1)
            for (x1, y1), (x2, y2) in zip(ring, ring[1:])
        )

    for f in homepage_nta_map()["features"]:
        geom = f["geometry"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        for poly in polys:
            assert shoelace(poly[0]) > 0, f"CCW exterior ring in {f['properties']['nta']}"


def test_homepage_nta_map_payload_is_inline_weight():
    """Geometry is simplified + coordinate-rounded so the FeatureCollection
    can ride inline in the homepage HTML without blowing up page weight."""
    s = json.dumps(homepage_nta_map(), separators=(",", ":"))
    assert len(s) < 250_000
