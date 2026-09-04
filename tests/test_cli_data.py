from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

import pytest

from ecsae import cli
from ecsae.data import clinical_trials, retraction_watch
from ecsae.determinism import seed_everything
from ecsae.extract import extract_jsonl


def sample_ctg() -> dict:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT1", "briefTitle": "A trial"},
            "statusModule": {"overallStatus": "COMPLETED"},
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "enrollmentInfo": {"count": 20, "type": "ACTUAL"},
                "designInfo": {"allocation": "RANDOMIZED", "interventionModel": "PARALLEL"},
            },
            "ipdSharingStatementModule": {"ipdSharing": "YES"},
        },
        "resultsSection": {
            "participantFlowModule": {
                "groups": [{"id": "G1", "title": "control"}, {"id": "G2", "title": "active"}],
                "periods": [{"milestones": [{"type": "STARTED", "achievements": [
                    {"groupId": "G1", "numSubjects": "10"}, {"groupId": "G2", "numSubjects": "10"}
                ]}]}],
            },
            "outcomeMeasuresModule": {"outcomeMeasures": [{
                "type": "PRIMARY", "analyses": [{"ciLowerLimit": "1", "ciUpperLimit": "2"}]
            }]},
            "adverseEventsModule": {"eventGroups": []},
        },
        "hasResults": True,
    }


def test_ctg_normalize_and_pull(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = clinical_trials.normalize_study(sample_ctg())
    assert record and record["study_id"] == "NCT1" and sum(record["arm_ns"].values()) == 20
    assert clinical_trials.normalize_study({"protocolSection": {"designModule": {}}}) is None
    monkeypatch.setattr(clinical_trials, "fetch_studies", lambda **_: iter([sample_ctg()]))
    target = tmp_path / "ctg.jsonl"
    report = clinical_trials.pull_to_jsonl(target, limit=1)
    assert report["downloaded_candidates"] == report["accepted"] == 1
    assert json.loads(target.read_text())["study_id"] == "NCT1"


def test_ctg_rich_baseline_preserves_classes_and_overlap() -> None:
    study = sample_ctg()
    baseline = {
        "groups": [
            {"id": "G1", "title": "control"}, {"id": "G2", "title": "active"},
            {"id": "GT", "title": "Total"},
        ],
        "denoms": [{"counts": [
            {"groupId": "G1", "value": "10"}, {"groupId": "G2", "value": "12"},
            {"groupId": "GT", "value": "22"},
        ]}],
        "measures": [
            {
                "title": "Sex", "paramType": "COUNT_OF_PARTICIPANTS",
                "classes": [
                    {"title": "Entry", "categories": [
                        {"title": "female", "measurements": [
                            {"groupId": "G1", "value": "4"}, {"groupId": "G2", "value": "5"}]},
                        {"title": "male", "measurements": [
                            {"groupId": "G1", "value": "6"}, {"groupId": "G2", "value": "7"}]},
                    ]},
                    {"title": "optional subset", "categories": [
                        {"title": "yes", "measurements": [{"groupId": "G1", "value": "3"}, {"groupId": "G2", "value": "4"}]},
                        {"title": "no", "measurements": [{"groupId": "G1", "value": "2"}, {"groupId": "G2", "value": "1"}]},
                    ]},
                ],
            },
            {
                "title": "Comorbidities", "description": "Select all that apply",
                "paramType": "COUNT_OF_PARTICIPANTS", "classes": [{"categories": [
                    {"title": "A", "measurements": [{"groupId": "G1", "value": "8"}, {"groupId": "G2", "value": "9"}]},
                    {"title": "B", "measurements": [{"groupId": "G1", "value": "7"}, {"groupId": "G2", "value": "8"}]},
                ]}],
            },
            {
                "title": "Age", "paramType": "MEAN", "dispersionType": "STANDARD_DEVIATION",
                "classes": [{"denoms": [{"counts": [{"groupId": "G1", "value": "10"}, {"groupId": "G2", "value": "12"}]}],
                    "categories": [{"measurements": [
                        {"groupId": "G1", "value": "41.20", "spread": "5.50"},
                        {"groupId": "G2", "value": "43.1", "spread": "6.2"},
                        {"groupId": "GT", "value": "bad", "spread": "bad"},
                    ]}]}],
            },
        ],
    }
    study["resultsSection"]["baselineCharacteristicsModule"] = baseline
    study["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"][0].update({
        "title": "Primary score", "populationDescription": "All analyzed participants",
        "groups": [{"id": "G1", "title": "control"}, {"id": "G2", "title": "active"}],
        "denoms": [{"counts": [{"groupId": "G1", "value": "9"}, {"groupId": "G2", "value": "11"}]}],
        "analyses": [{"pValue": "0.04", "statisticalMethod": "t test", "paramType": "MEAN_DIFFERENCE",
                      "paramValue": "2.0", "ciPctValue": "95",
                      "ciLowerLimit": "0.1", "ciUpperLimit": "3.9"}],
    })
    record = clinical_trials.normalize_study(study)
    assert record is not None
    assert [item["variable"] for item in record["subgroups"]] == [
        "Sex / Entry", "Sex / optional subset", "Comorbidities"
    ]
    assert record["subgroups"][0]["exhaustive"] is True
    assert record["subgroups"][1]["exhaustive"] is False
    assert record["subgroups"][2]["mutually_exclusive"] is False
    assert len(record["integer_summaries"]) == 2
    assert len(record["baseline_comparisons"]) == 1
    assert record["baseline_comparisons"][0]["mean_decimals_a"] == 2
    assert len(record["reported_effects"]) == 1
    assert record["metadata"]["ci_p_eligible_analyses_before_cap"] == 1
    assert record["metadata"]["ci_p_eligible_analyses_processed"] == 1
    assert record["reported_effects"][0]["statistical_method_family"] == "mean_comparison"
    assert record["reported_effects"][0]["applicability_tier"] == "high_confidence"
    assert record["metadata"]["analysis_population_ns"] == [20]
    assert record["reporting"]["analysis_population"] is True
    assert record["arm_counts_comparable_to_randomized_n"] is False


def test_ctg_helpers_pagination_and_failed_pull(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert clinical_trials._integer("1,234") == 1234
    assert clinical_trials._integer("bad") is None
    assert clinical_trials._number("1,234.5") == 1234.5
    assert clinical_trials._number(None) is None
    assert clinical_trials._decimals("1.230") == 3
    assert clinical_trials._decimals("bad") == 0
    assert clinical_trials._p_value("<0.001") == (0.001, "<", 3)
    assert clinical_trials._p_value("not applicable") is None
    assert clinical_trials._effect_scale("Hazard Ratio (HR)") == "ratio"
    errors = clinical_trials.qualification_errors({})
    assert set(errors) == {"not_completed", "not_interventional", "not_randomized", "no_posted_results"}
    with pytest.raises(ValueError):
        list(clinical_trials.fetch_studies(max_records=0))

    pages = [
        {"studies": [{"page": 1}], "nextPageToken": "next"},
        {"studies": [{"page": 2}]},
    ]
    requested_urls = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        return io.BytesIO(json.dumps(pages.pop(0)).encode())

    monkeypatch.setattr(clinical_trials.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(clinical_trials.time, "sleep", lambda _: None)
    assert [item["page"] for item in clinical_trials.fetch_studies(max_records=2)] == [1, 2]
    assert "filter.overallStatus=COMPLETED" in requested_urls[0]
    assert "pageToken=next" in requested_urls[1]

    monkeypatch.setattr(clinical_trials, "fetch_studies", lambda **_: iter([{}]))
    with pytest.raises(RuntimeError, match="cohort incomplete"):
        clinical_trials.pull_to_jsonl(tmp_path / "incomplete.jsonl", limit=1)
    assert not (tmp_path / "incomplete.jsonl.partial").exists()
    with pytest.raises(ValueError):
        clinical_trials.pull_to_jsonl(tmp_path / "bad.jsonl", limit=0)


def test_ctg_baseline_comparison_cap() -> None:
    groups = [{"id": f"G{i}", "title": f"arm-{i}"} for i in range(24)]
    counts = [{"groupId": f"G{i}", "value": "2"} for i in range(24)]
    measurements = [
        {"groupId": f"G{i}", "value": str(10 + i / 10), "spread": "1.0"}
        for i in range(24)
    ]
    baseline = {
        "groups": groups, "denoms": [{"counts": counts}],
        "measures": [{"title": "x", "paramType": "MEAN", "dispersionType": "STANDARD_DEVIATION",
                      "classes": [{"categories": [{"measurements": measurements}]}]}],
    }
    _, summaries, comparisons, metrics = clinical_trials._baseline_fields(baseline)
    assert len(summaries) == 24
    assert len(comparisons) == 250
    assert metrics["baseline_comparisons_truncated"] == 26


def test_retraction_enrichment(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps({"randomized_n": 10, "metadata": {"doi": "https://doi.org/10.1/X"}}) + "\n")
    csv_path = tmp_path / "rw.csv"
    csv_path.write_text("OriginalPaperDOI,Reason,RetractionDate\n10.1/x,Error,2020-01-01\n", encoding="utf-8")
    output = tmp_path / "enriched.jsonl"
    report = retraction_watch.enrich_jsonl(source, output, csv_path)
    assert report == {"total": 1, "matched": 1}
    assert json.loads(output.read_text())["metadata"]["retraction_watch"]["matched"]


def test_extract_jsonl_and_cli_commands(tmp_path: Path) -> None:
    text_in = tmp_path / "text.jsonl"
    text_in.write_text(json.dumps({"study_id": "S", "text": "Enrolled 20. Arm a: n=10; arm b: n=10."}) + "\n")
    extracted = tmp_path / "extracted.jsonl"
    assert extract_jsonl(text_in, extracted) == 1
    audit_in = tmp_path / "audit.json"
    audit_in.write_text(json.dumps({"randomized_n": 20, "arm_ns": {"a": 10, "b": 10}}))
    audit_out = tmp_path / "audit-out.json"
    assert cli.audit_command(argparse.Namespace(config=None, input=str(audit_in), output=str(audit_out))) == 0
    assert json.loads(audit_out.read_text())["input_hash"]
    rerun_in = tmp_path / "rerun.jsonl"
    rerun_in.write_text(audit_in.read_text() + "\n")
    rerun_out = tmp_path / "rerun-out.json"
    assert cli.rerun_command(argparse.Namespace(config=None, input=str(rerun_in), output=str(rerun_out), runs=2)) == 0
    rerun_report = json.loads(rerun_out.read_text())
    assert rerun_report["rerun_agreement"] == 1
    assert rerun_report["corpus_sha256"]
    assert rerun_report["rules_version"] == "1.2.0"
    extracted2 = tmp_path / "extracted2.jsonl"
    assert cli.extract_command(argparse.Namespace(input=str(text_in), output=str(extracted2))) == 0


def test_cli_evaluation_and_parser(tmp_path: Path) -> None:
    source = tmp_path / "eval.jsonl"
    source.write_text(json.dumps({"randomized_n": 10, "arm_ns": {"a": 8, "b": 8}, "reference_flag": True}) + "\n")
    output = tmp_path / "report.json"
    args = argparse.Namespace(config=None, input=str(source), output=str(output), bootstrap_replicates=20,
                              seed=7, reference_standard="test")
    assert cli.evaluate_command(args) == 0
    assert json.loads(output.read_text())["raw_agreement"] == 1
    assert cli.build_parser().parse_args(["audit", "-"]).command == "audit"


def test_seed_everything_reports_backends() -> None:
    result = seed_everything(7)
    assert result["python_random"] == 7
    assert "numpy" in result and "torch" in result
