# ECSAE project brief

Evidence Completeness Statistical Audit Engine audits randomized-study records for internal
feasibility and registry completeness proxy signals. The core system combines deterministic rule logic,
statistical consistency checks, and reproducible evidence artifacts so every claim can be traced to
checked-in output rather than presentation text.

The current repository snapshot is strongest on engineering discipline. It processes a 2,000-study
ClinicalTrials.gov cohort with checksummed provenance, keeps rule applicability explicit, records
machine-readable audit output, and packages the service behind FastAPI with cache-aware benchmark
artifacts. The reviewed benchmark suite separates warm, mixed, and cold paths instead of hiding the
miss path inside a single summary number.

The most important scientific correction in this snapshot is the flag-triage incident. An initial
run produced hard flags that looked like study defects but were actually parser defects: one path
compared participant-flow counts against a non-comparable enrollment field, and another flattened
baseline classes and time periods into false subgroup contradictions. After fixing those parser
assumptions, the remaining baseline warning issue was traced to registry rounding precision and
reduced from 38 warnings to 5 among 632 applicable studies.

The current evidence also activates CI-to-p screening on real registry data: 465 studies and 5,999
processed analyses are currently screened, with 137 additional eligible analyses counted as
truncated by the per-study cap. At the same time, the repository is explicit that eight
rule families are unavailable from the ClinicalTrials.gov source schema and therefore cannot be
misrepresented as record-level negatives.

What this package supports: a deterministic 220-case regression matrix, 69 passing tests in the
project environment, zero-mismatch rerun evidence, and a canonical benchmark story of sustained
1,000 rps warm with zero errors and degradation by 1,200 rps on this host. What it does not claim:
93% agreement and 99.5% pass rate across 30 releases. Those gates remain intentionally unclaimed
until the adjudication queue and release ledger exist.

For review, start with the repository README, then the audit summary and QC artifacts, then the
benchmark JSONs, then the core implementation files. The point of the package is not just that the
engine runs, but that the evidence, limitations, and non-claims are visible in the same place.