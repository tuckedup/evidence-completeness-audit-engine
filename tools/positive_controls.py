"""Measure perturbation sensitivity by magnitude using controlled mutations on registry carriers."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ecsae.engine import AuditEngine


DOSES = {
    "ENROLLMENT-ARM-SUM": [("count_+1", 1), ("count_+2", 2), ("count_+5", 5)],
    "SUBGROUP-PARTITION": [("count_+1", 1), ("count_+2", 2), ("count_+5", 5)],
    "STATCHECK": [("p_+0.001", 0.001), ("p_+0.010", 0.010), ("p_+0.050", 0.050)],
    "GRIM": [("mean_+1_unit", 1), ("mean_+2_units", 2), ("mean_+5_units", 5)],
}


def matching_check(result, rule_id: str, **evidence):
    candidates = [item for item in result.checks if item.rule_id == rule_id]
    for item in reversed(candidates):
        if all(item.evidence.get(key) == value for key, value in evidence.items()):
            return item
    raise AssertionError((rule_id, evidence))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--per-rule", type=int, default=50, help="carrier records per dose")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.per_rule < 1:
        raise ValueError("--per-rule must be positive")
    source = Path(args.input)
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    engine = AuditEngine.from_default()
    cases: list[dict] = []
    counts: Counter[tuple[str, str]] = Counter()

    def add_case(
        rule_id: str,
        dose: str,
        record: dict,
        corrupted: dict,
        locator: dict,
        mutation: str,
        field_origin: str,
    ) -> None:
        clean = matching_check(engine.audit(record), rule_id, **locator)
        changed = matching_check(engine.audit(corrupted), rule_id, **locator)
        cases.append({
            "study_id": record.get("study_id"),
            "rule_id": rule_id,
            "dose": dose,
            "mutation": mutation,
            "field_origin": field_origin,
            "clean_verdict": clean.verdict,
            "corrupted_verdict": changed.verdict,
            "clean_evidence": clean.evidence,
            "corrupted_evidence": changed.evidence,
            "clean_nonpass": clean.verdict in {"warn", "flag"},
            "detected": changed.verdict in {"warn", "flag"},
            "hard_flag": changed.verdict == "flag",
        })
        counts[(rule_id, dose)] += 1

    for dose, delta in DOSES["ENROLLMENT-ARM-SUM"]:
        for record in records:
            if counts[("ENROLLMENT-ARM-SUM", dose)] >= args.per_rule:
                break
            if record.get("arm_ns") and sum(record["arm_ns"].values()) == record["randomized_n"]:
                clean = copy.deepcopy(record)
                clean["arm_counts_comparable_to_randomized_n"] = True
                clean.setdefault("metadata", {})["arm_count_basis"] = "randomized_assignment_counts"
                corrupted = copy.deepcopy(clean)
                arm = sorted(corrupted["arm_ns"])[0]
                corrupted["arm_ns"][arm] += delta
                add_case(
                    "ENROLLMENT-ARM-SUM", dose, clean, corrupted, {},
                    f"increment arm {arm!r} by {delta}", "registry count",
                )

    for dose, delta in DOSES["SUBGROUP-PARTITION"]:
        for record in records:
            if counts[("SUBGROUP-PARTITION", dose)] >= args.per_rule:
                break
            for index, subgroup in enumerate(record.get("subgroups", [])):
                population = subgroup.get("population_n")
                if (
                    population and subgroup.get("mutually_exclusive", True)
                    and subgroup.get("exhaustive", True)
                    and sum(level["n"] for level in subgroup["levels"]) == population
                ):
                    corrupted = copy.deepcopy(record)
                    corrupted["subgroups"][index]["levels"][0]["n"] += delta
                    add_case(
                        "SUBGROUP-PARTITION", dose, record, corrupted,
                        {"variable": subgroup["variable"]},
                        f"increment first subgroup level by {delta}", "registry count",
                    )
                    break

    for dose, delta in DOSES["STATCHECK"]:
        for record in records[: args.per_rule]:
            clean = copy.deepcopy(record)
            clean["reported_stats"] = [{
                "stat_type": "t", "value": 2.20, "df1": 28, "reported_p": 0.036,
                "statistic_decimals": 2, "p_decimals": 3,
                "source": "synthetic field anchored to statcheck upstream case",
            }]
            corrupted = copy.deepcopy(clean)
            corrupted["reported_stats"][0]["reported_p"] = round(0.036 + delta, 3)
            add_case(
                "STATCHECK", dose, clean, corrupted,
                {"method_family": "test_statistic", "index": 0},
                f"increase reported p by {delta:.3f}", "synthetic statistic on registry carrier",
            )

    for dose, units in DOSES["GRIM"]:
        for record_index, record in enumerate(records[: args.per_rule]):
            clean = copy.deepcopy(record)
            original_count = len(clean.get("integer_summaries", []))
            n = 10 + record_index % 81
            integer_total = 3 * n + (record_index * 7) % n
            feasible_mean = round(integer_total / n, 2)
            clean.setdefault("integer_summaries", []).append({
                "name": "perturbation-sensitivity integer mean",
                "mean": feasible_mean,
                "mean_decimals": 2,
                "n": n,
                "integer_scale": True,
            })
            corrupted = copy.deepcopy(clean)
            corrupted["integer_summaries"][-1]["mean"] = round(feasible_mean + units * 0.01, 2)
            add_case(
                "GRIM", dose, clean, corrupted, {"index": original_count},
                f"increase mean by {units} unit(s) in the last reported decimal",
                "synthetic integer-scale summary on registry carrier",
            )

    expected = args.per_rule * sum(len(value) for value in DOSES.values())
    by_rule_and_dose = {}
    for rule_id, doses in DOSES.items():
        by_rule_and_dose[rule_id] = {}
        for dose, _ in doses:
            subset = [case for case in cases if case["rule_id"] == rule_id and case["dose"] == dose]
            by_rule_and_dose[rule_id][dose] = {
                "cases": len(subset),
                "detected": sum(case["detected"] for case in subset),
                "detection_rate": sum(case["detected"] for case in subset) / len(subset),
                "hard_flags": sum(case["hard_flag"] for case in subset),
                "hard_flag_rate": sum(case["hard_flag"] for case in subset) / len(subset),
                "clean_nonpass": sum(case["clean_nonpass"] for case in subset),
            }
    report = {
        "schema_version": "2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "carrier_records": "real hard-filtered ClinicalTrials.gov records",
        "cases": len(cases),
        "carriers_per_dose": args.per_rule,
        "passed": (
            len(cases) == expected
            and all(not case["clean_nonpass"] for case in cases)
            and all(
                counts[(rule, dose)] == args.per_rule
                for rule, doses in DOSES.items()
                for dose, _ in doses
            )
        ),
        "per_rule_and_dose": by_rule_and_dose,
        "case_results": cases,
        "field_provenance": {
            "ENROLLMENT-ARM-SUM": "registry counts; comparability enabled for the controlled experiment",
            "SUBGROUP-PARTITION": "genuinely present registry subgroup counts",
            "STATCHECK": "synthetic recomputable statistic and p-value on a registry carrier",
            "GRIM": "synthetic integer-scale provenance and mean on a registry carrier",
        },
        "limitations": [
            "Only subgroup partition mutations alter a field naturally eligible in every selected registry record; enrollment comparability is experimentally enabled.",
            "STATCHECK and GRIM fields are synthetic because ClinicalTrials.gov does not expose test-statistic degrees of freedom or raw integer-scale provenance.",
            "Perturbation sensitivity means warn or flag; hard-flag rates are reported separately.",
            "Controlled mutations measure sensitivity to specified perturbations, not clinical-corpus agreement or extraction recall.",
        ],
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({
        "passed": report["passed"], "cases": len(cases), "per_rule_and_dose": by_rule_and_dose,
    }, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
