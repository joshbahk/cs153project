# Experiment Protocol

## Scope
- Text-first studies representable as condition prompts and continuous response outcomes.
- The system prioritizes likely replication risks; it does not verify scientific truth.
- PDF uploads are supported when text is extractable. Scanned PDFs should be OCRed and pasted.

## Run Rules
- Every run records a run ID, seed, source hash, budget ledger, extraction metadata, and JSON artifacts.
- Local persistence uses SQLite at `artifacts/local.db`; production persistence uses DigitalOcean PostgreSQL.
- The maximum extracted text length is 120,000 characters.
- The maximum simulated sample size is 500 participants per study.
- Each participant ID appears at most once in the primary trial output for a study.

## Statistical Workflow
- Extract a `StudySpec` from paper text with methodology fields, confidence, fallback notes, and exact-replication warnings.
- Generate fixed, seed-reproducible stratified agent profiles from the extracted participant population.
- Run the primary simulation with a treatment shift of `3.0`.
- Run sensitivity scenarios with treatment shifts of `1.0` and `0.0`.
- Compute a matched-method approximation when the extracted original test is supported.
- Always compute standardized mean-difference triage outputs for cross-paper comparison.
- Store full agent profiles in `agents.json`.
- Rank studies by p-value, effect magnitude, and uncertainty.

## Budget Workflow
- Global cap: `$250`.
- Per-study and per-stage caps are enforced before charges are recorded.
- DigitalOcean App Platform, dev PostgreSQL, and CPU resources are allowed.
- Cloudflare, GPU resources, and paid third-party inference are blocked.

## Limitations
- Synthetic participants are not people.
- The response model is transparent but simplified.
- Training-data leakage is avoided by not using external LLM agents in v1, but the simulator is also less realistic.
- Results should guide follow-up human replication, not replace it.
