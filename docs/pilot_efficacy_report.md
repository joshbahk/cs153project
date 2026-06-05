# Pilot Efficacy Report (`pilot_run_001`)

## Run Metadata
- Run ID: `pilot_run_001`
- Seed: `153`
- Studies: 2 synthetic pilot studies
- Artifacts folder: `artifacts/pilot_run_001`
- Model tier: `digitalocean_cpu_transparent_simulator`

## Budget Results
- Global cap: `$250.00`
- Recorded pilot spend: `$3.40`
- Spend by stage:
  - Extraction: `$0.08`
  - Simulation: `$2.52`
  - Analysis: `$0.60`
  - Reporting: `$0.20`
- Estimated equivalent pilot batches under `$250`: about `73`.

## Primary Statistical Outputs
- `study_prompt_framing_001`
  - Effect size: `4.958`
  - 95% bootstrap CI: `[2.159, 7.732]`
  - Permutation p-value: `0.0017`
  - Replicated in simulation: `true`
- `study_social_norms_002`
  - Effect size: `1.750`
  - 95% bootstrap CI: `[-1.826, 4.809]`
  - Permutation p-value: `0.2729`
  - Replicated in simulation: `false`

## Risk Ranking
1. `study_social_norms_002` risk `0.8159`, uncertainty `0.8294`
2. `study_prompt_framing_001` risk `0.2976`, uncertainty `0.6966`

## Integrity Checks
- `study_prompt_framing_001`: `240` trial rows, `240` unique agent IDs.
- `study_social_norms_002`: `180` trial rows, `180` unique agent IDs.
- Sensitivity scenarios are included in `analysis.json`.
- Stratified synthetic population profiles are included in `agents.json`.
- Methodology extraction and matched-method flags are included in `analysis.json`.
- Credit status is DigitalOcean-only.

## Interpretation
The pilot demonstrates a reproducible triage workflow. It does not validate the original studies with humans. The strongest result is that the system can surface a high-risk candidate (`study_social_norms_002`) for follow-up replication while keeping cost and artifacts transparent.
