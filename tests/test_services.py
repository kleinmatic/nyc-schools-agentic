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
