# Replication Triage Dashboard

Simulation-based triage for text-first behavioral studies. The app helps prioritize which papers may deserve expensive human replication by extracting a study protocol, simulating synthetic participants, running statistical checks, and ranking replication risk. It does **not** claim that AI or synthetic agents are substitutes for human subjects.

## What This Builds
- FastAPI dashboard for uploading PDFs or pasting paper text.
- Batch importer for up to 50 open-access papers via OpenAlex search or pasted URL/DOI lists.
- SQLite locally, PostgreSQL on DigitalOcean App Platform.
- DB-backed run queue with a worker process.
- Deterministic synthetic-participant simulator with unique agents per run.
- Bootstrap confidence intervals, permutation p-values, risk ranking, and sensitivity scenarios.
- JSON artifacts for every run: extraction, analysis, ranking, trials, and budget ledger.

## Quick Start
```bash
python -m pip install -e .
PYTHONPATH=src uvicorn web.app:app --reload
```

Open `http://127.0.0.1:8000`, upload a PDF or paste text, and create a run. Local development defaults to `AUTO_PROCESS_ON_SUBMIT=1`, so runs process without a separate worker. To test worker mode:

```bash
AUTO_PROCESS_ON_SUBMIT=0 PYTHONPATH=src uvicorn web.app:app --reload
PYTHONPATH=src python -m worker.main --once
```

## CLI Pilot
```bash
PYTHONPATH=src python -m cli.main \
  --credits configs/credits.demo.json \
  --output-dir artifacts/pilot_run_001 \
  --run-id pilot_run_001
```

Current pilot results:
- `study_social_norms_002`: risk `0.8159`, primary p-value `0.2729`, flagged as high replication risk in simulation.
- `study_prompt_framing_001`: risk `0.2976`, primary p-value `0.0017`, replicated in simulation.
- Pilot estimated spend: `$3.40` under a `$250` cap.
- Trial rows use unique agent IDs within each study.

## DigitalOcean Deploy
This project is designed for DigitalOcean App Platform because the available credits are DigitalOcean-only.

1. Push the repo to GitHub.
2. In DigitalOcean, create an App Platform app from the repo.
3. Use `.do/app.yaml` as the app spec.
4. Bind the included dev PostgreSQL database.
5. Keep `MAX_TOTAL_USD=250`, `MAX_SAMPLE_SIZE=500`, `MAX_UPLOAD_MB=15`, `MAX_QUEUED_JOBS=50`, and `MAX_BATCH_IMPORT=50`.
6. Before production batches, copy `configs/credits.example.json` to `configs/credits.local.json` and set `validated=true` only after checking the DigitalOcean billing dashboard.

Cloudflare, GPU inference, and paid third-party inference are intentionally disabled.

## Batch Import
The dashboard can queue large batches with very little manual work:

1. Open the home page.
2. In **Batch Import**, leave the default OpenAlex query or enter a query such as `social norm field experiment`.
3. Set **Max papers** to `50`.
4. Click **Queue Batch**.

The importer uses OpenAlex open-access metadata, tries to extract full text from OA links/PDFs, and falls back to title/abstract metadata when full text is blocked. You can also paste one DOI, article URL, or PDF URL per line.

## Tests
```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
```

## AI Use And Integrity
AI tools were used to help design, implement, test, and document the software. The simulation itself is transparent and deterministic: it uses synthetic demographic priors plus a response model, not hidden LLM calls. Results are triage evidence only. A real replication decision still requires human participants, domain expertise, and careful study design.
