from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SEMANTIC_FIELDS = {
    "config_schema_version", "rules_version", "model_revision", "alpha",
    "stat_tolerance", "interaction_sample_multiplier", "min_events_per_parameter",
    "min_expected_cell_count", "baseline_extreme_p", "completeness_weights",
}
COMPLETENESS_FIELDS = {
    "randomized_n", "arm_sizes", "analysis_population", "primary_outcome",
    "effect_estimate", "precision", "harms", "protocol_registration", "data_sharing",
    "conflicts_of_interest", "patient_public_involvement",
}


@dataclass(frozen=True)
class EngineConfig:
    config_schema_version: str = "1"
    rules_version: str = "1.0.0"
    model_revision: str = "regex-biomed-v1"
    alpha: float = 0.05
    stat_tolerance: float = 1e-12
    interaction_sample_multiplier: float = 4.0
    min_events_per_parameter: int = 10
    min_expected_cell_count: float = 5.0
    baseline_extreme_p: float = 0.001
    cache_ttl_seconds: int = 86400
    memory_cache_entries: int = 4096
    completeness_weights: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 < self.alpha < 1:
            raise ValueError("alpha must be strictly between zero and one")
        if self.stat_tolerance < 0:
            raise ValueError("stat_tolerance must be non-negative")
        if self.interaction_sample_multiplier <= 0:
            raise ValueError("interaction_sample_multiplier must be positive")
        if self.min_events_per_parameter < 1:
            raise ValueError("min_events_per_parameter must be at least one")
        if self.min_expected_cell_count <= 0:
            raise ValueError("min_expected_cell_count must be positive")
        if not 0 < self.baseline_extreme_p < 1:
            raise ValueError("baseline_extreme_p must be strictly between zero and one")
        if self.cache_ttl_seconds < 1 or self.memory_cache_entries < 1:
            raise ValueError("cache TTL and capacity must be positive")
        unknown = set(self.completeness_weights) - COMPLETENESS_FIELDS
        missing = COMPLETENESS_FIELDS - set(self.completeness_weights)
        if unknown or missing or any(weight <= 0 for weight in self.completeness_weights.values()):
            raise ValueError(
                f"completeness weights must be positive and exactly match the schema; "
                f"unknown={sorted(unknown)}, missing={sorted(missing)}"
            )

    def resolved(self) -> dict[str, Any]:
        result = {
            key: value
            for key, value in self.__dict__.items()
            if key != "completeness_weights"
        }
        result["completeness_weights"] = dict(sorted(self.completeness_weights.items()))
        return result

    @property
    def version(self) -> str:
        semantic = {
            key: value for key, value in self.resolved().items() if key in SEMANTIC_FIELDS
        }
        raw = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def operations_version(self) -> str:
        operational = {
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "memory_cache_entries": self.memory_cache_entries,
        }
        raw = json.dumps(operational, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _default_path() -> Path:
    return Path(__file__).with_name("default.toml")


def load_config(path: str | Path | None = None) -> EngineConfig:
    selected = Path(path) if path else Path(os.environ.get("ECSAE_CONFIG", _default_path()))
    with selected.open("rb") as handle:
        raw = tomllib.load(handle)
    engine = raw.get("engine", {})
    weights = raw.get("completeness", {}).get("weights", {})
    return EngineConfig(**engine, completeness_weights=weights)
