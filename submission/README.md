# ECSAE submission guide

This folder contains the reviewer package for the Evidence Completeness Statistical Audit Engine.
`submission/package/` is the trimmed set to send for one-shot review: the top-level README, the
machine-generated evidence, the three canonical benchmark artifacts, the core implementation files,
and the reproducibility docs.

## What this snapshot supports

- A 2,000-study randomized ClinicalTrials.gov cohort with checksummed provenance and a separate
  QC artifact.
- Deterministic rule and statistical screening over that cohort, including live CI-to-p screening
  on 465 studies / 5,999 processed analyses, with 137 additional eligible analyses truncated by
  the per-study cap and accounted for separately.
- A resolved baseline false-positive incident: warnings fell from 38 to 5 across 632 applicable
  studies after rounding-aware handling.
- A versioned 220-case regression matrix, zero-mismatch rerun artifact, and a current 71-test
  passing suite.
- A canonical cache-hit benchmark suite at 20,000 requests with warm, mixed, and cold results
  reported separately.

## What this snapshot does not claim

- It does not claim 93% agreement, because the independent adjudication set is still pending.
- It does not claim 99.5% pass rate across 30 releases, because the release-ledger gate is still
  pending.
- It does not treat automated warnings or flags as findings of error or misconduct.

## Review order

1. Start with `README.md` for the current evidence summary and the flag-triage narrative.
2. Read the machine-generated artifacts under `eval/results/`.
3. Check the canonical benchmark artifacts under `bench/results/`.
4. Review the core rule implementation files under `src/ecsae/`.
5. Finish with `docs/`, `CHANGELOG.md`, and `uv.lock` for release discipline and reproducibility.

## Benchmark packaging note

The canonical submission set includes the 200 rps warm, mixed, and cold artifacts. Warm-cache knee
probing on this host supports the statement: sustained 1,000 rps warm with zero errors; degrades by
1,200 rps. The 1,100 rps point is intentionally omitted from both the repository and the submission
package.

## Submitted files

1. `package/PROJECT_BRIEF.md`
2. `package/README.md`
3. `package/eval/results/audit-summary-ctg-randomized-2000.json`
4. `package/eval/results/corpus-qc-ctg-randomized-2000.json`
5. `package/eval/results/statistical-validation.json`
6. `package/eval/results/positive-control-corruptions.json`
7. `package/bench/results/node-warm-hydrated-20k-200rps-local-windows.json`
8. `package/bench/results/node-cold-hydrated-20k-200rps-local-windows.json`
9. `package/bench/results/node-mixed90-hydrated-20k-200rps-local-windows.json`
10. `package/eval/results/rerun-ctg-randomized-2000.json`
11. `package/tests/regression/MATRIX.json`
12. `package/src/ecsae/stats.py`
13. `package/src/ecsae/engine.py`
14. `package/src/ecsae/data/clinical_trials.py`
15. `package/docs/EVAL_CARD.md`
16. `package/docs/MODEL_CARD.md`
17. `package/CHANGELOG.md`
18. `package/uv.lock`
19. `package/eval/results/pytest.xml`
20. `package/eval/results/coverage.json`
21. `package/docs/DEPLOYMENT.md`

## Verification commands

Run these from the repository root:

```bash
python tools/sync_readme_metrics.py --check
.\.venv\Scripts\python.exe -m pytest --basetemp .pytest_tmp
```

If you want to inspect the canonical benchmark metadata directly:

```bash
python -m json.tool bench/results/node-warm-hydrated-20k-200rps-local-windows.json
python -m json.tool bench/results/node-mixed90-hydrated-20k-200rps-local-windows.json
python -m json.tool bench/results/node-cold-hydrated-20k-200rps-local-windows.json
```