from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .config import load_config
from .engine import AuditEngine
from .evaluation import agreement_metrics, bootstrap_intervals
from .extract import extract_jsonl
from .normalize import canonical_json


def _load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(value: Any, destination: str | None = None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if destination:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(rendered)


def audit_command(args: argparse.Namespace) -> int:
    result = AuditEngine(load_config(args.config)).audit(_load_json(args.input))
    _write_json(result.model_dump(mode="json"), args.output)
    return 0


def pull_command(args: argparse.Namespace) -> int:
    from .data.clinical_trials import pull_to_jsonl

    report = pull_to_jsonl(args.output, limit=args.limit)
    if args.manifest:
        _write_json(report, args.manifest)
    _write_json(report)
    return 0


def extract_command(args: argparse.Namespace) -> int:
    _write_json({"extracted": extract_jsonl(args.input, args.output), "output": args.output})
    return 0


def rerun_command(args: argparse.Namespace) -> int:
    from .data.clinical_trials import upgrade_normalized_record

    engine = AuditEngine(load_config(args.config))
    source = Path(args.input)
    with source.open(encoding="utf-8") as handle:
        records = [upgrade_normalized_record(json.loads(line)) for line in handle if line.strip()]
    hashes: list[list[str]] = []
    for _ in range(args.runs):
        run_hashes = [
            hashlib.sha256(canonical_json(engine.audit(record)).encode()).hexdigest()
            for record in records
        ]
        hashes.append(run_hashes)
    mismatches = [
        index for index in range(len(records))
        if len({run[index] for run in hashes}) != 1
    ]
    report = {
        "schema_version": "1",
        "records": len(records),
        "runs": args.runs,
        "comparisons": len(records) * max(0, args.runs - 1),
        "mismatch_count": len(mismatches),
        "rerun_agreement": 1.0 if not records or not mismatches else 1 - len(mismatches) / len(records),
        "mismatch_indexes": mismatches,
        "corpus_path": str(source),
        "corpus_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "config_version": engine.config.version,
        "rules_version": engine.config.rules_version,
        "model_revision": engine.config.model_revision,
    }
    _write_json(report, args.output)
    return 1 if mismatches else 0


def evaluate_command(args: argparse.Namespace) -> int:
    engine = AuditEngine(load_config(args.config))
    reference: list[bool] = []
    predicted: list[bool] = []
    with Path(args.input).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            metadata = item.setdefault("metadata", {})
            label = item.pop("reference_flag", metadata.pop("reference_flag", None))
            if label is None:
                raise ValueError("each evaluation record needs reference_flag")
            result = engine.audit(item)
            reference.append(bool(label))
            predicted.append(result.feasibility_verdict == "flag")
    report = agreement_metrics(reference, predicted)
    report.update({
        "schema_version": "1",
        "confidence_intervals_95": bootstrap_intervals(
            reference, predicted, replicates=args.bootstrap_replicates, seed=args.seed
        ),
        "config_version": engine.config.version,
        "reference_standard": args.reference_standard,
        "evaluation_scope": "structured-label comparison",
        "corpus_path": str(Path(args.input).resolve()),
        "limitations": [
            "Agreement measures the supplied reference labels, not clinical truth.",
            "When reference and predicted labels share the same versioned checks, this measures extraction/pipeline fidelity rather than independent rule validity.",
            "Manual adjudication must be reported separately.",
        ],
    })
    _write_json(report, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecsae")
    parser.add_argument("--config", help="resolved TOML config path")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="audit one JSON study record")
    audit.add_argument("input", help="input JSON path or - for stdin")
    audit.add_argument("--output")
    audit.set_defaults(func=audit_command)
    pull = sub.add_parser("pull-ctg", help="download public ClinicalTrials.gov v2 records")
    pull.add_argument("--output", required=True)
    pull.add_argument("--limit", type=int, default=2000)
    pull.add_argument("--manifest", help="write the retrieval manifest JSON")
    pull.set_defaults(func=pull_command)
    extract = sub.add_parser("extract", help="run deterministic offline numeric extraction")
    extract.add_argument("input")
    extract.add_argument("output")
    extract.set_defaults(func=extract_command)
    rerun = sub.add_parser("rerun", help="verify canonical audit output across repeated runs")
    rerun.add_argument("input")
    rerun.add_argument("--runs", type=int, default=2)
    rerun.add_argument("--output")
    rerun.set_defaults(func=rerun_command)
    evaluate = sub.add_parser("evaluate", help="measure agreement against supplied reference labels")
    evaluate.add_argument("input")
    evaluate.add_argument("--output")
    evaluate.add_argument("--bootstrap-replicates", type=int, default=2000)
    evaluate.add_argument("--seed", type=int, default=20250314)
    evaluate.add_argument("--reference-standard", default="deterministic structured-field labeler")
    evaluate.set_defaults(func=evaluate_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
