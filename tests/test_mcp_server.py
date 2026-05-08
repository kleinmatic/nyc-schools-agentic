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


async def test_list_tools_returns_the_four_registered_tools(mcp_client):
    tools = await mcp_client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "search_schools",
        "get_school",
        "find_schools_for_address",
        "geocode_address",
    }


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
