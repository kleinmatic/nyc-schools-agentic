"""Service-layer tests for the address-based school search.

geocode() is async and hits a live API, so we don't test it here — instead
the tests exercise find_zoned_schools() directly with known coordinates.
PS 321's address (180 7th Avenue, Brooklyn → 40.671816, -73.978633) is the
fixture point we use because we know exactly what should resolve there.
"""
import pytest

from app.services.zoning import find_zoned_schools


PS321_LAT = 40.671816
PS321_LON = -73.978633


def test_ps321_address_resolves_to_15K321():
    """180 7th Ave, Brooklyn → PS 321's own zone."""
    result = find_zoned_schools(PS321_LAT, PS321_LON)
    es_dbns = [s.dbn for s in result.elementary]
    assert "15K321" in es_dbns, f"expected 15K321 in {es_dbns}"
    assert result.es_district == 15


def test_district_15_has_no_zoned_middle_school():
    """D15 went choice-based for middle school in 2018 — no MS zone polygons exist."""
    result = find_zoned_schools(PS321_LAT, PS321_LON)
    assert result.middle == [], (
        f"D15 should have no zoned MS, got {[s.dbn for s in result.middle]}"
    )


def test_zoned_match_includes_school_name_and_metadata():
    result = find_zoned_schools(PS321_LAT, PS321_LON)
    assert result.elementary, "expected at least one elementary match"
    match = next(s for s in result.elementary if s.dbn == "15K321")
    assert "321" in match.school_name
    assert match.boro == "Brooklyn"
    assert match.district == 15
    assert match.school_level  # populated from demographics


def test_offshore_point_returns_empty():
    """Coordinates well outside NYC should return no zoned schools."""
    result = find_zoned_schools(0.0, 0.0)
    assert result.elementary == []
    assert result.middle == []
