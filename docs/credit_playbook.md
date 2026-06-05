# DigitalOcean Credit Playbook

This project assumes DigitalOcean is the only outside resource provider.

## Pre-Run Checklist
- Confirm the DigitalOcean credit balance, expiration date, and monthly billing ceiling.
- Confirm App Platform, dev PostgreSQL, CPU Droplets, Spaces, and DigitalOcean serverless inference eligibility.
- Confirm GPU Droplets, Paperspace GPU resources, and third-party inference are not used.
- Copy `configs/credits.example.json` to `configs/credits.local.json`.
- Set `validated=true` only after checking the billing dashboard.

## Runtime Guardrails
- Keep `MAX_TOTAL_USD=250`.
- Keep `MAX_SAMPLE_SIZE=500` unless the budget file is deliberately updated.
- Keep `LLM_SAMPLE_SIZE=20` or `40` for the first LLM batch.
- Keep `MAX_QUEUED_JOBS=50` and `MAX_BATCH_IMPORT=50` for the course-scale batch workflow.
- Keep `BULK_IMPORT_RESOLVE=1`, `BULK_IMPORT_MULTISOURCE=1`, and `BULK_IMPORT_OVERFETCH_FACTOR=4` for production batch discovery.
- Use the checked-in `.do/app.yaml` for one web service, one worker, and one dev PostgreSQL database.
- Store `GRADIENT_MODEL_ACCESS_KEY` as an encrypted app-level environment variable before enabling `LLM_SIMULATION_ENABLED=1`.
- Set `OPENALEX_MAILTO` and `UNPAYWALL_EMAIL` to your email address to improve API rate-limit behavior.

## Governance
- Re-check billing before any non-demo batch.
- Stop the worker if recorded spend approaches the cap.
- Store run artifacts in PostgreSQL, not the App Platform local filesystem.
