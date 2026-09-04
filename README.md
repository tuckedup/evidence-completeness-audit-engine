# Evidence Completeness Statistical Audit Engine (ECSAE)

ECSAE audits whether controlled-study enrollment, arm sizes, subgroup claims, rounded
statistics, and reporting fields are internally feasible and complete. It combines an offline
biomedical extraction boundary with versioned deterministic rules and statistical checks, then
serves auditable findings through FastAPI and Docker.

> Every flag is a potential inconsistency for human review—not evidence of error, fabrication,
> or misconduct. Rounding, transcription, adjusted analyses, and legitimate methodological
> choices can produce flags.

## What is implemented

- Enrollment/arm and per-variable subgroup partition arithmetic, integer percentage checks,
  a formal interaction-test criterion aligned with ICEMAN principles, a Brookes-derived
  interaction-power heuristic,
  event-per-parameter and expected-cell checks.
- Rounded statcheck-style recomputation for t, F, chi-square, Q, r, and z; confidence-interval-
  to-p screening for additive and ratio effects; GRIM and analytic
  GRIMMER with applicability and parity constraints; weak Carlisle/Fisher/Stouffer screening for
  continuous baseline variables.
- Weighted registry completeness proxy signals, strict schemas, evidence-rich/versioned findings, stable
  ordering, canonical input/config hashes, and the disclaimer in every successful audit response.
- Offline regex extraction plus a GLiNER biomedical adapter that refuses unpinned revisions.
- FastAPI endpoints, ETags, bounded batch requests, in-process LRU plus optional Redis,
  Prometheus metrics, Docker Compose, a Grafana dashboard, and an open-loop 20K HTTP benchmark.
- Exactly 220 golden regression cases, property tests, a deterministic rerun-diff harness,
  agreement metrics (raw, kappa, AC1, bootstrap CIs), CI, model/evaluation cards, and manual
  adjudication protocol.

## Quick start

```bash
uv sync --extra test
pytest -n auto --cov=ecsae
uvicorn ecsae.api.main:app --host 0.0.0.0 --port 8000
```

`uv.lock` fixes transitive dependencies across the supported Python versions. On Windows, use
`.venv\Scripts\python` and `.venv\Scripts\uvicorn`. For the packaged stack:

```bash
docker compose up --build
```

The optional pinned transformer image bakes GLiNER weights at an immutable revision and exposes
its offline-oriented endpoint on port 8001:

```bash
docker compose --profile transformer up --build extractor-api
```

OpenAPI is at `http://localhost:8000/docs`; health, readiness, metrics, rule manifest, and
version metadata are at `/health`, `/ready`, `/metrics`, `/rules`, and `/version`.

## Audit example

```bash
curl -s http://localhost:8000/audit \
  -H 'content-type: application/json' \
  --data @bench/payload.json

ecsae audit bench/payload.json
```

The hot path accepts pre-extracted structured data. `/extract` exposes the deterministic numeric
fallback; transformer extraction is a pinned, offline batch operation so model initialization
cannot compromise latency or rerun determinism.

## Public corpus and evaluation

ClinicalTrials.gov API v2 records are public and require no API key:

```bash
ecsae pull-ctg --limit 2000 --output data/corpus/ctg-2000.jsonl
ecsae rerun data/corpus/ctg-2000.jsonl --runs 2 --output eval/results/rerun-ctg-2000.json
```

The checked-in AACT SQL offers a snapshot-based alternative. Retraction Watch enrichment accepts
the separately obtained open CSV and retains only factual match metadata. Large/raw datasets are
gitignored; persist query, retrieval time, checksums, licenses, and exclusion counts with every
evaluation artifact.

An agreement corpus must contain an independently derived `reference_flag` per record:

```bash
ecsae evaluate data/corpus/held-out-labeled.jsonl \
  --output eval/results/agreement-report.json
```

Follow [the evaluation card](docs/EVAL_CARD.md) and adjudicate 150–300 stratified records. The
tool will not manufacture reference labels or present automated agreement as clinical truth.

ClinicalTrials.gov directly supports the registry completeness proxy, baseline subgroup partitions,
continuous-baseline screening, and CI-to-p consistency. Eight other checks require fields or
provenance the registry schema does not provide—for example test-statistic degrees of freedom,
integer-scale provenance, subgroup effect claims, event counts, or expected cells. Audit artifacts
therefore separate source-schema non-applicability from fields absent in an individual record.

## Reproducibility and performance

Golden behavior is under `tests/regression/cases/`; its fixed distribution is recorded in
`tests/regression/MATRIX.json`: 20 immutable expectations come from the upstream statcheck and
scrutiny test suites, and 200 are end-to-end ECSAE snapshots. Regenerate only for an intentional
rule migration:

```bash
python tools/generate_regression_cases.py
```

The benchmark is fixed-rate/open-loop and measures completion minus scheduled-send time, so
queueing delay is included. The low-overhead Node generator also captures the API's
`X-ECSAE-Server-Ms` header to expose generator-versus-service divergence:

```bash
node bench/loadtest.mjs --scenario warm --requests 20000 --rate 200 \
  --output bench/results/warm-20k.json
```

Performance evidence is hardware- and rate-specific. Warm-cache and cold-cache results must be
reported separately. Cache identity is computed from the exact request body plus semantic
configuration, so insignificant JSON whitespace or key-order changes intentionally produce a
miss; this keeps cache hits off the validation path without conflating distinct wire inputs. A
release pass-rate claim is accepted only from a ledger containing at
least 30 unique releases:

```bash
python tools/release_pass_rate.py release-ledger.jsonl --minimum-releases 30
```

## Evidence status

| Target claim | Evidence gate |
|---|---|
| ~2,000 controlled studies | Public corpus file plus retrieval manifest and checksum |
| 93% agreement | Held-out report plus kappa, AC1, bootstrap CIs, and manual adjudication |
| cached p95 <190 ms / 20K | Successful open-loop HTTP result with rate and environment |
| 220 regression cases | `tests/regression/MATRIX.json` and passing golden test |
| 100% rerun agreement | Zero-mismatch rerun artifact on the named corpus/config |
| 99.5% across 30 releases | CI-backed 30-release ledger; the tool refuses smaller histories |

Only the 220-case statement is inherent to the repository. The other numbers become claims only
after their evidence gates pass; targets are never silently relabeled as measurements.

## Current evidence

This repository only states claims that are backed by checked-in artifacts. The current
workspace evidence supports the implementation status below:

- The baseline false-positive issue was reduced from 38 warnings to 5 warnings across 632
  applicable studies after rounding-interval handling.
- CI-to-p screening is live on 465 studies / 5,999 analyses from the hydrated registry cohort.
- Source-schema non-applicability is separated from record-level non-applicability in the audit
  summary.
- The warm-cache load knee is bounded: sustained 1,000 rps succeeds with zero errors, while the
  1,200 rps run degrades. The 1,100 rps point is intentionally omitted from the repository and the
  canonical submission set.

### Measurements from the current workspace run

<!-- BEGIN GENERATED EVIDENCE -->
- Corpus: **2,000** hard-filtered completed, randomized, interventional, results-posted studies; QC passed=true, SHA-256 `07f7c78355bab94e042fe9f827b0a0d30e31903ee5680fda2cc99c17e78f58dd`.
- Audit screen: pass=134, warn=1866. These are automated screening outcomes, not adjudicated errors.
- Perturbation sensitivity: **600** controlled mutations across four rule families and three magnitudes; clean controls all pass. Detection is 100% for count invariants, 0%/100%/100% for p shifts of .001/.01/.05, and 98%/62%/66% for GRIM shifts of 1/2/5 last-decimal units.
- Statistical validation: **60,000** seeded checks against SciPy and known-valid integer data; passed=true.
- Registry applicability: CI-to-p screening covers 465 studies / 5,999 processed analyses (137 truncated after the per-study cap); baseline warnings fell to 5/632. Eight rules are unavailable from the registry source schema and are counted separately from record-level N/A.
- Determinism: 3 full-corpus runs, 4,000 comparisons, 0 mismatches (100.0% agreement).
- Cached performance: 20,000/20,000 hydrated-corpus hits at 200 rps; client p95 **28.37 ms**, server p95 **1.83 ms**, 0 errors.
- Matched load curves: cold client/server p95 4734.21/1153.45 ms; 90/10 mixed 36.65/7.63 ms with 18,000 hits and 2,000 misses.
- Tests: **71 passed**; branch-aware coverage **90.13%**; regression matrix exactly 220 cases (20 literature-anchored + 200 snapshots).
- Transformer smoke set: exact-span F1 **0.537**, with 0 rerun mismatches. This is runtime evidence, not clinical-corpus accuracy.
- Agreement and release history: 93% agreement and 99.5% across 30 releases are **not claimed**; the 200-row two-reviewer queue and release-ledger gate remain pending.
<!-- END GENERATED EVIDENCE -->

### Flag-triage incident: the tool was wrong, not the studies

The first hydrated-corpus run produced 74 hard-flagged studies. Investigation split those into
39 invalid enrollment comparisons—participant-flow `STARTED` counts had been compared with a
protocol enrollment field from a different time point—and 35 apparent subgroup impossibilities.
The latter came from flattening ClinicalTrials.gov baseline classes/time periods and summing
overlapping multi-response categories as if they were one mutually exclusive partition.

The fix preserved class boundaries, added explicit population comparability and mutual-exclusion
semantics, and changed non-comparable checks to N/A. A second review found 38 baseline warnings
caused by identical means rounded to registry precision. Propagating mean/SD rounding intervals
reduced these to five genuine extreme-difference review signals among 632 applicable studies.

To ensure that applicability guards did not simply disable the rules, a perturbation-sensitivity study applies
600 controlled mutations at three magnitudes. It distinguishes genuinely present registry subgroup
counts from experimentally enabled enrollment comparability and synthetic statcheck/GRIM fields;
the artifact reports warn/flag rates separately. This establishes sensitivity to those specified
perturbations, not clinical truth.

See [rule semantics](docs/RULES.md), [model card](docs/MODEL_CARD.md), and
[evaluation card](docs/EVAL_CARD.md).

## License and data ethics

Code is MIT. ClinicalTrials.gov/AACT records are U.S. Government public-domain data. Retraction
Watch factual metadata is available through Crossref under CC0; source-article/PMC licenses vary
and must be honored. ECSAE processes aggregate report data, not protected health information.
