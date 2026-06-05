"""Job execution for queued dashboard runs."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from contracts.study_spec import BudgetState
from db.models import Run
from db.repository import claim_next_run, get_run, mark_run_failed, mark_run_running, mark_run_succeeded
from ingest.study_loader import StudyDocument
from pipeline.service import PipelineConfig, execute_documents, load_budget, load_credits
from storage.cache import JsonCache
from web.settings import AppSettings


def _document_from_run(run: Run) -> StudyDocument:
    return StudyDocument(
        study_id=run.id,
        title=run.paper.title,
        source_uri=run.paper.source_uri,
        text=run.paper.extracted_text,
        source_sha256=run.paper.source_sha256,
    )


def _budget_for_run(settings: AppSettings) -> BudgetState:
    state = load_budget(settings.budget_path)
    state.max_total_usd = min(state.max_total_usd, settings.max_total_usd)
    return state


def _pipeline_config(settings: AppSettings, seed: int) -> PipelineConfig:
    return PipelineConfig(seed=seed, max_sample_size=settings.max_sample_size)


def execute_run(session: Session, run: Run, settings: AppSettings) -> None:
    try:
        output = execute_documents(
            docs=[_document_from_run(run)],
            run_id=run.id,
            budget_state=_budget_for_run(settings),
            credits=load_credits(settings.credits_path),
            cache=JsonCache(settings.cache_dir),
            config=_pipeline_config(settings, run.seed),
            require_credit_validation=settings.require_credit_validation,
        )
        mark_run_succeeded(session, run, output.artifacts())
    except Exception as exc:
        mark_run_failed(session, run, str(exc))


def process_run_by_id(session_factory: sessionmaker[Session], run_id: str, settings: AppSettings) -> bool:
    with session_factory() as session:
        run = mark_run_running(session, run_id)
        if run is None:
            return False
        execute_run(session, run, settings)
        return True


def process_next_run(session_factory: sessionmaker[Session], settings: AppSettings) -> bool:
    with session_factory() as session:
        run = claim_next_run(session)
        if run is None:
            return False
        execute_run(session, run, settings)
        return True
