"""School data-access functions. Transport-agnostic: take primitives, return
Pydantic models. These are the operations every surface (HTML, JSON, MCP,
A2A, ACP) shares.
"""
import re
from typing import Optional

from .. import config  # noqa: F401  -- must precede nycschools import (sets data-dir env var)

import pandas as pd

from nycschools import schools as ns_schools

from .. import data
from .models import DemographicsYear, SchoolDetail, SchoolSummary

DBN_RE = re.compile(r"^\d{0,2}[MXKQR]\d{1,4}$", re.IGNORECASE)


def _opt_int(v) -> Optional[int]:
    if v is None or pd.isna(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _opt_float(v) -> Optional[float]:
    if v is None or pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_str(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _to_summary(row) -> SchoolSummary:
    return SchoolSummary(
        dbn=row["dbn"],
        school_name=row["school_name"],
        short_name=_opt_str(row.get("short_name")),
        district=_opt_int(row.get("district")),
        boro=_opt_str(row.get("boro")),
        school_level=_opt_str(row.get("school_level")),
        total_enrollment=_opt_int(row.get("total_enrollment")),
        zip=_opt_str(row.get("zip")),
    )


def search_schools(query: str, limit: int = 10) -> list[SchoolSummary]:
    """Find schools by name, short name, or partial DBN.

    Latest academic year only — one row per school.
    """
    q = (query or "").strip()
    if not q:
        return []

    df = data.get_store().demographics
    latest = df[df["ay"] == df["ay"].max()]

    if DBN_RE.match(q):
        results = latest[latest["dbn"].str.contains(q, case=False, na=False)]
    else:
        results = ns_schools.search(latest, q)

    return [_to_summary(row) for _, row in results.head(limit).iterrows()]


def get_school(dbn: str) -> Optional[SchoolDetail]:
    """Return the full report card for a DBN, or None if not found."""
    df = data.get_store().demographics
    rows = df[df["dbn"] == dbn]
    if rows.empty:
        return None

    rows = rows.sort_values("ay")
    latest_row = rows.iloc[-1]
    summary = _to_summary(latest_row)

    history = [
        DemographicsYear(
            ay=_opt_int(row["ay"]) or 0,
            total_enrollment=_opt_int(row.get("total_enrollment")),
            poverty_pct=_opt_float(row.get("poverty_pct")),
            eni=_opt_float(row.get("eni")),
            ell_pct=_opt_float(row.get("ell_pct")),
            swd_pct=_opt_float(row.get("swd_pct")),
            asian_pct=_opt_float(row.get("asian_pct")),
            black_pct=_opt_float(row.get("black_pct")),
            hispanic_pct=_opt_float(row.get("hispanic_pct")),
            white_pct=_opt_float(row.get("white_pct")),
        )
        for _, row in rows.iterrows()
    ]

    return SchoolDetail(summary=summary, demographics_by_year=history)
