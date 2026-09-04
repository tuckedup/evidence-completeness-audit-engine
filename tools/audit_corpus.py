from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ecsae.engine import AuditEngine
from ecsae.data.clinical_trials import upgrade_normalized_record


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    engine = AuditEngine.from_default()
    overall: Counter[str] = Counter()
    per_rule: dict[str, Counter[str]] = defaultdict(Counter)
    per_rule_study: dict[str, Counter[str]] = defaultdict(Counter)
    per_rule_na_scope: dict[str, Counter[str]] = defaultdict(Counter)
    hydration: Counter[str] = Counter()
    scores: list[float] = []
    records = 0
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            source_record = upgrade_normalized_record(json.loads(line))
            result = engine.audit(source_record)
            records += 1
            overall[result.feasibility_verdict] += 1
            scores.append(result.completeness_score)
            record_rules: dict[str, list[str]] = defaultdict(list)
            for check in result.checks:
                per_rule[check.rule_id][check.verdict] += 1
                record_rules[check.rule_id].append(check.verdict)
            for rule_id, verdicts in record_rules.items():
                study_verdict = next(
                    verdict for verdict in ("flag", "warn", "pass", "not_applicable")
                    if verdict in verdicts
                )
                per_rule_study[rule_id][study_verdict] += 1
                if study_verdict == "not_applicable":
                    scope = (
                        "source_schema"
                        if rule_id in source_record.get("metadata", {}).get(
                            "source_schema_unavailable_rules", []
                        )
                        else "record"
                    )
                    per_rule_na_scope[rule_id][f"not_applicable_by_{scope}"] += 1
            hydration["studies_with_subgroup_partitions"] += bool(source_record.get("subgroups"))
            hydration["studies_with_mean_sd_summaries"] += bool(source_record.get("integer_summaries"))
            hydration["studies_with_baseline_comparisons"] += bool(source_record.get("baseline_comparisons"))
            hydration["studies_with_reported_analyses"] += bool(
                source_record.get("metadata", {}).get("reported_analyses_count")
            )
            hydration["studies_with_ci_p_eligible_analyses"] += bool(
                source_record.get("metadata", {}).get("ci_p_eligible_analyses_before_cap")
            )
            hydration["ci_p_eligible_analyses_before_cap"] += int(
                source_record.get("metadata", {}).get("ci_p_eligible_analyses_before_cap", 0) or 0
            )
            hydration["ci_p_eligible_analyses_processed"] += len(source_record.get("reported_effects", []))
            hydration["ci_p_analyses_truncated"] += int(
                source_record.get("metadata", {}).get("ci_p_analyses_truncated", 0) or 0
            )
            hydration["ci_p_high_confidence_analyses_processed"] += sum(
                effect.get("applicability_tier") == "high_confidence"
                for effect in source_record.get("reported_effects", [])
            )
            hydration["ci_p_approximate_review_analyses_processed"] += sum(
                effect.get("applicability_tier") == "approximate_review"
                for effect in source_record.get("reported_effects", [])
            )
            hydration["studies_with_analysis_population_description"] += (
                source_record.get("reporting", {}).get("analysis_population") is True
            )
    report = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": str(source),
        "corpus_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "records": records,
        "config_version": engine.config.version,
        "rules_version": engine.config.rules_version,
        "overall_verdicts": dict(sorted(overall.items())),
        "rule_verdicts": {rule: dict(sorted(counts.items())) for rule, counts in sorted(per_rule.items())},
        "rule_study_outcomes": {
            rule: {
                **dict(sorted(counts.items())),
                **dict(sorted(per_rule_na_scope[rule].items())),
                "applicable_studies": records - counts.get("not_applicable", 0),
                "flag_rate_among_applicable": (
                    counts.get("flag", 0) / (records - counts.get("not_applicable", 0))
                    if records - counts.get("not_applicable", 0) else None
                ),
            }
            for rule, counts in sorted(per_rule_study.items())
        },
        "registry_hydration": dict(sorted(hydration.items())),
        "ci_p_accounting": {
            "eligible_before_cap": hydration["ci_p_eligible_analyses_before_cap"],
            "processed_after_cap": hydration["ci_p_eligible_analyses_processed"],
            "truncated_due_to_cap": hydration["ci_p_analyses_truncated"],
            "invariant_holds": (
                hydration["ci_p_eligible_analyses_processed"] + hydration["ci_p_analyses_truncated"]
                == hydration["ci_p_eligible_analyses_before_cap"]
            ),
            "processed_tiers": {
                "high_confidence": hydration["ci_p_high_confidence_analyses_processed"],
                "approximate_review": hydration["ci_p_approximate_review_analyses_processed"],
            },
        },
        "source_schema_capability": {
            "supported_rules": sorted(
                item["rule_id"] for item in engine.rule_manifest()
                if item["rule_id"] not in {
                    "ENROLLMENT-ARM-SUM", "GRIM", "GRIMMER", "SUBGROUP-PERCENT",
                    "SUBGROUP-INTERACTION-TEST", "SUBGROUP-INTERACTION-POWER",
                    "SUBGROUP-EVENT-FLOOR", "SUBGROUP-EXPECTED-CELL",
                }
            ),
            "unavailable_rules": [
                "ENROLLMENT-ARM-SUM", "GRIM", "GRIMMER", "SUBGROUP-PERCENT",
                "SUBGROUP-INTERACTION-TEST", "SUBGROUP-INTERACTION-POWER",
                "SUBGROUP-EVENT-FLOOR", "SUBGROUP-EXPECTED-CELL",
            ],
            "interpretation": (
                "ClinicalTrials.gov API v2 lacks the source fields or provenance required "
                "for these rules; record-level absence is counted separately."
            ),
        },
        "completeness_score": {
            "mean": sum(scores) / len(scores),
            "p05": percentile(scores, 0.05),
            "p50": percentile(scores, 0.50),
            "p95": percentile(scores, 0.95),
        },
        "interpretation": "Counts are automated screening outcomes, not adjudicated errors or misconduct findings.",
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"records": records, "overall_verdicts": report["overall_verdicts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
