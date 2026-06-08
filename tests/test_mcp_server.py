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
        "schools_in_district",
        "get_neighborhood",
        "school_staffing",
        "school_swd_outcomes",
        "co_located_schools",
        # Dynamic capability discovery — discover + access pair.
        "list_school_metrics",
        "list_neighborhood_metrics",
        "get_school_metric",
        "get_neighborhood_metric",
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
    what args to pass — caught here, not in production. No-arg discovery
    tools are explicitly allowed to have an empty properties dict."""
    NO_ARG_TOOLS = {"list_school_metrics", "list_neighborhood_metrics"}
    tools = await mcp_client.list_tools()
    for t in tools:
        assert t.inputSchema is not None, f"{t.name} has no inputSchema"
        if t.name in NO_ARG_TOOLS:
            continue
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


async def test_school_swd_outcomes_tool_returns_subgroup_metrics(mcp_client):
    r = await mcp_client.call_tool("school_swd_outcomes", {"dbn": "15K462"})
    assert r.data is not None
    assert r.data.is_d75 is False
    assert r.data.swd_enrollment_pct is not None
    assert r.data.graduation, "expected SWD grad cohorts on an HS"
    assert r.data.essa_status is not None
    assert r.data.notes


async def test_school_swd_outcomes_tool_flags_d75(mcp_client):
    r = await mcp_client.call_tool("school_swd_outcomes", {"dbn": "75K004"})
    assert r.data is not None
    assert r.data.is_d75 is True
    assert any("District 75" in n for n in r.data.notes)


async def test_school_swd_outcomes_tool_returns_none_for_unknown_dbn(mcp_client):
    r = await mcp_client.call_tool("school_swd_outcomes", {"dbn": "99Z999"})
    assert r.data is None


async def test_iep_prompt_registered_and_renders_with_arguments(mcp_client):
    """The IEP / special-needs prompt should be discoverable via prompts/list
    and render with the parent's concern interpolated into the body."""
    prompts = await mcp_client.list_prompts()
    names = {p.name for p in prompts}
    assert "iep_or_special_needs" in names

    rendered = await mcp_client.get_prompt(
        "iep_or_special_needs",
        {
            "concern": "emotional regulation",
            "address": "195 14th Street, Brooklyn",
            "grade_level": "rising 6th",
        },
    )
    body = "".join(m.content.text for m in rendered.messages if hasattr(m.content, "text"))
    assert "emotional regulation" in body
    assert "195 14th Street, Brooklyn" in body
    assert "rising 6th" in body
    # The prompt names the specific tools an agent should call.
    for tool in ("school_swd_outcomes", "school_staffing", "co_located_schools"):
        assert tool in body, f"prompt should mention {tool}"


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


# ----- Dynamic capability discovery (services/metrics.py) -----

async def test_list_school_metrics_tool_returns_registry(mcp_client):
    """Round-trip check: the discovery tool reaches the registry and
    every entry deserializes to SchoolMetricDef on the client side."""
    r = await mcp_client.call_tool("list_school_metrics", {})
    assert r.data, "expected at least one school metric"
    ids = {m.id for m in r.data}
    # ENI is the headline equity metric — must always be discoverable.
    assert "eni" in ids
    # Every entry carries provenance.
    assert all(m.vintage_note for m in r.data)


async def test_list_neighborhood_metrics_names_underlying_school_metric(mcp_client):
    r = await mcp_client.call_tool("list_neighborhood_metrics", {})
    school_ids = {m.id for m in (await mcp_client.call_tool("list_school_metrics", {})).data}
    for entry in r.data:
        assert entry.underlying_school_metric in school_ids


async def test_get_school_metric_tool_returns_value_for_known_school(mcp_client):
    r = await mcp_client.call_tool(
        "get_school_metric", {"dbn": "15K321", "metric": "eni"}
    )
    assert r.data is not None
    assert r.data.dbn == "15K321"
    assert r.data.metric == "eni"
    assert r.data.value is not None and 0.0 <= r.data.value <= 1.0


async def test_get_school_metric_tool_returns_none_for_unknown_dbn(mcp_client):
    r = await mcp_client.call_tool(
        "get_school_metric", {"dbn": "99Z999", "metric": "eni"}
    )
    assert r.data is None


async def test_get_school_metric_tool_notes_level_mismatch(mcp_client):
    """Regents on an elementary school: value=None, note explains why.
    The note is the journalism output — never strip it on the way to
    the agent."""
    r = await mcp_client.call_tool(
        "get_school_metric",
        {"dbn": "15K321", "metric": "regents_pct_above_64"},
    )
    assert r.data is not None
    assert r.data.value is None
    assert r.data.note and "elementary" in r.data.note.lower()


async def test_get_neighborhood_metric_tool_fuzzy_matches(mcp_client):
    r = await mcp_client.call_tool(
        "get_neighborhood_metric",
        {"nta": "Park Slope", "metric": "eni", "level": "elementary"},
    )
    assert r.data is not None
    assert r.data.nta_name == "Park Slope-Gowanus"
    assert r.data.n_schools > 0
    assert r.data.value is not None


async def test_discovery_tools_advertise_the_discover_then_access_pattern(mcp_client):
    """The instructions block tells agents to use the discovery pair when
    the curated tools don't fit — make sure the link is in the tool
    descriptions too, so an agent landing on get_*_metric without reading
    instructions still finds its way to list_*_metrics."""
    tools = {t.name: t for t in await mcp_client.list_tools()}
    assert "list_school_metrics" in (tools["get_school_metric"].description or "")
    assert "list_neighborhood_metrics" in (tools["get_neighborhood_metric"].description or "")


async def test_schools_in_district_middle_round_trip_carries_admission_methods(mcp_client):
    """End-to-end: the MCP adapter forwards the full DistrictSchoolsResult
    including per-school admission methods + per-program priority strings.
    A regression that flattens or drops those would break the agentic-
    newsroom Data Tribune demo path."""
    r = await mcp_client.call_tool(
        "schools_in_district", {"district": 2, "level": "middle"}
    )
    data = r.data
    assert data is not None
    assert data.district == 2
    assert data.level == "middle"
    assert data.n_schools == 23
    by_dbn = {s.dbn: s for s in data.schools}
    assert "02M297" in by_dbn
    ms297 = by_dbn["02M297"]
    assert set(ms297.admission_methods) == {"Screened", "Zone Priority"}
    assert len(ms297.ms_programs) == 2
    assert all(p.priorities for p in ms297.ms_programs)


async def test_schools_in_district_high_round_trip_returns_overview(mcp_client):
    r = await mcp_client.call_tool(
        "schools_in_district", {"district": 2, "level": "high"}
    )
    data = r.data
    assert data is not None
    assert data.level == "high"
    assert data.n_schools > 0
    assert "city-wide" in (data.admission_overview or "").lower()
