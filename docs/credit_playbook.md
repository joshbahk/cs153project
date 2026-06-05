# DigitalOcean Credit Playbook

This project assumes DigitalOcean is the only outside resource provider.

## Pre-Run Checklist
- Confirm the DigitalOcean credit balance, expiration date, and monthly billing ceiling.
- Confirm App Platform, dev PostgreSQL, CPU Droplets, and Spaces eligibility.
- Confirm GPU Droplets, Paperspace GPU resources, and third-party inference are not used.
- Copy `configs/credits.example.json` to `configs/credits.local.json`.
- Set `validated=true` only after checking the billing dashboard.

## Runtime Guardrails
- Keep `MAX_TOTAL_USD=250`.
- Keep `MAX_SAMPLE_SIZE=500` unless the budget file is deliberately updated.
- Keep `MAX_QUEUED_JOBS=50` and `MAX_BATCH_IMPORT=50` for the course-scale batch workflow.
- Use the checked-in `.do/app.yaml` for one web service, one worker, and one dev PostgreSQL database.

## Governance
- Re-check billing before any non-demo batch.
- Stop the worker if recorded spend approaches the cap.
- Store run artifacts in PostgreSQL, not the App Platform local filesystem.
