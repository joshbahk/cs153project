# Replicate

Replicate is a hosted replication-triage dashboard for text-first psychology and social-science papers. It helps answer: **which papers are worth spending real human-participant replication money on first?**

The system ingests a paper, extracts an executable study protocol, generates fixed synthetic participant profiles, runs a transparent simulation, computes uncertainty/risk, stores every artifact, and lets the user selectively rerun important papers with real DigitalOcean-hosted LLM agents. It is intentionally framed as **simulation-based triage**, not proof that a human study replicated.

## Rubric Summary

### Problem And Insight

Replication is expensive and slow, especially in behavioral science. This project targets the bottleneck before a full replication: deciding which old papers deserve scarce human-participant follow-up. Instead of claiming AI can replace people, Replicate uses AI and simulation to cheaply surface papers that look fragile, underspecified, or worth closer review.

The core insight is to separate the workflow into two cost tiers:

1. **Fast transparent triage:** run many papers cheaply with deterministic synthetic participants.
2. **Selective LLM study:** click one button on a specific paper to run a smaller, auditable LLM-agent study only when it is worth the extra cost.

This matches the available constraint: **DigitalOcean credits only; no Cloudflare credits.**

### Execution And Technical Work

Replicate includes:

- FastAPI server-rendered dashboard.
- PDF upload and pasted-text ingestion.
- Batch import for up to 50 papers using OpenAlex search and pasted DOI/URL/PDF URL lists.
- Full-text resolution from OpenAlex metadata, Europe PMC, Unpaywall, arXiv, OA PDFs, and HTML landing pages.
- SQLite for local development and PostgreSQL for DigitalOcean App Platform.
- DB-backed queue with a separate worker process.
- Live run monitor with current stage, progress, elapsed time, ETA, and skip controls.
- Soft-delete from the website for runs the user no longer wants to see.
- Transparent synthetic participant simulator with fixed demographic profiles.
- Per-paper **Queue LLM Study** button for DigitalOcean serverless LLM-agent runs.
- Stored JSON artifacts for extraction, analysis, ranking, trials, agents, LLM responses, progress, and budget.
- Methodology extraction with warnings, feasibility labels, original-statistical-test detection, and matched-method notes.
- Bootstrap confidence intervals, permutation p-values, sensitivity scenarios, and replication-risk ranking.

## How The Product Works

```text
PDF / pasted text / batch query
        |
        v
paper text extraction and normalization
        |
        v
protocol extraction
  - hypothesis
  - participant population
  - control/treatment arms when detected
  - outcome scale
  - original statistical test when detected
  - warnings when methodology is inferred
        |
        v
transparent simulation by default
  - fixed synthetic demographic agents
  - randomized assignment
  - response scores
  - sensitivity scenarios
        |
        v
analysis and ranking
  - effect size
  - confidence interval
  - permutation p-value
  - risk score
  - artifacts stored in DB
        |
        v
optional: click "Queue LLM Study" on one paper
  - same paper, new run
  - real DigitalOcean LLM participant calls
  - LLM abstract, methodology, results, and response audit
```

Normal uploads and batch runs are **always transparent first**. LLM calls happen only when the user clicks **Queue LLM Study** on a specific run detail page.

## Quick Start

```bash
python -m pip install -e .
PYTHONPATH=src uvicorn web.app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

Local development defaults to `AUTO_PROCESS_ON_SUBMIT=1`, so a submitted paper processes immediately.

To test production-style worker mode:

```bash
AUTO_PROCESS_ON_SUBMIT=0 PYTHONPATH=src uvicorn web.app:app --reload
PYTHONPATH=src python -m worker.main --once
```

## Recommended Demo

Use Tversky and Kahneman's Asian Disease framing experiment:

```text
Tversky Kahneman 1981 framing decisions psychology choice Asian disease
```

Why it is ideal:

- Famous psychology result.
- Fully text-based.
- Clean randomized conditions: gain frame vs loss frame.
- Simple outcome: sure option vs risky option.
- Easy to explain in a short demo.
- Maps naturally to both transparent synthetic agents and LLM participant agents.

Demo flow:

1. Queue the paper or paste the experiment text.
2. Let the transparent run finish.
3. Open the run detail page and show effect, p-value, uncertainty, methodology warnings, and artifacts.
4. Click **Queue LLM Study**.
5. Show the LLM study summary with abstract, methodology, results, response signal, and audit trail.

## Batch Import

The dashboard can queue many papers with little manual work.

1. Open the home page.
2. In **Batch Import**, enter a query such as:

```text
social norm field experiment
```

3. Set **Max papers** to `50`.
4. Click **Queue Batch**.

The importer over-fetches candidates, tries to resolve full text, and now queues papers leniently. If methodology is incomplete, the run is still created with warnings rather than silently dropped. This is better for a course demo because the system produces visible outputs while still disclosing uncertainty.

Useful production import env vars:

```text
BULK_IMPORT_RESOLVE=1
BULK_IMPORT_MULTISOURCE=1
BULK_IMPORT_OVERFETCH_FACTOR=4
BULK_IMPORT_MIN_FULLTEXT_CHARS=600
BULK_IMPORT_MAX_BYTES=31457280
MAX_TEXT_CHARS=300000
OPENALEX_MAILTO=<your email>
UNPAYWALL_EMAIL=<your email>
SEMANTIC_SCHOLAR_API_KEY=<optional>
```

## LLM Studies

The default run path is transparent and cheap. To enable the **button-based** LLM path, add this secret environment variable in DigitalOcean:

```text
GRADIENT_MODEL_ACCESS_KEY=<encrypted DigitalOcean model access key>
```

Recommended settings:

```text
LLM_MODEL=llama3.3-70b-instruct
LLM_SAMPLE_SIZE=40
LLM_PRIMARY_ONLY=1
LLM_MAX_TOKENS=180
LLM_TIMEOUT_SECONDS=60
LLM_SIMULATION_ENABLED=0
```

`LLM_SIMULATION_ENABLED` should stay `0`; normal runs and batch runs should stay transparent. The per-paper button stores `simulation_backend="llm"` on that new run, and the worker uses the LLM backend only for that run.

LLM artifacts include:

- fixed agent profile;
- assigned condition;
- prompt sent to the model;
- raw model response;
- parsed score, label, and reason;
- token usage;
- model name.

## DigitalOcean Deployment

This project is designed for DigitalOcean App Platform because DigitalOcean credits were the only confirmed outside resource.

1. Push the repo to GitHub.
2. In DigitalOcean, create an App Platform app from the repo.
3. Use `.do/app.yaml` as the app spec if DigitalOcean offers that option.
4. Add the included dev PostgreSQL database.
5. Confirm the app has one web service and one worker service.
6. Confirm both services have the same runtime env vars.
7. Store `GRADIENT_MODEL_ACCESS_KEY` as an encrypted secret if using LLM studies.
8. Deploy.

Important env vars:

```text
DATABASE_URL=${replication-db.DATABASE_URL}
AUTO_PROCESS_ON_SUBMIT=0
MAX_TOTAL_USD=250
MAX_SAMPLE_SIZE=500
MAX_UPLOAD_MB=15
MAX_TEXT_CHARS=300000
MAX_QUEUED_JOBS=50
MAX_BATCH_IMPORT=50
CACHE_DIR=/tmp/replication-cache
BULK_IMPORT_RESOLVE=1
BULK_IMPORT_MULTISOURCE=1
BULK_IMPORT_OVERFETCH_FACTOR=4
BULK_IMPORT_MIN_FULLTEXT_CHARS=600
BULK_IMPORT_MAX_BYTES=31457280
LLM_SIMULATION_ENABLED=0
LLM_SAMPLE_SIZE=40
LLM_PRIMARY_ONLY=1
```

Cloudflare, GPU resources, and paid third-party inference are not required.

## Evaluation And Evidence

Validation work included:

- Unit tests for paper ingestion, scanned-PDF rejection, PDF/text caps, DB persistence, FastAPI routes, queue transitions, skip/delete behavior, bulk import, deterministic simulation behavior, and LLM summary rendering.
- Manual local browser testing of the dashboard workflow.
- Budget reservation checks before queueing runs.
- Run progress monitoring and skip checkpoints so a stalled paper can be stopped.
- JSON artifacts for auditability and reproducibility.
- Pilot CLI run over sample studies in `data/studies`.

Run tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
```

Most recent verification during development:

```text
34 tests passed
```

Sample pilot command:

```bash
PYTHONPATH=src python -m cli.main \
  --credits configs/credits.demo.json \
  --output-dir artifacts/pilot_run_001 \
  --run-id pilot_run_001
```

## What The Results Mean

The app reports:

- `effect_size`: simulated treatment mean minus control mean.
- `ci_low` / `ci_high`: bootstrap confidence interval.
- `p_value`: permutation-test p-value over simulated outcomes.
- `replicated`: whether the simulated effect is statistically detectable under the app's threshold.
- `replication_risk`: ranking score combining uncertainty, p-value, effect magnitude, and extraction confidence.

These are **not human-subject findings**. They are triage signals for prioritizing real replication work.

## Limitations And Failure Modes

- Paper extraction is heuristic. If a PDF is scanned or badly formatted, paste OCR text.
- Methodology extraction is not exact. The app records warnings when arms, outcomes, or statistical tests are inferred.
- Lenient batch mode intentionally queues imperfect papers for demo throughput.
- Transparent synthetic agents are not people.
- LLM agents are also not people and may reflect training-data leakage, prompt sensitivity, and model/provider drift.
- A famous study may appear in model training data, which can bias LLM-agent responses.
- The statistical analysis is a matched approximation when possible, not a guaranteed reproduction of the original analysis.
- Visual, physical, interactive, deception-heavy, or longitudinal experiments are poorly suited to this v1.
- Small LLM samples such as `20` or `40` are useful for demos and qualitative signals, not rigorous claims.

## Repository Map

```text
src/web/          FastAPI app, templates, dashboard routes
src/worker/       queued-run worker
src/db/           SQLAlchemy models and repository helpers
src/ingest/       PDF/text ingestion, batch discovery, full-text resolution
src/extraction/   protocol extraction heuristics
src/simulation/   synthetic and LLM participant simulation
src/stats/        effect sizes, confidence intervals, p-values
src/ranking/      replication-risk ranking
configs/          budget and credit configuration examples
data/studies/     sample local studies for CLI pilot runs
docs/             deployment, credit, and protocol notes
tests/            unittest suite
.do/app.yaml      DigitalOcean App Platform spec
```

## AI Use, Sources, And Integrity

AI tools were used to help design, implement, debug, test, and document this project. The system itself also optionally uses DigitalOcean-hosted LLM calls as simulated participant agents, with prompts and responses stored for auditability.

External services and libraries used or supported:

- DigitalOcean App Platform and PostgreSQL for hosting.
- DigitalOcean serverless inference for optional LLM studies.
- OpenAlex, Unpaywall, Europe PMC, Semantic Scholar, and arXiv for open scholarly discovery/full-text resolution.
- FastAPI, SQLAlchemy, pypdf, httpx, Jinja2, and standard Python scientific/statistical code.

No existing project was forked as a base application. The implementation is original course-project code built around open-source Python dependencies and public scholarly APIs.

