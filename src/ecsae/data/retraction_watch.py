from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _doi(value: str | None) -> str:
    return (value or "").strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")


def load_retractions(csv_path: str | Path) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            doi = _doi(row.get("OriginalPaperDOI") or row.get("DOI"))
            if doi:
                index[doi] = row
    return index


def enrich_jsonl(source: str | Path, destination: str | Path, retraction_csv: str | Path) -> dict[str, int]:
    retractions = load_retractions(retraction_csv)
    matched = total = 0
    with Path(source).open(encoding="utf-8") as reader, Path(destination).open("w", encoding="utf-8", newline="\n") as writer:
        for line in reader:
            record: dict[str, Any] = json.loads(line)
            doi = _doi(record.get("metadata", {}).get("doi"))
            match = retractions.get(doi)
            record.setdefault("metadata", {})["retraction_watch"] = {
                "matched": bool(match),
                "reason": match.get("Reason") if match else None,
                "retraction_date": match.get("RetractionDate") if match else None,
            }
            matched += bool(match)
            total += 1
            writer.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return {"total": total, "matched": matched}

