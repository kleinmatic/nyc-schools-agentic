"""Smoke tests for the service layer. Hits real cached data."""
from app.services.schools import get_school, search_schools


def test_search_by_short_name_finds_ps_321():
    results = search_schools("PS 321")
    assert any(s.dbn == "15K321" for s in results)


def test_search_by_name_finds_midwood():
    results = search_schools("Midwood High School")
    assert any(s.dbn == "22K405" for s in results)


def test_search_by_partial_dbn_finds_midwood():
    results = search_schools("K405")
    assert any(s.dbn == "22K405" for s in results)


def test_search_empty_query_returns_empty():
    assert search_schools("") == []
    assert search_schools("   ") == []


def test_search_respects_limit():
    results = search_schools("school", limit=3)
    assert len(results) <= 3


def test_get_school_returns_detail():
    detail = get_school("15K321")
    assert detail is not None
    assert detail.summary.dbn == "15K321"
    assert "321" in detail.summary.school_name
    assert len(detail.demographics_by_year) > 0
    # years are sorted ascending
    years = [y.ay for y in detail.demographics_by_year]
    assert years == sorted(years)


def test_get_school_unknown_returns_none():
    assert get_school("99Z999") is None


def test_summary_fields_typed_correctly():
    detail = get_school("22K405")
    assert detail is not None
    s = detail.summary
    assert s.boro == "Brooklyn"
    assert s.district == 22
    assert s.school_level == "high"


def test_school_detail_includes_snapshot():
    detail = get_school("15K321")
    assert detail is not None
    snap = detail.snapshot
    assert snap is not None
    assert snap.principal_name  # PS 321 has a known principal
    assert snap.address  # has an address
    assert snap.attendance_rate is None or 0 <= snap.attendance_rate <= 1


def test_school_detail_includes_location():
    detail = get_school("15K321")
    assert detail is not None
    loc = detail.location
    assert loc is not None
    # PS 321 is in Park Slope, Brooklyn
    assert loc.latitude is not None and 40.5 < loc.latitude < 41
    assert loc.longitude is not None and -74.5 < loc.longitude < -73.5
    assert loc.nta_name and "Park Slope" in loc.nta_name


def test_school_detail_includes_exam_rows():
    detail = get_school("15K321")
    assert detail is not None
    assert len(detail.ela) > 0
    assert len(detail.math) > 0
    # All rows are All-Students filtered (no demographic categories leaked)
    for row in detail.ela + detail.math:
        # Pydantic models don't carry a "category" field, but we can sanity-check
        # the year + grade + presence of pct_proficient.
        assert row.ay > 2000
        assert row.grade


def test_exam_rows_sorted_year_desc():
    detail = get_school("15K321")
    assert detail is not None
    years = [r.ay for r in detail.ela]
    # Years are non-increasing (year-desc sort)
    assert years == sorted(years, reverse=True)


def test_school_detail_includes_class_size():
    detail = get_school("15K321")
    assert detail is not None
    assert len(detail.class_size) > 0
    assert detail.class_size_year is not None
    # Avg class size should be a sane elementary-school number.
    avgs = [r.avg_class_size for r in detail.class_size if r.avg_class_size is not None]
    assert all(5 < a < 50 for a in avgs)


def test_school_detail_includes_ptr():
    detail = get_school("15K321")
    assert detail is not None
    assert detail.ptr is not None
    assert detail.ptr.ratio is not None and 1 < detail.ptr.ratio < 50


def test_high_school_includes_regents():
    detail = get_school("22K405")  # Midwood, a high school
    assert detail is not None
    assert len(detail.regents) > 0
    # Regents rows are sorted year desc.
    years = [r.ay for r in detail.regents]
    assert years == sorted(years, reverse=True)
    # Spot-check that we got real exam names.
    exams = {r.regents_exam for r in detail.regents}
    assert any("English" in e or "Algebra" in e or "Geometry" in e for e in exams)


def test_high_school_includes_hs_directory():
    detail = get_school("22K405")
    assert detail is not None
    hs = detail.hs_directory
    assert hs is not None
    assert hs.total_students and hs.total_students > 1000
    assert hs.graduation_rate is not None
    assert hs.subway and "Brooklyn College" in hs.subway
    # Programs got reshaped into a list with at least one entry.
    assert len(hs.programs) >= 1
    p = hs.programs[0]
    assert p.name


def test_elementary_school_no_regents_or_hs_dir():
    detail = get_school("15K321")  # PS 321, elementary
    assert detail is not None
    assert detail.regents == []
    assert detail.hs_directory is None


def test_school_detail_includes_budget():
    detail = get_school("15K321")
    assert detail is not None
    b = detail.budget
    assert b is not None
    assert b.total > 1_000_000  # PS 321 has a multi-million-dollar budget
    assert b.by_category, "expected at least one budget category"
    # Largest category ranks first
    totals = [c.total for c in b.by_category]
    assert totals == sorted(totals, reverse=True)


def test_middle_or_high_school_has_shsat_or_empty():
    # SHSAT data is only meaningful for middle schools (8th-graders).
    # PS 321 is elementary — likely no SHSAT data, but if present the model
    # should validate.
    detail = get_school("15K321")
    assert detail is not None
    for r in detail.shsat:
        assert r.ay > 2000
