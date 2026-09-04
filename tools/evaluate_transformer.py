"""Run the pinned GLiNER adapter on a small, explicit synthetic smoke set."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from ecsae.extract.offline import GLiNERBiomedicalExtractor

REVISION = "146c133ff9da643f738f9817bc38cee479cebe8c"
EXAMPLES = [
    ("A total of 120 participants were randomized.", [("120 participants", "sample size")]),
    ("The trial enrolled 84 patients.", [("84 patients", "sample size")]),
    ("Sample size was n=246 at baseline.", [("n=246", "sample size")]),
    ("We analyzed 73 volunteers in the final model.", [("73 volunteers", "sample size")]),
    ("The placebo arm included 51 participants.", [("placebo arm", "arm"), ("51 participants", "sample size")]),
    ("Treatment group B had n=49.", [("Treatment group B", "arm"), ("n=49", "sample size")]),
    ("Participants assigned to usual care numbered 62.", [("usual care", "arm"), ("62", "sample size")]),
    ("The high-dose arm contained 45 subjects.", [("high-dose arm", "arm"), ("45 subjects", "sample size")]),
    ("Among women, the treatment effect was larger.", [("women", "subgroup")]),
    ("The prespecified age subgroup showed no heterogeneity.", [("age subgroup", "subgroup")]),
    ("Patients with diabetes formed the risk subgroup.", [("Patients with diabetes", "subgroup")]),
    ("Results differed in participants aged over 65 years.", [("participants aged over 65 years", "subgroup")]),
    ("The interaction yielded p = 0.032.", [("p = 0.032", "p-value")]),
    ("There was no difference (p<.001).", [("p<.001", "p-value")]),
    ("The reported probability was P = .047.", [("P = .047", "p-value")]),
    ("Evidence was weak, with p > 0.20.", [("p > 0.20", "p-value")]),
    ("The comparison gave t(98) = 2.11.", [("t(98) = 2.11", "test statistic")]),
    ("We observed F(2, 47)=4.30.", [("F(2, 47)=4.30", "test statistic")]),
    ("The chi-square result was chi2(3)=8.71.", [("chi2(3)=8.71", "test statistic")]),
    ("A z statistic of z = -1.96 was reported.", [("z = -1.96", "test statistic")]),
    ("There were 12 deaths in follow-up.", [("12 deaths", "event count")]),
    ("Investigators recorded 37 adverse events.", [("37 adverse events", "event count")]),
    ("Only 4 relapses occurred in the intervention arm.", [("4 relapses", "event count"), ("intervention arm", "arm")]),
    ("The control group experienced 19 hospitalizations.", [("control group", "arm"), ("19 hospitalizations", "event count")]),
    ("We randomized 120 participants; control arm had 58 and active arm had 62.", [("120 participants", "sample size"), ("control arm", "arm"), ("58", "sample size"), ("active arm", "arm"), ("62", "sample size")]),
    ("In women, t(42)=2.45 and p=.018, with 9 events.", [("women", "subgroup"), ("t(42)=2.45", "test statistic"), ("p=.018", "p-value"), ("9 events", "event count")]),
    ("The placebo group (n=40) had 6 events.", [("placebo group", "arm"), ("n=40", "sample size"), ("6 events", "event count")]),
    ("For adults younger than 40, F(1,88)=5.2, p=.025.", [("adults younger than 40", "subgroup"), ("F(1,88)=5.2", "test statistic"), ("p=.025", "p-value")]),
    ("No participant numbers or inferential statistics were stated.", []),
    ("Blood pressure and glucose were measured at discharge.", []),
]


def pct(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))]


def gold_spans(text: str, specs: list[tuple[str, str]]) -> set[tuple[int, int, str]]:
    spans = set()
    cursor = 0
    for substring, label in specs:
        start = text.index(substring, cursor)
        spans.add((start, start + len(substring), label))
        cursor = start + len(substring)
    return spans


def normalized_prediction(item: dict) -> tuple[int, int, str]:
    return int(item["start"]), int(item["end"]), str(item["label"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="data/raw/models/gliner-biomed-base-v1.0")
    parser.add_argument("--output", default="eval/results/transformer-smoke-evaluation.json")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    model_path = Path(args.model)
    weight_path = model_path / "pytorch_model.bin"
    load_started = time.perf_counter()
    extractor = GLiNERBiomedicalExtractor(str(model_path), revision=REVISION)
    load_seconds = time.perf_counter() - load_started
    tp = fp = fn = 0
    latencies = []
    rows = []
    first_predictions = []
    for text, specs in EXAMPLES:
        started = time.perf_counter()
        raw = extractor.spans(text)
        latencies.append((time.perf_counter() - started) * 1000)
        predicted = {normalized_prediction(item) for item in raw}
        expected = gold_spans(text, specs)
        row_tp = len(predicted & expected)
        row_fp = len(predicted - expected)
        row_fn = len(expected - predicted)
        tp += row_tp
        fp += row_fp
        fn += row_fn
        first_predictions.append(raw)
        rows.append({
            "text": text, "gold": sorted(expected), "predicted": raw,
            "true_positives": row_tp, "false_positives": row_fp, "false_negatives": row_fn,
        })
    # A second pass is a direct determinism check, separate from quality.
    second_predictions = [extractor.spans(text) for text, _ in EXAMPLES]
    mismatches = [index for index, pair in enumerate(zip(first_predictions, second_predictions)) if pair[0] != pair[1]]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    report = {
        "schema_version": "1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": "Ihor/gliner-biomed-base-v1.0", "model_revision": REVISION,
        "model_path": str(model_path.resolve()),
        "weight_sha256": hashlib.sha256(weight_path.read_bytes()).hexdigest(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "dataset": {
            "kind": "repository-authored synthetic smoke set", "examples": len(EXAMPLES),
            "gold_spans": sum(len(specs) for _, specs in EXAMPLES),
            "limitation": "This detects adapter/runtime regressions but is not a clinical-corpus accuracy estimate.",
        },
        "exact_span_and_label": {
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": precision, "recall": recall, "f1": f1,
        },
        "determinism": {"runs": 2, "examples": len(EXAMPLES), "mismatch_count": len(mismatches), "mismatch_indexes": mismatches},
        "timing": {
            "model_load_seconds": load_seconds, "inference_latency_ms_mean": statistics.fmean(latencies),
            "inference_latency_ms_p50": pct(latencies, 0.5), "inference_latency_ms_p95": pct(latencies, 0.95),
        },
        "examples": rows,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"examples": len(EXAMPLES), "precision": precision, "recall": recall, "f1": f1, "determinism_mismatches": len(mismatches)}, sort_keys=True))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
