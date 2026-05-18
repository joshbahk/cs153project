# Efficient AI Replication MVP

Budget-capped pipeline for triaging text-based studies by replication risk using simulated AI participants.

## Project layout
- `src/contracts`: `StudySpec`, `RunManifest`, and budget/credit contracts.
- `src/ingest`: raw study loading and normalization.
- `src/extraction`: protocol extraction with confidence + fallback tiering.
- `src/simulation`: agent generation and experiment runner with sequential sampling.
- `src/stats`: effect size, bootstrap CIs, and permutation p-values.
- `src/ranking`: replication-risk ranking logic.
- `src/cost`: budget guardrails and credit validation checks.
- `src/storage`: deterministic cache utilities.
- `src/cli/main.py`: end-to-end pipeline entrypoint.

## Quick start
1. Copy `configs/credits.example.json` to `configs/credits.local.json`.
2. Update credit entitlements and set `validated=true` only after manual billing checks.
3. Run:
   - `PYTHONPATH=src python -m cli.main --credits configs/credits.local.json --run-id pilot_run_001`
4. Inspect outputs in `artifacts/latest`.

## Tests
- `PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"`

## Operational docs
- `docs/credit_playbook.md`: required credit validation SOP.
- `docs/experiment_protocol.md`: reproducibility and statistical protocol.
