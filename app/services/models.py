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


class SnapshotInfo(BaseModel):
    """DOE official school snapshot — pulled from a single point in time
    (whatever was most recent in the upstream cache)."""
    ay: Optional[int] = None
    address: Optional[str] = None
    city_state_zip: Optional[str] = None
    principal_name: Optional[str] = None
    principal_phone: Optional[str] = None
    principal_years: Optional[float] = None
    attendance_rate: Optional[float] = None
    student_chronic_absent: Optional[str] = None
    teacher_3yr_exp_pct: Optional[float] = None
    quality_review_year: Optional[int] = None
    quality_review_url: Optional[str] = None
    es_admissions: Optional[str] = None
    ms_admissions: Optional[str] = None
    co_located: Optional[str] = None
    co_located_n: Optional[int] = None
    website: Optional[str] = None
    grades_text: Optional[str] = None
    dates_of_review: Optional[str] = None


class LocationInfo(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    nta_name: Optional[str] = None
    council_district: Optional[int] = None
    community_district: Optional[int] = None
    census_tract: Optional[float] = None
    open_year: Optional[int] = None
    grades_text: Optional[str] = None
    status: Optional[str] = None
    location_category: Optional[str] = None
    managed_by: Optional[str] = None


class ExamRow(BaseModel):
    """One year × grade slice of a 3-8 state exam (ELA or math), All Students."""
    ay: int
    grade: str
    number_tested: Optional[int] = None
    mean_scale_score: Optional[float] = None
    pct_level_1: Optional[float] = None
    pct_level_2: Optional[float] = None
    pct_level_3: Optional[float] = None
    pct_level_4: Optional[float] = None
    pct_proficient: Optional[float] = None  # level_3 + level_4


class ClassSizeRow(BaseModel):
    grade: str
    program_type: Optional[str] = None
    subject: Optional[str] = None
    students_n: Optional[int] = None
    classes_n: Optional[int] = None
    avg_class_size: Optional[float] = None
    min_class_size: Optional[int] = None
    max_class_size: Optional[int] = None


class PtrInfo(BaseModel):
    ay: int
    ratio: Optional[float] = None


class SchoolDetail(BaseModel):
    summary: SchoolSummary
    demographics_by_year: list[DemographicsYear]
    snapshot: Optional[SnapshotInfo] = None
    location: Optional[LocationInfo] = None
    ela: list[ExamRow] = []
    math: list[ExamRow] = []
    class_size: list[ClassSizeRow] = []
    class_size_year: Optional[int] = None
    ptr: Optional[PtrInfo] = None
