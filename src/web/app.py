"""FastAPI dashboard for simulation-based replication triage."""

from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.repository import (
    average_completed_duration_seconds,
    count_active_runs,
    create_paper_run,
    create_run_for_existing_paper,
    get_run,
    hide_run_from_view,
    list_runs,
    request_run_skip,
    summarize_run,
    total_recorded_spend,
    total_projected_spend,
)
from db.session import create_app_engine, create_session_factory, init_database
from ingest.batch_importer import (
    BatchImportError,
    discover_openalex,
    resolve_candidates_to_paper_inputs,
    source_candidates_from_lines,
)
from ingest.config import get_ingest_config
from ingest.paper_ingest import PaperIngestError, build_paper_input
from pipeline.service import PipelineConfig, estimate_run_cost
from web.settings import AppSettings, get_settings
from worker.jobs import process_run_batch_by_ids, process_run_by_id


templates = Jinja2Templates(directory="src/web/templates")


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


templates.env.filters["duration"] = _format_duration


def _primary_analysis(run) -> dict[str, Any]:
    if not run.analysis_json:
        return {}
    first = next(iter(run.analysis_json.values()), {})
    return first.get("primary", first)


def _sensitivity(run) -> dict[str, Any]:
    if not run.analysis_json:
        return {}
    first = next(iter(run.analysis_json.values()), {})
    return first.get("sensitivity", {})


def _trial_summary(run) -> dict[str, Any]:
    trials = run.trials_json or {}
    first_rows = next(iter(trials.values()), [])
    arms: dict[str, int] = {}
    unique_agents: set[str] = set()
    for row in first_rows:
        arms[row.get("arm", "unknown")] = arms.get(row.get("arm", "unknown"), 0) + 1
        unique_agents.add(row.get("agent_id", ""))
    return {
        "count": len(first_rows),
        "unique_agents": len(unique_agents),
        "arms": arms,
    }


def _agent_summary(run) -> dict[str, Any]:
    agents = run.agents_json or {}
    first_agents = next(iter(agents.values()), [])
    fields = ["age_bucket", "gender", "education", "income_bucket", "political_orientation", "region"]
    summary: dict[str, dict[str, int]] = {field: {} for field in fields}
    for agent in first_agents:
        for field in fields:
            value = str(agent.get(field, "unknown"))
            summary[field][value] = summary[field].get(value, 0) + 1
    return {"count": len(first_agents), "fields": summary}


def _artifact_payload(run) -> dict[str, Any]:
    return {
        "extraction": run.extraction_json or {},
        "analysis": run.analysis_json or {},
        "ranking": run.ranking_json or {},
        "trials": run.trials_json or {},
        "agents": run.agents_json or {},
        "llm_responses": run.llm_json or {},
        "budget": run.budget_json or {},
        "progress": run.progress_json or {},
    }


def _first_study_block(run, field_name: str) -> tuple[str, Any]:
    payload = getattr(run, field_name) or {}
    if not payload:
        return "", {}
    study_id, value = next(iter(payload.items()))
    return str(study_id), value


def _mean_score(rows: list[dict[str, Any]], arm: str) -> float | None:
    scores = [float(row.get("response_score", 0.0)) for row in rows if row.get("arm") == arm]
    if not scores:
        return None
    return sum(scores) / len(scores)


def _format_float(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "pending"


def _llm_study_summary(run) -> dict[str, Any]:
    primary = _primary_analysis(run)
    if run.status != "succeeded" or primary.get("simulation_backend") != "llm":
        return {"available": False}

    study_id, study_analysis = _first_study_block(run, "analysis_json")
    methodology = (study_analysis or {}).get("methodology", {})
    response_rows = (run.llm_json or {}).get(study_id, [])
    if not response_rows:
        response_rows = next(iter((run.llm_json or {}).values()), [])
    treatment_mean = _mean_score(response_rows, "treatment")
    control_mean = _mean_score(response_rows, "control")
    labels = Counter(
        str(row.get("response_label", "")).strip()
        for row in response_rows
        if str(row.get("response_label", "")).strip()
    )
    reasons: list[str] = []
    seen_reasons: set[str] = set()
    for row in response_rows:
        reason = str(row.get("brief_reason", "")).strip()
        if reason and reason not in seen_reasons:
            reasons.append(reason)
            seen_reasons.add(reason)
        if len(reasons) >= 3:
            break

    effect = float(primary.get("effect_size", 0.0) or 0.0)
    direction = "higher" if effect > 0 else "lower" if effect < 0 else "not meaningfully different"
    risk_items = (run.ranking_json or {}).get("items", [])
    risk = risk_items[0].get("replication_risk") if risk_items else None
    usage = primary.get("llm_usage", {})
    sample_n = int(primary.get("n_control", 0) or 0) + int(primary.get("n_treatment", 0) or 0)

    abstract = (
        f"This completed LLM-agent triage run evaluated '{run.paper.title}' by converting the paper text into "
        f"a two-arm executable protocol and simulating {sample_n} fixed demographic participant profiles with "
        f"{primary.get('llm_model', 'the configured DigitalOcean model')}. The extracted hypothesis was: "
        f"{study_analysis.get('primary', {}).get('hypothesis', '') or 'see extracted text below'}"
    )
    if methodology.get("participant_population"):
        abstract = (
            f"This completed LLM-agent triage run evaluated '{run.paper.title}' by converting the paper text into "
            f"a two-arm executable protocol for {methodology.get('participant_population')} and simulating "
            f"{sample_n} fixed demographic participant profiles with "
            f"{primary.get('llm_model', 'the configured DigitalOcean model')}."
        )

    return {
        "available": True,
        "abstract": abstract,
        "methodology_points": [
            f"Participants: {methodology.get('participant_population', 'not extracted')}.",
            f"Procedure: {methodology.get('procedure_summary', 'not extracted') or 'not extracted'}.",
            f"Assignment: {methodology.get('assignment_procedure', 'random assignment to extracted study arms')}.",
            f"Outcome: {methodology.get('outcome_scale', 'continuous')} score for {run.paper.title}.",
        ],
        "result_points": [
            (
                f"Treatment responses were {direction} than control by "
                f"{_format_float(primary.get('effect_size'))} points "
                f"(95% CI {_format_float(primary.get('ci_low'))} to {_format_float(primary.get('ci_high'))})."
            ),
            (
                f"Permutation p-value was {_format_float(primary.get('p_value'), 4)}; "
                f"replicated in simulation: {primary.get('replicated', False)}."
            ),
            f"Replication-risk score: {_format_float(risk)}.",
        ],
        "response_points": [
            f"Control mean response score: {_format_float(control_mean)}." if control_mean is not None else "",
            f"Treatment mean response score: {_format_float(treatment_mean)}." if treatment_mean is not None else "",
            (
                "Most common response labels: "
                + ", ".join(f"{label} ({count})" for label, count in labels.most_common(3))
                + "."
                if labels
                else ""
            ),
        ],
        "reasons": reasons,
        "audit_points": [
            f"Stored LLM response records: {len(response_rows)}.",
            (
                f"Token usage: {usage.get('prompt_tokens', 0)} prompt, "
                f"{usage.get('completion_tokens', 0)} completion, {usage.get('total_tokens', 0)} total."
            ),
            "Raw prompts, model outputs, parsed scores, and agent profiles are available in the JSON artifacts.",
        ],
        "caveat": (
            "This is not a human replication. It is an LLM-agent triage signal meant to identify papers "
            "worth prioritizing for real participant follow-up."
        ),
    }


def _app_state(request: Request) -> tuple[AppSettings, Any]:
    return request.app.state.settings, request.app.state.session_factory


async def _read_upload_with_cap(upload: UploadFile | None, max_upload_mb: int) -> tuple[bytes | None, str]:
    if upload is None or not upload.filename:
        return None, ""
    max_bytes = max_upload_mb * 1024 * 1024
    reported_size = getattr(upload, "size", None)
    if reported_size is not None and reported_size > max_bytes:
        raise PaperIngestError(f"PDF is larger than the {max_upload_mb} MB upload limit.")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PaperIngestError(f"PDF is larger than the {max_upload_mb} MB upload limit.")
        chunks.append(chunk)
    return b"".join(chunks), upload.filename


def _llm_pipeline_config(settings: AppSettings) -> PipelineConfig:
    return PipelineConfig(
        seed=settings.seed,
        max_sample_size=min(settings.max_sample_size, settings.llm_sample_size),
        model_tier=f"digitalocean_serverless_llm_agents:{settings.llm_model}",
        simulation_backend="llm",
        llm_model=settings.llm_model,
        llm_sample_size=settings.llm_sample_size,
        llm_temperature=settings.llm_temperature,
        llm_max_tokens=settings.llm_max_tokens,
        llm_max_retries=settings.llm_max_retries,
        llm_primary_only=settings.llm_primary_only,
        llm_estimated_input_tokens_per_trial=settings.llm_estimated_input_tokens_per_trial,
        llm_estimated_output_tokens_per_trial=settings.llm_estimated_output_tokens_per_trial,
        llm_input_cost_per_1m_tokens=settings.llm_input_cost_per_1m_tokens,
        llm_output_cost_per_1m_tokens=settings.llm_output_cost_per_1m_tokens,
    )


def _run_reservation(settings: AppSettings, backend: str | None = None) -> float:
    selected_backend = backend or "transparent"
    if selected_backend == "llm":
        config = _llm_pipeline_config(settings)
        return estimate_run_cost(config.max_sample_size, config)
    return estimate_run_cost(
        settings.max_sample_size,
        PipelineConfig(seed=settings.seed, max_sample_size=settings.max_sample_size),
    )


def _run_backend(run) -> str:
    budget = run.budget_json or {}
    if budget.get("simulation_backend") in {"llm", "transparent"}:
        return str(budget["simulation_backend"])
    primary = _primary_analysis(run)
    if primary.get("simulation_backend") in {"llm", "transparent"}:
        return str(primary["simulation_backend"])
    return "transparent"


def _llm_error_message(code: str) -> str:
    if code == "missing_key":
        return "Add GRADIENT_MODEL_ACCESS_KEY in DigitalOcean environment variables before queueing an LLM study."
    if code == "queue_full":
        return "Queue limit reached. Skip or wait for a running paper before queueing the LLM study."
    if code == "budget":
        return "Budget cap reached. The LLM study was not queued."
    if code == "already_llm":
        return "This run is already an LLM study."
    return ""


def _budget_slots_remaining(session: Session, settings: AppSettings, reservation_usd: float) -> int:
    if reservation_usd <= 0:
        return settings.max_queued_jobs
    remaining = settings.max_total_usd - total_projected_spend(session)
    return max(0, int(remaining // reservation_usd))


def _batch_discovery_limit(limit: int) -> int:
    config = get_ingest_config()
    return max(limit, limit * max(1, config.overfetch_factor))


def _batch_message(
    *,
    created: int,
    requested: int = 0,
    discovered: int = 0,
    resolved: int = 0,
    suitable: int = 0,
) -> str:
    if created <= 0:
        return ""
    skipped = max(0, discovered - suitable)
    if discovered:
        return (
            f"Queued {created} suitable papers. "
            f"Requested {requested}; discovered {discovered}; resolved {resolved} full texts; "
            f"suitable {suitable}; skipped {skipped}."
        )
    return f"Queued {created} batch runs."


def _render_dashboard(
    request: Request,
    session: Session,
    message: str = "",
    error: str = "",
    query: str = "",
) -> HTMLResponse:
    settings, _ = _app_state(request)
    average_duration = average_completed_duration_seconds(session)
    runs = [summarize_run(run, average_completed_seconds=average_duration) for run in list_runs(session, query=query)]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "runs": runs,
            "message": message,
            "error": error,
            "query": query,
            "settings": settings,
            "active_runs": count_active_runs(session),
            "total_spend": total_recorded_spend(session),
            "projected_spend": total_projected_spend(session),
            "average_duration": average_duration,
        },
    )


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_app_engine(settings.database_url)
    init_database(engine)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Replicate", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        q: str = "",
        batch_created: int = 0,
        batch_requested: int = 0,
        batch_discovered: int = 0,
        batch_resolved: int = 0,
        batch_suitable: int = 0,
        deleted: int = 0,
    ) -> HTMLResponse:
        with session_factory() as session:
            message = _batch_message(
                created=batch_created,
                requested=batch_requested,
                discovered=batch_discovered,
                resolved=batch_resolved,
                suitable=batch_suitable,
            )
            if deleted:
                message = "Deleted run from view."
            return _render_dashboard(request, session, query=q, message=message)

    @app.post("/runs", response_model=None)
    async def create_run(
        request: Request,
        background_tasks: BackgroundTasks,
        title: str = Form(""),
        paper_text: str = Form(""),
        paper_pdf: UploadFile | None = File(None),
    ) -> RedirectResponse | HTMLResponse:
        reservation_usd = _run_reservation(settings)

        with session_factory() as session:
            if count_active_runs(session) >= settings.max_queued_jobs:
                return _render_dashboard(
                    request,
                    session,
                    error=f"Queue limit reached ({settings.max_queued_jobs} active runs).",
                )
            if total_projected_spend(session) + reservation_usd > settings.max_total_usd:
                return _render_dashboard(
                    request,
                    session,
                    error=(
                        f"Budget cap reached (${settings.max_total_usd:.2f}). "
                        f"Each queued run reserves up to ${reservation_usd:.2f}."
                    ),
                )

            try:
                upload_bytes, upload_filename = await _read_upload_with_cap(paper_pdf, settings.max_upload_mb)
            except PaperIngestError as exc:
                return _render_dashboard(request, session, error=str(exc))
            try:
                paper_input = build_paper_input(
                    title=title,
                    pasted_text=paper_text,
                    upload_bytes=upload_bytes,
                    upload_filename=upload_filename,
                    max_upload_mb=settings.max_upload_mb,
                    max_chars=settings.max_text_chars,
                )
            except PaperIngestError as exc:
                return _render_dashboard(request, session, error=str(exc))

            run = create_paper_run(
                session,
                paper_input,
                seed=settings.seed,
                budget_reservation_usd=reservation_usd,
            )
            run_id = run.id

        if settings.auto_process_on_submit:
            background_tasks.add_task(process_run_by_id, session_factory, run_id, settings)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.post("/batch", response_model=None)
    async def create_batch(
        request: Request,
        background_tasks: BackgroundTasks,
        batch_query: str = Form(""),
        batch_sources: str = Form(""),
        batch_limit: int = Form(50),
    ) -> RedirectResponse | HTMLResponse:
        requested_limit = min(max(int(batch_limit), 1), settings.max_batch_import)
        reservation_usd = _run_reservation(settings)
        with session_factory() as session:
            capacity = max(0, settings.max_queued_jobs - count_active_runs(session))
            budget_slots = _budget_slots_remaining(session, settings, reservation_usd)
            limit = min(requested_limit, capacity, budget_slots)
            if limit <= 0:
                return _render_dashboard(
                    request,
                    session,
                    error=(
                        f"Queue or budget limit reached. Each queued run reserves up to ${reservation_usd:.2f}; "
                        f"projected spend is ${total_projected_spend(session):.2f} of ${settings.max_total_usd:.2f}."
                    ),
                )

        try:
            candidates = []
            discovery_limit = _batch_discovery_limit(limit)
            if batch_query.strip():
                candidates.extend(discover_openalex(batch_query, limit=discovery_limit, mailto=settings.openalex_mailto))
            if batch_sources.strip():
                candidates.extend(source_candidates_from_lines(batch_sources))
            paper_inputs, import_report = resolve_candidates_to_paper_inputs(
                candidates,
                max_chars=settings.max_text_chars,
            )
        except BatchImportError as exc:
            with session_factory() as session:
                return _render_dashboard(request, session, error=str(exc))
        except Exception as exc:
            with session_factory() as session:
                return _render_dashboard(request, session, error=f"Batch import failed: {exc}")

        if not paper_inputs:
            with session_factory() as session:
                details = import_report.summary() if "import_report" in locals() else {}
                return _render_dashboard(
                    request,
                    session,
                    error=(
                        "No importable papers found after full-text and methodology screening. "
                        f"Report: {details}"
                    ),
                )

        created_run_ids: list[str] = []
        with session_factory() as session:
            active = count_active_runs(session)
            capacity = max(0, settings.max_queued_jobs - active)
            budget_slots = _budget_slots_remaining(session, settings, reservation_usd)
            allowed = min(limit, capacity, budget_slots, len(paper_inputs))
            if allowed <= 0:
                return _render_dashboard(
                    request,
                    session,
                    error=(
                        f"Queue or budget limit reached. Each queued run reserves up to ${reservation_usd:.2f}; "
                        f"projected spend is ${total_projected_spend(session):.2f} of ${settings.max_total_usd:.2f}."
                    ),
                )

            for paper_input in paper_inputs[:allowed]:
                run = create_paper_run(
                    session,
                    paper_input,
                    seed=settings.seed,
                    budget_reservation_usd=reservation_usd,
                )
                created_run_ids.append(run.id)

        if settings.auto_process_on_submit:
            background_tasks.add_task(process_run_batch_by_ids, session_factory, created_run_ids, settings)
        return RedirectResponse(
            url=(
                f"/?q=&batch_created={len(created_run_ids)}"
                f"&batch_requested={limit}"
                f"&batch_discovered={import_report.discovered}"
                f"&batch_resolved={import_report.resolved_full_text}"
                f"&batch_suitable={import_report.created}"
            ),
            status_code=303,
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(
        request: Request,
        run_id: str,
        llm_created: int = 0,
        llm_error: str = "",
    ) -> HTMLResponse:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
            ranking_items = (run.ranking_json or {}).get("items", [])
            summary = summarize_run(run, average_completed_seconds=average_completed_duration_seconds(session))
            run_backend = _run_backend(run)
            return templates.TemplateResponse(
                request,
                "run_detail.html",
                {
                    "request": request,
                    "run": run,
                    "summary": summary,
                    "primary": _primary_analysis(run),
                    "sensitivity": _sensitivity(run),
                    "ranking": ranking_items[0] if ranking_items else {},
                    "trial_summary": _trial_summary(run),
                    "agent_summary": _agent_summary(run),
                    "methodology": next(iter((run.analysis_json or {}).values()), {}).get("methodology", {}),
                    "llm_study_summary": _llm_study_summary(run),
                    "run_backend": run_backend,
                    "llm_ready": bool(settings.llm_api_key),
                    "llm_reservation_usd": _run_reservation(settings, backend="llm"),
                    "page_message": "Queued LLM study for this paper." if llm_created else "",
                    "page_error": _llm_error_message(llm_error),
                    "artifacts": _artifact_payload(run),
                    "auto_refresh": run.status in {"queued", "running"},
                },
            )

    @app.post("/runs/{run_id}/llm", response_model=None)
    def queue_llm_run(request: Request, background_tasks: BackgroundTasks, run_id: str) -> RedirectResponse | HTMLResponse:
        reservation_usd = _run_reservation(settings, backend="llm")
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
            if _run_backend(run) == "llm":
                return RedirectResponse(url=f"/runs/{run_id}?llm_error=already_llm", status_code=303)
            if not settings.llm_api_key:
                return RedirectResponse(url=f"/runs/{run_id}?llm_error=missing_key", status_code=303)
            if count_active_runs(session) >= settings.max_queued_jobs:
                return RedirectResponse(url=f"/runs/{run_id}?llm_error=queue_full", status_code=303)
            if total_projected_spend(session) + reservation_usd > settings.max_total_usd:
                return RedirectResponse(url=f"/runs/{run_id}?llm_error=budget", status_code=303)

            llm_run = create_run_for_existing_paper(
                session,
                run.paper,
                seed=settings.seed,
                budget_reservation_usd=reservation_usd,
                metadata={"simulation_backend": "llm", "llm_model": settings.llm_model},
            )
            llm_run_id = llm_run.id

        if settings.auto_process_on_submit:
            background_tasks.add_task(process_run_by_id, session_factory, llm_run_id, settings)
        return RedirectResponse(url=f"/runs/{llm_run_id}?llm_created=1", status_code=303)

    @app.post("/runs/{run_id}/skip", response_model=None)
    def skip_run(run_id: str) -> RedirectResponse:
        with session_factory() as session:
            run = request_run_skip(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/delete", response_model=None)
    def delete_run_from_view(request: Request, run_id: str) -> RedirectResponse | HTMLResponse:
        with session_factory() as session:
            try:
                run = hide_run_from_view(session, run_id)
            except ValueError as exc:
                return _render_dashboard(request, session, error=str(exc))
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
        return RedirectResponse(url="/?deleted=1", status_code=303)

    @app.get("/api/runs")
    def api_runs(q: str = "") -> dict[str, Any]:
        with session_factory() as session:
            average_duration = average_completed_duration_seconds(session)
            return {
                "items": [
                    asdict(summarize_run(run, average_completed_seconds=average_duration))
                    for run in list_runs(session, query=q)
                ]
            }

    @app.post("/api/batch")
    def api_batch(payload: dict[str, Any], background_tasks: BackgroundTasks) -> dict[str, Any]:
        query = str(payload.get("query", ""))
        sources = str(payload.get("sources", ""))
        requested_limit = min(max(int(payload.get("limit", 50)), 1), settings.max_batch_import)
        reservation_usd = _run_reservation(settings)
        with session_factory() as session:
            limit = min(
                requested_limit,
                max(0, settings.max_queued_jobs - count_active_runs(session)),
                _budget_slots_remaining(session, settings, reservation_usd),
            )
            if limit <= 0:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Queue or budget limit reached. Each queued run reserves up to ${reservation_usd:.2f}."
                    ),
                )
        try:
            candidates = []
            discovery_limit = _batch_discovery_limit(limit)
            if query.strip():
                candidates.extend(discover_openalex(query, limit=discovery_limit, mailto=settings.openalex_mailto))
            if sources.strip():
                candidates.extend(source_candidates_from_lines(sources))
            papers, import_report = resolve_candidates_to_paper_inputs(candidates, max_chars=settings.max_text_chars)
        except BatchImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        run_ids: list[str] = []
        with session_factory() as session:
            capacity = max(0, settings.max_queued_jobs - count_active_runs(session))
            budget_slots = _budget_slots_remaining(session, settings, reservation_usd)
            for paper in papers[: min(limit, capacity, budget_slots)]:
                run_ids.append(
                    create_paper_run(
                        session,
                        paper,
                        seed=settings.seed,
                        budget_reservation_usd=reservation_usd,
                    ).id
                )
        if settings.auto_process_on_submit:
            background_tasks.add_task(process_run_batch_by_ids, session_factory, run_ids, settings)
        return {"created": len(run_ids), "run_ids": run_ids, "report": import_report.summary()}

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str) -> dict[str, Any]:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
            return {
                "id": run.id,
                "status": run.status,
                "paper": {
                    "id": run.paper.id,
                    "title": run.paper.title,
                    "source_kind": run.paper.source_kind,
                    "source_uri": run.paper.source_uri,
                    "source_sha256": run.paper.source_sha256,
                },
                "primary": _primary_analysis(run),
                "sensitivity": _sensitivity(run),
                "ranking": (run.ranking_json or {}).get("items", []),
                "budget": run.budget_json or {},
                "progress": asdict(
                    summarize_run(run, average_completed_seconds=average_completed_duration_seconds(session))
                ),
                "error_message": run.error_message,
            }

    @app.post("/api/runs/{run_id}/skip")
    def api_skip_run(run_id: str) -> dict[str, Any]:
        with session_factory() as session:
            run = request_run_skip(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
            return {
                "id": run.id,
                "status": run.status,
                "progress": asdict(
                    summarize_run(run, average_completed_seconds=average_completed_duration_seconds(session))
                ),
            }

    @app.delete("/api/runs/{run_id}")
    def api_delete_run_from_view(run_id: str) -> dict[str, Any]:
        with session_factory() as session:
            try:
                run = hide_run_from_view(session, run_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            return {"id": run.id, "hidden": True}

    @app.post("/api/runs/{run_id}/llm")
    def api_queue_llm_run(run_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
        if not settings.llm_api_key:
            raise HTTPException(status_code=400, detail=_llm_error_message("missing_key"))
        reservation_usd = _run_reservation(settings, backend="llm")
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
            if _run_backend(run) == "llm":
                raise HTTPException(status_code=409, detail=_llm_error_message("already_llm"))
            if count_active_runs(session) >= settings.max_queued_jobs:
                raise HTTPException(status_code=429, detail=_llm_error_message("queue_full"))
            if total_projected_spend(session) + reservation_usd > settings.max_total_usd:
                raise HTTPException(status_code=402, detail=_llm_error_message("budget"))
            llm_run = create_run_for_existing_paper(
                session,
                run.paper,
                seed=settings.seed,
                budget_reservation_usd=reservation_usd,
                metadata={"simulation_backend": "llm", "llm_model": settings.llm_model},
            )
            llm_run_id = llm_run.id
        if settings.auto_process_on_submit:
            background_tasks.add_task(process_run_by_id, session_factory, llm_run_id, settings)
        return {"created": True, "run_id": llm_run_id, "reserved_usd": reservation_usd}

    @app.get("/api/runs/{run_id}/artifacts")
    def api_artifacts(run_id: str) -> dict[str, Any]:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
            return _artifact_payload(run)

    @app.get("/api/runs/{run_id}/artifacts/{artifact}")
    def api_artifact(run_id: str, artifact: str) -> JSONResponse:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None or run.hidden_at is not None:
                raise HTTPException(status_code=404, detail="Run not found")
            payload = _artifact_payload(run)
            if artifact not in payload:
                raise HTTPException(status_code=404, detail="Artifact not found")
            return JSONResponse(
                payload[artifact],
                headers={"Content-Disposition": f"attachment; filename={run_id}-{artifact}.json"},
            )

    return app


app = create_app()
