from __future__ import annotations

import hashlib
import itertools
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from ..models import StudyRecord

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DEFAULT_REQUEST_PARAMS = {
    "filter.overallStatus": "COMPLETED",
    "filter.advanced": "AREA[DesignAllocation]RANDOMIZED AND AREA[StudyType]INTERVENTIONAL",
    "aggFilters": "results:with",
}


def _integer(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _decimals(value: Any) -> int:
    try:
        return min(10, max(0, -Decimal(str(value)).as_tuple().exponent))
    except (InvalidOperation, ValueError):
        return 0


def _p_value(value: Any) -> tuple[float, str, int] | None:
    text = str(value).strip().replace(" ", "")
    operator = "="
    for candidate in ("<=", ">=", "<", ">", "="):
        if text.startswith(candidate):
            operator, text = candidate, text[len(candidate):]
            break
    number = _number(text)
    if number is None or not 0 <= number <= 1:
        return None
    return number, operator, _decimals(text)


def _effect_scale(parameter: Any) -> str:
    value = str(parameter or "").lower()
    ratio_markers = (
        "hazard ratio", "odds ratio", "risk ratio", "rate ratio", "relative risk",
        "incidence ratio", "prevalence ratio", "ratio of", "geometric mean ratio",
        "cox proportional hazard",
    )
    return "ratio" if "ratio" in value or any(marker in value for marker in ratio_markers) else "additive"


def _method_family(method: str) -> str:
    value = method.lower()
    if not value:
        return "unspecified"
    if any(term in value for term in ("cox", "log-rank", "hazard")):
        return "survival_model"
    if any(term in value for term in ("logistic", "poisson", "regression", "ancova", "glm", "mixed")):
        return "regression_model"
    if any(term in value for term in ("wilcoxon", "mann-whitney", "rank-sum", "rank sum", "nonparam")):
        return "nonparametric"
    if any(term in value for term in ("exact", "fisher")):
        return "exact_test"
    if any(term in value for term in ("t test", "ttest", "anova", "mean difference")):
        return "mean_comparison"
    if "z test" in value:
        return "z_test"
    return "other"


def _ci_p_applicability_tier(method: str, *, one_tailed: bool, family: str) -> str:
    if one_tailed:
        return "approximate_review"
    if any(term in method.lower() for term in ("adjust", "correct", "strat", "covariate")):
        return "approximate_review"
    if family in {"survival_model", "regression_model", "nonparametric", "exact_test", "other"}:
        return "approximate_review"
    return "high_confidence"


def _method_from_source(source: Any) -> str:
    prefix = "ClinicalTrials.gov analysis;"
    text = str(source or "")
    return text.split(prefix, 1)[1].strip() if prefix in text else text


def upgrade_normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    """Backfill newer metadata fields onto an already-normalized CT.gov record.

    This keeps the persisted cohort stable for benchmark provenance while allowing
    newer generators to derive clearer CI-to-p accounting and applicability detail.
    """

    upgraded = dict(record)
    metadata = dict(upgraded.get("metadata", {}))
    analysis_populations = metadata.get("analysis_population_ns") or []
    analysis_population_n = analysis_populations[0] if len(analysis_populations) == 1 else None
    effects: list[dict[str, Any]] = []
    for raw_effect in upgraded.get("reported_effects", []):
        effect = dict(raw_effect)
        method = _method_from_source(effect.get("source"))
        family = effect.get("statistical_method_family") or _method_family(method)
        one_tailed = bool(effect.get("one_tailed"))
        effect.setdefault("statistical_method_family", family)
        effect.setdefault(
            "applicability_tier",
            _ci_p_applicability_tier(method, one_tailed=one_tailed, family=family),
        )
        effect.setdefault("analysis_population_n", analysis_population_n)
        effect.setdefault("ci_sidedness", "one_sided" if one_tailed else "two_sided")
        effect.setdefault("parameter_type", effect.get("name", "").split(": ")[-1] or None)
        effect.setdefault(
            "adjustment_comment",
            method if any(term in method.lower() for term in ("adjust", "correct", "strat", "covariate")) else None,
        )
        effect.setdefault("selected_groups", [])
        effects.append(effect)
    upgraded["reported_effects"] = effects
    before_cap = int(metadata.get("ci_p_eligible_analyses_before_cap", metadata.get("ci_p_eligible_analyses", len(effects))) or 0)
    truncated = int(metadata.get("ci_p_analyses_truncated", 0) or 0)
    processed = len(effects)
    metadata["ci_p_eligible_analyses_before_cap"] = before_cap
    metadata["ci_p_eligible_analyses_processed"] = processed
    metadata["ci_p_analyses_truncated"] = truncated
    metadata["ci_p_high_confidence_analyses_processed"] = sum(
        effect.get("applicability_tier") == "high_confidence" for effect in effects
    )
    metadata["ci_p_approximate_review_analyses_processed"] = sum(
        effect.get("applicability_tier") == "approximate_review" for effect in effects
    )
    upgraded["metadata"] = metadata
    return upgraded


def qualification_errors(study: dict[str, Any]) -> list[str]:
    protocol = study.get("protocolSection", {})
    design = protocol.get("designModule", {})
    status = protocol.get("statusModule", {})
    errors = []
    if status.get("overallStatus") != "COMPLETED":
        errors.append("not_completed")
    if design.get("studyType") != "INTERVENTIONAL":
        errors.append("not_interventional")
    if design.get("designInfo", {}).get("allocation") != "RANDOMIZED":
        errors.append("not_randomized")
    if study.get("hasResults") is not True or not study.get("resultsSection"):
        errors.append("no_posted_results")
    return errors


def fetch_studies(
    max_records: int = 6000,
    delay: float = 0.2,
    request_params: dict[str, str] | None = None,
) -> Iterator[dict[str, Any]]:
    if not 1 <= max_records <= 500_000:
        raise ValueError("max_records must be between 1 and 500000")
    token = None
    yielded = 0
    filters = dict(DEFAULT_REQUEST_PARAMS if request_params is None else request_params)
    while yielded < max_records:
        params = {**filters, "pageSize": min(1000, max_records - yielded), "format": "json"}
        if token:
            params["pageToken"] = token
        request = urllib.request.Request(
            BASE_URL + "?" + urllib.parse.urlencode(params),
            headers={"User-Agent": "ecsae/1.1 research audit (contact: local-user)"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            page = json.load(response)
        studies = page.get("studies", [])
        for study in studies:
            yield study
            yielded += 1
            if yielded >= max_records:
                return
        token = page.get("nextPageToken")
        if not token or not studies:
            return
        time.sleep(delay)


def _group_maps(module: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    titles = {
        str(item.get("id")): str(item.get("title", item.get("id")))
        for item in module.get("groups", [])
    }
    non_total = [
        group_id
        for group_id, title in titles.items()
        if title.strip().lower() not in {"total", "overall", "all participants", "all groups"}
    ]
    return titles, non_total or list(titles)


def _denom_map(denoms: list[dict[str, Any]]) -> dict[str, int]:
    for denom in denoms:
        counts = {
            str(row.get("groupId")): count
            for row in denom.get("counts", [])
            if (count := _integer(row.get("value"))) is not None
        }
        if counts:
            return counts
    return {}


def _flatten_categories(
    measure: dict[str, Any],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    rows = []
    for class_index, class_row in enumerate(measure.get("classes", [])):
        class_title = str(class_row.get("title", "")).strip()
        for category_index, category in enumerate(class_row.get("categories", [])):
            category_title = str(category.get("title", "")).strip()
            label = " / ".join(part for part in (class_title, category_title) if part)
            rows.append(
                (
                    label or f"category-{class_index + 1}-{category_index + 1}",
                    category,
                    class_row,
                )
            )
    return rows


def _baseline_fields(
    baseline: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    titles, group_ids = _group_maps(baseline)
    module_denoms = _denom_map(baseline.get("denoms", []))
    subgroups: list[dict[str, Any]] = []
    integer_summaries: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    metrics = Counter()

    for measure in baseline.get("measures", []):
        measure_title = str(measure.get("title", "unnamed baseline measure"))
        categories = _flatten_categories(measure)
        if measure.get("paramType") == "COUNT_OF_PARTICIPANTS":
            # A class is a distinct stratum/time point in the v2 schema.  It
            # must not be flattened with sibling classes and counted twice.
            for class_index, class_row in enumerate(measure.get("classes", [])):
                raw_categories = class_row.get("categories", [])
                if len(raw_categories) < 2:
                    continue
                class_title = str(class_row.get("title", "")).strip()
                levels = []
                for category_index, category in enumerate(raw_categories):
                    category_title = str(category.get("title", "")).strip()
                    label = category_title or f"category-{class_index + 1}-{category_index + 1}"
                    arms: dict[str, int] = {}
                    for row in category.get("measurements", []):
                        group_id = str(row.get("groupId"))
                        count = _integer(row.get("value"))
                        if group_id in group_ids and count is not None:
                            arms[titles.get(group_id, group_id)] = count
                    if arms:
                        levels.append({"name": label, "n": sum(arms.values()), "arm_ns": arms})
                class_denoms = _denom_map(class_row.get("denoms", [])) or module_denoms
                population_n = sum(class_denoms.get(group_id, 0) for group_id in group_ids) or None
                wording = " ".join((
                    measure_title, str(measure.get("description", "")), class_title,
                    str(class_row.get("description", "")),
                )).lower()
                explicit_overlap = any(term in wording for term in (
                    "select all", "check all", "multiple response", "multiple selection",
                    "more than one", "not mutually exclusive",
                ))
                observed_overlap = bool(
                    population_n and sum(level["n"] for level in levels) > population_n
                )
                mutually_exclusive = not (explicit_overlap or observed_overlap)
                exhaustive = mutually_exclusive and not any(
                    term in wording for term in ("among those", "subset", "if yes", "optional")
                )
                if len(levels) >= 2 and population_n:
                    subgroups.append(
                        {
                            "variable": " / ".join(
                                part for part in (measure_title, class_title) if part
                            ),
                            "levels": levels,
                            "population_n": population_n,
                            "mutually_exclusive": mutually_exclusive,
                            "exhaustive": exhaustive,
                        }
                    )
                    metrics["count_partitions"] += 1
                    metrics["overlapping_count_sets"] += not mutually_exclusive

        if (
            measure.get("paramType") != "MEAN"
            or measure.get("dispersionType") != "STANDARD_DEVIATION"
        ):
            continue
        for label, category, class_row in categories:
            denoms = _denom_map(class_row.get("denoms", [])) or module_denoms
            values: dict[str, tuple[float, float, int, Any, Any]] = {}
            for row in category.get("measurements", []):
                group_id = str(row.get("groupId"))
                mean = _number(row.get("value"))
                sd = _number(row.get("spread"))
                n = denoms.get(group_id)
                if group_id in group_ids and mean is not None and sd is not None and n:
                    integer_summaries.append(
                        {
                            "name": f"{measure_title}: {label}: {titles.get(group_id, group_id)}",
                            "mean": mean,
                            "n": n,
                            "mean_decimals": _decimals(row.get("value")),
                            "sd": sd,
                            "sd_decimals": _decimals(row.get("spread")),
                            "integer_scale": False,
                        }
                    )
                    if sd > 0 and n > 1:
                        values[group_id] = (mean, sd, n, row.get("value"), row.get("spread"))
            for left, right in itertools.combinations(sorted(values), 2):
                mean_a, sd_a, n_a, raw_mean_a, raw_sd_a = values[left]
                mean_b, sd_b, n_b, raw_mean_b, raw_sd_b = values[right]
                comparisons.append(
                    {
                        "name": (
                            f"{measure_title}: {label}: "
                            f"{titles.get(left, left)} vs {titles.get(right, right)}"
                        ),
                        "mean_a": mean_a,
                        "sd_a": sd_a,
                        "n_a": n_a,
                        "mean_b": mean_b,
                        "sd_b": sd_b,
                        "n_b": n_b,
                        "mean_decimals_a": _decimals(raw_mean_a),
                        "sd_decimals_a": _decimals(raw_sd_a),
                        "mean_decimals_b": _decimals(raw_mean_b),
                        "sd_decimals_b": _decimals(raw_sd_b),
                        "variable_type": "continuous",
                        "independent_from_other_variables": False,
                    }
                )
            metrics["mean_sd_measurements"] += len(values)
    if len(comparisons) > 250:
        metrics["baseline_comparisons_truncated"] = len(comparisons) - 250
        comparisons = comparisons[:250]
    metrics["baseline_comparisons"] = len(comparisons)
    return subgroups, integer_summaries, comparisons, dict(metrics)


def _outcome_fields(outcomes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analysis_count = 0
    populations: set[int] = set()
    population_descriptions = 0
    preview = []
    effects: list[dict[str, Any]] = []
    processed_effects = 0
    approximate_review_effects = 0
    high_confidence_effects = 0
    for outcome in outcomes:
        if str(outcome.get("populationDescription", "")).strip():
            population_descriptions += 1
        titles, group_ids = _group_maps(outcome)
        denoms = _denom_map(outcome.get("denoms", []))
        population_n = sum(denoms.get(group_id, 0) for group_id in group_ids)
        if population_n:
            populations.add(population_n)
        for analysis in outcome.get("analyses", []):
            analysis_count += 1
            if len(preview) < 25:
                preview.append(
                    {
                        "outcome": outcome.get("title"),
                        "p_value": analysis.get("pValue"),
                        "method": analysis.get("statisticalMethod"),
                        "parameter": analysis.get("paramType"),
                        "estimate": analysis.get("paramValue"),
                        "ci_lower": analysis.get("ciLowerLimit"),
                        "ci_upper": analysis.get("ciUpperLimit"),
                        "ci_level": analysis.get("ciPctValue"),
                    }
                )
            parsed_p = _p_value(analysis.get("pValue"))
            estimate = _number(analysis.get("paramValue"))
            lower = _number(analysis.get("ciLowerLimit"))
            upper = _number(analysis.get("ciUpperLimit"))
            confidence_level = _number(analysis.get("ciPctValue"))
            parameter = analysis.get("paramType")
            if (
                parsed_p is not None
                and estimate is not None
                and lower is not None
                and upper is not None
                and lower < upper
                and confidence_level is not None
                and 0 < confidence_level < 100
            ):
                reported_p, p_operator, p_decimals = parsed_p
                scale = _effect_scale(parameter)
                if scale == "additive" or min(estimate, lower, upper) > 0:
                    method = str(analysis.get("statisticalMethod", ""))
                    one_tailed = any(term in method.lower() for term in (
                        "1-sided", "1 sided", "one-sided", "one sided",
                    ))
                    family = _method_family(method)
                    applicability_tier = _ci_p_applicability_tier(method, one_tailed=one_tailed, family=family)
                    effects.append({
                        "name": f"{outcome.get('title', 'unnamed outcome')}: {parameter or 'effect'}",
                        "estimate": estimate,
                        "ci_lower": lower,
                        "ci_upper": upper,
                        "confidence_level": confidence_level,
                        "reported_p": reported_p,
                        "p_operator": p_operator,
                        "estimate_decimals": _decimals(analysis.get("paramValue")),
                        "ci_lower_decimals": _decimals(analysis.get("ciLowerLimit")),
                        "ci_upper_decimals": _decimals(analysis.get("ciUpperLimit")),
                        "p_decimals": p_decimals,
                        "scale": scale,
                        "one_tailed": one_tailed,
                        "statistical_method_family": family,
                        "applicability_tier": applicability_tier,
                        "analysis_population_n": population_n or None,
                        "ci_sidedness": "one_sided" if one_tailed else "two_sided",
                        "parameter_type": str(parameter or "") or None,
                        "adjustment_comment": method if any(
                            term in method.lower() for term in ("adjust", "correct", "strat", "covariate")
                        ) else None,
                        "selected_groups": [titles.get(group_id, group_id) for group_id in group_ids],
                        "source": f"ClinicalTrials.gov analysis; {method or 'method unspecified'}",
                    })
                    processed_effects += 1
                    approximate_review_effects += applicability_tier == "approximate_review"
                    high_confidence_effects += applicability_tier == "high_confidence"
    effect_count = len(effects)
    if effect_count > 250:
        effects = effects[:250]
        high_confidence_effects = sum(item["applicability_tier"] == "high_confidence" for item in effects)
        approximate_review_effects = sum(item["applicability_tier"] == "approximate_review" for item in effects)
    return {
        "reported_analyses_count": analysis_count,
        "reported_analyses_preview": preview,
        "ci_p_eligible_analyses_before_cap": effect_count,
        "ci_p_eligible_analyses_processed": len(effects),
        "ci_p_analyses_truncated": max(0, effect_count - len(effects)),
        "ci_p_high_confidence_analyses_processed": high_confidence_effects,
        "ci_p_approximate_review_analyses_processed": approximate_review_effects,
        "analysis_population_ns": sorted(populations),
        "outcomes_with_population_description": population_descriptions,
    }, effects


def normalize_study(study: dict[str, Any]) -> dict[str, Any] | None:
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    design = protocol.get("designModule", {})
    status = protocol.get("statusModule", {})
    randomized_n = _integer(design.get("enrollmentInfo", {}).get("count"))
    if not randomized_n or randomized_n <= 0:
        return None
    results = study.get("resultsSection", {})
    flow = results.get("participantFlowModule", {})
    flow_titles, _ = _group_maps(flow)
    arm_ns: dict[str, int] = {}
    periods = flow.get("periods", [])
    if periods:
        milestones = periods[0].get("milestones", [])
        candidates = [row for row in milestones if str(row.get("type", "")).upper() == "STARTED"]
        selected = (candidates or milestones)[:1]
        if selected:
            for row in selected[0].get("achievements", []):
                count = _integer(row.get("numSubjects"))
                if count is not None:
                    group_id = str(row.get("groupId"))
                    arm_ns[flow_titles.get(group_id, group_id)] = count

    baseline = results.get("baselineCharacteristicsModule", {})
    subgroups, summaries, comparisons, baseline_metrics = _baseline_fields(baseline)
    outcomes = results.get("outcomeMeasuresModule", {}).get("outcomeMeasures", [])
    outcome_metadata, reported_effects = _outcome_fields(outcomes)
    has_effect = outcome_metadata["reported_analyses_count"] > 0
    reporting = {
        "analysis_population": (
            outcome_metadata["outcomes_with_population_description"] > 0 if outcomes else None
        ),
        "primary_outcome": any(item.get("type") == "PRIMARY" for item in outcomes),
        "effect_estimate": has_effect,
        "precision": any(
            analysis.get("ciLowerLimit") is not None and analysis.get("ciUpperLimit") is not None
            for item in outcomes
            for analysis in item.get("analyses", [])
        ),
        "harms": bool(results.get("adverseEventsModule")),
        "protocol_registration": True,
        "data_sharing": protocol.get("ipdSharingStatementModule", {}).get("ipdSharing") is not None,
        "conflicts_of_interest": None,
        "patient_public_involvement": None,
    }
    record = {
        "study_id": identification.get("nctId"),
        "source": "ClinicalTrials.gov API v2",
        "title": identification.get("briefTitle"),
        "randomized_n": randomized_n,
        "analyzed_n": None,
        "arm_ns": arm_ns,
        # STARTED in the first flow period is a reported milestone; protocol
        # enrollment may use a different time point or population definition.
        "arm_counts_comparable_to_randomized_n": False,
        "subgroups": subgroups,
        "reported_effects": reported_effects,
        "integer_summaries": summaries,
        "baseline_comparisons": comparisons,
        "reporting": reporting,
        "metadata": {
            "retrieved_api_version": "v2",
            "overall_status": status.get("overallStatus"),
            "study_type": design.get("studyType"),
            "enrollment_type": design.get("enrollmentInfo", {}).get("type"),
            "allocation": design.get("designInfo", {}).get("allocation"),
            "intervention_model": design.get("designInfo", {}).get("interventionModel"),
            "has_results": study.get("hasResults") is True,
            "arm_count_basis": "participant_flow_first_period_started",
            "source_schema_unavailable_rules": [
                "ENROLLMENT-ARM-SUM",
                "GRIM",
                "GRIMMER",
                "SUBGROUP-PERCENT",
                "SUBGROUP-INTERACTION-TEST",
                "SUBGROUP-INTERACTION-POWER",
                "SUBGROUP-EVENT-FLOOR",
                "SUBGROUP-EXPECTED-CELL",
            ],
            **outcome_metadata,
            **baseline_metrics,
        },
    }
    return StudyRecord.model_validate(record).model_dump(mode="json")


def pull_to_jsonl(
    destination: str | Path,
    limit: int = 2000,
    request_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not 1 <= limit <= 100_000:
        raise ValueError("limit must be between 1 and 100000")
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    filters = dict(DEFAULT_REQUEST_PARAMS if request_params is None else request_params)
    downloaded = accepted = 0
    exclusions: Counter[str] = Counter()
    with partial.open("w", encoding="utf-8", newline="\n") as writer:
        for study in fetch_studies(
            max_records=max(limit * 5, limit + 1000), request_params=filters
        ):
            downloaded += 1
            errors = qualification_errors(study)
            if errors:
                exclusions.update(errors)
                continue
            try:
                record = normalize_study(study)
            except ValidationError:
                exclusions["schema_validation_error"] += 1
                continue
            if record is None:
                exclusions["invalid_enrollment"] += 1
                continue
            assert record["metadata"]["overall_status"] == "COMPLETED"
            assert record["metadata"]["study_type"] == "INTERVENTIONAL"
            assert record["metadata"]["allocation"] == "RANDOMIZED"
            assert record["metadata"]["has_results"] is True
            writer.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            accepted += 1
            if accepted == limit:
                break
    if accepted != limit:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"cohort incomplete: accepted {accepted} of requested {limit}")
    partial.replace(path)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema_version": "2",
        "requested": limit,
        "downloaded_candidates": downloaded,
        "accepted": accepted,
        "exclusions": dict(sorted(exclusions.items())),
        "path": str(path.resolve()),
        "sha256": checksum,
        "request_parameters": filters,
        "write_time_assertions": [
            "COMPLETED",
            "INTERVENTIONAL",
            "RANDOMIZED",
            "hasResults=true",
        ],
        "api": BASE_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "license_note": "ClinicalTrials.gov records are U.S. Government public-domain data.",
    }
