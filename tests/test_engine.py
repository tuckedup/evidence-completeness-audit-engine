from __future__ import annotations

from ecsae.engine import AuditEngine
from pydantic import ValidationError
import pytest


def test_impossible_partition_and_interaction_claim_are_flagged() -> None:
    result = AuditEngine.from_default().audit({
        "study_id": "X",
        "randomized_n": 100,
        "arm_ns": {"a": 50, "b": 50},
        "subgroups": [{
            "variable": "sex",
            "levels": [{"name": "f", "n": 60}, {"name": "m", "n": 50}],
            "effect_claimed": True,
            "formal_interaction_test": False,
            "main_effect_required_n": 80,
        }],
    })
    flagged = {check.rule_id for check in result.checks if check.verdict == "flag"}
    assert "SUBGROUP-PARTITION" in flagged
    warned = {check.rule_id for check in result.checks if check.verdict == "warn"}
    assert {"SUBGROUP-INTERACTION-TEST", "SUBGROUP-INTERACTION-POWER"} <= warned
    assert "not evidence of error" in result.disclaimer


def test_percentage_rounding_and_cache_hash_are_deterministic() -> None:
    engine = AuditEngine.from_default()
    record = {
        "randomized_n": 3,
        "arm_ns": {"a": 1, "b": 2},
        "subgroups": [{"variable": "x", "levels": [
            {"name": "a", "n": 1, "percentage": 33.3},
            {"name": "b", "n": 2, "percentage": 66.7},
        ]}],
    }
    first, second = engine.audit(record), engine.audit(record)
    assert first.model_dump() == second.model_dump()
    check = next(item for item in first.checks if item.rule_id == "SUBGROUP-PERCENT")
    assert check.verdict == "pass"


def test_completeness_excludes_explicit_not_applicable() -> None:
    result = AuditEngine.from_default().audit({
        "randomized_n": 20,
        "arm_ns": {"a": 10, "b": 10},
        "reporting": {"patient_public_involvement": None},
    })
    detail = result.checks[0].evidence
    assert detail["applicable_weight"] == 9.0


def test_sparse_events_cells_baseline_and_grimmer_paths() -> None:
    result = AuditEngine.from_default().audit({
        "randomized_n": 40,
        "arm_ns": {"a": 18, "b": 18},
        "subgroups": [{
            "variable": "risk",
            "levels": [{"name": "low", "n": 20, "events": 2}, {"name": "high", "n": 20, "events": 30}],
            "estimated_parameters": 2,
            "contingency_cells": [2, 3, 15, 20],
            "effect_claimed": True,
            "formal_interaction_test": True,
            "powered_for_interaction": False,
        }],
        "integer_summaries": [{"name": "x", "mean": 3.01, "n": 5, "sd": 1.58}],
        "baseline_comparisons": [{
            "name": "age", "mean_a": 30, "sd_a": 2, "n_a": 20,
            "mean_b": 50, "sd_b": 2, "n_b": 20, "correlated_with": ["weight"],
        }],
    })
    by_rule = {check.rule_id: check for check in result.checks}
    assert by_rule["ENROLLMENT-ARM-SUM"].verdict == "warn"
    assert by_rule["SUBGROUP-EVENT-FLOOR"].verdict == "warn"
    assert by_rule["SUBGROUP-EXPECTED-CELL"].verdict == "warn"
    assert by_rule["BASELINE-ANOMALY"].verdict == "warn"
    assert by_rule["GRIMMER"].verdict == "flag"


def test_overcount_missing_p_and_noninteger_scale_paths() -> None:
    result = AuditEngine.from_default().audit({
        "randomized_n": 10,
        "arm_ns": {"a": 7, "b": 7},
        "reported_stats": [{"stat_type": "z", "value": 2.0}],
        "integer_summaries": [{"name": "continuous", "mean": 1.2, "n": 10, "integer_scale": False}],
    })
    by_rule = {check.rule_id: check for check in result.checks}
    assert by_rule["ENROLLMENT-ARM-SUM"].verdict == "flag"
    assert by_rule["STATCHECK"].verdict == "not_applicable"
    assert by_rule["GRIM"].verdict == "not_applicable"


def test_overlapping_subgroup_levels_are_not_summed_as_a_partition() -> None:
    result = AuditEngine.from_default().audit({
        "randomized_n": 20,
        "subgroups": [{
            "variable": "comorbidities", "population_n": 20,
            "mutually_exclusive": False, "exhaustive": False,
            "levels": [{"name": "hypertension", "n": 14}, {"name": "diabetes", "n": 12}],
        }],
    })
    check = next(item for item in result.checks if item.rule_id == "SUBGROUP-PARTITION")
    assert check.verdict == "pass"
    assert "Overlapping" in check.rationale


def test_reporting_typos_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown reporting"):
        AuditEngine.from_default().audit({"randomized_n": 20, "reporting": {"harm": True}})


def test_ci_p_check_is_a_warning_not_a_hard_error() -> None:
    result = AuditEngine.from_default().audit({
        "randomized_n": 40,
        "reported_effects": [{
            "name": "mean difference", "estimate": 2.4,
            "ci_lower": 0.3, "ci_upper": 4.5, "reported_p": 0.30,
            "estimate_decimals": 1, "ci_lower_decimals": 1,
            "ci_upper_decimals": 1, "p_decimals": 2,
            "statistical_method_family": "mean_comparison",
            "applicability_tier": "high_confidence",
        }],
    })
    check = next(item for item in result.checks if item.rule_id == "STATCHECK")
    assert check.verdict == "warn"
    assert check.evidence["method_family"] == "ci_to_p"
    assert check.evidence["applicability_tier"] == "high_confidence"


def test_not_applicable_scope_is_explicit() -> None:
    result = AuditEngine.from_default().audit({
        "randomized_n": 20,
        "metadata": {"source_schema_unavailable_rules": ["GRIM"]},
    })
    by_rule = {item.rule_id: item for item in result.checks}
    assert by_rule["GRIM"].evidence["not_applicable_scope"] == "source_schema"
    assert by_rule["STATCHECK"].evidence["not_applicable_scope"] == "record"
