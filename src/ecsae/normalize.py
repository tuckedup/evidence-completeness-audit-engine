from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any


def _clean(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).split())
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and infinity are not valid audit inputs")
        return 0.0 if value == 0.0 else value
    return value


def canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(_clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any, config_version: str, model_revision: str) -> str:
    payload = canonical_json(
        {"record": value, "config_version": config_version, "model_revision": model_revision}
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
