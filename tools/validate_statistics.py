"""Independent numerical and generated-data validation for the statistical core."""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import scipy
from scipy import stats as scipy_stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecsae.models import BaselineComparison, IntegerSummary, ReportedEffect, ReportedStat  # noqa: E402
from ecsae.stats import ci_p_check, grim, grimmer, recompute_p, welch_p  # noqa: E402


def rounded(value: float, digits: int = 2) -> float:
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def scipy_p(spec: dict) -> float:
    kind, value = spec["stat_type"], spec["value"]
    if kind == "t":
        value_p = 2 * scipy_stats.t.sf(abs(value), spec["df1"])
    elif kind == "F":
        value_p = scipy_stats.f.sf(value, spec["df1"], spec["df2"])
    elif kind in {"chi2", "Q"}:
        value_p = scipy_stats.chi2.sf(value, spec["df1"])
    elif kind == "r":
        t_value = value * math.sqrt(spec["df1"] / (1 - value * value))
        value_p = 2 * scipy_stats.t.sf(abs(t_value), spec["df1"])
    elif kind == "z":
        value_p = 2 * scipy_stats.norm.sf(abs(value))
    else:  # pragma: no cover
        raise AssertionError(kind)
    if spec.get("one_tailed") and kind in {"t", "r", "z"}:
        value_p /= 2
    return float(value_p)


def validate_distributions(rng: random.Random, samples: int) -> dict:
    kinds = ["t", "F", "chi2", "r", "z", "Q"]
    max_error = 0.0
    failures: list[dict] = []
    per_kind = {kind: 0 for kind in kinds}
    for index in range(samples):
        kind = kinds[index % len(kinds)]
        spec: dict = {"stat_type": kind, "value": rng.uniform(0.001, 8), "one_tailed": bool(index % 2)}
        if kind in {"t", "r", "chi2", "Q"}:
            spec["df1"] = rng.randint(1, 300)
        if kind == "F":
            spec.update({"df1": rng.randint(1, 30), "df2": rng.randint(2, 300)})
        if kind == "r":
            spec["value"] = rng.uniform(-0.95, 0.95)
        actual = recompute_p(ReportedStat(**spec))
        expected = scipy_p(spec)
        error = abs(actual - expected)
        max_error = max(max_error, error)
        per_kind[kind] += 1
        if error > 2e-10:
            failures.append({"index": index, "input": spec, "ecsae": actual, "scipy": expected, "absolute_error": error})
    return {
        "oracle": f"SciPy {scipy.__version__}", "samples": samples,
        "samples_by_statistic": per_kind, "max_absolute_error": max_error,
        "tolerance": 2e-10, "failure_count": len(failures), "failure_examples": failures[:10],
    }


def validate_generated_integer_data(rng: random.Random, samples: int) -> dict:
    grim_failures: list[dict] = []
    grimmer_failures: list[dict] = []
    for index in range(samples):
        n = rng.randint(3, 50)
        values = [rng.randint(-3, 9) for _ in range(n)]
        mean = sum(values) / n
        sample_sd = math.sqrt(sum((value - mean) ** 2 for value in values) / (n - 1))
        summary = IntegerSummary(
            name=f"generated-{index}", mean=rounded(mean), mean_decimals=2,
            sd=rounded(sample_sd), sd_decimals=2, n=n, items=1, integer_scale=True,
        )
        grim_result = grim(summary)
        grimmer_result = grimmer(summary)
        if not grim_result.get("consistent"):
            grim_failures.append({"index": index, "values": values, "summary": summary.model_dump(), "result": grim_result})
        if not grimmer_result.get("consistent"):
            grimmer_failures.append({"index": index, "values": values, "summary": summary.model_dump(), "result": grimmer_result})
    return {
        "generator": "uniform integer observations in [-3,9], n=3..50, half-up rounded mean/SD",
        "samples": samples,
        "grim_false_flag_count": len(grim_failures),
        "grimmer_false_flag_count": len(grimmer_failures),
        "grim_false_flag_examples": grim_failures[:5],
        "grimmer_false_flag_examples": grimmer_failures[:5],
    }


def validate_welch(rng: random.Random, samples: int) -> dict:
    failures: list[dict] = []
    max_error = 0.0
    for index in range(samples):
        item = BaselineComparison(
            name=f"welch-{index}", mean_a=rng.uniform(-10, 10), mean_b=rng.uniform(-10, 10),
            sd_a=rng.uniform(0.1, 10), sd_b=rng.uniform(0.1, 10),
            n_a=rng.randint(2, 500), n_b=rng.randint(2, 500),
        )
        variance = item.sd_a**2 / item.n_a + item.sd_b**2 / item.n_b
        t_value = abs(item.mean_a - item.mean_b) / math.sqrt(variance)
        df = variance**2 / (
            (item.sd_a**2 / item.n_a) ** 2 / (item.n_a - 1)
            + (item.sd_b**2 / item.n_b) ** 2 / (item.n_b - 1)
        )
        expected = float(2 * scipy_stats.t.sf(t_value, df))
        actual = welch_p(item)
        error = abs(actual - expected)
        max_error = max(max_error, error)
        if error > 2e-10:
            failures.append({"index": index, "input": item.model_dump(), "ecsae": actual, "scipy": expected, "absolute_error": error})
    return {
        "oracle": f"SciPy {scipy.__version__}", "samples": samples,
        "max_absolute_error": max_error, "tolerance": 2e-10,
        "failure_count": len(failures), "failure_examples": failures[:10],
    }


def validate_ci_to_p(rng: random.Random, samples: int) -> dict:
    failures: list[dict] = []
    max_error = 0.0
    for index in range(samples):
        confidence_level = (90.0, 95.0, 99.0)[index % 3]
        critical = float(scipy_stats.norm.ppf(1 - (1 - confidence_level / 100) / 2))
        standard_error = rng.uniform(0.05, 3.0)
        transformed_estimate = rng.uniform(-4.0, 4.0)
        ratio = bool(index % 2)
        one_tailed = bool(index % 5 == 0)
        lower = transformed_estimate - critical * standard_error
        upper = transformed_estimate + critical * standard_error
        estimate = transformed_estimate
        if ratio:
            estimate, lower, upper = math.exp(estimate), math.exp(lower), math.exp(upper)
        expected = float(scipy_stats.norm.sf(abs(transformed_estimate) / standard_error))
        if not one_tailed:
            expected *= 2
        effect = ReportedEffect(
            name=f"ci-{index}", estimate=estimate, ci_lower=lower, ci_upper=upper,
            confidence_level=confidence_level, reported_p=expected,
            estimate_decimals=10, ci_lower_decimals=10, ci_upper_decimals=10,
            p_decimals=10, scale="ratio" if ratio else "additive", one_tailed=one_tailed,
        )
        result = ci_p_check(effect)
        actual = result["recomputed_p"]
        error = abs(actual - expected)
        max_error = max(max_error, error)
        if error > 2e-10:
            failures.append({
                "index": index, "input": effect.model_dump(), "ecsae": actual,
                "scipy": expected, "absolute_error": error,
            })
    return {
        "oracle": f"SciPy {scipy.__version__}", "samples": samples,
        "max_absolute_error": max_error, "tolerance": 2e-10,
        "failure_count": len(failures), "failure_examples": failures[:10],
        "scales": ["additive", "ratio"], "confidence_levels": [90, 95, 99],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution-samples", type=int, default=30000)
    parser.add_argument("--integer-samples", type=int, default=20000)
    parser.add_argument("--welch-samples", type=int, default=5000)
    parser.add_argument("--ci-samples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20250903)
    parser.add_argument("--output", default="eval/results/statistical-validation.json")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    distributions = validate_distributions(rng, args.distribution_samples)
    integer_data = validate_generated_integer_data(rng, args.integer_samples)
    welch = validate_welch(rng, args.welch_samples)
    ci_to_p = validate_ci_to_p(rng, args.ci_samples)
    passed = not (
        distributions["failure_count"] or integer_data["grim_false_flag_count"]
        or integer_data["grimmer_false_flag_count"] or welch["failure_count"]
        or ci_to_p["failure_count"]
    )
    report = {
        "schema_version": "1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed, "passed": passed,
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "scipy": scipy.__version__},
        "distribution_oracle": distributions, "known_integer_data": integer_data,
        "welch_oracle": welch, "ci_to_p_oracle": ci_to_p,
        "external_reference_cases": {
            "count": 20,
            "sources": [
                "https://github.com/MicheleNuijten/statcheck/tree/68376e18dc1c6f5c8a1e95b5146ec564c6c7ced9/tests/testthat",
                "https://github.com/lhdjung/scrutiny/tree/a81cd540332cf402c1ffc798f93534c31da17009/tests/testthat",
            ],
            "note": "Executed by tests/test_regression.py, not by this generator.",
        },
        "limitations": [
            "Generated integer data measure false-flag behavior on known-valid summaries, not sensitivity.",
            "SciPy numerical agreement does not validate study extraction or clinical interpretation.",
        ],
    }
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"passed": passed, "output": str(target), "distribution_samples": args.distribution_samples, "integer_samples": args.integer_samples, "welch_samples": args.welch_samples, "ci_samples": args.ci_samples}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
