"""Transport-agnostic Pydantic models. These are the schemas surfaced over
HTML, the JSON API, and (later) MCP/A2A/ACP — define each field once here.
"""
from typing import Optional

from pydantic import BaseModel


class SchoolSummary(BaseModel):
    dbn: str
    school_name: str
    short_name: Optional[str] = None
    district: Optional[int] = None
    boro: Optional[str] = None
    school_level: Optional[str] = None
    total_enrollment: Optional[int] = None
    zip: Optional[str] = None


class DemographicsYear(BaseModel):
    ay: int
    total_enrollment: Optional[int] = None
    poverty_pct: Optional[float] = None
    eni: Optional[float] = None
    ell_pct: Optional[float] = None
    swd_pct: Optional[float] = None
    asian_pct: Optional[float] = None
    black_pct: Optional[float] = None
    hispanic_pct: Optional[float] = None
    white_pct: Optional[float] = None


class SchoolDetail(BaseModel):
    summary: SchoolSummary
    demographics_by_year: list[DemographicsYear]
