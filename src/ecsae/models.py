from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REPORTING_FIELDS = {
    "analysis_population", "primary_outcome", "effect_estimate", "precision", "harms",
    "protocol_registration", "data_sharing", "conflicts_of_interest",
    "patient_public_involvement",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportedStat(StrictModel):
    stat_type: Literal["t", "F", "chi2", "r", "z", "Q"]
    value: float
    df1: float | None = None
    df2: float | None = None
    reported_p: float | None = Field(default=None, ge=0, le=1)
    p_operator: Literal["=", "<", ">", "<=", ">="] = "="
    statistic_decimals: int = Field(default=2, ge=0, le=10)
    p_decimals: int = Field(default=3, ge=0, le=10)
    adjusted: bool = False
    one_tailed: bool = False
    source: str | None = None

    @model_validator(mode="after")
    def validate_dfs(self) -> "ReportedStat":
        if self.stat_type in {"t", "chi2", "r", "Q"} and not (self.df1 and self.df1 > 0):
            raise ValueError(f"{self.stat_type} requires positive df1")
        if self.stat_type == "F" and not (
            self.df1 and self.df1 > 0 and self.df2 and self.df2 > 0
        ):
            raise ValueError("F requires positive df1 and df2")
        if self.stat_type == "r" and not -1 < self.value < 1:
            raise ValueError("r must be strictly between -1 and 1")
        return self


class SubgroupLevel(StrictModel):
    name: str
    n: int = Field(ge=0)
    arm_ns: dict[str, int] = Field(default_factory=dict)
    percentage: float | None = Field(default=None, ge=0, le=100)
    percentage_decimals: int = Field(default=1, ge=0, le=6)
    percentage_denominator: int | None = Field(default=None, gt=0)
    events: int | None = Field(default=None, ge=0)

    @field_validator("arm_ns")
    @classmethod
    def nonnegative_arms(cls, value: dict[str, int]) -> dict[str, int]:
        if any(n < 0 for n in value.values()):
            raise ValueError("arm counts must be non-negative")
        return value


class SubgroupAnalysis(StrictModel):
    variable: str
    levels: list[SubgroupLevel]
    population_n: int | None = Field(default=None, gt=0)
    mutually_exclusive: bool = True
    exhaustive: bool = True
    prespecified: bool | None = None
    effect_claimed: bool = False
    formal_interaction_test: bool | None = None
    interaction_p: float | None = Field(default=None, ge=0, le=1)
    powered_for_interaction: bool | None = None
    main_effect_required_n: int | None = Field(default=None, gt=0)
    estimated_parameters: int = Field(default=1, gt=0)
    contingency_cells: list[float] = Field(default_factory=list)


class IntegerSummary(StrictModel):
    name: str
    mean: float
    n: int = Field(gt=0)
    mean_decimals: int = Field(default=2, ge=0, le=10)
    items: int = Field(default=1, gt=0)
    sd: float | None = Field(default=None, ge=0)
    sd_decimals: int = Field(default=2, ge=0, le=10)
    integer_scale: bool = True


class BaselineComparison(StrictModel):
    name: str
    mean_a: float
    sd_a: float = Field(gt=0)
    n_a: int = Field(gt=1)
    mean_b: float
    sd_b: float = Field(gt=0)
    n_b: int = Field(gt=1)
    mean_decimals_a: int = Field(default=10, ge=0, le=10)
    sd_decimals_a: int = Field(default=10, ge=0, le=10)
    mean_decimals_b: int = Field(default=10, ge=0, le=10)
    sd_decimals_b: int = Field(default=10, ge=0, le=10)
    variable_type: Literal["continuous", "binary", "categorical"] = "continuous"
    independent_from_other_variables: bool = False
    correlated_with: list[str] = Field(default_factory=list)


class ReportedEffect(StrictModel):
    """Effect estimate, confidence interval, and p-value reported for one analysis."""

    name: str
    estimate: float
    ci_lower: float
    ci_upper: float
    confidence_level: float = Field(default=95.0, gt=0, lt=100)
    reported_p: float = Field(ge=0, le=1)
    p_operator: Literal["=", "<", ">", "<=", ">="] = "="
    estimate_decimals: int = Field(default=10, ge=0, le=10)
    ci_lower_decimals: int = Field(default=10, ge=0, le=10)
    ci_upper_decimals: int = Field(default=10, ge=0, le=10)
    p_decimals: int = Field(default=3, ge=0, le=10)
    scale: Literal["additive", "ratio"] = "additive"
    one_tailed: bool = False
    statistical_method_family: str | None = None
    applicability_tier: Literal["high_confidence", "approximate_review"] | None = None
    analysis_population_n: int | None = Field(default=None, gt=0)
    ci_sidedness: Literal["two_sided", "one_sided", "unspecified"] = "unspecified"
    parameter_type: str | None = None
    adjustment_comment: str | None = None
    selected_groups: list[str] = Field(default_factory=list)
    source: str | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> "ReportedEffect":
        if self.ci_lower >= self.ci_upper:
            raise ValueError("confidence interval lower bound must be below upper bound")
        if self.scale == "ratio" and min(self.estimate, self.ci_lower, self.ci_upper) <= 0:
            raise ValueError("ratio-scale estimate and confidence limits must be positive")
        return self


class StudyRecord(StrictModel):
    study_id: str | None = None
    source: str | None = None
    title: str | None = None
    randomized_n: int = Field(gt=0)
    analyzed_n: int | None = Field(default=None, gt=0)
    arm_ns: dict[str, int] = Field(default_factory=dict)
    arm_counts_comparable_to_randomized_n: bool = True
    subgroups: list[SubgroupAnalysis] = Field(default_factory=list)
    reported_stats: list[ReportedStat] = Field(default_factory=list)
    reported_effects: list[ReportedEffect] = Field(default_factory=list)
    integer_summaries: list[IntegerSummary] = Field(default_factory=list)
    baseline_comparisons: list[BaselineComparison] = Field(default_factory=list)
    reporting: dict[str, bool | None] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arm_ns")
    @classmethod
    def valid_arm_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if any(n < 0 for n in value.values()):
            raise ValueError("arm counts must be non-negative")
        return value

    @field_validator("reporting")
    @classmethod
    def known_reporting_fields(cls, value: dict[str, bool | None]) -> dict[str, bool | None]:
        unknown = set(value) - REPORTING_FIELDS
        if unknown:
            raise ValueError(f"unknown reporting field(s): {', '.join(sorted(unknown))}")
        return value


class CheckResult(StrictModel):
    rule_id: str
    rule_version: str
    verdict: Literal["pass", "warn", "flag", "not_applicable"]
    severity: Literal["info", "low", "medium", "high"]
    rationale: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    guideline_ref: str | None = None


class AuditResponse(StrictModel):
    study_id: str | None
    completeness_score: float = Field(ge=0, le=1)
    feasibility_verdict: Literal["pass", "warn", "flag"]
    checks: list[CheckResult]
    config_version: str
    model_revision: str
    rules_version: str
    input_hash: str
    disclaimer: str
