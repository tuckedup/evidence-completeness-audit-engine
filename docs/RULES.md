# Rule semantics

All thresholds live in the packaged `src/ecsae/default.toml` and are included in `config_version`.

| Rule | Outcome | Important boundary |
|---|---|---|
| Enrollment/arm sum | pass, warn, flag, N/A | internal arithmetic feasibility only; comparisons require matched population/time-point definitions |
| Subgroup partition | pass, warn, flag | internal arithmetic feasibility per variable and registry class; overlapping levels are checked individually, not summed |
| Percentage feasibility | pass, flag, N/A | count/denominator must round to the printed percentage |
| Interaction test | pass, warn | formal interaction-test criterion aligned with ICEMAN principles; this is not a full ICEMAN credibility assessment |
| Interaction power | pass, warn, N/A | configurable Brookes-derived heuristic; missing planning targets return cannot assess |
| Event floor | pass, warn, N/A | heuristic only: events per estimated parameter |
| Expected cells | pass, warn | warns below configured chi-square approximation floor |
| statcheck | pass, warn, flag, N/A | test-statistic rounding interval; threshold crossing flags; adjusted p-values are N/A |
| CI-to-p consistency | pass, warn, N/A | Altman–Bland normal approximation; printed precision propagated, ratios log-transformed, incompatibilities only warn because CI and p may use different models |
| GRIM | pass, flag, N/A | integer scales only; N > 10^decimals is uninformative |
| GRIMMER | pass, flag, N/A | single-item integer observations only; integer sum of squares, rounded SD, and parity must all be feasible |
| Baseline anomaly | pass, warn, N/A | continuous variables only; mean/SD rounding boxes propagated; one variable is a Welch screen, multiple variables require explicit independence for Fisher combination |

Every N/A result includes `not_applicable_scope=source_schema|record`. The ClinicalTrials.gov
normalizer marks eight rule families that cannot be populated faithfully from that source.

No finding asserts fraud or misconduct. The disclaimer is part of every audit response.
