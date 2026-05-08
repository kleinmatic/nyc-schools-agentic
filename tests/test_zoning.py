"""Service-layer tests for the address-based school search.

geocode() hits NYC's GeoSearch API. We mock that API with respx so the
tests don't depend on network. find_zoned_schools() is tested directly
with known coordinates — PS 321's address (180 7th Avenue, Brooklyn →
40.671816, -73.978633) is the fixture point because we know exactly what
should resolve there.
"""
import httpx
import pytest
import respx

from app.services.zoning import GEOSEARCH_URL, find_zoned_schools, geocode


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


# ----- geocode() — mocked NYC GeoSearch API -----

@respx.mock
async def test_geocode_parses_a_successful_response():
    respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"coordinates": [-73.978633, 40.671816]},
                        "properties": {
                            "label": "180 7 AVENUE, Brooklyn, NY, USA",
                            "borough": "Brooklyn",
                            "addendum": {"pad": {"bbl": "3009710028"}},
                        },
                    }
                ]
            },
        )
    )
    result = await geocode("180 7 Ave Brooklyn")
    assert result is not None
    assert result.lat == 40.671816
    assert result.lon == -73.978633
    assert result.borough == "Brooklyn"
    assert result.bbl == "3009710028"
    assert "180" in result.label


@respx.mock
async def test_geocode_no_match_returns_none():
    """An empty features array → None, not a default-coords match."""
    respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(200, json={"features": []}))
    assert await geocode("garbage address xyzzy") is None


@respx.mock
async def test_geocode_http_500_returns_none():
    """A 5xx → None, no exception leaks to the caller."""
    respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(500))
    assert await geocode("180 7 Ave") is None


@respx.mock
async def test_geocode_malformed_geometry_returns_none():
    """A feature with missing coordinates is treated as no match."""
    respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"features": [{"geometry": {}, "properties": {"label": "x"}}]},
        )
    )
    assert await geocode("addr") is None


async def test_geocode_empty_input_short_circuits():
    """Empty/whitespace input returns None without making any HTTP call."""
    # No respx.mock here — if geocode tried to hit the network we'd notice.
    assert await geocode("") is None
    assert await geocode("   ") is None
    assert await geocode(None) is None  # type: ignore[arg-type]
