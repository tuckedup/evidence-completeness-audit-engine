"""Generate the fixed 220-case regression corpus.

Cases 1-20 are source-anchored assertions copied from upstream test suites.  They
are deliberately *not* evaluated through AuditEngine while being generated.
Cases 21-220 are golden end-to-end snapshots.  Run only when intentionally
migrating rule behavior; review resulting diffs and CHANGELOG.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecsae.engine import AuditEngine  # noqa: E402
from ecsae.models import IntegerSummary, ReportedStat  # noqa: E402
from ecsae.normalize import canonical_json  # noqa: E402
from ecsae.stats import recompute_p  # noqa: E402

FULL_REPORTING = {
    "primary_outcome": True,
    "effect_estimate": True,
    "precision": True,
    "harms": True,
    "protocol_registration": True,
    "data_sharing": True,
    "conflicts_of_interest": True,
    "patient_public_involvement": True,
}


def base(index: int, n: int = 100) -> dict:
    return {
        "study_id": f"REG-{index:03d}",
        "source": "versioned synthetic regression fixture",
        "randomized_n": n,
        "analyzed_n": n,
        "arm_ns": {"control": n // 2, "intervention": n - n // 2},
        "reporting": dict(FULL_REPORTING),
    }


def stat_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    kinds = ["t", "F", "chi2", "r", "z", "Q"]
    for offset in range(30):
        kind = kinds[offset % len(kinds)]
        # Moderate statistics/df avoid degenerate expected p-values of exactly
        # zero or one, which were weak tests in the original matrix.
        spec = {"stat_type": kind, "value": 1.2 + (offset % 7) * 0.31, "statistic_decimals": 2}
        if kind in {"t", "chi2", "r", "Q"}:
            spec["df1"] = 2 + offset % 8 if kind in {"chi2", "Q"} else 12 + offset % 20
        if kind == "F":
            spec.update({"df1": 2 + offset % 4, "df2": 20 + offset % 20})
        if kind == "r":
            spec["value"] = 0.12 + (offset % 6) * 0.08
        p = recompute_p(ReportedStat(**spec))
        assert 0.001 < p < 0.999, (kind, spec, p)
        spec.update({"reported_p": round(p, 3), "p_decimals": 3})
        if offset % 4 == 3:
            spec["reported_p"] = min(0.999, round(p + 0.2, 3))
        record = base(start + offset, 80 + 2 * offset)
        record["reported_stats"] = [spec]
        cases.append(("statcheck", record, "APA-style recomputation with rounding interval"))
    return cases


def grim_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    for offset in range(30):
        n = 8 + offset % 25
        total = n * 3 + offset % n
        mean = round(total / n, 2)
        if offset % 3 == 2:
            mean = round(mean + 0.01, 2)
        record = base(start + offset, max(40, n * 2))
        record["integer_summaries"] = [{"name": "Likert outcome", "mean": mean, "n": n, "mean_decimals": 2}]
        cases.append(("grim", record, "Integer mean feasibility and rounding boundaries"))
    return cases


def grimmer_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    for offset in range(25):
        values = [1 + ((i + offset) % 5) for i in range(7 + offset % 7)]
        n = len(values)
        mean = sum(values) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1))
        shown_sd = round(sd + (0.11 if offset % 4 == 3 else 0), 2)
        record = base(start + offset, 60)
        record["integer_summaries"] = [{
            "name": "integer score", "mean": round(mean, 2), "n": n,
            "mean_decimals": 2, "sd": shown_sd, "sd_decimals": 2,
        }]
        cases.append(("grimmer", record, "Integer sum-of-squares, SD, and parity feasibility"))
    return cases


def partition_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    for offset in range(30):
        n = 100 + offset
        left = n // 2
        right = n - left + (3 if offset % 3 == 2 else (-2 if offset % 3 == 1 else 0))
        record = base(start + offset, n)
        record["subgroups"] = [{
            "variable": "age band",
            "levels": [
                {"name": "younger", "n": left, "percentage": round(100 * left / n, 1)},
                {"name": "older", "n": right, "percentage": round(100 * right / n, 1)},
            ],
            "exhaustive": True,
        }]
        cases.append(("subgroup_partition", record, "Enrollment, partition, and integer percentage arithmetic"))
    return cases


def interaction_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    for offset in range(25):
        n = 300 if offset % 2 == 0 else 500
        record = base(start + offset, n)
        record["subgroups"] = [{
            "variable": "biomarker",
            "levels": [{"name": "negative", "n": n // 2}, {"name": "positive", "n": n - n // 2}],
            "effect_claimed": True,
            "prespecified": offset % 3 != 0,
            "formal_interaction_test": offset % 5 != 0,
            "interaction_p": 0.03,
            "powered_for_interaction": False,
            "main_effect_required_n": 100,
        }]
        cases.append(("interaction_power", record, "formal interaction-test criterion and Brookes-derived heuristic"))
    return cases


def baseline_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    for offset in range(20):
        difference = 5.0 if offset % 4 == 0 else 0.1 + offset / 200
        record = base(start + offset, 120)
        record["baseline_comparisons"] = [{
            "name": "age", "mean_a": 50.0, "sd_a": 5.0, "n_a": 60,
            "mean_b": 50.0 + difference, "sd_b": 5.2, "n_b": 60,
            "correlated_with": ["weight"] if offset % 6 == 0 else [],
        }]
        cases.append(("baseline", record, "Weak continuous-baseline Fisher/Stouffer signal"))
    return cases


def completeness_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    keys = list(FULL_REPORTING)
    for offset in range(20):
        record = base(start + offset, 100)
        record["reporting"] = {key: ((i + offset) % 4 != 0) for i, key in enumerate(keys)}
        if offset % 5 == 0:
            record["reporting"]["patient_public_involvement"] = None
        cases.append(("completeness", record, "Weighted applicable CONSORT reporting fields"))
    return cases


def adversarial_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    for offset in range(10):
        record = base(start + offset, 150)
        mode = offset % 5
        if mode == 0:
            record["reported_stats"] = [{"stat_type": "z", "value": 4.0, "reported_p": 0.01, "adjusted": True}]
        elif mode == 1:
            record["integer_summaries"] = [{"name": "large n", "mean": 2.34, "n": 150, "mean_decimals": 2}]
        elif mode == 2:
            record["arm_ns"] = {}
        elif mode == 3:
            record["subgroups"] = [{
                "variable": "event risk", "levels": [
                    {"name": "low", "n": 75, "events": 2},
                    {"name": "high", "n": 75, "events": 8},
                ], "estimated_parameters": 2, "contingency_cells": [2, 4, 70, 74],
            }]
        else:
            record["subgroups"] = [{
                "variable": "rounded percent", "levels": [
                    {"name": "a", "n": 50, "percentage": 33.3, "percentage_denominator": 150},
                    {"name": "b", "n": 100, "percentage": 66.7, "percentage_denominator": 150},
                ],
            }]
        cases.append(("adversarial", record, "Applicability, sparse cells, adjusted p, or rounding edge"))
    return cases


STATCHECK_SOURCE = {
    "project": "statcheck",
    "version": "1.6.1.9000",
    "commit": "68376e18dc1c6f5c8a1e95b5146ec564c6c7ced9",
    "url": "https://github.com/MicheleNuijten/statcheck/blob/68376e18dc1c6f5c8a1e95b5146ec564c6c7ced9/tests/testthat/test-compute-pvalues.R",
}
SCRUTINY_SOURCE = {
    "project": "scrutiny",
    "version": "0.6.1",
    "commit": "a81cd540332cf402c1ffc798f93534c31da17009",
    "url": "https://github.com/lhdjung/scrutiny",
}


def reference_cases() -> list[dict]:
    """Static upstream expectations; never call ECSAE to manufacture these."""
    stats = [
        ({"stat_type": "t", "value": 2.20, "df1": 28}, 0.036225484778837864),
        ({"stat_type": "F", "value": 2.20, "df1": 2, "df2": 28}, 0.1295932244740937),
        ({"stat_type": "r", "value": 0.22, "df1": 28}, 0.2427389879127071),
        ({"stat_type": "z", "value": 2.20}, 0.02780689502699722),
        ({"stat_type": "chi2", "value": 22.20, "df1": 28}, 0.7719487040329371),
        ({"stat_type": "Q", "value": 22.20, "df1": 28}, 0.7719487040329371),
    ]
    cases: list[dict] = []
    for index, (inputs, expected) in enumerate(stats, 1):
        cases.append({
            "schema_version": "2",
            "case_id": f"REF-{index:03d}",
            "category": "external_statcheck_recompute",
            "provenance": {**STATCHECK_SOURCE, "kind": "literature", "upstream_file": "tests/testthat/test-compute-pvalues.R"},
            "operation": "recompute_p",
            "input": inputs,
            "expected": {"recomputed_p": expected, "absolute_tolerance": 1e-12},
        })
    reported = [0.03, 0.15, 0.26, 0.04, 0.79, 0.79]
    for offset, ((inputs, _), reported_p) in enumerate(zip(stats, reported), 7):
        cases.append({
            "schema_version": "2",
            "case_id": f"REF-{offset:03d}",
            "category": "external_statcheck_classification",
            "provenance": {
                **STATCHECK_SOURCE,
                "kind": "literature",
                "url": STATCHECK_SOURCE["url"].replace("test-compute-pvalues.R", "test-error.R"),
                "upstream_file": "tests/testthat/test-error.R",
            },
            "operation": "statcheck",
            "input": {**inputs, "reported_p": reported_p, "p_decimals": 2},
            "expected": {"consistent": False},
        })
    grim_inputs = [
        ({"mean": 5.19, "mean_decimals": 2, "n": 28}, False),
        ({"mean": 5.19, "mean_decimals": 2, "n": 32}, True),
        ({"mean": 2.84, "mean_decimals": 2, "n": 16, "items": 2}, True),
        ({"mean": 5.21, "mean_decimals": 2, "n": 28}, True),
        ({"mean": 5.22, "mean_decimals": 2, "n": 28}, False),
    ]
    for offset, (inputs, expected) in enumerate(grim_inputs, 13):
        cases.append({
            "schema_version": "2", "case_id": f"REF-{offset:03d}",
            "category": "external_scrutiny_grim", "provenance": {
                **SCRUTINY_SOURCE,
                "kind": "literature",
                "url": "https://github.com/lhdjung/scrutiny/blob/a81cd540332cf402c1ffc798f93534c31da17009/tests/testthat/test-grim.R",
                "upstream_file": "R/grim.R and tests/testthat/test-grim.R",
            },
            "operation": "grim", "input": inputs, "expected": {"consistent": expected},
        })
    grimmer_inputs = [
        ({"mean": 5.21, "mean_decimals": 2, "sd": 1.6, "sd_decimals": 1, "n": 28}, True),
        ({"mean": 3.44, "mean_decimals": 2, "sd": 2.47, "sd_decimals": 2, "n": 18}, False),
        ({"mean": 5.23, "mean_decimals": 2, "sd": 2.55, "sd_decimals": 2, "n": 35}, False),
    ]
    for offset, (inputs, expected) in enumerate(grimmer_inputs, 18):
        cases.append({
            "schema_version": "2", "case_id": f"REF-{offset:03d}",
            "category": "external_scrutiny_grimmer", "provenance": {
                **SCRUTINY_SOURCE,
                "kind": "literature",
                "url": "https://github.com/lhdjung/scrutiny/blob/a81cd540332cf402c1ffc798f93534c31da17009/tests/testthat/test-grimmer.R",
                "upstream_file": "R/grimmer.R and tests/testthat/test-grimmer.R",
            },
            "operation": "grimmer", "input": inputs, "expected": {"consistent": expected},
        })
    assert len(cases) == 20
    return cases


def realistic_cases(start: int) -> list[tuple[str, dict, str]]:
    cases = []
    for offset in range(10):
        n = 200 + 10 * offset
        record = base(start + offset, n)
        record.update({"study_id": f"NCT-FIXTURE-{offset:04d}", "source": "de-identified realistic fixture"})
        record["subgroups"] = [{
            "variable": "sex", "levels": [
                {"name": "female", "n": n // 2, "events": 24 + offset},
                {"name": "male", "n": n - n // 2, "events": 22 + offset},
            ], "prespecified": True, "effect_claimed": offset % 2 == 0,
            "formal_interaction_test": True, "powered_for_interaction": True,
        }]
        record["reported_stats"] = [{
            "stat_type": "chi2", "value": 3.84, "df1": 1,
            "reported_p": 0.05, "p_decimals": 2,
        }]
        cases.append(("realistic", record, "Multi-rule integration fixture shaped like a controlled study"))
    return cases


def main() -> None:
    engine = AuditEngine.from_default()
    builders = [stat_cases, grim_cases, grimmer_cases, partition_cases, interaction_cases,
                baseline_cases, completeness_cases, adversarial_cases, realistic_cases]
    all_cases: list[tuple[str, dict, str]] = []
    for builder in builders:
        all_cases.extend(builder(len(all_cases) + 1))
    assert len(all_cases) == 200
    target = ROOT / "tests" / "regression" / "cases"
    target.mkdir(parents=True, exist_ok=True)
    references = reference_cases()
    for index, case in enumerate(references, 1):
        (target / f"case_{index:03d}.json").write_text(
            json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    for index, (category, record, rationale) in enumerate(all_cases, 21):
        result = engine.audit(record)
        fingerprint = hashlib.sha256(canonical_json(result).encode()).hexdigest()
        case = {
            "schema_version": "2",
            "case_id": f"REG-{index:03d}",
            "category": category,
            "rationale": rationale,
            "provenance": {"project": "ecsae", "kind": "engine_snapshot"},
            "operation": "audit",
            "input_record": record,
            "expected": {
                "fingerprint": fingerprint,
                "feasibility_verdict": result.feasibility_verdict,
                "completeness_score": result.completeness_score,
                "flag_rule_ids": [check.rule_id for check in result.checks if check.verdict == "flag"],
                "config_version": result.config_version,
                "rules_version": result.rules_version,
            },
        }
        (target / f"case_{index:03d}.json").write_text(
            json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    fixture = ROOT / "tests" / "fixtures"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "rerun_records.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for _, record, _ in all_cases),
        encoding="utf-8", newline="\n",
    )
    matrix: dict[str, int] = {}
    for case in references:
        matrix[case["category"]] = matrix.get(case["category"], 0) + 1
    for category, _, _ in all_cases:
        matrix[category] = matrix.get(category, 0) + 1
    (ROOT / "tests" / "regression" / "MATRIX.json").write_text(
        json.dumps({
            "total": len(references) + len(all_cases),
            "externally_anchored": len(references),
            "engine_snapshots": len(all_cases),
            "sources": {"literature": len(references), "snapshot": len(all_cases)},
            "categories": matrix,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(json.dumps({"generated": len(references) + len(all_cases), "config_version": engine.config.version, "categories": matrix}, sort_keys=True))


if __name__ == "__main__":
    main()
