"""Address-based school search.

Two pieces:

1. `geocode(address)` calls NYC Planning Labs' free GeoSearch API to turn
   a street address into lat/lon plus some context fields (borough, BBL).
2. `find_zoned_schools(lat, lon)` runs a point-in-polygon test against the
   ES + MS zone GeoDataFrames in `app.data` and returns the schools whose
   zones contain that point. Some zone polygons serve multiple DBNs
   (comma-separated in the source data); we split on commas.
"""
import asyncio
import re
import time
from collections import OrderedDict
from typing import Optional

import httpx
import pandas as pd
from shapely.geometry import Point

from .. import config  # noqa: F401  -- keep early so nycschools imports work
from .. import data
from .models import GeocodingResult, ZonedSchoolMatch, ZonedSearchResult


GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
# Per-ATTEMPT timeout, not per-call: with GEOCODE_ATTEMPTS below, worst-case
# wall time is roughly GEOCODE_ATTEMPTS * HTTP_TIMEOUT + the backoff sum.
HTTP_TIMEOUT = 5.0

# GeoSearch is intermittently unreliable: measured 2026-07-29, 5 of 15
# identical requests for a valid address failed (400, 500, and a timeout)
# while the other 10 returned the correct result. Without a retry a single
# upstream blip collapses the whole address chain to "no result" — that's
# the demo's opening step and the address-based MCP tools.
#
# Retrying a 4xx is deliberate and safe here: GeoSearch answers an unknown
# address with 200 + an empty `features` array, so a 4xx is never a
# legitimate "no such address" signal, only upstream misbehavior. A
# definitive no-match (200, empty features) is NOT retried — it's a real
# answer, and it stays cached as before.
GEOCODE_ATTEMPTS = 3
GEOCODE_BACKOFF_S = 0.25
# Cap the address before it's forwarded to GeoSearch and stored as a cache
# key (F4): bounds the outbound request payload and the per-key cache
# memory, and rejects DoS-shaped inputs. Real NYC addresses are «100 chars.
MAX_ADDRESS_LEN = 256

# TTL/LRU cache over geocode results (issue #4): without it every /zoned
# hit and every MCP geocode_address / find_schools_for_address call is an
# uncached outbound request to NYC GeoSearch — an anonymous caller could
# drive unbounded volume through our server IP. Addresses don't move;
# 24h is conservative. Definitive no-match results are cached too (same
# abuse profile); transient API errors are NOT (they should retry).
GEOCODE_CACHE_TTL_S = 24 * 3600
GEOCODE_CACHE_MAX = 4096
_geocode_cache: OrderedDict[str, tuple[float, Optional[GeocodingResult]]] = OrderedDict()


def clear_geocode_cache() -> None:
    """Test hook — respx-mocked tests must not leak entries to each other."""
    _geocode_cache.clear()


async def geocode(address: str) -> Optional[GeocodingResult]:
    """Resolve a street address to lat/lon via NYC GeoSearch. Returns None
    if the address is empty, no features come back, or the API still errors
    after GEOCODE_ATTEMPTS tries (see the retry rationale above)."""
    address = (address or "").strip()
    if not address or len(address) > MAX_ADDRESS_LEN:
        return None
    key = " ".join(address.casefold().split())
    now = time.monotonic()
    hit = _geocode_cache.get(key)
    if hit is not None and now - hit[0] < GEOCODE_CACHE_TTL_S:
        _geocode_cache.move_to_end(key)
        return hit[1]
    body = None
    for attempt in range(GEOCODE_ATTEMPTS):
        try:
            # Client per call on purpose: a module-level AsyncClient binds its
            # connection pool to one event loop, which breaks under the many
            # short-lived loops tests create. The cache absorbs the volume.
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                r = await client.get(GEOSEARCH_URL, params={"text": address, "size": 1})
            r.raise_for_status()
            body = r.json()
            break
        except (httpx.HTTPError, ValueError):
            # Transient upstream failure. Back off and retry; on the last
            # attempt fall through to None *without* caching, so a flaky
            # window doesn't pin a bogus miss for the next 24h.
            if attempt == GEOCODE_ATTEMPTS - 1:
                return None
            await asyncio.sleep(GEOCODE_BACKOFF_S * (2**attempt))
    result = _parse_geosearch(body, address)
    _geocode_cache[key] = (now, result)
    _geocode_cache.move_to_end(key)
    while len(_geocode_cache) > GEOCODE_CACHE_MAX:
        _geocode_cache.popitem(last=False)
    return result


def _parse_geosearch(body: dict, address: str) -> Optional[GeocodingResult]:
    features = body.get("features") or []
    if not features:
        return None
    feat = features[0]
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) != 2:
        return None
    lon, lat = coords
    props = feat.get("properties") or {}
    pad = (props.get("addendum") or {}).get("pad") or {}
    return GeocodingResult(
        label=props.get("label", address),
        lat=float(lat),
        lon=float(lon),
        borough=props.get("borough"),
        bbl=pad.get("bbl"),
    )


def _split_dbns(raw) -> list[str]:
    """Some zones serve multiple DBNs as a comma-joined string; normalize."""
    if raw is None or pd.isna(raw):
        return []
    return [d.strip() for d in str(raw).split(",") if d.strip()]


def _enrich(dbn: str, zone_label: Optional[str]) -> Optional[ZonedSchoolMatch]:
    """Build a ZonedSchoolMatch from our demographics data for a DBN."""
    df = data.get_store().demographics
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        # Zone references a DBN we don't have demographics for (rare —
        # could be a school that opened/closed between data vintages).
        return ZonedSchoolMatch(dbn=dbn, school_name=dbn, zone_label=zone_label)
    r = rows.sort_values("ay").iloc[-1]
    district = r.get("district")
    enroll = r.get("total_enrollment")
    return ZonedSchoolMatch(
        dbn=dbn,
        school_name=str(r.get("school_name", dbn)),
        school_level=_opt_str(r.get("school_level")),
        boro=_opt_str(r.get("boro")),
        district=int(district) if pd.notna(district) else None,
        total_enrollment=int(enroll) if pd.notna(enroll) else None,
        zone_label=zone_label,
    )


def _opt_str(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s or None


def _zoned_matches(zones, point: Point) -> tuple[list[ZonedSchoolMatch], Optional[int]]:
    """Run point-in-polygon, expand multi-DBN zones, return matches + district."""
    if zones.empty:
        return [], None
    hits = zones[zones.geometry.contains(point)]
    if hits.empty:
        return [], None
    matches: list[ZonedSchoolMatch] = []
    district: Optional[int] = None
    for _, zone_row in hits.iterrows():
        sd = zone_row.get("schooldist")
        if district is None and pd.notna(sd):
            try:
                district = int(float(sd))
            except (TypeError, ValueError):
                pass
        zone_label = _opt_str(zone_row.get("label"))
        for dbn in _split_dbns(zone_row.get("dbn")):
            match = _enrich(dbn, zone_label)
            if match:
                matches.append(match)
    return matches, district


_DISTRICT_LABEL_RE = re.compile(r"^D\d+$", re.IGNORECASE)


def _classify_ms_polygons(ms_zones, pt: Point) -> tuple[list[ZonedSchoolMatch], Optional[int], Optional[str]]:
    """MS zoning works differently than ES: an MS polygon labeled "D2"
    is the whole-district-choice fallback, while a polygon labeled
    "297" represents a school-specific zone-priority. The point can
    fall in both at once (school-priority polygons typically nest
    inside the district fallback). We surface only the school-priority
    matches in the result and use the label-pattern split to derive
    `ms_admission_type`."""
    if ms_zones.empty:
        return [], None, None
    hits = ms_zones[ms_zones.geometry.contains(pt)]
    if hits.empty:
        return [], None, None

    school_hits, district_hits = [], []
    for _, zone_row in hits.iterrows():
        label = _opt_str(zone_row.get("label"))
        if label and _DISTRICT_LABEL_RE.match(label):
            district_hits.append(zone_row)
        elif label:
            school_hits.append(zone_row)
        # else: zone with no label — ignore (a few zone polygons in the
        # source file have NaN labels)

    district: Optional[int] = None
    matches: list[ZonedSchoolMatch] = []
    for zone_row in school_hits:
        sd = zone_row.get("schooldist")
        if district is None and pd.notna(sd):
            try:
                district = int(float(sd))
            except (TypeError, ValueError):
                pass
        zone_label = _opt_str(zone_row.get("label"))
        for dbn in _split_dbns(zone_row.get("dbn")):
            match = _enrich(dbn, zone_label)
            if match:
                matches.append(match)
    if district is None:
        for zone_row in district_hits:
            sd = zone_row.get("schooldist")
            if pd.notna(sd):
                try:
                    district = int(float(sd))
                    break
                except (TypeError, ValueError):
                    pass

    if school_hits:
        admission_type = "zone_priority_choice"
    elif district_hits:
        admission_type = "district_choice"
    else:
        admission_type = None
    return matches, district, admission_type


def _ms_admission_note(admission_type: Optional[str],
                      district: Optional[int],
                      matches: list[ZonedSchoolMatch]) -> Optional[str]:
    if admission_type is None or district is None:
        return None
    if admission_type == "zone_priority_choice" and matches:
        names = ", ".join(m.school_name for m in matches[:3])
        return (
            f"District {district} middle school admission is choice-based "
            f"— families rank schools and offers run by priority group. "
            f"This address falls inside the zone-priority polygon for "
            f"{names}, which makes zone-residency one of the listed "
            f"priority tiers at that school (not necessarily the top tier — "
            f"siblings and district-residency typically rank higher; see the "
            f"school's per-program priorities for the exact order). For the "
            f"full D{district} middle-school set with per-school admission "
            f"methods (Open, Screened, Zone Priority, etc.), call "
            f"`schools_in_district({district}, level=\"middle\")`."
        )
    if admission_type == "district_choice":
        return (
            f"District {district} middle school admission is choice-based "
            f"and there is no zone-priority school for this address — the "
            f"only polygon covering this point is the whole-district "
            f"fallback. Families rank schools and offers run by district "
            f"and city priority tiers. For the full D{district} middle-"
            f"school set with per-school admission methods, call "
            f"`schools_in_district({district}, level=\"middle\")`."
        )
    return None


def find_zoned_schools(lat: float, lon: float) -> ZonedSearchResult:
    """Point-in-polygon against ES + MS zone polygons. Returns the list of
    schools whose zones contain (lat, lon).

    ES: returns each zoned school. Districts that have moved to
    choice-based admissions and have no zoning at all (D1, D7) return
    empty `elementary`.

    MS: NYC middle-school admission is district-based **choice**; the
    `middle` list reports the school-specific zone-priority polygons
    that contain the point (numeric `label` zones like "297"), but
    that's a priority tier within a choice process, not a placement.
    `ms_admission_type` ("zone_priority_choice" vs "district_choice")
    + `ms_admission_note` tell the caller how to interpret. Whole-
    district polygons (label like "D2", "D15") count as
    `district_choice` and produce no entries in `middle`."""
    pt = Point(lon, lat)  # GeoJSON convention is (lon, lat)
    store = data.get_store()
    es_matches, es_district = _zoned_matches(store.es_zones, pt)
    ms_matches, ms_district, ms_admission_type = _classify_ms_polygons(store.ms_zones, pt)
    return ZonedSearchResult(
        elementary=es_matches,
        middle=ms_matches,
        es_district=es_district,
        ms_district=ms_district,
        ms_admission_type=ms_admission_type,
        ms_admission_note=_ms_admission_note(ms_admission_type, ms_district, ms_matches),
    )
