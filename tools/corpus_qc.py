"""Verify the persisted ClinicalTrials.gov cohort and summarize hydration."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ecsae.data.clinical_trials import upgrade_normalized_record
from ecsae.models import StudyRecord


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    failures: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    identifiers: set[str] = set()
    records = 0
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            raw = upgrade_normalized_record(json.loads(line))
            record = StudyRecord.model_validate(raw)
            records += 1
            metadata = record.metadata
            for name, actual, expected in (
                ("not_completed", metadata.get("overall_status"), "COMPLETED"),
                ("not_interventional", metadata.get("study_type"), "INTERVENTIONAL"),
                ("not_randomized", metadata.get("allocation"), "RANDOMIZED"),
                ("no_results", metadata.get("has_results"), True),
            ):
                if actual != expected:
                    failures[name] += 1
            if not record.study_id or record.study_id in identifiers:
                failures["missing_or_duplicate_study_id"] += 1
            else:
                identifiers.add(record.study_id)
            counts["with_participant_flow_arm_counts"] += bool(record.arm_ns)
            counts["with_two_or_more_flow_arms"] += len(record.arm_ns) >= 2
            counts["with_baseline_subgroup_partitions"] += bool(record.subgroups)
            counts["with_baseline_mean_sd_summaries"] += bool(record.integer_summaries)
            counts["with_baseline_comparisons"] += bool(record.baseline_comparisons)
            counts["with_reported_outcome_analyses"] += bool(metadata.get("reported_analyses_count"))
            counts["with_ci_p_eligible_analyses"] += bool(metadata.get("ci_p_eligible_analyses_before_cap"))
            counts["ci_p_eligible_analyses_before_cap"] += int(
                metadata.get("ci_p_eligible_analyses_before_cap", 0) or 0
            )
            counts["ci_p_eligible_analyses_processed"] += len(record.reported_effects)
            counts["ci_p_analyses_truncated"] += int(
                metadata.get("ci_p_analyses_truncated", 0) or 0
            )
            counts["ci_p_high_confidence_analyses_processed"] += sum(
                effect.applicability_tier == "high_confidence" for effect in record.reported_effects
            )
            counts["ci_p_approximate_review_analyses_processed"] += sum(
                effect.applicability_tier == "approximate_review" for effect in record.reported_effects
            )
            counts["with_analysis_population_description"] += (
                record.reporting.get("analysis_population") is True
            )
            counts["arm_counts_marked_noncomparable"] += (
                metadata.get("arm_count_basis") == "participant_flow_first_period_started"
            )
            if not metadata.get("source_schema_unavailable_rules"):
                failures["missing_source_schema_capability_metadata"] += 1
    manifest_checks = {
        "checksum_matches": checksum == manifest.get("sha256"),
        "accepted_count_matches": records == manifest.get("accepted"),
        "hard_filters_match": manifest.get("request_parameters") == {
            "filter.overallStatus": "COMPLETED",
            "filter.advanced": "AREA[DesignAllocation]RANDOMIZED AND AREA[StudyType]INTERVENTIONAL",
            "aggFilters": "results:with",
        },
    }
    passed = records == 2000 and not failures and all(manifest_checks.values())
    report = {
        "schema_version": "2", "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed, "records": records, "unique_study_ids": len(identifiers),
        "source": "ClinicalTrials.gov API v2", "snapshot_sha256": checksum,
        "manifest": str(Path(args.manifest)), "manifest_checks": manifest_checks,
        "record_invariant_failures": dict(sorted(failures.items())),
        "hydration": dict(sorted(counts.items())),
        "ci_p_accounting": {
            "eligible_before_cap": counts["ci_p_eligible_analyses_before_cap"],
            "processed_after_cap": counts["ci_p_eligible_analyses_processed"],
            "truncated_due_to_cap": counts["ci_p_analyses_truncated"],
            "invariant_holds": (
                counts["ci_p_eligible_analyses_processed"] + counts["ci_p_analyses_truncated"]
                == counts["ci_p_eligible_analyses_before_cap"]
            ),
            "processed_tiers": {
                "high_confidence": counts["ci_p_high_confidence_analyses_processed"],
                "approximate_review": counts["ci_p_approximate_review_analyses_processed"],
            },
        },
        "interpretation": (
            "Every persisted record passes the completed/interventional/randomized/results-posted "
            "invariants. Hydration counts describe available registry fields, not audit accuracy. "
            "Participant-flow STARTED counts are intentionally not equated with protocol enrollment."
        ),
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"passed": passed, "records": records, "failures": dict(failures), "hydration": dict(counts)}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
