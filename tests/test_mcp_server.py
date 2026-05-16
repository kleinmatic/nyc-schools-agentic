"""MCP adapter tests. Use FastMCP's in-memory transport — Client(mcp) skips
the network entirely and exercises the same tool dispatch path the
Streamable HTTP server uses, with no session-handshake plumbing."""
import httpx
import pytest
import respx
from fastmcp import Client

from app.mcp_server import mcp
from app.services.zoning import GEOSEARCH_URL


@pytest.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


async def test_list_tools_returns_all_registered_tools(mcp_client):
    tools = await mcp_client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "search_schools",
        "get_school",
        "find_schools_for_address",
        "geocode_address",
        "list_high_schools",
        "top_schools",
        "bulk_metrics",
        "top_neighborhoods",
        "borough_summary",
        "school_peers",
        "schools_in_neighborhood",
        "get_neighborhood",
        "school_staffing",
        "co_located_schools",
    }


async def test_top_schools_and_bulk_metrics_descriptions_advertise_metric_vocabulary(mcp_client):
    """Critical for LLM discovery: the agent only knows which strings to
    pass for the `metric` arg if the description names them. A regression
    here (e.g. broken docstring concatenation) silently degrades agents."""
    tools = {t.name: t for t in await mcp_client.list_tools()}
    for name in ("top_schools", "bulk_metrics"):
        d = tools[name].description or ""
        # Spot-check a metric from each major data source.
        for metric in ("eni", "regents_pct_above_64", "graduation_rate_4yr",
                       "chronic_absent_rate", "per_pupil_expenditure"):
            assert metric in d, f"{name} description missing {metric!r}"


async def test_each_tool_advertises_an_input_schema(mcp_client):
    """Sanity: a missing/empty inputSchema would mean callers can't tell
    what args to pass — caught here, not in production."""
    tools = await mcp_client.list_tools()
    for t in tools:
        assert t.inputSchema, f"{t.name} has no inputSchema"
        assert t.inputSchema.get("properties"), f"{t.name} schema has no properties"


async def test_search_schools_returns_summaries(mcp_client):
    r = await mcp_client.call_tool("search_schools", {"query": "PS 321", "limit": 3})
    assert r.data, "expected results for 'PS 321'"
    dbns = [s.dbn for s in r.data]
    assert "15K321" in dbns


async def test_search_schools_respects_limit(mcp_client):
    r = await mcp_client.call_tool("search_schools", {"query": "PS", "limit": 2})
    assert len(r.data) <= 2


async def test_get_school_returns_full_detail_for_known_dbn(mcp_client):
    r = await mcp_client.call_tool("get_school", {"dbn": "15K321"})
    detail = r.data
    assert detail is not None
    assert detail.summary.dbn == "15K321"
    # Service layer is already tested in depth; just confirm the adapter
    # forwards the full SchoolDetail rather than truncating it.
    assert detail.demographics_by_year, "expected demographics rows"


async def test_get_school_returns_none_for_missing_dbn(mcp_client):
    r = await mcp_client.call_tool("get_school", {"dbn": "99Z999"})
    assert r.data is None


@respx.mock
async def test_find_schools_for_address_combines_geocode_and_zoning(mcp_client):
    """The tool's value-add over plain geocode_address: it stitches the
    geocode result onto a point-in-polygon zone lookup. PS 321's own
    address should resolve to PS 321 in the ES list."""
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
                        },
                    }
                ]
            },
        )
    )
    r = await mcp_client.call_tool(
        "find_schools_for_address", {"address": "180 7 Ave Brooklyn"}
    )
    assert r.data is not None
    assert r.data.geocoding.borough == "Brooklyn"
    es_dbns = [s.dbn for s in r.data.schools.elementary]
    assert "15K321" in es_dbns


@respx.mock
async def test_find_schools_for_address_returns_none_when_geocode_fails(mcp_client):
    """If the address can't be geocoded, the tool returns None instead of
    handing back an empty zone-search at (0, 0) or some default point."""
    respx.get(GEOSEARCH_URL).mock(return_value=httpx.Response(200, json={"features": []}))
    r = await mcp_client.call_tool(
        "find_schools_for_address", {"address": "garbage xyzzy"}
    )
    assert r.data is None


async def test_top_schools_tool_returns_ranked_schools(mcp_client):
    r = await mcp_client.call_tool(
        "top_schools",
        {"metric": "regents_pct_above_64", "level": "high", "limit": 5},
    )
    assert len(r.data) == 5
    assert [s.rank for s in r.data] == [1, 2, 3, 4, 5]
    assert all(s.metric == "regents_pct_above_64" for s in r.data)
    # Descending by default.
    values = [s.value for s in r.data]
    assert values == sorted(values, reverse=True)


async def test_bulk_metrics_tool_returns_per_school_rows(mcp_client):
    r = await mcp_client.call_tool(
        "bulk_metrics",
        {"level": "high", "metrics": ["eni", "regents_pct_above_64"]},
    )
    assert r.data
    first = r.data[0]
    assert set(first.metrics.keys()) == {"eni", "regents_pct_above_64"}


async def test_list_high_schools_tool_filters_by_borough(mcp_client):
    r = await mcp_client.call_tool(
        "list_high_schools", {"borough": "Brooklyn", "limit": 5}
    )
    assert r.data
    assert all(s.boro == "Brooklyn" for s in r.data)


async def test_top_neighborhoods_tool_returns_ranked_ntas(mcp_client):
    r = await mcp_client.call_tool(
        "top_neighborhoods",
        {"metric": "ela_pct_proficient", "level": "elementary", "limit": 5},
    )
    assert len(r.data) == 5
    # Each NTA cohort meets the min-schools floor.
    assert all(n.n_schools >= 5 for n in r.data)
    # Descending by default.
    values = [n.value for n in r.data]
    assert values == sorted(values, reverse=True)


async def test_borough_summary_tool_returns_5_borough_grid(mcp_client):
    r = await mcp_client.call_tool(
        "borough_summary",
        {"metrics": ["eni", "regents_pct_above_64"], "level": "high"},
    )
    assert [b.name for b in r.data.rows] == [
        "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island",
    ]
    assert r.data.metric_names == ["eni", "regents_pct_above_64"]


async def test_school_peers_tool_returns_focal_school_flagged(mcp_client):
    r = await mcp_client.call_tool(
        "school_peers", {"dbn": "15K321", "scope": "neighborhood"}
    )
    assert r.data is not None
    selves = [p for p in r.data.rows if p.is_self]
    assert len(selves) == 1
    assert selves[0].dbn == "15K321"


async def test_schools_in_neighborhood_tool_resolves_colloquial_name(mcp_client):
    r = await mcp_client.call_tool(
        "schools_in_neighborhood", {"query": "park slope", "limit": 5}
    )
    assert r.data is not None
    assert r.data.nta_name == "Park Slope-Gowanus"
    assert r.data.schools
    assert r.data.n_schools_total >= len(r.data.schools)


async def test_schools_in_neighborhood_tool_surfaces_alternatives(mcp_client):
    """'harlem' matches multiple NTAs — alternatives must come back so
    the agent can offer disambiguation to the user."""
    r = await mcp_client.call_tool("schools_in_neighborhood", {"query": "harlem"})
    assert r.data is not None
    assert "Harlem" in r.data.nta_name
    assert len(r.data.other_candidates) >= 2


async def test_get_neighborhood_tool_returns_full_report(mcp_client):
    r = await mcp_client.call_tool("get_neighborhood", {"query": "park slope"})
    assert r.data is not None
    assert r.data.nta_name == "Park Slope-Gowanus"
    # Schools have lat/lon for mapping + per-school metric values for the table.
    assert r.data.schools
    sample = r.data.schools[0]
    assert sample.latitude is not None and sample.longitude is not None
    assert set(sample.metrics).issuperset(r.data.metric_names)
    # Peer ranks tell the agent where this NTA falls vs other NYC NTAs.
    assert r.data.peer_ranks
    for rank in r.data.peer_ranks:
        assert 1 <= rank.rank <= rank.total
        assert rank.extreme_high is not None and rank.extreme_low is not None
    # Boundary is a GeoJSON Polygon for mapping.
    assert r.data.boundary is not None
    assert r.data.boundary["type"] == "Polygon"


async def test_school_staffing_tool_returns_fte_counts(mcp_client):
    r = await mcp_client.call_tool("school_staffing", {"dbn": "75K372"})
    assert r.data is not None
    # PS 372 had 1.0 GC + 1.3 SW per 2025-26 reporting; shape check only,
    # not the exact numbers (data refreshes annually).
    assert r.data.total_gc is not None
    assert r.data.total_sw is not None
    assert r.data.ay > 2020


async def test_co_located_schools_tool_returns_building_mates(mcp_client):
    """PS 372 shares building K113 with M.S. 113 per the 2020-21 report."""
    r = await mcp_client.call_tool("co_located_schools", {"dbn": "75K372"})
    assert r.data
    names = {s.school_name for s in r.data}
    assert any("M.S. 113" in n for n in names), f"got {names}"


async def test_get_neighborhood_tool_unknown_query_returns_none(mcp_client):
    r = await mcp_client.call_tool("get_neighborhood", {"query": "xyzzy fake neighborhood"})
    assert r.data is None


async def test_school_peers_tool_unknown_dbn_returns_none(mcp_client):
    r = await mcp_client.call_tool(
        "school_peers", {"dbn": "99Z999", "scope": "neighborhood"}
    )
    assert r.data is None


@respx.mock
async def test_geocode_address_tool_delegates_to_service(mcp_client):
    respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"coordinates": [-73.978633, 40.671816]},
                        "properties": {"label": "180 7 AVE", "borough": "Brooklyn"},
                    }
                ]
            },
        )
    )
    r = await mcp_client.call_tool("geocode_address", {"address": "180 7 Ave"})
    assert r.data is not None
    assert r.data.lat == 40.671816
    assert r.data.borough == "Brooklyn"
