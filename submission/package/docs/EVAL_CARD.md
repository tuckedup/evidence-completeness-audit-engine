# Evaluation card

The evaluation unit is a controlled study, never an extracted span. Split by NCT ID so reports
from one trial cannot cross train/test boundaries.

Treat evaluation as three distinct layers:

- Rule correctness: independently verified structured examples and worked literature cases with
	mathematically or expert-derived expected outcomes.
- Extraction correctness: raw text or tables compared against human span labels and normalized
	field labels.
- End-to-end audit validity: study-level audit decisions compared against blinded manual
	adjudication.

`ecsae evaluate` currently supports only the structured-label comparison layer. When reference
labels are generated from public structured fields and predictions are generated from extracted
fields using the same versioned checks, the result measures extraction/pipeline fidelity rather
than independent validation of the rules themselves.

Required report fields:

- corpus snapshot identifier, query, retrieval timestamp, exclusions, and final count;
- per-family and overall confusion matrices;
- raw agreement, Cohen's kappa, Gwet's AC1, and deterministic bootstrap 95% intervals;
- rules-only, extractor-only, and hybrid ablations;
- error taxonomy: extraction miss, unit mismatch, rounding, source ambiguity, and rule gap;
- separate 150–300-record stratified manual-adjudication results.

`ecsae evaluate` will not invent a reference label. Every input row must carry
`reference_flag`, and the report explicitly states that agreement is not clinical truth.

## Perturbation sensitivity

`tools/positive_controls.py` creates paired clean/corrupted inputs using real registry records as
carriers. It evaluates three magnitudes per family: +1/+2/+5 participant counts, p-value shifts of
.001/.01/.05, and GRIM mean shifts of 1/2/5 last-decimal units. The artifact distinguishes genuine
registry subgroup fields, experimentally enabled enrollment comparability, and synthetic
statcheck/GRIM fields. Detection and hard-flag rates are reported separately. This does not replace
independent human labels, estimate prevalence, or establish extraction recall.
