# Replicate

Simulation-based triage for text-first behavioral studies. The app helps prioritize which papers may deserve expensive human replication by extracting a study protocol, simulating synthetic participants, running statistical checks, and ranking replication risk. It does **not** claim that AI or synthetic agents are substitutes for human subjects.

## What This Builds
- FastAPI dashboard for uploading PDFs or pasting paper text.
- Batch importer for up to 50 open-access papers via OpenAlex search or pasted URL/DOI lists.
- Full-text batch resolver with suitability screening before runs are created.
- Projected budget reservations before queueing so batches cannot silently exceed the DigitalOcean credit cap.
- Live run monitor with stage, progress percentage, elapsed time, ETA, and one-click skip for queued/running papers.
- SQLite locally, PostgreSQL on DigitalOcean App Platform.
- DB-backed run queue with a worker process.
- Deterministic synthetic-participant simulator with unique agents per run.
- Optional DigitalOcean serverless LLM-agent simulator for real model-based participant responses.
- Stratified synthetic demographic profiles stored in `agents.json`.
- Methodology extraction with feasibility flags, original-test detection, warnings, and exact-replication caveats.
- Matched-method analysis when the original statistical test is supported, plus standardized triage analysis otherwise.
- Bootstrap confidence intervals, permutation p-values, risk ranking, and sensitivity scenarios.
- JSON artifacts for every run: extraction, analysis, ranking, trials, agents, optional LLM responses, and budget ledger.

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
6. For higher-yield bulk import, keep `BULK_IMPORT_RESOLVE=1`, `BULK_IMPORT_MULTISOURCE=1`, and `BULK_IMPORT_OVERFETCH_FACTOR=4`.
7. Add `OPENALEX_MAILTO` and `UNPAYWALL_EMAIL` as app-level environment variables using your email address. Optionally add `SEMANTIC_SCHOLAR_API_KEY`.
8. Before production batches, copy `configs/credits.example.json` to `configs/credits.local.json` and set `validated=true` only after checking the DigitalOcean billing dashboard.

Cloudflare, GPU inference, and paid third-party inference are intentionally disabled.

### Optional DigitalOcean LLM Agents
The default backend is the transparent deterministic simulator because it is fast and cheap. To use real LLM participant agents through DigitalOcean serverless inference, add these app-level environment variables in DigitalOcean and redeploy:

```text
LLM_SIMULATION_ENABLED=1
GRADIENT_MODEL_ACCESS_KEY=<encrypted DigitalOcean model access key>
LLM_MODEL=llama3.3-70b-instruct
LLM_SAMPLE_SIZE=40
LLM_PRIMARY_ONLY=1
```

`GRADIENT_MODEL_ACCESS_KEY` must be stored as an encrypted/secret variable. The app calls `https://inference.do-ai.run/v1/chat/completions`, stores raw prompts/responses in `llm_responses.json`, and still labels results as simulation-based triage. Keep `LLM_SAMPLE_SIZE` at `20` or `40` for the first 5-paper test, then scale after checking runtime and spend.

## Batch Import
The dashboard can queue large batches with very little manual work:

1. Open the home page.
2. In **Batch Import**, leave the default OpenAlex query or enter a query such as `social norm field experiment`.
3. Set **Max papers** to `50`.
4. Click **Queue Batch**.

The web request over-fetches candidate papers, resolves full text from OpenAlex, Europe PMC, Unpaywall, arXiv, OA PDFs, and landing pages, then creates runs only for papers whose text contains enough methodology and condition detail to simulate responsibly. The dashboard reports discovered, resolved, suitable, and skipped counts after the batch request. You can also paste one DOI, article URL, or PDF URL per line.

## Monitoring And Skipping
The dashboard and run detail pages auto-refresh while work is queued or running. Each paper shows its current stage, progress bar, elapsed time, and ETA. ETA is estimated from the current run’s progress and, when available, recent completed-run durations.

Use **Skip** on the dashboard or **Skip Paper** on the run detail page to stop work. Queued papers are skipped immediately. Running papers stop at the next worker checkpoint, including hydration, extraction, methodology checks, and each simulation batch.

## Scientific Rigor Notes
- The extractor now records participant population, recruitment context, randomization unit, condition stimuli, outcome scale, original statistical test, reported p-value/effect when detected, and extraction warnings.
- Runs refuse to simulate when the extractor cannot identify actual study arms and would otherwise fall back to generic control/treatment prompts.
- Synthetic agents are fixed for each seed and stratified against transparent demographic priors. Agent profiles include age, gender, education, income bucket, political orientation, region, baseline compliance, prosociality, risk preference, and latent trait.
- In LLM mode, those same fixed demographic profiles are passed into DigitalOcean-hosted chat completions one participant at a time. The worker records every prompt, model response, parsed score, and token usage in the run artifacts.
- Sensitivity scenarios reuse the same generated agents, assignment stream, and noise stream so scenario differences reflect the configured treatment shift rather than a resampled population.
- If the original statistical method is recognized (`mean_difference`, `t_test`, `linear_regression`, or `anova`), the analysis is marked as a matched approximation. Otherwise the app explicitly reports that it used standardized mean-difference triage instead.
- The app still does not guarantee exact human replication. It reports when a paper requires review before its methodology can be simulated responsibly.

## Tests
```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
```

## AI Use And Integrity
AI tools were used to help design, implement, test, and document the software. The default simulation is transparent and deterministic. Optional LLM-agent mode uses DigitalOcean serverless inference with stored prompts/responses for auditability. Results are triage evidence only. A real replication decision still requires human participants, domain expertise, and careful study design.
