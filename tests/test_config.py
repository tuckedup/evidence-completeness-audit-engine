from __future__ import annotations

from dataclasses import replace

import pytest

from ecsae.config import load_config


def test_semantic_version_excludes_operational_cache_knobs() -> None:
    baseline = load_config()
    operational = replace(
        baseline,
        cache_ttl_seconds=baseline.cache_ttl_seconds // 2,
        memory_cache_entries=baseline.memory_cache_entries * 2,
    )
    assert operational.version == baseline.version
    assert operational.operations_version != baseline.operations_version
    assert replace(baseline, alpha=0.01).version != baseline.version


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("alpha", 5.0), ("stat_tolerance", -1),
        ("interaction_sample_multiplier", 0), ("min_events_per_parameter", 0),
        ("min_expected_cell_count", 0), ("baseline_extreme_p", 1.0),
        ("cache_ttl_seconds", 0), ("memory_cache_entries", 0),
    ],
)
def test_config_rejects_invalid_ranges(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        replace(load_config(), **{field: value})


def test_config_rejects_weight_schema_drift() -> None:
    config = load_config()
    weights = dict(config.completeness_weights)
    weights["typo"] = weights.pop("harms")
    with pytest.raises(ValueError, match="exactly match"):
        replace(config, completeness_weights=weights)
