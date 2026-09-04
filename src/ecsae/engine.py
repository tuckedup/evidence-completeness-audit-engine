from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .config import EngineConfig, load_config
from .constants import CHECK_ORDER, DISCLAIMER
from .models import AuditResponse, CheckResult, StudyRecord
from .normalize import content_hash
from .stats import ci_p_check, combine_baseline, grim, grimmer, statcheck


def _check(
    rule_id: str,
    version: str,
    verdict: str,
    severity: str,
    rationale: str,
    evidence: dict[str, Any] | None = None,
    guideline_ref: str | None = None,
) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        rule_version=version,
        verdict=verdict,
        severity=severity,
        rationale=rationale,
        evidence=evidence or {},
        guideline_ref=guideline_ref,
    )


@dataclass
class AuditEngine:
    config: EngineConfig

    @classmethod
    def from_default(cls) -> "AuditEngine":
        return cls(load_config())

    def rule_manifest(self) -> list[dict[str, str]]:
        version = self.config.rules_version
        return [
            {"rule_id": "REPORTING-COMPLETENESS", "version": version, "reference": "CONSORT-informed registry completeness proxy"},
            {"rule_id": "ENROLLMENT-ARM-SUM", "version": version, "reference": "internal arithmetic feasibility; CONSORT participant-flow context"},
            {"rule_id": "SUBGROUP-PARTITION", "version": version, "reference": "internal arithmetic feasibility; CONSORT subgroup reporting context"},
            {"rule_id": "SUBGROUP-PERCENT", "version": version, "reference": "integer-feasibility"},
            {"rule_id": "SUBGROUP-INTERACTION-TEST", "version": version, "reference": "formal interaction-test criterion aligned with ICEMAN principles"},
            {"rule_id": "SUBGROUP-INTERACTION-POWER", "version": version, "reference": "Brookes-derived interaction-power heuristic"},
            {"rule_id": "SUBGROUP-EVENT-FLOOR", "version": version, "reference": "events-per-parameter heuristic"},
            {"rule_id": "SUBGROUP-EXPECTED-CELL", "version": version, "reference": "chi-square approximation"},
            {"rule_id": "STATCHECK", "version": version, "reference": "Nuijten-Epskamp; Altman-Bland-2011"},
            {"rule_id": "GRIM", "version": version, "reference": "Brown-Heathers-2016"},
            {"rule_id": "GRIMMER", "version": version, "reference": "Allard-2018"},
            {"rule_id": "BASELINE-ANOMALY", "version": version, "reference": "Carlisle-2017"},
        ]

    def _completeness(self, record: StudyRecord) -> tuple[float, CheckResult]:
        observed = dict(record.reporting)
        observed["randomized_n"] = record.randomized_n > 0
        observed["arm_sizes"] = bool(record.arm_ns)
        if record.analyzed_n is not None:
            observed["analysis_population"] = True
        elif "analysis_population" not in observed:
            # A structured source that cannot represent the analysis population is
            # not evidence that the report omitted it.
            observed["analysis_population"] = None
        weights = self.config.completeness_weights
        applicable = {
            key: weight
            for key, weight in weights.items()
            if key not in observed or observed[key] is not None
        }
        denominator = sum(applicable.values())
        numerator = sum(weight for key, weight in applicable.items() if observed.get(key) is True)
        score = numerator / denominator if denominator else 1.0
        missing = sorted(key for key in applicable if observed.get(key) is not True)
        verdict = "pass" if not missing else "warn"
        return score, _check(
            "REPORTING-COMPLETENESS",
            self.config.rules_version,
            verdict,
            "low" if missing else "info",
            "All applicable registry completeness proxy fields are present."
            if not missing
            else f"{len(missing)} applicable registry completeness proxy field(s) are absent.",
            {"present_weight": numerator, "applicable_weight": denominator, "missing": missing},
            "CONSORT-informed registry completeness proxy",
        )

    def audit(
        self,
        record: StudyRecord | dict[str, Any],
        *,
        input_hash: str | None = None,
    ) -> AuditResponse:
        if not isinstance(record, StudyRecord):
            record = StudyRecord.model_validate(record)
        version = self.config.rules_version
        checks: list[CheckResult] = []
        completeness_score, completeness_check = self._completeness(record)
        checks.append(completeness_check)

        if not record.arm_ns:
            checks.append(_check(
                "ENROLLMENT-ARM-SUM", version, "not_applicable", "info",
                "Per-arm enrollment was not reported.", {"randomized_n": record.randomized_n},
                "internal arithmetic feasibility; CONSORT participant-flow context",
            ))
        elif (
            not record.arm_counts_comparable_to_randomized_n
            or record.metadata.get("arm_count_basis") == "participant_flow_first_period_started"
        ):
            checks.append(_check(
                "ENROLLMENT-ARM-SUM", version, "not_applicable", "info",
                "Arm counts and enrollment use different population/time-point definitions.",
                {
                    "arm_total": sum(record.arm_ns.values()),
                    "randomized_n": record.randomized_n,
                    "arm_ns": record.arm_ns,
                    "arm_count_basis": record.metadata.get("arm_count_basis"),
                },
                "internal arithmetic feasibility; CONSORT participant-flow context",
            ))
        else:
            arm_total = sum(record.arm_ns.values())
            if arm_total > record.randomized_n:
                verdict, severity = "flag", "high"
                rationale = "Reported arm counts exceed randomized enrollment."
            elif arm_total < record.randomized_n:
                verdict, severity = "warn", "low"
                rationale = "Reported arm counts do not account for every randomized participant."
            else:
                verdict, severity = "pass", "info"
                rationale = "Reported arm counts equal randomized enrollment."
            checks.append(_check(
                "ENROLLMENT-ARM-SUM", version, verdict, severity, rationale,
                {"arm_total": arm_total, "randomized_n": record.randomized_n, "arm_ns": record.arm_ns},
                "internal arithmetic feasibility; CONSORT participant-flow context",
            ))

        if not record.subgroups:
            for rule_id, reference in (
                ("SUBGROUP-PARTITION", "CONSORT-2025-item-21"),
                
                ("SUBGROUP-PERCENT", "integer-feasibility"),
                ("SUBGROUP-INTERACTION-TEST", "formal interaction-test criterion aligned with ICEMAN principles"),
                ("SUBGROUP-INTERACTION-POWER", "Brookes-derived interaction-power heuristic"),
                ("SUBGROUP-EVENT-FLOOR", "events-per-parameter heuristic"),
                ("SUBGROUP-EXPECTED-CELL", "chi-square approximation"),
            ):
                checks.append(_check(
                    rule_id, version, "not_applicable", "info",
                    "No eligible subgroup analysis was supplied.", {}, reference,
                ))
        for subgroup in record.subgroups:
            target_n = subgroup.population_n or record.analyzed_n or record.randomized_n
            total = sum(level.n for level in subgroup.levels)
            level_overcounts = [
                {"level": level.name, "level_n": level.n, "target_n": target_n}
                for level in subgroup.levels
                if level.n > target_n
            ]
            arm_mismatches = [
                {"level": level.name, "level_n": level.n, "arm_total": sum(level.arm_ns.values())}
                for level in subgroup.levels
                if level.arm_ns and sum(level.arm_ns.values()) != level.n
            ]
            if level_overcounts or arm_mismatches or (
                subgroup.mutually_exclusive and total > target_n
            ):
                verdict, severity = "flag", "high"
                rationale = "Subgroup counts are arithmetically impossible for the analysis population."
            elif subgroup.mutually_exclusive and subgroup.exhaustive and total < target_n:
                verdict, severity = "warn", "medium"
                rationale = "An exhaustive subgroup does not account for the analysis population."
            else:
                verdict, severity = "pass", "info"
                rationale = (
                    "Overlapping subgroup levels are individually feasible for the analysis population."
                    if not subgroup.mutually_exclusive
                    else "Subgroup counts are feasible for the analysis population."
                )
            checks.append(_check(
                "SUBGROUP-PARTITION", version, verdict, severity, rationale,
                {"variable": subgroup.variable, "subgroup_total": total, "target_n": target_n,
                 "mutually_exclusive": subgroup.mutually_exclusive,
                 "exhaustive": subgroup.exhaustive, "level_overcounts": level_overcounts,
                 "arm_mismatches": arm_mismatches},
                "internal arithmetic feasibility; CONSORT subgroup reporting context",
            ))

            percentage_issues: list[dict[str, Any]] = []
            percentage_count = 0
            for level in subgroup.levels:
                if level.percentage is None:
                    continue
                percentage_count += 1
                denominator = level.percentage_denominator or target_n
                implied = 100.0 * level.n / denominator
                tolerance = 0.5 * 10 ** (-level.percentage_decimals)
                if abs(implied - level.percentage) > tolerance + 1e-12:
                    percentage_issues.append({
                        "level": level.name, "n": level.n, "denominator": denominator,
                        "reported_percentage": level.percentage,
                        "implied_percentage": implied, "tolerance": tolerance,
                    })
            checks.append(_check(
                "SUBGROUP-PERCENT", version,
                "not_applicable" if not percentage_count else ("flag" if percentage_issues else "pass"),
                "info" if not percentage_issues else "medium",
                "No subgroup percentages were supplied." if not percentage_count else (
                    "Reported percentages are incompatible with integer counts."
                    if percentage_issues else "Reported percentages are compatible with integer counts."
                ),
                {"variable": subgroup.variable, "issues": percentage_issues},
                "integer-feasibility",
            ))

            if subgroup.effect_claimed:
                has_test = subgroup.formal_interaction_test is True
                checks.append(_check(
                    "SUBGROUP-INTERACTION-TEST", version,
                    "pass" if has_test else "warn", "info" if has_test else "medium",
                    "A formal interaction test supports the subgroup claim." if has_test
                    else "A subgroup effect is claimed without a reported formal interaction test criterion.",
                    {"variable": subgroup.variable, "prespecified": subgroup.prespecified,
                     "interaction_p": subgroup.interaction_p}, "formal interaction-test criterion aligned with ICEMAN principles",
                ))
                required = subgroup.main_effect_required_n
                if subgroup.powered_for_interaction is True:
                    power_verdict, power_severity = "pass", "info"
                    power_text = "The report states that sample size was planned for the interaction."
                    power_evidence = {"variable": subgroup.variable, "powered_for_interaction": True}
                elif required:
                    interaction_floor = math.ceil(required * self.config.interaction_sample_multiplier)
                    underpowered = record.randomized_n < interaction_floor
                    power_verdict = "warn" if underpowered else "pass"
                    power_severity = "medium" if underpowered else "info"
                    power_text = (
                        "Enrollment is below the configured Brookes-derived interaction-power heuristic."
                        if underpowered else "Enrollment meets the configured Brookes-derived interaction-power heuristic."
                    )
                    power_evidence = {"variable": subgroup.variable, "randomized_n": record.randomized_n,
                                      "main_effect_required_n": required,
                                      "interaction_required_n": interaction_floor,
                                      "multiplier": self.config.interaction_sample_multiplier,
                                      "heuristic": True}
                else:
                    power_verdict, power_severity = "not_applicable", "info"
                    power_text = "Interaction power heuristic cannot be assessed because the planning target is absent."
                    power_evidence = {
                        "variable": subgroup.variable,
                        "powered_for_interaction": subgroup.powered_for_interaction,
                        "heuristic": True,
                    }
                checks.append(_check(
                    "SUBGROUP-INTERACTION-POWER", version, power_verdict, power_severity,
                    power_text, power_evidence, "Brookes-derived interaction-power heuristic",
                ))
            else:
                checks.append(_check(
                    "SUBGROUP-INTERACTION-TEST", version, "not_applicable", "info",
                    "No subgroup effect is claimed.", {"variable": subgroup.variable}, "formal interaction-test criterion aligned with ICEMAN principles",
                ))
                checks.append(_check(
                    "SUBGROUP-INTERACTION-POWER", version, "not_applicable", "info",
                    "No subgroup effect is claimed.", {"variable": subgroup.variable}, "Brookes-derived interaction-power heuristic",
                ))

            event_rows = [
                {"level": level.name, "events": level.events}
                for level in subgroup.levels if level.events is not None
            ]
            if event_rows:
                floor = self.config.min_events_per_parameter * subgroup.estimated_parameters
                failures = [row for row in event_rows if (row["events"] or 0) < floor]
                checks.append(_check(
                    "SUBGROUP-EVENT-FLOOR", version, "warn" if failures else "pass",
                    "medium" if failures else "info",
                    "One or more subgroup levels fall below the configured event floor."
                    if failures else "Every reported subgroup level meets the configured event floor.",
                    {"variable": subgroup.variable, "floor": floor, "failures": failures,
                     "heuristic": "events per estimated parameter"},
                    "events-per-parameter heuristic",
                ))
            else:
                checks.append(_check(
                    "SUBGROUP-EVENT-FLOOR", version, "not_applicable", "info",
                    "No subgroup event counts were supplied.", {"variable": subgroup.variable},
                    "events-per-parameter heuristic",
                ))
            if subgroup.contingency_cells:
                low_cells = [value for value in subgroup.contingency_cells if value < self.config.min_expected_cell_count]
                checks.append(_check(
                    "SUBGROUP-EXPECTED-CELL", version, "warn" if low_cells else "pass",
                    "medium" if low_cells else "info",
                    "Chi-square approximation may be unreliable because expected cells are below the floor."
                    if low_cells else "Expected cell counts meet the configured chi-square floor.",
                    {"variable": subgroup.variable, "minimum": self.config.min_expected_cell_count,
                     "observed_min": min(subgroup.contingency_cells), "low_cells": low_cells},
                    "chi-square approximation",
                ))
            else:
                checks.append(_check(
                    "SUBGROUP-EXPECTED-CELL", version, "not_applicable", "info",
                    "No contingency-table expected counts were supplied.",
                    {"variable": subgroup.variable}, "chi-square approximation",
                ))

        for index, reported in enumerate(record.reported_stats):
            result = statcheck(reported, self.config.alpha)
            if not result["applicable"]:
                verdict, severity = "not_applicable", "info"
                rationale = result["reason"]
            elif result["decision_error"]:
                verdict, severity = "flag", "high"
                rationale = "Reported and recomputed p-values cross the configured significance threshold."
            elif not result["consistent"]:
                verdict, severity = "warn", "medium"
                rationale = "Reported p-value is outside the interval allowed by printed-statistic rounding."
            else:
                verdict, severity = "pass", "info"
                rationale = "Reported p-value is consistent with the recomputed rounding interval."
            checks.append(_check(
                "STATCHECK", version, verdict, severity, rationale,
                {"method_family": "test_statistic", "index": index,
                 "stat_type": reported.stat_type, "value": reported.value,
                 "reported_p": reported.reported_p, **result}, "Nuijten-Epskamp",
            ))
        for index, effect in enumerate(record.reported_effects):
            result = ci_p_check(effect, self.config.alpha)
            if not result["applicable"]:
                verdict, severity = "not_applicable", "info"
                rationale = result["reason"]
            elif result["decision_error"]:
                verdict, severity = "warn", "medium"
                rationale = (
                    "The CI-derived and reported p-values cross the significance threshold; "
                    "review model, adjustment, and tail assumptions."
                )
            elif not result["consistent"]:
                verdict, severity = "warn", "medium"
                rationale = (
                    "The reported p-value is outside the range implied by the confidence "
                    "interval and printed precision."
                )
            else:
                verdict, severity = "pass", "info"
                rationale = "The reported p-value is compatible with the confidence interval."
            checks.append(_check(
                "STATCHECK", version, verdict, severity, rationale,
                {
                    "method_family": "ci_to_p",
                    "index": index,
                    "name": effect.name,
                    "estimate": effect.estimate,
                    "ci": [effect.ci_lower, effect.ci_upper],
                    "reported_p": effect.reported_p,
                    "p_operator": effect.p_operator,
                    "statistical_method_family": effect.statistical_method_family,
                    "applicability_tier": effect.applicability_tier,
                    "analysis_population_n": effect.analysis_population_n,
                    "ci_sidedness": effect.ci_sidedness,
                    "parameter_type": effect.parameter_type,
                    "adjustment_comment": effect.adjustment_comment,
                    "selected_groups": effect.selected_groups,
                    **result,
                    "limitations": (
                        "Normal approximation; reported CI and p-value can legitimately use "
                        "different adjusted models. Findings are review signals, not errors."
                    ),
                },
                "Altman-Bland-2011",
            ))
        if not record.reported_stats and not record.reported_effects:
            registry_count = int(record.metadata.get("reported_analyses_count", 0) or 0)
            checks.append(_check(
                "STATCHECK", version, "not_applicable", "info",
                "Registry analyses do not report a test statistic with degrees of freedom."
                if registry_count else "No eligible reported statistics were supplied.",
                {"registry_analyses_without_recomputable_statistic": registry_count},
                "Nuijten-Epskamp",
            ))

        for index, summary in enumerate(record.integer_summaries):
            grim_result = grim(summary)
            if not grim_result["applicable"]:
                grim_verdict, grim_severity = "not_applicable", "info"
                grim_text = grim_result["reason"]
            elif grim_result["consistent"]:
                grim_verdict, grim_severity = "pass", "info"
                grim_text = "The rounded mean is feasible for the integer sample."
            else:
                grim_verdict, grim_severity = "flag", "high"
                grim_text = "The rounded mean is not feasible for the integer sample."
            checks.append(_check(
                "GRIM", version, grim_verdict, grim_severity, grim_text,
                {"index": index, "name": summary.name, **grim_result}, "Brown-Heathers-2016",
            ))
            if summary.sd is not None:
                grimmer_result = grimmer(summary)
                if not grimmer_result["applicable"]:
                    verdict, severity, text = "not_applicable", "info", grimmer_result["reason"]
                elif grimmer_result["consistent"]:
                    verdict, severity, text = "pass", "info", "A feasible integer sum of squares matches the reported SD."
                else:
                    verdict, severity, text = "flag", "high", "No feasible integer sum of squares matches the mean, SD, and parity constraints."
                checks.append(_check(
                    "GRIMMER", version, verdict, severity, text,
                    {"index": index, "name": summary.name, **grimmer_result}, "Allard-2018",
                ))
            else:
                checks.append(_check(
                    "GRIMMER", version, "not_applicable", "info",
                    "A standard deviation was not supplied.",
                    {"index": index, "name": summary.name}, "Allard-2018",
                ))
        if not record.integer_summaries:
            checks.append(_check(
                "GRIM", version, "not_applicable", "info",
                "No integer-scale summaries were supplied.", {}, "Brown-Heathers-2016",
            ))
            checks.append(_check(
                "GRIMMER", version, "not_applicable", "info",
                "No integer-scale mean/SD summaries were supplied.", {}, "Allard-2018",
            ))

        baseline = combine_baseline(record.baseline_comparisons)
        if baseline["applicable"]:
            # Fisher's two-sided extremeness is the sole decision statistic.
            # Stouffer is reported descriptively, not minimized with Fisher (which
            # would introduce an uncorrected multiple-testing opportunity).
            extreme_p = min(
                1.0, 2.0 * min(baseline["fisher_p"], 1.0 - baseline["fisher_p"])
            )
            fisher_low, fisher_high = baseline["fisher_p_interval"]
            if fisher_low <= 0.5 <= fisher_high:
                conservative_extreme_p = 1.0
            else:
                conservative_extreme_p = max(
                    2.0 * min(fisher_low, 1.0 - fisher_low),
                    2.0 * min(fisher_high, 1.0 - fisher_high),
                )
            extreme = conservative_extreme_p < self.config.baseline_extreme_p
            one_variable = len(record.baseline_comparisons) == 1
            checks.append(_check(
                "BASELINE-ANOMALY", version, "warn" if extreme else "pass",
                "low" if extreme else "info",
                (
                    "The continuous-baseline Welch p-value is unusually extreme after "
                    "propagating printed precision; treat this as a weak screening signal."
                    if one_variable
                    else "Combined continuous-baseline p-values are unusually extreme after "
                    "propagating printed precision; treat this as a weak screening signal."
                ) if extreme else (
                    "The continuous-baseline Welch p-value is not extreme after propagating "
                    "printed precision."
                    if one_variable
                    else "Combined continuous-baseline p-values are not extreme after "
                    "propagating printed precision."
                ),
                {**baseline, "two_sided_extreme_p": extreme_p,
                 "conservative_two_sided_extreme_p": conservative_extreme_p,
                 "threshold": self.config.baseline_extreme_p,
                 "combination_method": "single-variable Welch" if one_variable else "Fisher",
                 "limitations": (
                     "Continuous variables only; printed mean/SD precision is propagated. "
                     "Cross-variable combination requires declared independence."
                 )},
                "Carlisle-2017",
            ))
        else:
            checks.append(_check(
                "BASELINE-ANOMALY", version, "not_applicable", "info",
                baseline["reason"], baseline, "Carlisle-2017",
            ))

        source_schema_unavailable = set(record.metadata.get("source_schema_unavailable_rules", []))
        for check in checks:
            if check.verdict == "not_applicable":
                check.evidence["not_applicable_scope"] = (
                    "source_schema" if check.rule_id in source_schema_unavailable else "record"
                )

        # Python's sort is stable, so checks within a rule retain the explicit
        # record/enumeration order in which they were appended. Evidence changes
        # cannot silently reshuffle otherwise identical findings.
        checks.sort(key=lambda item: (CHECK_ORDER.get(item.rule_id, 999), item.rule_id))
        if any(item.verdict == "flag" for item in checks):
            overall = "flag"
        elif any(item.verdict == "warn" for item in checks):
            overall = "warn"
        else:
            overall = "pass"
        digest = input_hash or content_hash(
            record, self.config.version, self.config.model_revision
        )
        return AuditResponse(
            study_id=record.study_id,
            completeness_score=round(completeness_score, 6),
            feasibility_verdict=overall,
            checks=checks,
            config_version=self.config.version,
            model_revision=self.config.model_revision,
            rules_version=version,
            input_hash=digest,
            disclaimer=DISCLAIMER,
        )
