"""Create a deterministic, blinded, stratified human-adjudication queue."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ecsae.engine import AuditEngine

FAMILIES = (
    "STATCHECK", "BASELINE-ANOMALY", "SUBGROUP-PARTITION", "REPORTING-COMPLETENESS",
)
QUOTAS = {
    ("STATCHECK", "warn"): 45,
    ("STATCHECK", "pass"): 35,
    ("STATCHECK", "not_applicable"): 10,
    ("BASELINE-ANOMALY", "warn"): 10,
    ("BASELINE-ANOMALY", "pass"): 20,
    ("BASELINE-ANOMALY", "not_applicable"): 10,
    ("SUBGROUP-PARTITION", "warn"): 10,
    ("SUBGROUP-PARTITION", "pass"): 20,
    ("SUBGROUP-PARTITION", "not_applicable"): 10,
    ("REPORTING-COMPLETENESS", "warn"): 15,
    ("REPORTING-COMPLETENESS", "pass"): 15,
}
TARGET_ROWS = 200
VERDICT_ORDER = ("flag", "warn", "pass", "not_applicable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--seed", type=int, default=20250903)
    args = parser.parse_args()
    source = Path(args.input)
    engine = AuditEngine.from_default()
    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            result = engine.audit(record)
            by_family = defaultdict(list)
            for check in result.checks:
                if check.rule_id in FAMILIES:
                    by_family[check.rule_id].append(check)
            for family, checks in by_family.items():
                verdict = next(item for item in VERDICT_ORDER if any(c.verdict == item for c in checks))
                relevant = [check for check in checks if check.verdict == verdict]
                strata[(family, verdict)].append({
                    "study_id": record.get("study_id", ""),
                    "title": record.get("title", ""),
                    "source_url": f"https://clinicaltrials.gov/study/{record.get('study_id', '')}",
                    "check_family": family,
                    "pipeline_label": verdict,
                    "pipeline_rationale": " | ".join(sorted({check.rationale for check in relevant})),
                    "pipeline_evidence_json": json.dumps(
                        [check.evidence for check in relevant], sort_keys=True
                    ),
                    "reference_label": "",
                    "adjudicator_1_label": "",
                    "adjudicator_1_id": "",
                    "adjudicator_2_label": "",
                    "adjudicator_2_id": "",
                    "consensus_label": "",
                    "notes": "",
                    "adjudicated_at_utc": "",
                })

    rng = random.Random(args.seed)
    shuffled = {}
    for key, rows in strata.items():
        shuffled[key] = list(rows)
        rng.shuffle(shuffled[key])
    selected: list[dict] = []
    selection = {}
    offsets = defaultdict(int)
    for key, quota in QUOTAS.items():
        available = shuffled.get(key, [])
        take = min(quota, len(available))
        selected.extend(available[:take])
        offsets[key] = take
        selection[f"{key[0]}:{key[1]}"] = {
            "available": len(available), "requested": quota, "selected": take,
        }

    shortage = TARGET_ROWS - len(selected)
    if shortage > 0:
        fill_pool: list[tuple[tuple[str, str], dict]] = []
        for key, rows in shuffled.items():
            fill_pool.extend((key, row) for row in rows[offsets[key]:])
        rng.shuffle(fill_pool)
        for key, row in fill_pool[:shortage]:
            selected.append(row)
            label = f"{key[0]}:{key[1]}"
            selection.setdefault(label, {
                "available": len(shuffled[key]), "requested": 0, "selected": 0,
            })
            selection[label]["selected"] += 1
            selection[label]["filled_shortfall"] = selection[label].get("filled_shortfall", 0) + 1

    rng.shuffle(selected)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(selected[0]) if selected else []
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)
    report = {
        "schema_version": "2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "queue": str(target),
        "rows": len(selected),
        "target_rows": TARGET_ROWS,
        "unit": "study-by-check-family; multiple analysis-level findings are aggregated",
        "strata": selection,
        "blinding": (
            "Pipeline output is retained for error taxonomy; two adjudicators must work "
            "independently and remain blinded to each other's labels."
        ),
        "status": "awaiting two independent human adjudicators",
    }
    manifest = Path(args.manifest)
    manifest.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({
        "rows": len(selected), "status": report["status"], "strata": selection,
    }, sort_keys=True))
    return 0 if len(selected) == TARGET_ROWS else 1


if __name__ == "__main__":
    raise SystemExit(main())
