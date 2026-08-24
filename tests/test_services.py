"""Smoke tests for the service layer. Hits real cached data."""
import httpx
import pytest
import respx

from app.services.schools import (
    co_located_schools,
    get_school,
    school_staffing,
    school_swd_outcomes,
    search_schools,
)
from app.services.zoning import GEOSEARCH_URL, find_zoned_schools, geocode


def test_search_by_short_name_finds_ps_321():
    results = search_schools("PS 321")
    assert any(s.dbn == "15K321" for s in results)


def test_oversized_search_query_returns_empty_without_fuzzy_scan():
    """F4 input cap: a DoS-shaped query is rejected before the rapidfuzz
    scan over ~1,800 names, instead of burning CPU proportional to its
    length."""
    assert search_schools("x" * 5000) == []


def test_oversized_neighborhood_query_returns_none():
    """Same cap on the NTA fuzzy match (get_neighborhood → _fuzzy_match_ntas)."""
    from app.services.analytics import get_neighborhood
    assert get_neighborhood("x" * 5000) is None


def test_oversized_address_short_circuits_before_geocode():
    """F4: an over-length address returns None WITHOUT an outbound GeoSearch
    call. No respx mock is installed, so any real call would raise — reaching
    None proves the length guard short-circuits before the network."""
    import asyncio
    assert asyncio.run(geocode("x" * 5000)) is None


def test_search_handles_periods_in_ps_abbreviation():
    """The school's canonical name is 'P.S. 321 William Penn' (with
    periods); a user typing the more common abbreviation 'PS 321' should
    still find it as the top result."""
    results = search_schools("PS 321")
    assert results[0].dbn == "15K321", f"got {results[0].dbn}"
    # And the period form should also work — both normalize the same way.
    results = search_schools("P.S. 321")
    assert results[0].dbn == "15K321"


def test_search_strips_leading_zeros_from_school_numbers():
    """NYC names canonically zero-pad ('P.S. 039 Henry Bristow') but
    users typing from memory usually drop the zero ('PS 39'). Same
    school, either way."""
    results = search_schools("PS 39")
    dbns = [s.dbn for s in results]
    # 15K039 is P.S. 039 Henry Bristow — should be findable.
    assert "15K039" in dbns
    # Extra leading zeros should also work.
    results = search_schools("PS 0321")
    assert results[0].dbn == "15K321"


def test_search_by_name_finds_midwood():
    results = search_schools("Midwood High School")
    assert any(s.dbn == "22K405" for s in results)


def test_search_by_partial_dbn_finds_midwood():
    results = search_schools("K405")
    assert any(s.dbn == "22K405" for s in results)


def test_search_finds_laguardia_hs_alongside_namesake_elementary():
    """Regression: 'Fiorello LaGuardia' used to short-circuit to PS 205
    (the elementary school whose clean_name is exactly 'fiorello laguardia')
    and never return the famous LaGuardia HS. Both should appear now."""
    results = search_schools("Fiorello LaGuardia")
    dbns = [s.dbn for s in results]
    assert "10X205" in dbns, "PS 205 (the namesake elementary) should match"
    assert "03M485" in dbns, "Fiorello H. LaGuardia HS should match"


def test_search_finds_bronx_science_with_non_contiguous_tokens():
    """Regression: 'Bronx Science' couldn't find 'The Bronx High School of
    Science' with partial_ratio alone (78), since the query tokens are
    non-contiguous in the target. token_set_ratio fixes it."""
    results = search_schools("Bronx Science")
    assert results, "Bronx Science should return results"
    assert results[0].dbn == "10X445", (
        f"Bronx HS of Science should rank first; got {results[0].dbn}"
    )


def test_search_partial_name_finds_full_school():
    """'art and design' should find 'Art and Design High School' even
    though the query is shorter than the target."""
    results = search_schools("art and design")
    assert any(s.dbn == "02M630" for s in results)


def test_search_stuyvesant_ranks_real_stuy_above_namesakes():
    """When tokens match multiple schools at the same primary score,
    the tie-breaker (full-string ratio, length-sensitive) should rank
    Stuyvesant HS above Bedford Stuyvesant Charter."""
    results = search_schools("Stuyvesant")
    assert results[0].dbn == "02M475", (
        f"Stuyvesant HS should rank first; got {results[0].dbn}"
    )


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


def test_regents_pcts_are_fractions():
    """The InfoHub Regents export is 0-100; the service boundary must
    convert to 0-1 fractions like every other pct in the API. Regression
    pin for the '9902.9%' school-page table bug."""
    detail = get_school("22K405")
    assert detail is not None
    scored = [r for r in detail.regents if r.pct_above_64 is not None]
    assert scored, "expected at least one scored Regents row"
    for r in scored:
        assert 0.0 <= r.pct_above_64 <= 1.0
        if r.pct_above_79 is not None:
            assert 0.0 <= r.pct_above_79 <= 1.0
        if r.pct_below_65 is not None:
            assert 0.0 <= r.pct_below_65 <= 1.0


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


def test_school_includes_nysed_essa_status():
    detail = get_school("15K321")
    assert detail is not None and detail.nysed is not None
    statuses = detail.nysed.essa_status
    assert len(statuses) >= 1
    # Each status row has a year and a non-empty status text.
    for s in statuses:
        assert s.year >= 2024
        assert s.overall_status


def test_school_includes_nysed_chronic_absenteeism():
    detail = get_school("15K321")
    assert detail is not None and detail.nysed is not None
    rows = detail.nysed.chronic_absenteeism
    assert any(r.subgroup == "All Students" for r in rows)
    # Rates are stored as 0-1 fractions, not 0-100 units.
    rates = [r.absent_rate for r in rows if r.absent_rate is not None]
    assert all(0 <= r <= 1 for r in rates)


def test_school_includes_nysed_expenditures():
    detail = get_school("15K321")
    assert detail is not None and detail.nysed is not None
    exps = detail.nysed.expenditures
    assert exps, "expected at least one expenditure year for PS 321"
    e = exps[-1]
    # Sanity-check: NYC public school per-pupil totals are ~$15-50k.
    assert e.per_pupil_combined and 5_000 < e.per_pupil_combined < 100_000


def test_high_school_includes_grad_rate_and_cccr():
    detail = get_school("22K405")  # Midwood
    assert detail is not None and detail.nysed is not None
    grad_4yr_all = next(
        (r.grad_rate for r in detail.nysed.hs_graduation
         if r.year == 2025 and r.subgroup == "All Students" and r.cohort == "4-Year"),
        None,
    )
    assert grad_4yr_all and 0.5 < grad_4yr_all <= 1.0
    cccr_all = next(
        ((r.index_score, r.level) for r in detail.nysed.hs_cccr
         if r.year == 2025 and r.subgroup == "All Students"),
        None,
    )
    assert cccr_all is not None
    score, level = cccr_all
    assert score and score > 50
    assert level in (1, 2, 3, 4)


def test_peer_rank_eni_for_low_need_school():
    """PS 321 in Park Slope has unusually low ENI (~0.07) — should rank
    near the bottom (high rank number) among elementary schools."""
    detail = get_school("15K321")
    assert detail is not None
    pr = detail.peer_ranks.get("eni")
    assert pr is not None
    assert pr.metric_label == "Economic Need Index"
    assert "elementary" in pr.cohort_label
    assert pr.total > 100  # should be ~1000 elementary schools
    assert pr.rank > pr.total * 0.7, (
        f"PS 321 should rank in the bottom 30% by ENI, got rank "
        f"{pr.rank} of {pr.total}"
    )
    # Extreme info has dbns we can navigate to.
    assert pr.extreme_high and pr.extreme_high.dbn
    assert pr.extreme_low and pr.extreme_low.dbn


def test_peer_rank_invariants():
    """Every peer rank entry should satisfy: total >= 2, 1 <= rank <= total,
    and (when present) extreme_high.dbn / extreme_low.dbn are non-empty."""
    for dbn in ["15K321", "22K405", "02M475"]:
        detail = get_school(dbn)
        if not detail:
            continue
        for key, pr in detail.peer_ranks.items():
            assert pr.total >= 2, f"{dbn}/{key}: total {pr.total} < 2"
            assert 1 <= pr.rank <= pr.total, f"{dbn}/{key}: rank {pr.rank} out of bounds"
            if pr.extreme_high:
                assert pr.extreme_high.dbn, f"{dbn}/{key}: empty extreme_high.dbn"
            if pr.extreme_low:
                assert pr.extreme_low.dbn, f"{dbn}/{key}: empty extreme_low.dbn"


def test_stuyvesant_picked_up_from_manhattan_beds_prefix():
    """Regression: BEDS prefix '31' (Manhattan) must be considered NYC, not just '33'."""
    detail = get_school("02M475")
    assert detail is not None and detail.nysed is not None
    assert detail.nysed.essa_status, "Stuyvesant must have NYSED data"


def test_middle_or_high_school_has_shsat_or_empty():
    # SHSAT data is only meaningful for middle schools (8th-graders).
    # PS 321 is elementary — likely no SHSAT data, but if present the model
    # should validate.
    detail = get_school("15K321")
    assert detail is not None
    for r in detail.shsat:
        assert r.ay > 2000


def test_is_d75_flag_derives_from_district_75():
    """75K004 is a D75 school; 15K462 is not."""
    d75 = get_school("75K004")
    assert d75 is not None and d75.summary.is_d75 is True
    assert d75.summary.district == 75

    not_d75 = get_school("15K462")
    assert not_d75 is not None and not_d75.summary.is_d75 is False


def test_school_swd_outcomes_returns_none_for_unknown_dbn():
    assert school_swd_outcomes("99Z999") is None


def test_school_swd_outcomes_for_hs_includes_subgroup_metrics():
    """A regular HS with substantive SWD enrollment should have grad +
    chronic + ESSA SWD rows; small-cohort cells may be suppressed."""
    out = school_swd_outcomes("15K462")
    assert out is not None
    assert out.is_d75 is False
    assert out.swd_enrollment_pct is not None and 0 <= out.swd_enrollment_pct <= 1
    assert out.graduation, "expected grad cohorts for an HS"
    # 4-Year should come first in the ordered cohort list.
    assert out.graduation[0].cohort == "4-Year"
    assert out.chronic_absenteeism is not None
    assert out.essa_status is not None
    # The "SWD lumps all IEPs" caveat is always emitted.
    assert any("speech-only" in n for n in out.notes)


def test_school_swd_outcomes_marks_suppressed_cells():
    """Stuyvesant has a tiny SWD cohort; the 4-Year grad cell is redacted
    by NYSED but the cohort_count survives — that's the suppressed=True
    contract."""
    out = school_swd_outcomes("02M475")
    assert out is not None
    grad4 = next((g for g in out.graduation if g.cohort == "4-Year"), None)
    assert grad4 is not None
    # Either the rate is present, or it's suppressed with N still reported.
    if grad4.grad_rate is None:
        assert grad4.suppressed is True
    # Suppression note is appended once any cell is suppressed.
    if any(g.suppressed for g in out.graduation):
        assert any("suppresses cells" in n for n in out.notes)


def test_school_swd_outcomes_for_d75_emits_placement_caveat():
    """D75 schools get the placement-system caveat unconditionally,
    regardless of whether NYSED reports outcomes."""
    out = school_swd_outcomes("75K004")
    assert out is not None
    assert out.is_d75 is True
    assert any("District 75" in n for n in out.notes)


def test_swd_cohort_context_present_for_chronic_absent():
    """Most ES/MS/HS schools with a non-suppressed SWD chronic-absent value
    pick up a cohort_context entry. Sanity-check rank bounds + that the
    extremes carry both a name and a DBN."""
    out = school_swd_outcomes("15K321")  # PS 321 — well-attended ES
    assert out is not None
    ctx = out.cohort_context.get("swd_chronic_absent_rate")
    assert ctx is not None
    assert ctx.higher_is_better is False
    assert 1 <= ctx.rank <= ctx.cohort_size
    assert ctx.cohort_size >= 50  # plenty of NYC ES with SWD chronic data
    assert 0 <= ctx.value <= 1
    assert 0 <= ctx.cohort_median <= 1
    assert ctx.extreme_high and ctx.extreme_high.dbn and ctx.extreme_high.school_name
    assert ctx.extreme_low and ctx.extreme_low.dbn and ctx.extreme_low.school_name
    # narrative is the journalism layer — should be quotable.
    assert "quartile" in ctx.narrative or "median" in ctx.narrative


def test_swd_cohort_context_narrative_uses_neutral_journalistic_language():
    """Narratives describe POSITION in a distribution, not a verdict.
    Conclusory words ("best", "worst", "better", "worse") are banned
    — house style. Use highest/lowest/above/below instead."""
    banned = {"best", "worst", "better", "worse"}
    # Spot-check across schools at multiple levels and outcome shapes.
    for dbn in ("15K321", "15K462", "02M475", "02M033"):
        out = school_swd_outcomes(dbn)
        if not out:
            continue
        for ctx in out.cohort_context.values():
            words = set(ctx.narrative.lower().split())
            collisions = words & banned
            assert not collisions, (
                f"{dbn}/{ctx.metric}: narrative contains conclusory word(s) "
                f"{collisions}: {ctx.narrative!r}"
            )


def test_swd_cohort_context_absent_for_school_without_data():
    """D75 / very-small / not-reported schools come back with empty
    cohort_context, not crashing or fabricating ranks."""
    out = school_swd_outcomes("75K004")  # D75 school, no NYSED SWD data
    assert out is not None
    assert out.cohort_context == {}


# ----- Demo path: pins the address→zoned→SWD→co-located→staffing chain -----
#
# Scott uses this exact narrative in every demo: an agent asks what school
# 428 W 26th St, Manhattan is zoned for, gets PS 33 Chelsea Prep, then asks
# about services for a child with an emotional regulation disorder, and the
# answer surfaces the co-located D75 school (PS 138, 75M138) in the same
# building plus the school's counseling staffing. If any leg of this chain
# silently changes shape, the demo breaks. The whole flow is one test on
# purpose — the value is the chain, not the individual asserts.
@respx.mock
async def test_demo_chain_428_w_26_st_to_chelsea_prep_to_d75_neighbor():
    # PS 33 Chelsea Prep's own coordinates — guaranteed to be inside its
    # own ES attendance-zone polygon.
    ps33_lat, ps33_lon = 40.7490139997319, -74.00023799963121
    respx.get(GEOSEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"coordinates": [ps33_lon, ps33_lat]},
                        "properties": {
                            "label": "428 W 26 STREET, Manhattan, NY, USA",
                            "borough": "Manhattan",
                        },
                    }
                ]
            },
        )
    )

    geo = await geocode("428 W 26th St, Manhattan")
    assert geo is not None
    assert geo.borough == "Manhattan"

    zoned = find_zoned_schools(geo.lat, geo.lon)
    assert "02M033" in [s.dbn for s in zoned.elementary], (
        f"428 W 26th St should be zoned for PS 33 Chelsea Prep (02M033); "
        f"got ES={[s.dbn for s in zoned.elementary]}"
    )
    assert zoned.es_district == 2

    # MS admission context — the agentic-newsroom Data Tribune demo
    # relies on the publisher reading these to explain that MS 297 is
    # a zone-priority signal within district choice, not a placement.
    assert zoned.ms_district == 2
    assert zoned.ms_admission_type == "zone_priority_choice", (
        f"428 W 26th St falls in M.S. 297's school-priority polygon; "
        f"expected zone_priority_choice, got {zoned.ms_admission_type!r}"
    )
    assert "02M297" in [s.dbn for s in zoned.middle]
    assert zoned.ms_admission_note is not None
    assert "choice" in zoned.ms_admission_note.lower()
    assert "schools_in_district(2" in zoned.ms_admission_note

    swd = school_swd_outcomes("02M033")
    assert swd is not None and swd.is_d75 is False
    assert swd.swd_enrollment_pct is not None and swd.swd_enrollment_pct > 0
    # Cohort context is what makes the answer journalism instead of a
    # number — every SWD stat must arrive paired with where the school
    # sits among NYC ES peers on the SWD subgroup.
    assert swd.cohort_context, "SWD outcomes must carry cohort context for the demo"

    co = co_located_schools("02M033")
    co_dbns = [c.dbn for c in co]
    assert "75M138" in co_dbns, (
        f"PS 33 shares a building with the D75 school 75M138 — the demo "
        f"relies on this surfacing; got {co_dbns}"
    )

    staffing = school_staffing("02M033")
    assert staffing is not None
    assert staffing.total_gc is not None
    assert staffing.total_sw is not None


# ----- MS admission context (zoning + schools_in_district) -----
#
# The MS Directory + zone-polygon-label work landed for the agentic-
# newsroom Data Tribune demo. NYC middle-school admission is district-
# based choice, not strict zoning, and the polygon labels themselves
# carry the zoned-vs-choice signal (numeric label = school-priority
# polygon, `D{n}` label = whole-district fallback). These tests pin the
# classification + the schools_in_district answer.

# Centroid of D15 polygon labeled '62' (New Voices MS / 20K062).
_D15_INSIDE_62_LAT, _D15_INSIDE_62_LON = 40.6477, -73.9744
# Centroid of the D15 fallback polygon — a point inside D15 but NOT
# inside any school-specific zone polygon.
_D15_FALLBACK_LAT, _D15_FALLBACK_LON = 40.6693, -73.9970


def test_zoning_zone_priority_choice_when_school_specific_polygon_contains_point():
    """A point inside a numeric-label MS polygon (e.g. '62') is a zone-
    priority signal — the school is reported in `middle` and
    `ms_admission_type` is `zone_priority_choice`."""
    r = find_zoned_schools(_D15_INSIDE_62_LAT, _D15_INSIDE_62_LON)
    assert r.ms_district == 15
    assert r.ms_admission_type == "zone_priority_choice"
    dbns = [s.dbn for s in r.middle]
    assert "20K062" in dbns, f"expected New Voices (20K062) in middle; got {dbns}"
    assert r.ms_admission_note is not None
    assert "schools_in_district(15" in r.ms_admission_note


def test_zoning_district_choice_when_only_whole_district_polygon_contains_point():
    """A point inside only the D15 fallback polygon (label like 'D15')
    is a district-choice signal — `middle` is empty and the note tells
    the caller to use `schools_in_district`."""
    r = find_zoned_schools(_D15_FALLBACK_LAT, _D15_FALLBACK_LON)
    assert r.ms_district == 15
    assert r.ms_admission_type == "district_choice"
    assert r.middle == []
    assert r.ms_admission_note is not None
    assert "no zone-priority school" in r.ms_admission_note


def test_schools_in_district_middle_returns_full_directory_with_methods():
    """D2 middle schools per the Fall 2025 MS Directory: 23 schools, MS
    297 carries both Screened and Zone Priority programs (the demo
    nuance). Admission overview names district-choice mechanics."""
    from app.services.analytics import schools_in_district
    r = schools_in_district(2, "middle")
    assert r is not None
    assert r.district == 2
    assert r.level == "middle"
    assert r.n_schools == 23, f"D2 MS directory should have 23 schools; got {r.n_schools}"
    assert r.admission_overview is not None
    assert "choice" in r.admission_overview.lower()

    by_dbn = {s.dbn: s for s in r.schools}
    assert "02M297" in by_dbn
    ms297 = by_dbn["02M297"]
    assert set(ms297.admission_methods) == {"Screened", "Zone Priority"}, (
        f"M.S. 297 should carry both methods; got {ms297.admission_methods}"
    )
    # Two programs, each with non-empty priorities cascade.
    assert len(ms297.ms_programs) == 2
    for p in ms297.ms_programs:
        assert p.admission_method in {"Screened", "Zone Priority"}
        assert p.priorities, f"program {p.program_index} has no priorities"

    # The Open / Screened claim in earlier draft narratives — pin the
    # ground truth from the directory so future regressions can't drift.
    assert by_dbn["02M255"].admission_methods == ["Open"]  # Salk
    assert by_dbn["02M114"].admission_methods == ["Open"]  # East Side
    # NYC Lab carries an ASD/ACES inclusion program in addition to Open.
    assert set(by_dbn["02M312"].admission_methods) == {"Open", "ASD/ACES Program"}


def test_schools_in_district_high_explains_citywide_choice():
    """HS is city-wide choice in NYC. The district grouping is geographic
    only, and the overview should make that explicit so a downstream
    agent doesn't tell the user "rank these N schools" as if HS works
    like MS."""
    from app.services.analytics import schools_in_district
    r = schools_in_district(2, "high")
    assert r is not None
    assert r.level == "high"
    assert r.n_schools > 0
    assert r.admission_overview is not None
    assert "city-wide" in r.admission_overview.lower()
    # HS schools carry no MS-style admission methods.
    assert all(s.admission_methods == [] for s in r.schools)
    assert all(s.ms_programs == [] for s in r.schools)


def test_schools_in_district_unknown_level_returns_none():
    from app.services.analytics import schools_in_district
    assert schools_in_district(2, "kindergarten") is None
    assert schools_in_district(2, "K-8") is None
    assert schools_in_district(2, "") is None


def test_schools_in_district_unknown_district_at_middle_returns_none():
    """D99 doesn't exist; the function should return None rather than an
    empty result (the caller can tell apart 'no such district' from
    'district has no schools at this level')."""
    from app.services.analytics import schools_in_district
    assert schools_in_district(99, "middle") is None


# ----- numbered-school query tolerance -----
#
# Most NYC schools are known by a level prefix and a number, and users
# type both loosely: spacing, zero-padding and periods vary, and the
# level prefixes (I.S. / M.S. / J.H.S.) are used interchangeably in
# speech regardless of what the DOE calls the school.

@pytest.mark.parametrize(
    "query",
    ["P.S. 30", "PS 30", "PS 030", "P.S. 030", "ps30", "PS30", "p.s.030"],
)
def test_ps_30_spelling_variants_all_find_the_same_schools(query):
    """Every way of writing 'P.S. 30' returns the P.S. 030 schools. The
    glued form ('ps30') used to return nothing at all — clean_name left
    the prefix stuck to the number, so it matched no school name."""
    dbns = [s.dbn for s in search_schools(query, limit=5)]
    assert "07X030" in dbns, f"{query!r} lost P.S. 030 Wilton: {dbns}"


@pytest.mark.parametrize("query", ["IS 123", "MS 123", "JHS 123", "I.S. 123", "M.S. 123"])
def test_middle_school_prefixes_are_interchangeable(query):
    """J.H.S. 123 James M. Kieran is what the DOE calls 08X123, but a
    parent will say 'IS 123' or 'MS 123'. All three spellings must rank
    it first. Before the number boost, 'IS 123' returned I.S. 232 and
    I.S. 237 — schools matching the PREFIX text — and dropped the school
    the user actually asked for out of the results entirely."""
    results = search_schools(query, limit=5)
    assert results, f"{query!r} returned nothing"
    assert results[0].dbn == "08X123", (
        f"{query!r} should rank J.H.S. 123 first, got "
        f"{[(s.dbn, s.school_name) for s in results[:3]]}"
    )


def test_number_outranks_prefix_text_similarity():
    """The number is the high-signal token. A school whose NUMBER matches
    must outrank one that merely shares the prefix and fuzzy-matches the
    digits (123 vs 232 / 237)."""
    dbns = [s.dbn for s in search_schools("IS 123", limit=5)]
    assert dbns.index("08X123") == 0
    for wrong_number in ("09X232", "25Q237"):
        assert wrong_number not in dbns[:1]


def test_double_prefix_schools_match_either_prefix():
    """'P.S./I.S. 187 Hudson Cliffs' claims both levels. clean_name
    collapses that to 'psis 187', which is neither prefix — so the class
    check reads the raw name."""
    from app.services.schools import _name_prefix_classes
    assert _name_prefix_classes("P.S./I.S. 187 Hudson Cliffs") == {"elementary", "middle"}
    # Backslash separator appears in the real data too.
    assert "middle" in _name_prefix_classes("The Christa McAuliffe School\\I.S. 187")


def test_prefix_detection_does_not_fire_on_ordinary_words():
    """The prefix pattern is guarded on both sides — 'High School' must
    not read as an 'H.S.' prefix, or every school in the city claims the
    high-school class."""
    from app.services.schools import _name_prefix_classes
    assert _name_prefix_classes("Brooklyn Technical High School") == set()
    assert _name_prefix_classes("Stuyvesant High School") == set()


def test_named_schools_still_rank_correctly():
    """Regression guard for the boosts: queries with no number must be
    untouched, and the borough-word-in-the-name cases must survive."""
    assert search_schools("Bronx Science")[0].dbn == "10X445"
    assert search_schools("Brooklyn Tech")[0].dbn == "13K430"
    # Manhattan Beach is a Brooklyn school whose name contains a borough.
    assert search_schools("Manhattan Beach")[0].dbn == "22K195"
    assert search_schools("Stuyvesant")[0].dbn == "02M475"


def test_numbered_query_with_borough_keeps_the_right_school():
    """'PS 321 Brooklyn' used to rank P.S. 131 Brooklyn, P.S. 326 and
    P.S. 36 — the borough word was matched against school NAMES, and
    P.S. 321 fell out of the top 10. The number boost restores it.

    Note the borough word is still not used as a borough SIGNAL; see
    test_borough_word_is_not_yet_a_borough_filter."""
    results = search_schools("PS 321 Brooklyn", limit=5)
    assert results[0].dbn == "15K321", (
        f"got {[(s.dbn, s.school_name) for s in results[:3]]}"
    )


def test_borough_word_is_not_yet_a_borough_filter():
    """Documents what is still broken, so the next fix has a failing
    baseline to move.

    'PS 33 Manhattan' now returns P.S. 033 schools (the number boost
    works), but ranks them in an arbitrary borough order — 10X033 is in
    the Bronx. Making the borough token an actual filter is the open
    piece; whoever does it must keep test_named_schools_still_rank_correctly
    passing, since Bronx Science / Brooklyn Tech / Manhattan Beach all
    carry a borough word as part of the real name."""
    results = search_schools("PS 33 Manhattan", limit=5)
    dbns = [s.dbn for s in results]
    assert "02M033" in dbns, "the Manhattan P.S. 033 should at least be present"
    assert results[0].dbn != "02M033", (
        "borough ranking appears fixed — delete this test and tighten "
        "test_numbered_query_with_borough_keeps_the_right_school instead"
    )
