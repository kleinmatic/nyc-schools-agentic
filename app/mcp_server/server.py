"""MCP tool definitions. Each tool is a thin adapter over `app.services` —
no business logic, no dataframe access, no transport leakage in services/."""
from typing import Literal, Optional

from fastmcp import FastMCP
from pydantic import BaseModel

from ..services.analytics import (
    METRIC_DESCRIPTIONS,
    VALID_ACCESSIBILITY,
    VALID_LEVELS,
    bulk_metrics as _bulk_metrics,
    list_high_schools as _list_high_schools,
    top_schools as _top_schools,
)
from ..services.models import (
    GeocodingResult,
    HsListing,
    MetricRow,
    RankedSchool,
    SchoolDetail,
    SchoolSummary,
    ZonedSearchResult,
)
from ..services.schools import get_school as _get_school
from ..services.schools import search_schools as _search_schools
from ..services.zoning import find_zoned_schools as _find_zoned_schools
from ..services.zoning import geocode as _geocode

# Literal types so the JSON schema enumerates valid values for the LLM.
MetricName = Literal[
    "eni", "poverty_pct", "attendance_rate", "chronic_absent_rate",
    "ela_pct_proficient", "math_pct_proficient",
    "regents_pct_above_64", "regents_pct_above_79", "graduation_rate_4yr",
    "pupil_teacher_ratio", "pct_inexperienced_teachers",
    "pct_out_of_cert_teachers", "per_pupil_expenditure",
]
SchoolLevel = Literal["elementary", "middle", "high", "K-8", "6-12"]
Borough = Literal["M", "X", "K", "Q", "R", "Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"]
Accessibility = Literal["Fully Accessible", "Partially Accessible", "Not Accessible"]


_METRIC_DOC_BLOCK = "\n".join(
    f"  - `{k}` — {v}" for k, v in METRIC_DESCRIPTIONS.items()
)

_TOP_SCHOOLS_DESC = f"""Rank schools by an accountability metric. Returns top N by metric value; pass `ascending=True` for the lowest-value end.

Use for: "top high schools by Regents passing rate", "Bronx elementary schools with the highest math proficiency", "high schools with the lowest chronic absenteeism."

Metric vocabulary (all 0..1 fractions, except `per_pupil_expenditure` which is dollars):
{_METRIC_DOC_BLOCK}

Note level applicability — graduation/Regents are HS-only; ela/math_pct_proficient is ES/MS/K-8/6-12 only. Schools without data for the requested metric are silently dropped from results."""

_BULK_METRICS_DESC = f"""One row per active school with the requested metrics. For cross-school analytics: correlations, scatter plots, "is X associated with Y across schools."

Use this instead of calling `get_school` 400 times. ~440 HS rows × 13 metrics ≈ 10 K tokens for the full dump; specify a subset of `metrics` to shrink. Default is all 13. Missing values are returned as None — never coerce to 0, since that breaks downstream stats.

Available metrics:
{_METRIC_DOC_BLOCK}"""


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


@mcp.tool
def list_high_schools(
    borough: Optional[Borough] = None,
    accessibility: Optional[Accessibility] = None,
    program_keyword: Optional[str] = None,
    limit: int = 50,
) -> list[HsListing]:
    """Browse / filter NYC high schools from the HS Directory (AY 2021).

    Use this when the user is shopping for a high school and gives
    criteria but doesn't have a specific school in mind: "performing arts
    high schools in Brooklyn", "fully accessible HS in the Bronx",
    "schools with strong CTE programs."

    Filters compose AND. `program_keyword` is a case-insensitive
    substring search across overview, academic opportunities, language
    classes, and AP courses fields. Returns slim summaries; call
    `get_school` for full detail on candidates of interest."""
    return _list_high_schools(
        borough=borough,
        accessibility=accessibility,
        program_keyword=program_keyword,
        limit=limit,
    )


@mcp.tool(description=_TOP_SCHOOLS_DESC)
def top_schools(
    metric: MetricName,
    level: SchoolLevel = "high",
    limit: int = 20,
    borough: Optional[Borough] = None,
    ascending: bool = False,
) -> list[RankedSchool]:
    return _top_schools(
        metric=metric, level=level, limit=limit,
        borough=borough, ascending=ascending,
    )


@mcp.tool(description=_BULK_METRICS_DESC)
def bulk_metrics(
    level: SchoolLevel = "high",
    metrics: Optional[list[MetricName]] = None,
    borough: Optional[Borough] = None,
) -> list[MetricRow]:
    return _bulk_metrics(level=level, metrics=metrics, borough=borough)
