"""MCP tool definitions. Each tool is a thin adapter over `app.services` —
no business logic, no dataframe access, no transport leakage in services/."""
from typing import Literal, Optional

import functools
import inspect

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from ..services.analytics import (
    METRIC_DESCRIPTIONS,
    METRIC_NAMES,
    VALID_ACCESSIBILITY,
    VALID_LEVELS,
    aggregate_by_neighborhood as _aggregate_by_neighborhood,
    borough_summary as _borough_summary,
    bulk_metrics as _bulk_metrics,
    get_neighborhood as _get_neighborhood,
    list_high_schools as _list_high_schools,
    school_peers as _school_peers,
    schools_in_district as _schools_in_district,
    schools_in_neighborhood as _schools_in_neighborhood,
    top_schools as _top_schools,
)
from ..services.metrics import (
    get_neighborhood_metric as _get_neighborhood_metric,
    get_school_metric as _get_school_metric,
    list_neighborhood_metrics as _list_neighborhood_metrics,
    list_school_metrics as _list_school_metrics,
)
from ..services.models import (
    BoroughGrid,
    CoLocatedSchool,
    DistrictSchoolsResult,
    GeocodingResult,
    HsListing,
    MetricRow,
    NeighborhoodAggregate,
    NeighborhoodDetail,
    NeighborhoodMetricDef,
    NeighborhoodMetricValue,
    NeighborhoodSchoolsResult,
    PeerCohort,
    RankedSchool,
    SchoolDetail,
    SchoolMetricDef,
    SchoolMetricValue,
    SchoolSummary,
    StaffingInfo,
    SwdOutcomes,
    ZonedSearchResult,
)
from ..services.schools import co_located_schools as _co_located_schools
from ..services.schools import get_school as _get_school
from ..services.schools import school_staffing as _school_staffing
from ..services.schools import school_swd_outcomes as _school_swd_outcomes
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
PeerScope = Literal["neighborhood", "district"]


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
    # Hide unexpected internal exceptions from token-holders (recon leg of
    # the C3 audit chain). Intentional ValueError validation messages are
    # re-surfaced as ToolError by the @tool wrapper (_surface_value_errors).
    mask_error_details=True,
    instructions=(
        "Tools for querying NYC public school data: demographics, NY State "
        "exam results, attendance zones, peer comparisons, and DOE/NYSED "
        "accountability reporting.\n\n"
        "Conventions:\n"
        "- DBN (e.g. '15K321') is the primary key for every school.\n"
        "- Percentages are 0..1 fractions (0.83 = 83%), not 0..100.\n"
        "- ENI (Economic Need Index) is the equity-proxy of choice for "
        "ranking; poverty_pct is for direct interpretability only.\n"
        "- All years are academic years (the spring year — '2024' = 2023-24).\n"
        "- Neighborhood = NTA (Neighborhood Tabulation Area), NYC's official "
        "boundaries — the closest formal proxy to a colloquial neighborhood.\n"
        "- Zone / district = one of NYC's 32 geographic school districts. "
        "Districts matter for ES / MS admissions; HS is city-wide choice.\n\n"
        "Workflow hints:\n"
        "- 'Where should I send my kid?' → geocode the address, "
        "find_schools_for_address to get the zoned ES (and the MS zone-"
        "priority signal — middle school is district choice, see the "
        "result's `ms_admission_type` and `ms_admission_note`), then "
        "get_school + school_peers for full detail and neighborhood "
        "context, and `schools_in_district(ms_district, level=\"middle\")` "
        "for the full district MS set with per-school admission methods.\n"
        "- 'Tell me about the schools in <neighborhood>' → "
        "schools_in_neighborhood (just the school list) or get_neighborhood "
        "(full report: peer ranks vs other NTAs, plus per-school metric "
        "values and lat/lon for mapping).\n"
        "- 'Tell me about district N middle schools' → schools_in_district "
        "with level=\"middle\" — returns each school's admission methods "
        "(Open / Screened / Zone Priority / etc.) and per-program priority "
        "cascades, the right cohort for district-choice admissions.\n"
        "- 'How does this school compare to its neighbors?' → school_peers.\n"
        "- 'Best/worst schools by some metric' → top_schools.\n"
        "- 'Best/worst neighborhoods by some metric' → top_neighborhoods.\n"
        "- 'Borough overview' → borough_summary.\n"
        "- 'Cross-school correlations' → bulk_metrics.\n"
        "- 'What can I ask about a school / a neighborhood?' → "
        "list_school_metrics / list_neighborhood_metrics — the discovery "
        "surface. Returns every metric this server can answer with id, "
        "label, unit, applicable school levels, and a vintage note. Use "
        "the returned ids with get_school_metric / get_neighborhood_metric "
        "for single-value lookups. The curated tools above are shortcuts "
        "for common patterns; discovery is the open-ended escape hatch."
    ),
)


def _surface_value_errors(fn):
    """Wrap a tool so a service-layer ValueError becomes a ToolError.

    mask_error_details=True hides unexpected exceptions (pandas KeyError,
    bad dtype) from token-holders — no internal column names / paths leak.
    But the service layer raises ValueError with curated, actionable
    messages for bad arguments ("unknown metric: 'foo'. Valid: [...]"), and
    those must reach the agent so it can self-correct and retry (an
    MCP/WebMCP best practice). ToolError is the one exception FastMCP always
    surfaces verbatim, even under masking. Masking happens inside the tool
    runner — below any middleware — so the conversion has to sit here, at
    the tool boundary. services/ must never import a transport type, so the
    adapter owns it."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except ValueError as e:
                raise ToolError(str(e)) from e
        return wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise ToolError(str(e)) from e
    return wrapper


def tool(*args, **kwargs):
    """Drop-in for @mcp.tool that also runs the ValueError→ToolError
    conversion (see _surface_value_errors). Supports both the bare
    `@tool` and the parameterized `@tool(description=...)` forms."""
    if len(args) == 1 and not kwargs and callable(args[0]):
        return mcp.tool(_surface_value_errors(args[0]))

    def deco(fn):
        return mcp.tool(*args, **kwargs)(_surface_value_errors(fn))
    return deco


class FindSchoolsForAddressResult(BaseModel):
    """Combined geocode + zoning lookup. Returned by find_schools_for_address."""
    geocoding: GeocodingResult
    schools: ZonedSearchResult


@tool
def search_schools(query: str, limit: int = 10) -> list[SchoolSummary]:
    """Fuzzy-search NYC public schools by name.

    Use this when the user names a school but you don't know the DBN.
    Examples of inputs that work: "PS 321", "LaGuardia", "Bronx Science",
    "art and design high school". Returns up to `limit` best matches by
    fuzzy-name score, each as a SchoolSummary with DBN you can pass to
    get_school."""
    return _search_schools(query, limit=limit)


@tool
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


@tool
async def find_schools_for_address(address: str) -> Optional[FindSchoolsForAddressResult]:
    """Find the elementary and middle schools whose attendance zones
    contain a given NYC street address.

    Combines geocoding (NYC Planning Labs GeoSearch API) with point-in-
    polygon zone lookup. Returns both the resolved address and the
    matched schools.

    Caveats:
    - ES: districts that have moved entirely to choice-based admission
      (D1, D7) have no zoning polygons; `elementary` will be empty.
    - MS: NYC middle-school admission is **district-based choice**, not
      strict zoning. The `middle` list reports any school-specific zone-
      priority polygons that contain the address (numeric `label` like
      "297") — that's a priority tier in the choice process, not a
      placement. Always read `ms_admission_type` and `ms_admission_note`
      to interpret. If the only polygon covering the address is the
      whole-district fallback (label like "D2", "D15"), `middle` will be
      empty and `ms_admission_type` will be "district_choice". For the
      full district set with per-school admission methods, call
      `schools_in_district(ms_district, level="middle")`.
    - Returns None if the address can't be geocoded.
    - High schools are NOT zoned in NYC; they're city-wide choice. Use
      get_school after a search if the user is asking about a high
      school."""
    geo = await _geocode(address)
    if geo is None:
        return None
    schools = _find_zoned_schools(geo.lat, geo.lon)
    return FindSchoolsForAddressResult(geocoding=geo, schools=schools)


@tool
async def geocode_address(address: str) -> Optional[GeocodingResult]:
    """Resolve a NYC street address to lat/lon + borough via NYC Planning
    Labs' GeoSearch API. Mostly an escape hatch — for the common case of
    'what schools serve this address', call find_schools_for_address
    instead, which combines this with zone lookup."""
    return await _geocode(address)


@tool
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


@tool(description=_TOP_SCHOOLS_DESC)
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


@tool(description=_BULK_METRICS_DESC)
def bulk_metrics(
    level: SchoolLevel = "high",
    metrics: Optional[list[MetricName]] = None,
    borough: Optional[Borough] = None,
) -> list[MetricRow]:
    return _bulk_metrics(level=level, metrics=metrics, borough=borough)


_TOP_NEIGHBORHOODS_DESC = f"""Rank NYC neighborhoods (NTAs — the official Neighborhood Tabulation Areas) by the mean of a metric across their schools. Returns top N by aggregated value; pass `ascending=True` for the lowest end.

Use for: "best neighborhoods for elementary schools", "neighborhoods with the highest chronic absenteeism", "where are the highest-graduation-rate HS clustered."

NTAs with fewer than `min_schools` schools (default 5) are excluded — single-school cohorts produce noisy "averages." This is also true for the homepage leaderboards.

Available metrics:
{_METRIC_DOC_BLOCK}"""


@tool(description=_TOP_NEIGHBORHOODS_DESC)
def top_neighborhoods(
    metric: MetricName,
    level: SchoolLevel = "high",
    limit: int = 10,
    ascending: bool = False,
    min_schools: int = 5,
) -> list[NeighborhoodAggregate]:
    return _aggregate_by_neighborhood(
        metric=metric, level=level, limit=limit,
        ascending=ascending, min_schools=min_schools,
    )


@tool
def borough_summary(
    metrics: Optional[list[MetricName]] = None,
    level: Optional[SchoolLevel] = "high",
) -> BoroughGrid:
    """5-borough × N-metric overview grid: one row per borough (Manhattan,
    Brooklyn, Queens, Bronx, Staten Island), one column per requested
    metric, each cell the mean of that metric across schools in that
    borough at the given level.

    Use for borough-level comparisons: "how does ENI / attendance /
    Regents passing differ across boroughs?" Defaults: all 13 metrics,
    HS level. Pass `level=None` to mix all school levels (less
    interpretable but available)."""
    return _borough_summary(
        metrics=metrics if metrics is not None else list(METRIC_NAMES),
        level=level,
    )


@tool
def schools_in_neighborhood(
    query: str,
    level: Optional[SchoolLevel] = None,
    limit: int = 50,
) -> Optional[NeighborhoodSchoolsResult]:
    """Look up schools in a NYC neighborhood by colloquial name.

    The caller doesn't need to know NYC's official NTA names — fuzzy-
    matches the query against all 189 NTAs and returns the schools in
    the best match. Use for "tell me about the schools in park slope",
    "what middle schools are in mott haven", "harlem high schools."

    Behavior on ambiguous queries: returns the single best match in
    `nta_name`, but populates `other_candidates` with alternative NTAs
    that scored well — so a query like "harlem" returns Central Harlem
    North-Polo Grounds plus Central Harlem South / East Harlem North /
    East Harlem South in `other_candidates` for the caller to offer the
    user. Returns None when nothing matches above a low threshold.

    Pair with `get_school` (one of the returned DBNs) for full detail
    on individual schools, or with `school_peers(dbn, "neighborhood")`
    for the same-NTA peer comparison table the school page uses."""
    return _schools_in_neighborhood(query=query, level=level, limit=limit)


@tool
def schools_in_district(
    district: int,
    level: Literal["elementary", "middle", "high"],
) -> Optional[DistrictSchoolsResult]:
    """All NYC public schools in one school district at one level. The
    natural answer to "tell me about District 2 middle schools."

    For `level="middle"` this is the rich case and the primary intended
    use: NYC middle-school admission is **district-based choice**, not
    strict zoning. Each school's `admission_methods` lists the methods
    it admits by (Open, Screened, Zone Priority, Audition, etc.) and
    `ms_programs` carries the per-program priority cascade strings as
    published. A school commonly has 1 program, sometimes up to 4
    (e.g. M.S. 131 carries Zone Priority + Language Criteria + Screened
    + ASD/ACES). Source of truth is NYC DOE's Middle School Directory
    (Fall 2025).

    For `level="elementary"` and `level="high"` you get the school list
    + an `admission_overview` explaining the admission mechanic, but no
    per-school methods (the ES/HS directories don't share the MS
    Directory shape; HS admission is also city-wide choice, so the
    district grouping there is geographic only).

    Pairs with `find_schools_for_address`: when that returns
    `ms_admission_type="zone_priority_choice"` or `"district_choice"`,
    call this with the returned `ms_district` to get the full set the
    family can rank. Returns None for unknown level, or when the
    district + level combination has zero schools.

    `district` is one of 1..32 (geographic) or 75 (D75 specialized
    special-ed). For D75 + middle the result falls back to a basic
    listing without admission methods — D75 placement is by the
    Committee on Special Education, not the choice application.
    """
    return _schools_in_district(district=district, level=level)


@tool
def get_neighborhood(
    query: str,
    level: Optional[SchoolLevel] = None,
) -> Optional[NeighborhoodDetail]:
    """Full report on a NYC neighborhood (NTA): how this NTA ranks vs
    other NTAs on the 5 default metrics (ENI, attendance, ELA/math
    proficient, Regents passing rate), the list of schools with per-
    school metric values and lat/lon, fuzzy-match alternatives if the
    query was ambiguous, and the NTA's boundary as GeoJSON for mapping.

    Use for: "give me a full report on Park Slope", "compare Bedford-
    Stuyvesant to other NYC neighborhoods", "what schools are in
    Sunset Park and how do they stack up." For the lighter "just the
    school list" version use `schools_in_neighborhood` instead.

    Pass `level` (elementary, middle, high, K-8, 6-12) to restrict the
    schools and aggregate rankings to that level — useful if the user
    only cares about HS, say. Default: all levels mixed.

    Returns None when the query doesn't fuzzy-match any NTA above the
    threshold, or when no schools live in the matched NTA at the
    requested level."""
    return _get_neighborhood(query=query, level=level)


@tool
def school_staffing(dbn: str) -> Optional[StaffingInfo]:
    """Counseling + social-work staffing for one school: FTE counts and
    pupils-per-staff ratios.

    Source: NYC DOE InfoHub annual Guidance Counselor & Social Worker
    report (most recent year on file). Returns FTE counts for the two
    main roles (GC, SW) plus full-time/part-time breakdowns, plus
    DOE-computed ratios using same-year enrollment. The American School
    Counselor Association recommends 250:1 — useful as a benchmark.

    Also returns the school psychologist + CBO partner fields for
    completeness when the user asks about mental-health resources.

    Returns None if the school isn't in the report (newly opened, etc.)."""
    return _school_staffing(dbn)


@tool
def school_swd_outcomes(dbn: str) -> Optional[SwdOutcomes]:
    """Outcomes for the Students-With-Disabilities (SWD) subgroup at one
    school — distinct from `swd_pct` on the school summary.

    `swd_pct` answers "are there kids with IEPs here?" (an enrollment
    share). This tool answers "how do kids with IEPs *do* here?" by
    surfacing the same accountability metrics the rest of the app
    reports for "All Students", but filtered to just the SWD subgroup:
    4/5/6-year graduation rate, chronic absenteeism, CCCR index +
    level, and the school's ESSA-subgroup accountability status. NYSED
    School Report Card Database; latest year per metric.

    Use for any question about IEPs, special education, or how
    students with disabilities are served. Pair with `school_staffing`
    (counselor + social-worker FTE) for adult-support inputs, and with
    `co_located_schools` since a co-located D75 program is often the
    nearby specialized option.

    Caveats baked into the response:
    - NYSED redacts cells where the SWD cohort is below ~30 students;
      those come back with `suppressed=true` and the cohort N when
      available.
    - "SWD" lumps every IEP together — a speech-only accommodation
      through a 12:1:1 self-contained class. The number is coarse.
    - D75 schools (`is_d75=true`) are the citywide specialized
      special-ed district; placement is by the Committee on Special
      Education, not by zoning or choice, and direct comparison to
      non-D75 schools should be made with care.

    Returns None only if the DBN doesn't exist; schools without NYSED
    SWD data return a populated object with empty outcome fields and
    an explanatory note in `notes`."""
    return _school_swd_outcomes(dbn)


@tool
def co_located_schools(dbn: str) -> list[CoLocatedSchool]:
    """Schools sharing a building with the given school.

    Source: NYC DOE 2020-21 Co-Location Report (latest published). Each
    school's "building IDs" (e.g. K113, K834) identify the physical
    buildings it occupies; schools with overlapping IDs share that
    building. Co-locations rarely shift year-over-year, so the 2020-21
    snapshot is still informative.

    Use for "what other schools are in this building" and as context
    for shared-space dynamics (PS/MS co-occupations, charter+district
    splits, etc.). Empty list if the school isn't in the report or
    occupies its building alone."""
    return _co_located_schools(dbn)


@mcp.prompt(
    name="iep_or_special_needs",
    description=(
        "Workflow for answering a parent who is evaluating NYC public "
        "schools for a child with an IEP or special needs (e.g. "
        "emotional-regulation issues, learning disability, autism, "
        "speech). Pass the concern and, optionally, an address and the "
        "child's grade level."
    ),
)
def iep_or_special_needs(
    concern: str,
    address: Optional[str] = None,
    grade_level: Optional[str] = None,
) -> str:
    addr_block = (
        f"- Parent address: {address}\n"
        "- Start by calling `find_schools_for_address` with that "
        "address to get the zoned ES and MS (HS is city-wide choice, "
        "so for a HS-age child skip to candidate-search via "
        "`list_high_schools` or `search_schools` instead).\n"
        if address
        else "- No address provided. If the parent has one, ask for it "
             "and re-run; without it, anchor on neighborhood "
             "(`schools_in_neighborhood`) or a specific school name "
             "(`search_schools`).\n"
    )
    grade_block = (
        f"- Grade level: {grade_level}. Filter to schools that serve "
        f"that grade.\n"
        if grade_level
        else ""
    )

    return f"""You are helping a parent of a NYC public-school student who has an IEP or special needs. The parent's concern, in their own words: "{concern}".

{addr_block}{grade_block}
## What this question actually has two halves

1. **Inputs — what supports does the school have?** Adult-staffing, mental-health partners, physical accessibility, co-located specialized programs.
2. **Outcomes — how do kids with IEPs actually do here?** Subgroup graduation, attendance, accountability status.

A parent usually conflates the two. Surface both — labeled.

## Tool plan

For each candidate school (one tool call per school is fine — these are cheap):

- `school_swd_outcomes(dbn)` — graduation/attendance/CCCR/ESSA broken out for just the Students-With-Disabilities subgroup. Read `notes` and surface them verbatim. If `is_d75=true`, flag prominently — D75 is a different placement system.
- `school_staffing(dbn)` — Guidance Counselor + Social Worker FTE and pupils-per-staff ratios. ASCA-recommended ratio is 250:1; report whether the school meets it. Also surface `school_psychologist_mandated` and `cbo_partner_mental_health` if either is populated — these are direct signals for emotional/behavioral support.
- `co_located_schools(dbn)` — if a D75 program shares the building, name it. That's often the nearby specialized option a parent should know about even if not the focal school.
- `get_school(dbn)` — only if the parent asks for school-wide context (overall demographics, programs, etc.). It's a heavy payload; don't pull it for every candidate.

For HS specifically: `list_high_schools(accessibility="Fully Accessible")` if the concern involves physical access, and check each HS's HS Directory `admissions` block for inclusion / ICT / specialized-program language via `get_school`.

## Things to tell the parent up front

- **`swd_pct` (enrollment %) is NOT the same as how well SWDs do.** A school can be 25% SWD and have very different SWD outcomes than another 25%-SWD school.
- **"Students with Disabilities" lumps every IEP together** — a kid with a speech accommodation and a kid in a 12:1:1 self-contained class are in the same subgroup number. Read the number with that caveat.
- **NYSED suppresses small cells** (~<30 SWDs in the cohort). Missing data ≠ bad school; it means the subgroup is small. Don't infer outcomes from absence.
- **What this tool surface CANNOT tell you**: it does NOT have program-mix detail (ICT vs SETSS vs 12:1:1 vs self-contained), specific behavioral supports, or restorative-justice / SEL adoption. For those, the parent should ask the school directly — frame what's surfaced here as "the data half" of the question.

## Format the response

- One section per candidate school.
- Within each, two subheads — "Supports on paper" (staffing, accessibility, co-located programs) and "Outcomes for kids with IEPs" (SWD-subgroup metrics).
- End with a short "questions to ask the school" list — things only a building visit can answer.
"""


@tool
def school_peers(
    dbn: str,
    scope: PeerScope = "neighborhood",
    limit: int = 20,
) -> Optional[PeerCohort]:
    """Same-level schools in the same NTA (`scope="neighborhood"`) or the
    same district (`scope="district"`) as the given school. Each row
    includes 4 headline metrics for side-by-side comparison; the focal
    school is included with `is_self=true`.

    Use for: "how does this school compare to its neighbors", "what
    other schools serve this neighborhood", "where else in District 15
    could my kid go." District-scope cohorts are most meaningful for
    ES/MS — high schools are city-wide choice and not district-zoned.

    Returns None if the DBN is unknown or the school has no NTA / district
    assigned."""
    return _school_peers(dbn=dbn, scope=scope, limit=limit)


# ----- Dynamic capability discovery (services/metrics.py) -----
#
# Two pairs: list_* enumerates available metrics; get_*_metric returns a
# single value for a specific entity. Distinct school and neighborhood
# universes — they don't share a single dispatcher.

@tool
def list_school_metrics() -> list[SchoolMetricDef]:
    """Every per-school metric this server can answer. Returns id, label,
    unit, applicable school levels, source-table provenance, and vintage
    note. The id is what you pass to `get_school_metric`.

    Use this when the user's question is open-ended or when none of the
    curated tools (`top_schools`, `bulk_metrics`, `get_school`, the
    SWD / staffing / co-location tools) is the obvious fit. The curated
    tools are workflow-shaped shortcuts for common patterns; this is the
    escape hatch for arbitrary single-metric, single-school lookups.

    Discovery is also how new metrics surface. When a column lands in
    the upstream NYSED / DOE data and gets a registry entry, it appears
    here without an MCP code change — so agents that drive off
    `list_school_metrics` adapt automatically."""
    return _list_school_metrics()


@tool
def list_neighborhood_metrics() -> list[NeighborhoodMetricDef]:
    """Every per-neighborhood (NTA) metric this server can answer. Each
    entry is an aggregation over the schools in an NTA and names its
    `underlying_school_metric` — that's the per-school field the
    aggregation rolls up. Returns id, label, unit, applicable school
    levels, aggregation method, and vintage note.

    Use this when the user is asking about a neighborhood-level pattern
    and `top_neighborhoods` doesn't fit (e.g. "give me the value for
    Park Slope", not "rank all NTAs"). The id you get back goes to
    `get_neighborhood_metric`."""
    return _list_neighborhood_metrics()


@tool
def get_school_metric(dbn: str, metric: str) -> Optional[SchoolMetricValue]:
    """Look up one metric for one school by DBN. Returns the value plus
    label, unit, and vintage note. Discovery counterpart to
    `list_school_metrics` — pass any id from that list.

    Behavior at the edges:
    - Unknown DBN → returns None.
    - Unknown metric → raises (the agent should have discovered with
      `list_school_metrics` first).
    - Metric doesn't apply to the school's level (e.g. Regents on an
      elementary school) → returns the record with `value=None` and
      a `note` explaining the level mismatch. The note is the journalism
      output, not an error.
    - Metric applies but the school has no data → `value=None`, `note=None`.
      Distinct from suppression — see `school_swd_outcomes` for the
      SWD-subgroup suppression case."""
    return _get_school_metric(dbn=dbn, metric=metric)


@tool
def get_neighborhood_metric(
    nta: str,
    metric: str,
    level: Optional[SchoolLevel] = None,
) -> Optional[NeighborhoodMetricValue]:
    """Aggregated value of one metric for one NTA (fuzzy-matched). Returns
    the value plus the number of schools contributing, the canonical NTA
    name, and other NTAs that scored well on the fuzzy match — same
    disambiguation surface as `schools_in_neighborhood` and
    `get_neighborhood`. Discovery counterpart to `list_neighborhood_metrics`
    — pass any id from that list.

    Use this for direct lookups ("what's chronic absenteeism in Park
    Slope's elementary schools?") rather than ranking — `top_neighborhoods`
    is the right tool for "which NTAs have the highest X."

    `level` filters the underlying school set: pass "elementary" to
    aggregate only across the NTA's elementary schools, etc. If the NTA
    has no schools at that level, `value=None` and `n_schools=0`."""
    return _get_neighborhood_metric(nta=nta, metric=metric, level=level)
