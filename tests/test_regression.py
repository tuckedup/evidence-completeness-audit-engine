from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecsae.engine import AuditEngine
from ecsae.models import IntegerSummary, ReportedStat
from ecsae.normalize import canonical_json
from ecsae.stats import grim, grimmer, recompute_p, statcheck


def test_canonical_json_stabilizes_floats() -> None:
    payload = {"value": 0.014499999999999999, "zero": -0.0}
    assert canonical_json(payload) == '{"value":0.0145,"zero":0.0}'


def test_canonical_json_preserves_meaningful_float_changes() -> None:
    left = {"p_value": 0.123456}
    right = {"p_value": 0.124456}
    assert canonical_json(left) != canonical_json(right)


def test_exactly_220_versioned_golden_cases() -> None:
    paths = sorted((Path(__file__).parent / "regression" / "cases").glob("*.json"))
    assert len(paths) == 220
    engine = AuditEngine.from_default()
    seen = set()
    external = 0
    for path in paths:
        case = json.loads(path.read_text(encoding="utf-8"))
        assert case["schema_version"] == "2"
        assert case["case_id"] not in seen
        seen.add(case["case_id"])
        if case["provenance"].get("kind") != "engine_snapshot":
            external += 1
            operation = case["operation"]
            if operation == "recompute_p":
                actual = recompute_p(ReportedStat(**case["input"]))
                assert actual == pytest.approx(
                    case["expected"]["recomputed_p"], abs=case["expected"]["absolute_tolerance"]
                ), path.name
            elif operation == "statcheck":
                actual = statcheck(ReportedStat(**case["input"]))
                assert actual["consistent"] is case["expected"]["consistent"], path.name
            elif operation in {"grim", "grimmer"}:
                summary = IntegerSummary(name=case["case_id"], **case["input"])
                actual = grim(summary) if operation == "grim" else grimmer(summary)
                assert actual["consistent"] is case["expected"]["consistent"], path.name
            else:  # pragma: no cover - case schema is repository-controlled
                raise AssertionError(f"unsupported regression operation: {operation}")
            continue
        result = engine.audit(case["input_record"])
        fingerprint = hashlib.sha256(canonical_json(result).encode()).hexdigest()
        assert fingerprint == case["expected"]["fingerprint"], path.name
        assert result.feasibility_verdict == case["expected"]["feasibility_verdict"]
        assert result.config_version == case["expected"]["config_version"]
    assert external == 20
