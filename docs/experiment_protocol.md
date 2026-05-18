# Experiment Protocol

## Scope
- Text-only studies that can be represented as prompt/condition/outcome pipelines.
- Goal is triage: identify likely replication failures for prioritization in human studies.

## Reproducibility rules
- Every run must define `run_id`, `random_seed`, and config snapshot.
- Cache all extraction results and model responses keyed by study hash + settings.
- Store trial-level outputs and aggregate stats under `artifacts/<run_id>/`.

## Statistical workflow
- Sequential sampling in fixed batches (default 50).
- Stop early if CI width is below threshold to avoid unnecessary spend.
- Compute effect size, bootstrap CI, and permutation p-value.

## Budget workflow
- Global cap: $250.
- Per-study cap and per-stage caps enforced before each spend event.
- Abort pipeline immediately when any cap is exceeded.

## Quality gates
- Extraction confidence must exceed threshold, otherwise fallback extraction is used.
- Runs with missing control or treatment samples are invalid.
- All outputs include manifest metadata and budget ledger.
