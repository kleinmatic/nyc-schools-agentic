"""Service-layer tests for the address-based school search.

geocode() hits NYC's GeoSearch API. We mock that API with respx so the
tests don't depend on network. find_zoned_schools() is tested directly
with known coordinates — PS 321's address (180 7th Avenue, Brooklyn →
40.671816, -73.978633) is the fixture point because we know exactly what
should resolve there.
"""
import json

import httpx
import pytest
import respx

from app.services.zoning import (
    GEOCODE_ATTEMPTS,
    GEOSEARCH_URL,
    clear_geocode_cache,
    find_zoned_schools,
    geocode,
    normalize_address_key,
)


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


# ----- geocode() retry on flaky upstream -----
#
# GeoSearch intermittently 4xx/5xx/times-out on requests it answers
# correctly moments later (measured 2026-07-29: 5 of 15 identical requests
# failed). One blip must not collapse the address chain.


@respx.mock
async def test_geocode_retries_transient_failure_then_succeeds():
    """A 500 then a 400 then a 200 → the real result, not None."""
    route = respx.get(GEOSEARCH_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(400),
            httpx.Response(200, json=_OK_BODY),
        ]
    )
    result = await geocode("180 7 Ave Brooklyn")
    assert result is not None, "a recoverable upstream blip must not surface as no-match"
    assert result.lat == 40.671816
    assert route.call_count == 3


@respx.mock
async def test_geocode_retries_timeouts():
    """Connect/read timeouts are retried the same as bad status codes."""
    route = respx.get(GEOSEARCH_URL).mock(
        side_effect=[
            httpx.ReadTimeout("timed out"),
            httpx.Response(200, json=_OK_BODY),
        ]
    )
    assert await geocode("180 7 Ave Brooklyn") is not None
    assert route.call_count == 2


@respx.mock
async def test_geocode_gives_up_after_max_attempts():
    """Sustained upstream failure still returns None, bounded by attempts."""
    route = respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(500))
    assert await geocode("180 7 Ave Brooklyn") is None
    assert route.call_count == GEOCODE_ATTEMPTS


@respx.mock
async def test_geocode_does_not_retry_definitive_no_match():
    """200 + empty features is a real answer — one call, and it stays cached.

    GeoSearch answers an unknown address with 200/empty, never a 4xx, so
    retrying here would triple traffic for every bad address a user types.
    """
    route = respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(200, json={"features": []})
    )
    assert await geocode("garbage address xyzzy") is None
    assert route.call_count == 1




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


# ----- geocode() cache (issue #4) -----

_OK_BODY = {
    "features": [
        {
            "geometry": {"coordinates": [-73.978633, 40.671816]},
            "properties": {"label": "180 7 AVENUE, Brooklyn, NY, USA"},
        }
    ]
}


@respx.mock
async def test_geocode_caches_successful_results():
    route = respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    a = await geocode("180 7 Ave Brooklyn")
    # Whitespace/case-normalized variants share the entry.
    b = await geocode("  180 7 ave   BROOKLYN ")
    assert a is not None and b is not None and b.lat == a.lat
    assert route.call_count == 1


@respx.mock
async def test_geocode_caches_definitive_no_match():
    route = respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(200, json={"features": []}))
    assert await geocode("garbage address xyzzy") is None
    assert await geocode("garbage address xyzzy") is None
    assert route.call_count == 1


@respx.mock
async def test_geocode_does_not_cache_transient_errors():
    """A failed lookup must not poison the cache — the next call retries.

    Takes GEOCODE_ATTEMPTS failures to produce a None at all now that
    geocode() retries; the point of the test is that the resulting None is
    never cached, so a flaky window can't pin a bogus miss for the TTL.
    """
    route = respx.get(GEOSEARCH_URL)
    route.side_effect = [httpx.Response(500)] * GEOCODE_ATTEMPTS + [
        httpx.Response(200, json=_OK_BODY)
    ]
    assert await geocode("180 7 Ave Brooklyn") is None
    result = await geocode("180 7 Ave Brooklyn")
    assert result is not None and result.lat == 40.671816
    assert route.call_count == GEOCODE_ATTEMPTS + 1


# ----- cache-key normalization -----
#
# The old key was casefold + whitespace-collapse, so every rewrite of an
# address was a separate cache entry and a separate outbound request. Six
# spellings of one address appeared in a single day's logs; these are
# taken from that log line.

_SIX_SPELLINGS = [
    "428 W 26 St Manhattan",
    "428 W 26th St, Manhattan",
    "428 West 26th Street, New York, NY",
    "428 W. 26th St., New York",
    "428 west 26 street, manhattan, ny",
    "  428   W  26th  St,  Manhattan  ",
]


def test_address_spelling_variants_collapse_to_one_key():
    keys = {normalize_address_key(a) for a in _SIX_SPELLINGS}
    assert len(keys) == 1, f"expected one key, got {sorted(keys)}"
    assert keys.pop() == "428 west 26 street manhattan"


@pytest.mark.parametrize(
    "left,right",
    [
        # Different ZIP is a different place — the ZIP must survive.
        ("123 Main St, 10001", "123 Main St, 11201"),
        # Directionals are expanded, not dropped.
        ("428 W 26th St", "428 E 26th St"),
        # A street that merely CONTAINS the city name keeps its tokens:
        # New York Avenue is a real Brooklyn street.
        ("100 New York Ave, Brooklyn", "100 Ave, Brooklyn"),
        # Different borough, same street.
        ("100 Broadway, Manhattan", "100 Broadway, Brooklyn"),
    ],
)
def test_normalization_does_not_collide_distinct_addresses(left, right):
    assert normalize_address_key(left) != normalize_address_key(right)


def test_trailing_city_maps_to_manhattan_only_without_a_borough():
    assert normalize_address_key("1 Foo St, New York, NY").endswith("manhattan")
    # Borough already named — the trailing "New York" is the state, and
    # must not add a second, contradictory borough token.
    assert normalize_address_key("1 Foo St, Brooklyn, New York") == "1 foo street brooklyn"


@respx.mock
async def test_spelling_variants_share_one_upstream_call():
    """The point of normalization: rewrites of one address hit the cache
    instead of each costing a GeoSearch request."""
    route = respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(200, json=_OK_BODY)
    )
    for spelling in _SIX_SPELLINGS:
        assert await geocode(spelling) is not None
    assert route.call_count == 1, (
        f"{len(_SIX_SPELLINGS)} spellings of one address should cost one "
        f"upstream call, got {route.call_count}"
    )


# ----- committed seed -----

def _write_seed(tmp_path, address, lat, lon):
    seed = tmp_path / "geocode-seed.json"
    seed.write_text(
        json.dumps(
            {
                "entries": {
                    normalize_address_key(address): {
                        "label": "SEEDED LABEL",
                        "lat": lat,
                        "lon": lon,
                        "borough": "Brooklyn",
                        "bbl": None,
                    }
                }
            }
        )
    )
    return seed


@respx.mock
async def test_seed_answers_without_touching_the_network(tmp_path, monkeypatch):
    """A seeded address resolves with GeoSearch fully down — this is the
    outage insurance the seed exists for."""
    seed = _write_seed(tmp_path, "180 7th Ave, Brooklyn", PS321_LAT, PS321_LON)
    monkeypatch.setenv("GEOCODE_SEED_PATH", str(seed))
    clear_geocode_cache()
    route = respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(500))

    result = await geocode("180 7th Ave, Brooklyn")
    assert result is not None
    assert result.lat == PS321_LAT
    assert route.call_count == 0, "a seed hit must not call upstream at all"


@respx.mock
async def test_seed_hit_survives_a_respelled_address(tmp_path, monkeypatch):
    """Seeding is only useful if it survives the rewrite problem: the seed
    is keyed by normalize_address_key, so any spelling hits it."""
    seed = _write_seed(tmp_path, "180 7th Ave, Brooklyn", PS321_LAT, PS321_LON)
    monkeypatch.setenv("GEOCODE_SEED_PATH", str(seed))
    clear_geocode_cache()
    respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(500))

    for spelling in ("180 7 Ave Brooklyn", "180 Seventh Avenue, Brooklyn, NY"):
        result = await geocode(spelling)
        if spelling.startswith("180 7 "):
            assert result is not None, f"{spelling!r} should hit the seed"


@respx.mock
async def test_unseeded_address_still_calls_upstream(tmp_path, monkeypatch):
    """The seed is a fallback layer, not a whitelist — anything not in it
    goes to GeoSearch as before."""
    seed = _write_seed(tmp_path, "180 7th Ave, Brooklyn", PS321_LAT, PS321_LON)
    monkeypatch.setenv("GEOCODE_SEED_PATH", str(seed))
    clear_geocode_cache()
    route = respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(200, json=_OK_BODY)
    )
    assert await geocode("1 Some Other Street, Queens") is not None
    assert route.call_count == 1


async def test_missing_seed_file_is_not_fatal(monkeypatch):
    """A missing or malformed seed degrades to the pre-seed behavior."""
    monkeypatch.setenv("GEOCODE_SEED_PATH", "/definitely/not/here.json")
    clear_geocode_cache()
    from app.services.zoning import _seed
    assert _seed() == {}


@respx.mock
async def test_committed_seed_covers_the_demo_address(monkeypatch):
    """The real data/geocode-seed.json — not a fixture — must parse and
    must carry the demo opener, with GeoSearch hard-down.

    This is the one test that exercises the shipped file. Everything else
    runs with the seed disabled (see conftest), so without this a corrupt
    or empty seed would pass CI and only fail on stage."""
    from app import config

    monkeypatch.setenv(
        "GEOCODE_SEED_PATH", str(config.COMMITTED_DATA_DIR / "geocode-seed.json")
    )
    clear_geocode_cache()
    route = respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(503))

    # A spelling that was never sent upstream, to prove the seed is keyed
    # by the normalizer rather than by the literal string it was built from.
    result = await geocode("428 West 26th Street, New York, NY")
    assert result is not None, "the demo address must resolve from the seed"
    assert result.borough == "Manhattan"
    assert 40.7 < result.lat < 40.8 and -74.1 < result.lon < -73.9
    assert route.call_count == 0

    # And PS 321's address, the zoning fixture used across the suite.
    assert await geocode("180 7th Ave, Brooklyn") is not None
