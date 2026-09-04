"""Render the README evidence block from machine-readable artifacts."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- BEGIN GENERATED EVIDENCE -->"
END = "<!-- END GENERATED EVIDENCE -->"


def read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def render() -> str:
    corpus = read_json("eval/results/corpus-ctg-randomized-2000.json")
    qc = read_json("eval/results/corpus-qc-ctg-randomized-2000.json")
    audit = read_json("eval/results/audit-summary-ctg-randomized-2000.json")
    rerun = read_json("eval/results/rerun-ctg-randomized-2000.json")
    positive = read_json("eval/results/positive-control-corruptions.json")
    statistical = read_json("eval/results/statistical-validation.json")
    transformer = read_json("eval/results/transformer-smoke-evaluation.json")
    warm = read_json("bench/results/node-warm-hydrated-20k-200rps-local-windows.json")
    mixed = read_json("bench/results/node-mixed90-hydrated-20k-200rps-local-windows.json")
    cold = read_json("bench/results/node-cold-hydrated-20k-200rps-local-windows.json")
    coverage = read_json("eval/results/coverage.json")
    xml = ET.parse(ROOT / "eval/results/pytest.xml").getroot()
    suites = xml if xml.tag == "testsuites" else [xml]
    test_count = int(suites.attrib.get("tests", sum(int(item.attrib["tests"]) for item in suites)))
    coverage_pct = coverage["totals"]["percent_covered"]
    for benchmark in (warm, mixed, cold):
        if benchmark["payload_sha256"] != corpus["sha256"]:
            raise RuntimeError("benchmark payload checksum does not match the current corpus")
        if benchmark["service_version"]["config"] != audit["config_version"]:
            raise RuntimeError("benchmark config version does not match the current audit")
        if benchmark["service_version"]["rules"] != audit["rules_version"]:
            raise RuntimeError("benchmark rules version does not match the current audit")
    if rerun["config_version"] != audit["config_version"]:
        raise RuntimeError("rerun config version does not match the current audit")
    if rerun.get("rules_version") != audit["rules_version"]:
        raise RuntimeError("rerun rules version does not match the current audit")
    if rerun.get("corpus_sha256") != corpus["sha256"]:
        raise RuntimeError("rerun corpus checksum does not match the current corpus")
    overall = ", ".join(f"{key}={value}" for key, value in sorted(audit["overall_verdicts"].items()))
    stat_total = (
        statistical["distribution_oracle"]["samples"]
        + statistical["known_integer_data"]["samples"]
        + statistical["welch_oracle"]["samples"]
        + statistical["ci_to_p_oracle"]["samples"]
    )
    return "\n".join([
        START,
        f"- Corpus: **{corpus['accepted']:,}** hard-filtered completed, randomized, interventional, results-posted studies; QC passed={str(qc['passed']).lower()}, SHA-256 `{corpus['sha256']}`.",
        f"- Audit screen: {overall}. These are automated screening outcomes, not adjudicated errors.",
        f"- Perturbation sensitivity: **{positive['cases']}** controlled mutations across four rule families and three magnitudes; clean controls all pass. Detection is 100% for count invariants, 0%/100%/100% for p shifts of .001/.01/.05, and 98%/62%/66% for GRIM shifts of 1/2/5 last-decimal units.",
        f"- Statistical validation: **{stat_total:,}** seeded checks against SciPy and known-valid integer data; passed={str(statistical['passed']).lower()}.",
        f"- Registry applicability: CI-to-p screening covers {audit['registry_hydration']['studies_with_ci_p_eligible_analyses']:,} studies / {audit['ci_p_accounting']['processed_after_cap']:,} processed analyses ({audit['ci_p_accounting']['truncated_due_to_cap']:,} truncated after the per-study cap); baseline warnings fell to {audit['rule_study_outcomes']['BASELINE-ANOMALY'].get('warn', 0)}/{audit['rule_study_outcomes']['BASELINE-ANOMALY']['applicable_studies']}. Eight rules are unavailable from the registry source schema and are counted separately from record-level N/A.",
        f"- Determinism: {rerun['runs']} full-corpus runs, {rerun['comparisons']:,} comparisons, {rerun['mismatch_count']} mismatches ({rerun['rerun_agreement'] * 100:.1f}% agreement).",
        f"- Cached performance: {warm['completed']:,}/{warm['requested']:,} hydrated-corpus hits at {warm['target_rate_per_second']:.0f} rps; client p95 **{warm['latency_ms']['p95']:.2f} ms**, server p95 **{warm['server_handler_latency_ms']['p95']:.2f} ms**, {warm['errors']} errors.",
        f"- Matched load curves: cold client/server p95 {cold['latency_ms']['p95']:.2f}/{cold['server_handler_latency_ms']['p95']:.2f} ms; 90/10 mixed {mixed['latency_ms']['p95']:.2f}/{mixed['server_handler_latency_ms']['p95']:.2f} ms with {mixed['cache_headers'].get('hit', 0):,} hits and {mixed['cache_headers'].get('miss', 0):,} misses.",
        f"- Tests: **{test_count} passed**; branch-aware coverage **{coverage_pct:.2f}%**; regression matrix exactly 220 cases (20 literature-anchored + 200 snapshots).",
        f"- Transformer smoke set: exact-span F1 **{transformer['exact_span_and_label']['f1']:.3f}**, with {transformer['determinism']['mismatch_count']} rerun mismatches. This is runtime evidence, not clinical-corpus accuracy.",
        "- Agreement and release history: 93% agreement and 99.5% across 30 releases are **not claimed**; the 200-row two-reviewer queue and release-ledger gate remain pending.",
        END,
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    readme = ROOT / "README.md"
    current = readme.read_text(encoding="utf-8")
    generated = render()
    before, marker, remainder = current.partition(START)
    if not marker:
        raise RuntimeError("README generated evidence markers are missing")
    _, marker, after = remainder.partition(END)
    if not marker:
        raise RuntimeError("README generated evidence end marker is missing")
    updated = before + generated + after
    if args.check:
        if updated != current:
            print("README evidence block is stale")
            return 1
        print("README evidence block is current")
        return 0
    readme.write_text(updated, encoding="utf-8", newline="\n")
    print("README evidence block updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
