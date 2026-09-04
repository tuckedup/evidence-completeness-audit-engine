# Changelog

## 1.2.0 - 2026-09-03

- Propagated reported mean/SD precision through baseline Welch and Fisher screening, removing
  similarity warnings caused solely by rounded-equal arm means.
- Added Altman–Bland confidence-interval-to-p screening, including log-scale ratio measures,
  confidence levels, inequality p-values, one-sided methods, and rounding intervals.
- Split source-schema and record-level non-applicability in audit evidence and corpus summaries.
- Replaced ceiling-only positive controls with three-dose curves and explicit field provenance.

## 1.1.0 - 2026-09-03

- Replaced relevance-query cohort selection with hard API filters and write-time assertions.
- Hydrated registry baseline partitions, mean/SD summaries, outcome-analysis counts, and
  analysis-population denominators.
- Corrected one-tailed F/chi-square/Q behavior and made multi-item GRIMMER inapplicable.
- Recalibrated heuristic severities, guarded correlated baseline combinations, removed the
  process-global cache mutex, moved cold/batch work off the event loop, and used ORJSON caching.
- Added diverse-key cold/mixed/warm benchmark scenarios and externally anchored fixtures.
- Preserved registry baseline classes and overlapping-category semantics, eliminating parser-created hard flags.
- Split semantic and operational configuration hashes, validated reporting keys/config ranges, and made packaged TOML authoritative.
- Added raw-request cache keys, server timing, a low-overhead Node generator, and matched 20K hydrated-corpus curves.
- Added 20 upstream reference cases, 55K numerical checks, and 200 paired positive controls.

## 1.0.0 - 2026-09-03

- Initial deterministic audit engine, offline extraction adapters, public-data ingestion,
  FastAPI/Redis packaging, open-loop benchmark, agreement/rerun harnesses, and 220 versioned
  regression cases.
