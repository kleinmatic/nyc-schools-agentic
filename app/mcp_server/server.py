"""MCP tool definitions. Each tool is a thin adapter over `app.services` —
no business logic, no dataframe access, no transport leakage in services/."""
from typing import Optional

from fastmcp import FastMCP
from pydantic import BaseModel

from ..services.models import (
    GeocodingResult,
    SchoolDetail,
    SchoolSummary,
    ZonedSearchResult,
)
from ..services.schools import get_school as _get_school
from ..services.schools import search_schools as _search_schools
from ..services.zoning import find_zoned_schools as _find_zoned_schools
from ..services.zoning import geocode as _geocode


mcp = FastMCP(
    name="nyc-schools",
    instructions=(
        "Tools for querying NYC public school data: demographics, NY State "
        "exam results, attendance zones, peer comparisons, and DOE/NYSED "
        "accountability reporting.\n\n"
        "Conventions:\n"
        "- DBN (e.g. '15K321') is the primary key for every school.\n"
        "- Percentages are 0..1 fractions (0.83 = 83%), not 0..100.\n"
        "- ENI (Economic Need Index) is the equity-proxy of choice for "
        "ranking; poverty_pct is for direct interpretability only.\n"
        "- All years are academic years (the spring year — '2024' = 2023-24).\n\n"
        "Workflow for 'where should I send my kid' questions: geocode the "
        "address, find_schools_for_address to get the zoned ES/MS, then "
        "get_school on each DBN for full detail."
    ),
)


class FindSchoolsForAddressResult(BaseModel):
    """Combined geocode + zoning lookup. Returned by find_schools_for_address."""
    geocoding: GeocodingResult
    schools: ZonedSearchResult


@mcp.tool
def search_schools(query: str, limit: int = 10) -> list[SchoolSummary]:
    """Fuzzy-search NYC public schools by name.

    Use this when the user names a school but you don't know the DBN.
    Examples of inputs that work: "PS 321", "LaGuardia", "Bronx Science",
    "art and design high school". Returns up to `limit` best matches by
    fuzzy-name score, each as a SchoolSummary with DBN you can pass to
    get_school."""
    return _search_schools(query, limit=limit)


@mcp.tool
def get_school(dbn: str) -> Optional[SchoolDetail]:
    """Get the full report for one school by DBN.

    Returns demographics by year, location, NYS 3-8 ELA/math, Regents,
    class size, pupil:teacher ratio, SHSAT outcomes (HS only), Galaxy
    budget, HS directory entry (HS only), full NYSED School Report Card
    (ESSA, chronic absenteeism, expenditures, teacher quality, graduation,
    CCCR), and peer-cohort ranks for ENI / PTR / chronic absenteeism.

    Note: this is a large payload. If you only need school name + level
    + enrollment, use search_schools instead. Returns None if no school
    has that DBN."""
    return _get_school(dbn)


@mcp.tool
async def find_schools_for_address(address: str) -> Optional[FindSchoolsForAddressResult]:
    """Find the elementary and middle schools whose attendance zones
    contain a given NYC street address.

    Combines geocoding (NYC Planning Labs GeoSearch API) with point-in-
    polygon zone lookup. Returns both the resolved address and the
    matched schools.

    Caveats:
    - Some districts have moved to choice-based admissions and have NO
      zoned ES (D1, D7) or MS (D15) — the corresponding list will be
      empty, by design.
    - Returns None if the address can't be geocoded.
    - High schools are NOT zoned in NYC; they're city-wide choice. Use
      get_school after a search if the user is asking about a high
      school."""
    geo = await _geocode(address)
    if geo is None:
        return None
    schools = _find_zoned_schools(geo.lat, geo.lon)
    return FindSchoolsForAddressResult(geocoding=geo, schools=schools)


@mcp.tool
async def geocode_address(address: str) -> Optional[GeocodingResult]:
    """Resolve a NYC street address to lat/lon + borough via NYC Planning
    Labs' GeoSearch API. Mostly an escape hatch — for the common case of
    'what schools serve this address', call find_schools_for_address
    instead, which combines this with zone lookup."""
    return await _geocode(address)
