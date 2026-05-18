# Pilot Efficacy Report (`pilot_run_001`)

## Run metadata
- Run ID: `pilot_run_001`
- Seed: `153`
- Studies: 2 (`study_prompt_framing_001`, `study_social_norms_002`)
- Artifacts folder: `artifacts/pilot_run_001`

## Budget results
- Global budget cap: **$250.00**
- Actual pilot spend: **$1.38**
- Spend by stage:
  - Extraction: $0.08
  - Simulation: $0.90
  - Analysis: $0.20
  - Reporting: $0.20
- Estimated equivalent runs under $250 at this scale: ~181 runs (`250 / 1.38`)

## Statistical outputs
- `study_prompt_framing_001`
  - Effect size: `3.211`
  - 95% bootstrap CI: `[0.846, 5.662]`
  - Permutation p-value: `0.0166`
  - Replicated in simulation: `true`
- `study_social_norms_002`
  - Effect size: `4.006`
  - 95% bootstrap CI: `[1.267, 6.658]`
  - Permutation p-value: `0.0033`
  - Replicated in simulation: `true`

## Risk ranking output
1. `study_prompt_framing_001` (risk `0.3536`, uncertainty `0.6020`)
2. `study_social_norms_002` (risk `0.3014`, uncertainty `0.6739`)

## Reproducibility artifacts generated
- `artifacts/pilot_run_001/manifest.json`
- `artifacts/pilot_run_001/extraction.json`
- `artifacts/pilot_run_001/analysis.json`
- `artifacts/pilot_run_001/ranking.json`
- `artifacts/pilot_run_001/trials.json`

## Notes
- This pilot used `configs/credits.demo.json` for local validation testing.
- Before production runs, replace with a manually verified credit file per `docs/credit_playbook.md`.
