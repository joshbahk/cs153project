# Experiment Protocol

## Scope
- Text-first studies representable as condition prompts and continuous response outcomes.
- The system prioritizes likely replication risks; it does not verify scientific truth.
- PDF uploads are supported when text is extractable. Scanned PDFs should be OCRed and pasted.

## Run Rules
- Every run records a run ID, seed, source hash, budget ledger, extraction metadata, and JSON artifacts.
- Local persistence uses SQLite at `artifacts/local.db`; production persistence uses DigitalOcean PostgreSQL.
- The maximum extracted text length is 120,000 characters.
- The maximum transparent simulated sample size is 500 participants per study.
- The optional LLM-agent sample size defaults to 40 participants per study to protect the DigitalOcean credit budget.
- Each participant ID appears at most once in the primary trial output for a study.

## Statistical Workflow
- Extract a `StudySpec` from paper text with methodology fields, confidence, fallback notes, and exact-replication warnings.
- Generate fixed, seed-reproducible stratified agent profiles from the extracted participant population.
- In transparent mode, run the primary simulation with a treatment shift of `3.0`.
- In transparent mode, run sensitivity scenarios with treatment shifts of `1.0` and `0.0`.
- In LLM mode, send the fixed demographic profiles, extracted condition, procedure, outcome, and hypothesis to DigitalOcean serverless inference one participant at a time.
- Store LLM prompts, responses, parsed scores, model name, and token usage in `llm_responses.json`.
- Compute a matched-method approximation when the extracted original test is supported.
- Always compute standardized mean-difference triage outputs for cross-paper comparison.
- Store full agent profiles in `agents.json`.
- Rank studies by p-value, effect magnitude, and uncertainty.

## Budget Workflow
- Global cap: `$250`.
- Per-study and per-stage caps are enforced before charges are recorded.
- DigitalOcean App Platform, dev PostgreSQL, CPU resources, and DigitalOcean serverless inference are allowed.
- Cloudflare, GPU resources, and paid third-party inference are blocked.

## Limitations
- Synthetic participants are not people.
- The default response model is transparent but simplified.
- LLM-agent mode is more realistic but can reflect training-data leakage, prompt sensitivity, and provider/model drift.
- Results should guide follow-up human replication, not replace it.
