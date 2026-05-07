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


class RegentsRow(BaseModel):
    """One year × exam slice of a Regents exam, All Students."""
    ay: int
    regents_exam: str
    number_tested: Optional[int] = None
    mean_score: Optional[float] = None
    pct_below_65: Optional[float] = None
    pct_above_64: Optional[float] = None
    pct_above_79: Optional[float] = None
    pct_college_ready: Optional[float] = None


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


class ShsatYear(BaseModel):
    """How many of this school's 8th-graders took / got offers for the SHSAT."""
    ay: int
    applicants_n: Optional[int] = None
    testers_n: Optional[int] = None
    offers_n: Optional[int] = None
    offers_pct: Optional[float] = None


class BudgetCategory(BaseModel):
    category: str
    total: float
    positions: Optional[float] = None


class BudgetSummary(BaseModel):
    """Galaxy budget summary for the latest cached year."""
    ay: int
    total: float
    total_positions: Optional[float] = None
    by_category: list[BudgetCategory] = []


class HsProgram(BaseModel):
    """One admissions program at a high school (HS Directory)."""
    code: Optional[str] = None
    name: Optional[str] = None
    interest: Optional[str] = None
    description: Optional[str] = None
    method: Optional[str] = None
    eligibility: Optional[str] = None
    seats_9th: Optional[int] = None
    applicants_9th: Optional[int] = None
    applicants_per_seat_9th: Optional[float] = None
    requirements: list[str] = []
    admissions_priority: Optional[str] = None


class HsDirectoryInfo(BaseModel):
    """A high school's directory entry. Only populated for high schools that
    appear in the academic-year-2021 directory dataset."""
    ay: int
    overview: Optional[str] = None
    total_students: Optional[int] = None
    attendance_rate: Optional[float] = None
    graduation_rate: Optional[float] = None
    college_career_rate: Optional[float] = None
    pct_students_safe: Optional[float] = None
    pct_students_enough_variety: Optional[float] = None
    school_accessibility: Optional[str] = None
    neighborhood: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    sqr_website: Optional[str] = None
    recruitment_website: Optional[str] = None
    subway: Optional[str] = None
    bus: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    advanced_placement_courses: Optional[str] = None
    language_classes: Optional[str] = None
    psal_sports_boys: Optional[str] = None
    psal_sports_girls: Optional[str] = None
    psal_sports_coed: Optional[str] = None
    diploma_endorsements: Optional[str] = None
    diversity_in_admissions: Optional[bool] = None
    diversity_details: Optional[str] = None
    school_10th_seats: Optional[str] = None
    ell_programs: Optional[str] = None
    school_sports: Optional[str] = None
    online_ap_courses: Optional[str] = None
    online_language_courses: Optional[str] = None
    summer_session: Optional[str] = None
    extracurricular_activities: Optional[str] = None
    addtl_info: Optional[str] = None
    grades_served: Optional[str] = None
    campus_name: Optional[str] = None
    building_code: Optional[str] = None
    academic_opportunities: list[str] = []
    programs: list[HsProgram] = []


class SchoolDetail(BaseModel):
    summary: SchoolSummary
    demographics_by_year: list[DemographicsYear]
    snapshot: Optional[SnapshotInfo] = None
    location: Optional[LocationInfo] = None
    ela: list[ExamRow] = []
    math: list[ExamRow] = []
    regents: list[RegentsRow] = []
    class_size: list[ClassSizeRow] = []
    class_size_year: Optional[int] = None
    ptr: Optional[PtrInfo] = None
    shsat: list[ShsatYear] = []
    budget: Optional[BudgetSummary] = None
    hs_directory: Optional[HsDirectoryInfo] = None
