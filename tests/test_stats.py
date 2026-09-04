from __future__ import annotations

import math

import pytest

from ecsae.mathx import chi2_sf, f_sf, normal_ppf, regularized_beta, regularized_gamma_q, student_t_two_sided_p
from ecsae.models import BaselineComparison, IntegerSummary, ReportedEffect, ReportedStat
from ecsae.stats import ci_p_check, combine_baseline, grim, grimmer, recompute_p, statcheck


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (student_t_two_sided_p(2.228139, 10), 0.05),
        (chi2_sf(3.841459, 1), 0.05),
        (f_sf(4.964603, 1, 10), 0.05),
        (normal_ppf(0.975), 1.959964),
    ],
)
def test_distribution_reference_values(actual: float, expected: float) -> None:
    assert actual == pytest.approx(expected, abs=2e-6)


def test_statcheck_accepts_rounding_interval() -> None:
    result = statcheck(ReportedStat(stat_type="t", value=2.35, df1=98, reported_p=0.021, p_decimals=3))
    assert result["applicable"] and result["consistent"] and not result["decision_error"]


def test_statcheck_excludes_adjusted_p() -> None:
    result = statcheck(ReportedStat(stat_type="z", value=3, reported_p=0.01, adjusted=True))
    assert not result["applicable"]


def test_grim_and_grimmer_constructed_integer_data() -> None:
    assert grim(IntegerSummary(name="x", mean=3.0, n=5))["consistent"]
    result = grimmer(IntegerSummary(name="x", mean=3.0, n=5, sd=1.58))
    assert result["applicable"] and result["consistent"]


def test_grim_applicability_limit() -> None:
    assert not grim(IntegerSummary(name="x", mean=2.34, n=101, mean_decimals=2))["applicable"]


@pytest.mark.parametrize(
    "function,args",
    [
        (regularized_beta, (-0.1, 1, 1)),
        (regularized_gamma_q, (0, 1)),
        (student_t_two_sided_p, (1, 0)),
        (f_sf, (-1, 1, 1)),
        (chi2_sf, (-1, 1)),
        (normal_ppf, (-0.1,)),
    ],
)
def test_distribution_domain_validation(function, args) -> None:
    with pytest.raises(ValueError):
        function(*args)


def test_distribution_boundaries_and_stat_types() -> None:
    assert regularized_beta(0, 1, 1) == 0
    assert regularized_beta(1, 1, 1) == 1
    assert regularized_gamma_q(2, 0) == 1
    assert normal_ppf(0) == -math.inf
    assert normal_ppf(1) == math.inf
    for spec in [
        ReportedStat(stat_type="F", value=2, df1=2, df2=20, reported_p=0.2),
        ReportedStat(stat_type="chi2", value=2, df1=2, reported_p=0.3),
        ReportedStat(stat_type="r", value=0.2, df1=30, reported_p=0.3),
        ReportedStat(stat_type="z", value=2, reported_p=0.04),
        ReportedStat(stat_type="Q", value=2, df1=2, reported_p=0.3),
    ]:
        assert statcheck(spec)["applicable"]


def test_statcheck_inequality_and_decision_error() -> None:
    assert statcheck(ReportedStat(stat_type="z", value=3, reported_p=0.01, p_operator="<"))["consistent"]
    result = statcheck(ReportedStat(stat_type="z", value=0.2, reported_p=0.01))
    assert result["decision_error"]


@pytest.mark.parametrize(
    ("operator", "reported_p", "alpha", "expected"),
    [
        ("<", 0.10, 0.05, "indeterminate"),
        (">", 0.01, 0.05, "indeterminate"),
        ("<", 0.05, 0.05, "significant"),
        (">", 0.05, 0.05, "nonsignificant"),
        ("=", 0.050, 0.05, "indeterminate"),
        ("<", 0.10, 0.10, "significant"),
    ],
)
def test_statcheck_significance_handles_inequality_intervals(
    operator: str,
    reported_p: float,
    alpha: float,
    expected: str,
) -> None:
    result = statcheck(
        ReportedStat(stat_type="z", value=1.8, reported_p=reported_p, p_operator=operator, p_decimals=3),
        alpha=alpha,
    )
    assert result["reported_significance"] == expected


def test_statcheck_inequality_interval_does_not_force_decision_error() -> None:
    result = statcheck(ReportedStat(stat_type="z", value=1.8, reported_p=0.01, p_operator=">"))
    assert result["consistent"]
    assert result["reported_significance"] == "indeterminate"
    assert result["decision_error"] is False


def test_one_tailed_only_halves_symmetric_distributions() -> None:
    two_f = ReportedStat(stat_type="F", value=3, df1=3, df2=40)
    one_f = two_f.model_copy(update={"one_tailed": True})
    assert recompute_p(one_f) == recompute_p(two_f)
    two_chi = ReportedStat(stat_type="chi2", value=3, df1=4)
    one_chi = two_chi.model_copy(update={"one_tailed": True})
    assert recompute_p(one_chi) == recompute_p(two_chi)
    two_t = ReportedStat(stat_type="t", value=2, df1=20)
    one_t = two_t.model_copy(update={"one_tailed": True})
    assert recompute_p(one_t) == pytest.approx(recompute_p(two_t) / 2)


def test_grimmer_rejects_unknown_multi_item_geometry() -> None:
    result = grimmer(IntegerSummary(name="scale", mean=3.5, n=20, items=4, sd=1.2))
    assert not result["applicable"]
    assert "multi-item" in result["reason"]


def test_zero_reported_p_is_explicitly_inconsistent() -> None:
    result = statcheck(ReportedStat(stat_type="t", value=22.2, df1=28, reported_p=0.0))
    assert result["applicable"]
    assert result["reported_zero"] is True
    assert result["consistent"] is False


def test_ci_to_p_additive_and_ratio_examples() -> None:
    additive = ci_p_check(ReportedEffect(
        name="difference", estimate=2.4, ci_lower=0.3, ci_upper=4.5,
        confidence_level=95, reported_p=0.03, estimate_decimals=1,
        ci_lower_decimals=1, ci_upper_decimals=1, p_decimals=2,
    ))
    assert additive["applicable"] and additive["consistent"]
    assert additive["recomputed_p"] == pytest.approx(0.0251, abs=0.001)
    ratio = ci_p_check(ReportedEffect(
        name="hazard ratio", estimate=0.72, ci_lower=0.55, ci_upper=0.94,
        confidence_level=95, reported_p=0.02, scale="ratio",
        estimate_decimals=2, ci_lower_decimals=2, ci_upper_decimals=2, p_decimals=2,
    ))
    assert ratio["applicable"] and ratio["consistent"]
    assert ratio["recomputed_p"] == pytest.approx(0.0163, abs=0.002)


def test_ci_to_p_detects_incompatibility_and_handles_inequality() -> None:
    incompatible = ci_p_check(ReportedEffect(
        name="difference", estimate=2.4, ci_lower=0.3, ci_upper=4.5,
        reported_p=0.30, estimate_decimals=1, ci_lower_decimals=1,
        ci_upper_decimals=1, p_decimals=2,
    ))
    assert not incompatible["consistent"] and incompatible["decision_error"]
    bounded = ci_p_check(ReportedEffect(
        name="difference", estimate=4.0, ci_lower=2.0, ci_upper=6.0,
        reported_p=0.001, p_operator="<", estimate_decimals=1,
        ci_lower_decimals=1, ci_upper_decimals=1,
    ))
    assert bounded["consistent"]


def test_ci_to_p_inequality_interval_can_be_indeterminate() -> None:
    result = ci_p_check(ReportedEffect(
        name="difference", estimate=2.4, ci_lower=0.3, ci_upper=4.5,
        reported_p=0.01, p_operator=">", estimate_decimals=1,
        ci_lower_decimals=1, ci_upper_decimals=1, p_decimals=2,
    ))
    assert result["reported_significance"] == "indeterminate"
    assert result["decision_error"] is False


def test_statcheck_interval_includes_zero_when_rounding_box_crosses_null() -> None:
    result = statcheck(ReportedStat(stat_type="z", value=0.01, reported_p=0.99, statistic_decimals=1, p_decimals=2))
    assert result["recomputed_interval"][1] == pytest.approx(1.0)


def test_ci_to_p_interval_includes_null_when_rounding_box_crosses_null() -> None:
    result = ci_p_check(ReportedEffect(
        name="difference", estimate=0.01, ci_lower=-0.04, ci_upper=0.06,
        reported_p=0.95, estimate_decimals=1, ci_lower_decimals=1,
        ci_upper_decimals=1, p_decimals=2,
    ))
    assert result["recomputed_interval"][1] == pytest.approx(1.0)


def test_baseline_rounding_interval_prevents_identical_mean_artifact() -> None:
    comparison = BaselineComparison(
        name="age", mean_a=50.0, sd_a=10.0, n_a=100,
        mean_b=50.0, sd_b=10.0, n_b=100,
        mean_decimals_a=1, sd_decimals_a=1,
        mean_decimals_b=1, sd_decimals_b=1,
    )
    result = combine_baseline([comparison])
    assert result["applicable"]
    assert result["p_intervals"][0][1] == 1.0
    assert result["fisher_p_interval"][1] == pytest.approx(1.0)
