from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


_N_PATTERNS = [
    re.compile(r"(?:randomi[sz]ed|enrolled|sample(?:\s+size)?)\s*(?:n\s*=|[:=]|was)?\s*(\d+)", re.I),
    re.compile(r"\bN\s*=\s*(\d+)\b"),
]
_ARM = re.compile(r"(?:arm|group)\s+([\w -]{1,40}?)\s*[:;,]\s*n\s*=\s*(\d+)", re.I)
_STAT = re.compile(
    r"\b(t|F|z|r|chi(?:\^?2|²)|χ²|Q)\s*\(\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)"
    r"\s*=\s*(-?[\d.]+)\s*[,;]?\s*p\s*([<>=]+)\s*(0?\.\d+|1(?:\.0+)?)",
    re.I,
)


class RegexBiomedicalExtractor:
    revision = "regex-biomed-v1"

    def extract(self, text: str, *, study_id: str | None = None) -> dict[str, Any]:
        randomized_n = None
        for pattern in _N_PATTERNS:
            match = pattern.search(text)
            if match:
                randomized_n = int(match.group(1))
                break
        arm_ns = {name.strip(): int(n) for name, n in _ARM.findall(text)}
        stats: list[dict[str, Any]] = []
        for match in _STAT.finditer(text):
            kind, df1, df2, value, operator, p_value = match.groups()
            normalized = {"chi2": "chi2", "χ²": "chi2"}.get(kind.lower(), kind)
            if normalized.lower().startswith("chi"):
                normalized = "chi2"
            elif normalized.lower() == "f":
                normalized = "F"
            else:
                normalized = normalized.lower()
            stats.append({
                "stat_type": normalized,
                "value": float(value),
                "df1": float(df1) if df1 else None,
                "df2": float(df2) if df2 else None,
                "reported_p": float(p_value),
                "p_operator": operator,
                "statistic_decimals": len(value.partition(".")[2]),
                "p_decimals": len(p_value.partition(".")[2]),
                "source": match.group(0),
            })
        return {
            "study_id": study_id,
            "source": "offline-text-extraction",
            "randomized_n": randomized_n,
            "arm_ns": arm_ns,
            "reported_stats": stats,
            "metadata": {"extractor_revision": self.revision},
        }


class GLiNERBiomedicalExtractor:
    """Optional transformer span extractor for offline use only."""

    labels = ["sample size", "arm", "subgroup", "p-value", "test statistic", "event count"]

    def __init__(self, model_id: str = "Ihor/gliner-biomed-base-v1.0", revision: str | None = None):
        if revision is None or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("a 40-character model revision SHA is required for reproducible extraction")
        model_path = Path(model_id)
        if not model_path.is_dir():
            raise ValueError("transformer extraction requires a local, pre-downloaded model directory")
        from gliner import GLiNER

        self.model = GLiNER.from_pretrained(str(model_path), local_files_only=True)
        self.revision = revision

    def spans(self, text: str) -> list[dict[str, Any]]:
        spans = self.model.predict_entities(text, self.labels, threshold=0.5)
        return sorted(spans, key=lambda item: (item["start"], item["end"], item["label"]))


def extract_jsonl(source: str | Path, destination: str | Path) -> int:
    extractor = RegexBiomedicalExtractor()
    count = 0
    with Path(source).open(encoding="utf-8") as reader, Path(destination).open("w", encoding="utf-8", newline="\n") as writer:
        for line in reader:
            raw = json.loads(line)
            result = extractor.extract(raw["text"], study_id=raw.get("study_id"))
            writer.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
            count += 1
    return count
