"""FastAPI dashboard for simulation-based replication triage."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.repository import (
    count_active_runs,
    create_paper_run,
    get_run,
    list_runs,
    summarize_run,
    total_recorded_spend,
)
from db.session import create_app_engine, create_session_factory, init_database
from ingest.paper_ingest import PaperIngestError, build_paper_input
from web.settings import AppSettings, get_settings
from worker.jobs import process_run_by_id


templates = Jinja2Templates(directory="src/web/templates")


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


def _artifact_payload(run) -> dict[str, Any]:
    return {
        "extraction": run.extraction_json or {},
        "analysis": run.analysis_json or {},
        "ranking": run.ranking_json or {},
        "trials": run.trials_json or {},
        "budget": run.budget_json or {},
    }


def _app_state(request: Request) -> tuple[AppSettings, Any]:
    return request.app.state.settings, request.app.state.session_factory


def _render_dashboard(
    request: Request,
    session: Session,
    message: str = "",
    error: str = "",
    query: str = "",
) -> HTMLResponse:
    settings, _ = _app_state(request)
    runs = [summarize_run(run) for run in list_runs(session, query=query)]
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
        },
    )


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_app_engine(settings.database_url)
    init_database(engine)
    session_factory = create_session_factory(engine)

    app = FastAPI(title="Replication Triage Dashboard")
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, q: str = "") -> HTMLResponse:
        with session_factory() as session:
            return _render_dashboard(request, session, query=q)

    @app.post("/runs", response_model=None)
    async def create_run(
        request: Request,
        background_tasks: BackgroundTasks,
        title: str = Form(""),
        paper_text: str = Form(""),
        paper_pdf: UploadFile | None = File(None),
    ) -> RedirectResponse | HTMLResponse:
        upload_bytes = None
        upload_filename = ""
        if paper_pdf is not None and paper_pdf.filename:
            upload_filename = paper_pdf.filename
            upload_bytes = await paper_pdf.read()

        with session_factory() as session:
            if count_active_runs(session) >= settings.max_queued_jobs:
                return _render_dashboard(
                    request,
                    session,
                    error=f"Queue limit reached ({settings.max_queued_jobs} active runs).",
                )
            if total_recorded_spend(session) >= settings.max_total_usd:
                return _render_dashboard(
                    request,
                    session,
                    error=f"Budget cap reached (${settings.max_total_usd:.2f}).",
                )
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

            run = create_paper_run(session, paper_input, seed=settings.seed)
            run_id = run.id

        if settings.auto_process_on_submit:
            background_tasks.add_task(process_run_by_id, session_factory, run_id, settings)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str) -> HTMLResponse:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            ranking_items = (run.ranking_json or {}).get("items", [])
            return templates.TemplateResponse(
                request,
                "run_detail.html",
                {
                    "request": request,
                    "run": run,
                    "primary": _primary_analysis(run),
                    "sensitivity": _sensitivity(run),
                    "ranking": ranking_items[0] if ranking_items else {},
                    "trial_summary": _trial_summary(run),
                    "artifacts": _artifact_payload(run),
                },
            )

    @app.get("/api/runs")
    def api_runs(q: str = "") -> dict[str, Any]:
        with session_factory() as session:
            return {"items": [asdict(summarize_run(run)) for run in list_runs(session, query=q)]}

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str) -> dict[str, Any]:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
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
                "error_message": run.error_message,
            }

    @app.get("/api/runs/{run_id}/artifacts")
    def api_artifacts(run_id: str) -> dict[str, Any]:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            return _artifact_payload(run)

    @app.get("/api/runs/{run_id}/artifacts/{artifact}")
    def api_artifact(run_id: str, artifact: str) -> JSONResponse:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
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
