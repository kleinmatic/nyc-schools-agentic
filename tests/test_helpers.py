"""Tests for the small helpers in services/schools.py — currency parsing,
nullable-int coercion, etc. Cheap and fast (no data fixtures required)."""
import math

import pandas as pd
import pytest

from app.services.schools import (
    _opt_float,
    _opt_int,
    _opt_pct,
    _opt_str,
    _parse_budget,
)


class TestParseBudget:
    """Galaxy budget cells ship as currency strings — pin behavior."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("$ 187,530", 187530.0),
            ("$187,530.50", 187530.50),
            ("$1,234.56", 1234.56),
            ("0", 0.0),
            ("$ 0", 0.0),
            ("  $ 12,345  ", 12345.0),
        ],
    )
    def test_parses_currency_strings(self, raw, expected):
        assert _parse_budget(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "abc", "$", "$$", pd.NA, math.nan],
    )
    def test_returns_none_for_unparseable(self, raw):
        assert _parse_budget(raw) is None

    def test_returns_none_for_lone_minus(self):
        # "-" passes the regex (- is allowed) but float("-") raises.
        assert _parse_budget("-") is None


class TestOptHelpers:
    """The defensive coercion helpers used everywhere in schools.py."""

    def test_opt_int(self):
        assert _opt_int(5) == 5
        assert _opt_int(5.7) == 5  # truncates float
        assert _opt_int("5") == 5
        # Note: _opt_int does NOT round-trip "5.7" — int("5.7") raises and we
        # don't catch it via float(). That's the conservative call, matching
        # demographics where ints arrive as ints, not float-strings. If you
        # expect a float-string column, use _opt_int(float(v)) at the caller.
        assert _opt_int("5.7") is None
        assert _opt_int(None) is None
        assert _opt_int(math.nan) is None
        assert _opt_int(pd.NA) is None
        assert _opt_int("abc") is None
        assert _opt_int("") is None

    def test_opt_float(self):
        assert _opt_float(5) == 5.0
        assert _opt_float(5.7) == 5.7
        assert _opt_float("5.7") == 5.7
        assert _opt_float(None) is None
        assert _opt_float(math.nan) is None
        assert _opt_float(pd.NA) is None
        assert _opt_float("abc") is None

    def test_opt_str(self):
        assert _opt_str("hi") == "hi"
        assert _opt_str("  hi  ") == "hi"
        assert _opt_str("") is None
        assert _opt_str("   ") is None
        assert _opt_str(None) is None
        assert _opt_str(math.nan) is None
        # Non-stringy values stringify (NYSED data has Int64 → str fine).
        assert _opt_str(42) == "42"

    def test_opt_str_filters_pandas_na_strings(self):
        # mdb-export sometimes emits literal "nan", "None", "<NA>" strings.
        assert _opt_str("nan") is None
        assert _opt_str("NaN") is None
        assert _opt_str("None") is None
        assert _opt_str("<NA>") is None

    def test_opt_pct_divides_by_100(self):
        # NYSED stores percentages in 0–100 units; we use 0–1 throughout.
        # Use approx to avoid float-precision flakiness.
        assert _opt_pct("8.2") == pytest.approx(0.082)
        assert _opt_pct(96.3) == pytest.approx(0.963)
        assert _opt_pct(0) == 0.0
        assert _opt_pct(None) is None
        assert _opt_pct(math.nan) is None
        assert _opt_pct("abc") is None
