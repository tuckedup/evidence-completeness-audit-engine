from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_DOWN, ROUND_HALF_UP
from typing import Any

from .mathx import chi2_sf, f_sf, normal_ppf, normal_sf, student_t_two_sided_p
from .models import BaselineComparison, IntegerSummary, ReportedEffect, ReportedStat


def _interval_overlap(
    left_low: float,
    left_high: float,
    *,
    left_low_closed: bool,
    left_high_closed: bool,
    right_low: float,
    right_high: float,
    right_low_closed: bool,
    right_high_closed: bool,
) -> bool:
    if left_high < right_low or right_high < left_low:
        return False
    if left_high == right_low and not (left_high_closed and right_low_closed):
        return False
    if right_high == left_low and not (right_high_closed and left_low_closed):
        return False
    return True


def _interval_significance(
    low: float,
    high: float,
    *,
    low_closed: bool,
    high_closed: bool,
    alpha: float,
) -> str:
    if high <= alpha:
        return "significant"
    if low > alpha or (low == alpha and not low_closed):
        return "nonsignificant"
    return "indeterminate"


def _reported_p_interval(
    reported: float,
    operator: str,
    decimals: int,
    *,
    alpha: float,
) -> dict[str, Any]:
    reported_zero = reported == 0.0
    if reported_zero:
        return {
            "consistent": False,
            "reported_zero": True,
            "reported_interval": [0.0, 0.0],
            "reported_interval_closed": [True, True],
            "reported_significance": "significant",
            "alpha": alpha,
        }
    if operator == "<":
        interval = [0.0, reported]
        closed = [True, False]
    elif operator == "<=":
        interval = [0.0, reported]
        closed = [True, True]
    elif operator == ">":
        interval = [reported, 1.0]
        closed = [False, True]
    elif operator == ">=":
        interval = [reported, 1.0]
        closed = [True, True]
    else:
        half_unit = 0.5 * 10.0 ** (-decimals)
        interval = [max(0.0, reported - half_unit), min(1.0, reported + half_unit)]
        closed = [True, True]
    return {
        "reported_zero": False,
        "reported_interval": interval,
        "reported_interval_closed": closed,
        "reported_significance": _interval_significance(
            interval[0], interval[1], low_closed=closed[0], high_closed=closed[1], alpha=alpha
        ),
        "alpha": alpha,
    }


def recompute_p(stat: ReportedStat, value: float | None = None) -> float:
    x = stat.value if value is None else value
    if stat.stat_type == "t":
        p = student_t_two_sided_p(x, stat.df1 or 0)
    elif stat.stat_type == "F":
        p = f_sf(x, stat.df1 or 0, stat.df2 or 0)
    elif stat.stat_type in {"chi2", "Q"}:
        p = chi2_sf(x, stat.df1 or 0)
    elif stat.stat_type == "r":
        df = stat.df1 or 0
        t_value = x * math.sqrt(df / (1.0 - x * x))
        p = student_t_two_sided_p(t_value, df)
    elif stat.stat_type == "z":
        p = 2.0 * normal_sf(abs(x))
    else:  # pragma: no cover - Pydantic excludes this
        raise ValueError(stat.stat_type)
    # F, chi-square, and Q are already upper-tail tests. Only symmetric
    # t/z/r statistics have their default two-sided p-value halved.
    halve = stat.one_tailed and stat.stat_type in {"t", "z", "r"}
    return min(1.0, p * 0.5 if halve else p)


def statcheck(stat: ReportedStat, alpha: float = 0.05) -> dict[str, Any]:
    if stat.reported_p is None:
        return {"applicable": False, "reason": "reported p-value is absent"}
    if stat.adjusted:
        return {
            "applicable": False,
            "reason": "adjusted/corrected p-values are outside statcheck assumptions",
        }
    step = 10.0 ** (-stat.statistic_decimals)
    low_value, high_value = stat.value - step / 2, stat.value + step / 2
    if stat.stat_type in {"F", "chi2", "Q"}:
        low_value = max(0.0, low_value)
    if stat.stat_type == "r":
        low_value, high_value = max(-0.999999999999, low_value), min(0.999999999999, high_value)
    stat_candidates = [stat.value, low_value, high_value]
    if stat.stat_type in {"t", "z", "r"} and low_value <= 0.0 <= high_value:
        stat_candidates.append(0.0)
    candidates = [recompute_p(stat, candidate) for candidate in stat_candidates]
    p_min, p_max = min(candidates), max(candidates)
    compatibility = _reported_p_consistency(
        p_min,
        p_max,
        stat.reported_p,
        stat.p_operator,
        stat.p_decimals,
        alpha,
    )
    return {
        "applicable": True,
        "recomputed_p": recompute_p(stat, stat.value),
        "recomputed_interval": [p_min, p_max],
        "recomputed_significance": _interval_significance(
            p_min, p_max, low_closed=True, high_closed=True, alpha=alpha
        ),
        **compatibility,
    }


def _reported_p_consistency(
    p_min: float,
    p_max: float,
    reported: float,
    operator: str,
    decimals: int,
    alpha: float,
) -> dict[str, Any]:
    reported_interval = _reported_p_interval(reported, operator, decimals, alpha=alpha)
    interval = reported_interval["reported_interval"]
    closed = reported_interval["reported_interval_closed"]
    consistent = _interval_overlap(
        p_min,
        p_max,
        left_low_closed=True,
        left_high_closed=True,
        right_low=interval[0],
        right_high=interval[1],
        right_low_closed=closed[0],
        right_high_closed=closed[1],
    ) and not reported_interval["reported_zero"]
    recomputed_significance = _interval_significance(
        p_min, p_max, low_closed=True, high_closed=True, alpha=alpha
    )
    reported_significance = reported_interval["reported_significance"]
    return {
        "consistent": consistent,
        "decision_error": (
            reported_significance != "indeterminate"
            and recomputed_significance != "indeterminate"
            and reported_significance != recomputed_significance
        ),
        "recomputed_significance": recomputed_significance,
        **reported_interval,
    }


def ci_p_check(effect: ReportedEffect, alpha: float = 0.05) -> dict[str, Any]:
    """Approximate a p-value from a CI, conservatively propagating printed precision.

    This is the Altman–Bland normal approximation. Ratio measures are evaluated
    on the log scale. The result is a screening check, because a reported p-value
    and CI can legitimately come from different adjusted models.
    """

    transform = math.log if effect.scale == "ratio" else float
    central = tuple(transform(value) for value in (effect.estimate, effect.ci_lower, effect.ci_upper))
    estimate, lower, upper = central
    if not lower <= estimate <= upper:
        return {
            "applicable": False,
            "reason": "reported confidence interval does not contain the point estimate",
        }
    critical = normal_ppf(1.0 - (1.0 - effect.confidence_level / 100.0) / 2.0)
    if not math.isfinite(critical) or critical <= 0:
        return {"applicable": False, "reason": "confidence level has no finite normal critical value"}

    def rounded_values(value: float, decimals: int) -> list[float]:
        half_unit = 0.5 * 10.0 ** (-decimals)
        values = [value - half_unit, value, value + half_unit]
        if effect.scale == "ratio":
            values = [item for item in values if item > 0]
            if value - half_unit <= 1.0 <= value + half_unit:
                values.append(1.0)
        elif value - half_unit <= 0.0 <= value + half_unit:
            values.append(0.0)
        return values

    estimates = rounded_values(effect.estimate, effect.estimate_decimals)
    lowers = rounded_values(effect.ci_lower, effect.ci_lower_decimals)
    uppers = rounded_values(effect.ci_upper, effect.ci_upper_decimals)
    candidates: list[float] = []
    for raw_estimate in estimates:
        for raw_lower in lowers:
            for raw_upper in uppers:
                transformed_estimate = transform(raw_estimate)
                transformed_lower = transform(raw_lower)
                transformed_upper = transform(raw_upper)
                if not transformed_lower < transformed_upper:
                    continue
                if not transformed_lower <= transformed_estimate <= transformed_upper:
                    continue
                standard_error = (transformed_upper - transformed_lower) / (2.0 * critical)
                if standard_error <= 0:
                    continue
                z_value = abs(transformed_estimate) / standard_error
                p_value = normal_sf(z_value)
                candidates.append(p_value if effect.one_tailed else min(1.0, 2.0 * p_value))
    if not candidates:
        return {"applicable": False, "reason": "printed-precision confidence interval is invalid"}
    p_min, p_max = min(candidates), max(candidates)
    point_se = (upper - lower) / (2.0 * critical)
    point_z = abs(estimate) / point_se
    point_p = normal_sf(point_z)
    if not effect.one_tailed:
        point_p = min(1.0, 2.0 * point_p)
    compatibility = _reported_p_consistency(
        p_min,
        p_max,
        effect.reported_p,
        effect.p_operator,
        effect.p_decimals,
        alpha,
    )
    left_width = estimate - lower
    right_width = upper - estimate
    symmetry_ratio = (
        max(left_width, right_width) / min(left_width, right_width)
        if min(left_width, right_width) > 0
        else None
    )
    return {
        "applicable": True,
        "method": "Altman-Bland normal approximation from confidence interval",
        "scale": effect.scale,
        "confidence_level": effect.confidence_level,
        "one_tailed": effect.one_tailed,
        "recomputed_p": point_p,
        "recomputed_interval": [p_min, p_max],
        "ci_symmetry_ratio_on_analysis_scale": symmetry_ratio,
        **compatibility,
    }


def _quantize(value: Decimal, decimals: int, rounding: str) -> Decimal:
    quantum = Decimal(1).scaleb(-decimals)
    return value.quantize(quantum, rounding=rounding)


def grim(summary: IntegerSummary) -> dict[str, Any]:
    if not summary.integer_scale:
        return {"applicable": False, "reason": "summary is not from an integer scale"}
    if summary.n > 10 ** summary.mean_decimals:
        return {
            "applicable": False,
            "reason": "n exceeds 10^reported decimals; GRIM is uninformative",
        }
    granule = summary.n * summary.items
    reported = Decimal(str(summary.mean))
    total = reported * granule
    candidate_totals = {
        int(total.to_integral_value(rounding=ROUND_HALF_UP)),
        int(total.to_integral_value(rounding=ROUND_HALF_DOWN)),
        int(total.to_integral_value(rounding=ROUND_FLOOR)),
        int(total.to_integral_value(rounding=ROUND_CEILING)),
    }
    reconstructed = sorted(
        {
            _quantize(Decimal(candidate) / granule, summary.mean_decimals, mode)
            for candidate in candidate_totals
            for mode in (ROUND_HALF_UP, ROUND_HALF_DOWN)
        }
    )
    expected = _quantize(reported, summary.mean_decimals, ROUND_HALF_UP)
    consistent = expected in reconstructed
    return {
        "applicable": True,
        "consistent": consistent,
        "granule": granule,
        "candidate_means": [str(item) for item in reconstructed],
    }


def grimmer(summary: IntegerSummary) -> dict[str, Any]:
    if summary.items != 1:
        return {
            "applicable": False,
            "reason": "GRIMMER requires single-item observations; multi-item scale geometry is unknown",
        }
    grim_result = grim(summary)
    if not grim_result["applicable"]:
        return grim_result
    if not grim_result["consistent"]:
        return {**grim_result, "consistent": False, "stage": "GRIM"}
    if summary.sd is None:
        return {"applicable": False, "reason": "standard deviation is absent"}
    n = summary.n
    real_sum = int((Decimal(str(summary.mean)) * n).to_integral_value(rounding=ROUND_HALF_UP))
    real_mean = real_sum / n
    half_unit = 0.5 * 10 ** (-summary.sd_decimals)
    lower_sigma = max(0.0, summary.sd - half_unit)
    upper_sigma = summary.sd + half_unit
    lower_ss = (n - 1) * lower_sigma**2 + n * real_mean**2
    upper_ss = (n - 1) * upper_sigma**2 + n * real_mean**2
    first, last = math.ceil(lower_ss - 1e-12), math.floor(upper_ss + 1e-12)
    matches: list[int] = []
    for ss in range(first, last + 1):
        if ss % 2 != real_sum % 2:
            continue
        variance_numerator = ss - n * real_mean**2
        if variance_numerator < -1e-12:
            continue
        predicted = math.sqrt(max(0.0, variance_numerator) / (n - 1))
        rounded = _quantize(Decimal(str(predicted)), summary.sd_decimals, ROUND_HALF_UP)
        target = _quantize(Decimal(str(summary.sd)), summary.sd_decimals, ROUND_HALF_UP)
        if rounded == target:
            matches.append(ss)
    return {
        "applicable": True,
        "consistent": bool(matches),
        "stage": "integer_ss_sd_parity",
        "integer_ss_interval": [first, last],
        "matching_ss": matches[:25],
        "real_sum": real_sum,
    }


def welch_p(comparison: BaselineComparison) -> float:
    variance = comparison.sd_a**2 / comparison.n_a + comparison.sd_b**2 / comparison.n_b
    t_value = abs(comparison.mean_a - comparison.mean_b) / math.sqrt(variance)
    numerator = variance**2
    denominator = (
        (comparison.sd_a**2 / comparison.n_a) ** 2 / (comparison.n_a - 1)
        + (comparison.sd_b**2 / comparison.n_b) ** 2 / (comparison.n_b - 1)
    )
    return student_t_two_sided_p(t_value, numerator / denominator)


def welch_p_interval(comparison: BaselineComparison) -> tuple[float, float]:
    """Welch p range implied by rounding boxes for both means and SDs."""

    def candidates(value: float, decimals: int, *, positive: bool = False) -> list[float]:
        half_unit = 0.5 * 10.0 ** (-decimals)
        values = [value - half_unit, value, value + half_unit]
        return [item for item in values if not positive or item > 0]

    values: list[float] = []
    for mean_a in candidates(comparison.mean_a, comparison.mean_decimals_a):
        for mean_b in candidates(comparison.mean_b, comparison.mean_decimals_b):
            for sd_a in candidates(comparison.sd_a, comparison.sd_decimals_a, positive=True):
                for sd_b in candidates(comparison.sd_b, comparison.sd_decimals_b, positive=True):
                    values.append(welch_p(comparison.model_copy(update={
                        "mean_a": mean_a,
                        "mean_b": mean_b,
                        "sd_a": sd_a,
                        "sd_b": sd_b,
                    })))
    mean_a_half = 0.5 * 10.0 ** (-comparison.mean_decimals_a)
    mean_b_half = 0.5 * 10.0 ** (-comparison.mean_decimals_b)
    if (
        comparison.mean_a - mean_a_half <= comparison.mean_b + mean_b_half
        and comparison.mean_b - mean_b_half <= comparison.mean_a + mean_a_half
    ):
        values.append(1.0)
    return min(values), max(values)


def combine_baseline(comparisons: list[BaselineComparison]) -> dict[str, Any]:
    if not comparisons:
        return {"applicable": False, "reason": "no continuous baseline comparisons"}
    noncontinuous = [item.name for item in comparisons if item.variable_type != "continuous"]
    if noncontinuous:
        return {
            "applicable": False,
            "reason": "Carlisle combination is disabled for non-continuous variables",
            "excluded_variables": noncontinuous,
        }
    raw_p_values = [welch_p(item) for item in comparisons]
    p_values = [max(1e-300, min(1 - 1e-16, item)) for item in raw_p_values]
    p_intervals = [welch_p_interval(item) for item in comparisons]
    dependence_unknown = any(
        not item.independent_from_other_variables or item.correlated_with for item in comparisons
    )
    if dependence_unknown and len(comparisons) > 1:
        return {
            "applicable": False,
            "reason": "cross-variable independence is not established; combination would be anti-conservative",
            "p_values": p_values,
            "p_intervals": p_intervals,
            "correlation_warning": True,
        }
    fisher_stat = -2.0 * sum(math.log(p) for p in p_values)
    fisher_p = chi2_sf(fisher_stat, 2 * len(p_values))
    fisher_stat_low = -2.0 * sum(math.log(max(1e-300, high)) for _, high in p_intervals)
    fisher_stat_high = -2.0 * sum(math.log(max(1e-300, low)) for low, _ in p_intervals)
    fisher_p_interval = [
        chi2_sf(fisher_stat_high, 2 * len(p_values)),
        chi2_sf(fisher_stat_low, 2 * len(p_values)),
    ]
    # Transform two-sided p-values directly so very high p-values carry a
    # negative sign (unusual similarity) and very low p-values a positive sign.
    # Keep the descriptive transform finite when a tiny p-value is below
    # binary precision relative to one.
    z_values = [normal_ppf(1.0 - max(1e-15, min(1.0 - 1e-15, p))) for p in p_values]
    stouffer_z = sum(z_values) / math.sqrt(len(z_values))
    stouffer_p = 2.0 * normal_sf(abs(stouffer_z))
    correlation_warning = any(item.correlated_with for item in comparisons)
    return {
        "applicable": True,
        "p_values": p_values,
        "p_intervals": p_intervals,
        "fisher_stat": fisher_stat,
        "fisher_p": fisher_p,
        "fisher_p_interval": fisher_p_interval,
        "stouffer_z": stouffer_z,
        "stouffer_p": stouffer_p,
        "correlation_warning": correlation_warning,
    }
