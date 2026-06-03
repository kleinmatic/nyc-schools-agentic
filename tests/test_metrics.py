"""Service-layer tests for dynamic capability discovery.

Pairs with app/services/metrics.py. Verifies the registry stays in sync
with analytics._compute_metric (the import-time invariant), that the
discovery surface returns Pydantic models the agent can consume, and
that get_*_metric values match the curated tools' values for the same
inputs — discovery is additive precisely because the numbers are
identical to top_schools / aggregate_by_neighborhood."""

import pytest

from app.services import metrics
from app.services.analytics import (
    METRIC_NAMES,
    aggregate_by_neighborhood,
    top_schools,
)


# ---------- Registry consistency ----------

def test_school_registry_covers_every_compute_metric_path():
    """Import-time guard catches drift, but a test makes the invariant
    visible in the test report when someone tries to add a metric in
    analytics.py without a registry entry."""
    assert set(metrics.SCHOOL_METRICS) == set(METRIC_NAMES), (
        "SCHOOL_METRICS must match analytics.METRIC_NAMES exactly. "
        f"Diff: only-in-registry={set(metrics.SCHOOL_METRICS) - set(METRIC_NAMES)}; "
        f"only-in-analytics={set(METRIC_NAMES) - set(metrics.SCHOOL_METRICS)}"
    )


def test_neighborhood_registry_mirrors_school_registry():
    """Every school metric has a neighborhood projection (mean over the
    NTA's schools). If we ever introduce a school-only metric, this test
    is the reminder to also remove it from NEIGHBORHOOD_METRICS."""
    assert set(metrics.NEIGHBORHOOD_METRICS) == set(metrics.SCHOOL_METRICS)


def test_every_school_metric_has_a_vintage_note():
    """Provenance is part of every discovery response. A blank
    vintage_note would surface as a missing-source byline on the agent's
    final answer."""
    for k, spec in metrics.SCHOOL_METRICS.items():
        assert spec.vintage_note.strip(), f"{k} has empty vintage_note"


# ---------- Discovery ----------

def test_list_school_metrics_returns_thirteen_entries():
    out = metrics.list_school_metrics()
    assert len(out) == 13
    assert out[0].id == "eni", "ENI should lead — it's the headline equity metric"


def test_list_neighborhood_metrics_names_underlying_school_metric():
    out = metrics.list_neighborhood_metrics()
    for entry in out:
        assert entry.underlying_school_metric in metrics.SCHOOL_METRICS
        assert entry.aggregation == "mean"


# ---------- get_school_metric ----------

def test_get_school_metric_matches_top_schools_for_eni_at_ps321():
    """Discovery must return the same number the curated path returns —
    that's what makes the refactor additive instead of behavior-changing.
    PS 321 (Park Slope) is a low-ENI ES, so it shows up early in the
    ascending sort."""
    via_discovery = metrics.get_school_metric("15K321", "eni")
    via_top = {r.dbn: r.value for r in top_schools(
        metric="eni", level="elementary", limit=200, ascending=True,
    )}
    assert "15K321" in via_top, (
        "PS 321 should appear in the 200 lowest-ENI elementary schools; "
        "if not, the test fixture has drifted from the data."
    )
    assert via_discovery is not None
    assert via_discovery.value is not None
    assert abs(via_discovery.value - via_top["15K321"]) < 1e-9


def test_get_school_metric_returns_none_for_unknown_dbn():
    assert metrics.get_school_metric("99Z999", "eni") is None


def test_get_school_metric_raises_for_unknown_metric_with_discovery_hint():
    with pytest.raises(ValueError) as exc:
        metrics.get_school_metric("15K321", "made_up_metric")
    msg = str(exc.value)
    assert "made_up_metric" in msg
    # The error should point the agent at discovery, not just complain.
    assert "list_school_metrics" in msg


def test_get_school_metric_explains_level_mismatch_for_regents_on_elementary():
    """Regents only apply to HS / 6-12. Asking on an ES should return a
    populated record with value=None and a journalism-grade note
    explaining why — not an error, not a silent None."""
    out = metrics.get_school_metric("15K321", "regents_pct_above_64")
    assert out is not None
    assert out.value is None
    assert out.note is not None
    assert "elementary" in out.note.lower()


def test_get_school_metric_carries_provenance():
    out = metrics.get_school_metric("15K321", "eni")
    assert out is not None
    assert out.unit == "pct"
    assert out.vintage_note  # populated


# ---------- get_neighborhood_metric ----------

def test_get_neighborhood_metric_fuzzy_matches_park_slope():
    """The query "Park Slope" should resolve to the canonical NTA name
    "Park Slope-Gowanus" — same fuzzy threshold as schools_in_neighborhood."""
    out = metrics.get_neighborhood_metric("Park Slope", "eni", level="elementary")
    assert out is not None
    assert out.nta_name == "Park Slope-Gowanus"
    assert out.n_schools > 0
    assert out.value is not None and 0.0 <= out.value <= 1.0


def test_get_neighborhood_metric_matches_aggregate_by_neighborhood():
    """Both code paths feed into _aggregate_metric_by_group; the value
    they produce for the same NTA must be identical."""
    via_discovery = metrics.get_neighborhood_metric(
        "Park Slope", "eni", level="elementary"
    )
    via_top = aggregate_by_neighborhood(
        metric="eni", level="elementary", limit=200,
    )
    by_name = {a.name: a.value for a in via_top}
    assert via_discovery is not None
    assert via_discovery.value is not None
    assert abs(via_discovery.value - by_name["Park Slope-Gowanus"]) < 1e-9


def test_get_neighborhood_metric_returns_none_for_no_fuzzy_match():
    out = metrics.get_neighborhood_metric("not-a-real-place-zyxxqq", "eni")
    assert out is None


def test_get_neighborhood_metric_raises_for_unknown_metric_with_discovery_hint():
    with pytest.raises(ValueError) as exc:
        metrics.get_neighborhood_metric("Park Slope", "made_up_metric")
    msg = str(exc.value)
    assert "made_up_metric" in msg
    assert "list_neighborhood_metrics" in msg


def test_get_neighborhood_metric_returns_zero_schools_when_level_has_none():
    """An NTA can match the query but have no schools at the requested
    level (e.g. asking for HS in an entirely-ES NTA). The discovery
    response should surface that as value=None + n_schools=0, not raise."""
    # SoHo-TriBeCa: residential, very few schools — most aren't HS.
    out = metrics.get_neighborhood_metric(
        "SoHo-TriBeCa-Civic Center-Little Italy", "graduation_rate_4yr", level="high"
    )
    assert out is not None
    if out.value is None:
        assert out.n_schools == 0
