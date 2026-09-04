# Extraction model card

## Intended use

ECSAE extracts aggregate trial-report fields for statistical feasibility screening. It is not a
clinical decision system, misconduct detector, or substitute for source-document review.

## Components

- `regex-biomed-v1` deterministically extracts explicitly printed sample sizes, arm counts, and
  APA-style test statistics. It is the bundled fallback and is exposed at `/extract`.
- `GLiNERBiomedicalExtractor` is an offline-only transformer adapter for
  `Ihor/gliner-biomed-base-v1.0`. Construction fails unless the caller supplies an immutable
  model revision. `Dockerfile.extract` pins revision
  `146c133ff9da643f738f9817bc38cee479cebe8c` and bakes its weights; the slim API image never
  silently downloads a model.
- Numeric outputs are validated by strict Pydantic schemas before rule evaluation.

## Limitations

The fallback misses tables, non-APA prose, OCR corruption, adjusted p-values, nested/overlapping
subgroups, and domain-specific units. Transformer output requires a held-out evaluation by trial
ID, with no AACT structured-field leakage into input text. Extraction confidence is not evidence
that a statistical claim is correct.

The pinned transformer was executed twice over a 30-example repository-authored synthetic smoke
set. It was deterministic but achieved only 0.537 exact-span-and-label F1. This proves that the
adapter and pinned weights run; it is not evidence of clinical extraction quality, and transformer
spans are not promoted into audit inputs without validation. See
`eval/results/transformer-smoke-evaluation.json`.

## Reproducibility

The model revision participates in every cache key and response. Offline jobs must set
`PYTHONHASHSEED`, call `seed_everything`, pin model/tokenizer revisions, and persist input and
resolved-config hashes.

The transformer profile exposes `/extract/transformer` on port 8001. Its latency is explicitly
outside the cached `/audit` benchmark; production extraction should remain an offline batch.
