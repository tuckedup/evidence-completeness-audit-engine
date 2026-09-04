from __future__ import annotations

from hypothesis import given, strategies as st

from ecsae.models import IntegerSummary, ReportedStat
from ecsae.stats import grim, recompute_p, statcheck


@given(st.integers(min_value=2, max_value=100), st.integers(min_value=0, max_value=500))
def test_constructed_integer_mean_is_grim_consistent(n: int, total: int) -> None:
    shown = round(total / n, 2)
    result = grim(IntegerSummary(name="generated", mean=shown, n=n, mean_decimals=2))
    # Printed two-decimal rounding can map multiple totals, but the originating total must pass.
    assert result["consistent"]


@given(
    st.floats(min_value=0.1, max_value=6, allow_nan=False, allow_infinity=False),
    st.integers(min_value=2, max_value=500),
)
def test_statcheck_round_trip(value: float, df: int) -> None:
    shown = round(value, 3)
    base = ReportedStat(stat_type="t", value=shown, df1=df, statistic_decimals=3)
    p = recompute_p(base)
    reported = round(p, 4)
    result = statcheck(base.model_copy(update={"reported_p": reported, "p_decimals": 4}))
    if reported == 0:
        assert result["reported_zero"] and not result["consistent"]
    else:
        assert result["consistent"]
