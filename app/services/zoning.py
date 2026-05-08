"""Address-based school search.

Two pieces:

1. `geocode(address)` calls NYC Planning Labs' free GeoSearch API to turn
   a street address into lat/lon plus some context fields (borough, BBL).
2. `find_zoned_schools(lat, lon)` runs a point-in-polygon test against the
   ES + MS zone GeoDataFrames in `app.data` and returns the schools whose
   zones contain that point. Some zone polygons serve multiple DBNs
   (comma-separated in the source data); we split on commas.
"""
from typing import Optional

import httpx
import pandas as pd
from shapely.geometry import Point

from .. import config  # noqa: F401  -- keep early so nycschools imports work
from .. import data
from .models import GeocodingResult, ZonedSchoolMatch, ZonedSearchResult


GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
HTTP_TIMEOUT = 10.0


async def geocode(address: str) -> Optional[GeocodingResult]:
    """Resolve a street address to lat/lon via NYC GeoSearch. Returns None
    if the address is empty, the API errors, or no features come back."""
    address = (address or "").strip()
    if not address:
        return None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(GEOSEARCH_URL, params={"text": address, "size": 1})
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return None
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


def find_zoned_schools(lat: float, lon: float) -> ZonedSearchResult:
    """Point-in-polygon against ES + MS zone polygons. Returns the list of
    schools whose zones contain (lat, lon). Districts that have moved to
    choice-based admissions (D15 for middle school, D1/D7 for elementary)
    will return empty lists for the affected level — by design."""
    pt = Point(lon, lat)  # GeoJSON convention is (lon, lat)
    store = data.get_store()
    es_matches, es_district = _zoned_matches(store.es_zones, pt)
    ms_matches, ms_district = _zoned_matches(store.ms_zones, pt)
    return ZonedSearchResult(
        elementary=es_matches,
        middle=ms_matches,
        es_district=es_district,
        ms_district=ms_district,
    )
