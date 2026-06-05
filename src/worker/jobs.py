"""Job execution for queued dashboard runs."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from contracts.study_spec import BudgetState
from db.models import Run
from db.repository import (
    claim_next_run,
    get_run,
    is_skip_requested,
    mark_run_failed,
    mark_run_running,
    mark_run_skipped,
    mark_run_succeeded,
    total_recorded_spend,
    update_run_progress,
)
from ingest.batch_importer import extract_url_text
from ingest.paper_ingest import normalize_paper_text, sha256_text
from ingest.study_loader import StudyDocument
from llm.client import DigitalOceanLLMClient
from pipeline.service import PipelineCancelled, PipelineConfig, estimate_run_cost, execute_documents, load_budget, load_credits
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
    if not settings.llm_simulation_enabled:
        return PipelineConfig(seed=seed, max_sample_size=settings.max_sample_size)

    effective_max_sample_size = min(settings.max_sample_size, settings.llm_sample_size)
    return PipelineConfig(
        seed=seed,
        max_sample_size=effective_max_sample_size,
        model_tier=f"digitalocean_serverless_llm_agents:{settings.llm_model}",
        simulation_backend="llm",
        llm_client=DigitalOceanLLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
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


def _run_reservation(settings: AppSettings, seed: int) -> float:
    return estimate_run_cost(settings.max_sample_size, _pipeline_config(settings, seed))


def _metadata_only_text(text: str) -> bool:
    lower = text.lower()
    return "full text will be fetched by the worker" in lower or (
        "abstract:" in lower and "condition" not in lower and "method" not in lower
    )


def _hydrate_fetchable_source(session: Session, run: Run, settings: AppSettings) -> None:
    if run.paper.source_kind not in {"openalex", "url"} or not run.paper.source_uri:
        return
    try:
        extracted = extract_url_text(
            run.paper.source_uri,
            max_upload_mb=settings.max_upload_mb,
            max_chars=settings.max_text_chars,
        )
    except Exception as exc:
        if _metadata_only_text(run.paper.extracted_text):
            raise ValueError(
                "Full-text extraction failed and the queued record only has metadata/abstract text. "
                f"Paste the method/condition text or upload an extractable PDF. Fetch error: {exc}"
            ) from exc
        return

    if len(extracted) <= len(run.paper.extracted_text):
        return
    text = normalize_paper_text(
        f"Title: {run.paper.title}\nSource: {run.paper.source_uri}\n\n{extracted}",
        max_chars=settings.max_text_chars,
    )
    run.paper.extracted_text = text
    run.paper.source_sha256 = sha256_text(text)
    session.commit()


def execute_run(session: Session, run: Run, settings: AppSettings) -> None:
    try:
        update_run_progress(session, run, stage="starting", message="Starting run.", percent=1.0)
        reservation = float((run.budget_json or {}).get("reserved_usd") or _run_reservation(settings, run.seed))
        if total_recorded_spend(session) + reservation > settings.max_total_usd:
            raise ValueError(
                f"Budget cap reached before execution. This run reserves up to ${reservation:.2f}, "
                f"with ${total_recorded_spend(session):.2f} already recorded."
            )
        if is_skip_requested(session, run.id):
            raise PipelineCancelled("Skipped by user request.")
        update_run_progress(
            session,
            run,
            stage="hydrating_source",
            message="Fetching full text for URL/OpenAlex sources if available.",
            percent=4.0,
        )
        _hydrate_fetchable_source(session, run, settings)
        if is_skip_requested(session, run.id):
            raise PipelineCancelled("Skipped by user request.")

        def report_progress(event: dict) -> None:
            update_run_progress(
                session,
                run,
                stage=str(event.get("stage", "running")),
                message=str(event.get("message", "")),
                percent=float(event.get("percent", 0.0)),
                current=event.get("current"),
                total=event.get("total"),
                extra={k: v for k, v in event.items() if k not in {"stage", "message", "percent", "current", "total"}},
            )

        output = execute_documents(
            docs=[_document_from_run(run)],
            run_id=run.id,
            budget_state=_budget_for_run(settings),
            credits=load_credits(settings.credits_path),
            cache=JsonCache(settings.cache_dir),
            config=_pipeline_config(settings, run.seed),
            require_credit_validation=settings.require_credit_validation,
            progress_callback=report_progress,
            should_cancel=lambda: is_skip_requested(session, run.id),
        )
        mark_run_succeeded(session, run, output.artifacts())
    except PipelineCancelled as exc:
        mark_run_skipped(session, run, str(exc))
    except Exception as exc:
        mark_run_failed(session, run, str(exc))


def process_run_by_id(session_factory: sessionmaker[Session], run_id: str, settings: AppSettings) -> bool:
    with session_factory() as session:
        run = mark_run_running(session, run_id)
        if run is None:
            return False
        execute_run(session, run, settings)
        return True


def process_run_batch_by_ids(session_factory: sessionmaker[Session], run_ids: list[str], settings: AppSettings) -> int:
    processed = 0
    for run_id in run_ids:
        if process_run_by_id(session_factory, run_id, settings):
            processed += 1
    return processed


def process_next_run(session_factory: sessionmaker[Session], settings: AppSettings) -> bool:
    with session_factory() as session:
        run = claim_next_run(session)
        if run is None:
            return False
        execute_run(session, run, settings)
        return True
