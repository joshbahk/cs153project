"""Repository helpers for papers and queued runs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from db.models import Paper, Run, utc_now
from ingest.paper_ingest import PaperInput


ACTIVE_STATUSES = {"queued", "running"}


@dataclass(slots=True)
class RunSummary:
    id: str
    status: str
    title: str
    source_kind: str
    created_at: str
    finished_at: str | None
    risk: float | None
    p_value: float | None
    effect_size: float | None
    progress_stage: str
    progress_message: str
    progress_percent: float
    elapsed_seconds: float | None
    eta_seconds: float | None
    skip_requested: bool


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _iso_now() -> str:
    return utc_now().isoformat()


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _seconds_between(start: datetime | None, end: datetime | None = None) -> float | None:
    start = _coerce_utc(start)
    if start is None:
        return None
    end = _coerce_utc(end) or utc_now()
    return max(0.0, (end - start).total_seconds())


def _initial_progress() -> dict[str, Any]:
    return {
        "stage": "queued",
        "message": "Waiting for the worker.",
        "percent": 0.0,
        "current": 0,
        "total": 1,
        "updated_at": _iso_now(),
        "stage_started_at": _iso_now(),
        "skip_requested": False,
    }


def create_paper_run(
    session: Session,
    paper_input: PaperInput,
    seed: int,
    budget_reservation_usd: float | None = None,
) -> Run:
    paper = Paper(
        id=_new_id("paper"),
        title=paper_input.title,
        source_kind=paper_input.source_kind,
        source_uri=paper_input.source_uri,
        extracted_text=paper_input.text,
        source_sha256=paper_input.source_sha256,
    )
    budget_json = None
    if budget_reservation_usd is not None:
        budget_json = {"reserved_usd": budget_reservation_usd, "spent_total_usd": 0.0}
    run = Run(
        id=_new_id("run"),
        paper=paper,
        status="queued",
        seed=seed,
        budget_json=budget_json,
        progress_json=_initial_progress(),
    )
    session.add_all([paper, run])
    session.commit()
    return run


def get_run(session: Session, run_id: str) -> Run | None:
    stmt = select(Run).options(joinedload(Run.paper)).where(Run.id == run_id)
    return session.scalar(stmt)


def list_runs(session: Session, query: str = "", limit: int = 50) -> list[Run]:
    stmt = (
        select(Run)
        .options(joinedload(Run.paper))
        .where(Run.hidden_at.is_(None))
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    if query:
        pattern = f"%{query.lower()}%"
        stmt = (
            select(Run)
            .join(Run.paper)
            .options(joinedload(Run.paper))
            .where(
                Run.hidden_at.is_(None),
                or_(func.lower(Paper.title).like(pattern), func.lower(Paper.extracted_text).like(pattern)),
            )
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
    return list(session.scalars(stmt))


def count_active_runs(session: Session) -> int:
    stmt = select(func.count()).select_from(Run).where(Run.status.in_(ACTIVE_STATUSES), Run.hidden_at.is_(None))
    return int(session.scalar(stmt) or 0)


def claim_next_run(session: Session) -> Run | None:
    stmt = (
        select(Run.id)
        .where(Run.status == "queued", Run.hidden_at.is_(None))
        .order_by(Run.created_at.asc())
        .limit(1)
    )
    run_id = session.scalar(stmt)
    if run_id is None:
        return None
    changed = session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == "queued")
        .values(
            status="running",
            started_at=utc_now(),
            error_message=None,
            progress_json={
                **_initial_progress(),
                "stage": "starting",
                "message": "Worker claimed the run.",
                "percent": 1.0,
            },
        )
    )
    if changed.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return get_run(session, run_id)


def mark_run_running(session: Session, run_id: str) -> Run | None:
    changed = session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == "queued")
        .values(
            status="running",
            started_at=utc_now(),
            error_message=None,
            progress_json={
                **_initial_progress(),
                "stage": "starting",
                "message": "Worker claimed the run.",
                "percent": 1.0,
            },
        )
    )
    if changed.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return get_run(session, run_id)


def mark_run_succeeded(session: Session, run: Run, artifacts: dict[str, Any]) -> None:
    run.status = "succeeded"
    run.finished_at = utc_now()
    run.error_message = None
    run.extraction_json = artifacts["extraction"]
    run.analysis_json = artifacts["analysis"]
    run.ranking_json = artifacts["ranking"]
    run.trials_json = artifacts["trials"]
    run.agents_json = artifacts.get("agents", {})
    run.llm_json = artifacts.get("llm_responses", {})
    run.budget_json = artifacts["manifest"].get("budget", {})
    run.progress_json = {
        **(run.progress_json or {}),
        "stage": "succeeded",
        "message": "Run completed.",
        "percent": 100.0,
        "current": 1,
        "total": 1,
        "updated_at": _iso_now(),
        "stage_started_at": (run.progress_json or {}).get("stage_started_at", _iso_now()),
        "skip_requested": False,
    }
    extraction_items = artifacts["extraction"].get("items", [])
    if extraction_items:
        run.sample_size_target = extraction_items[0].get("sample_size_target")
    session.commit()


def mark_run_failed(session: Session, run: Run, message: str) -> None:
    run.status = "failed"
    run.finished_at = utc_now()
    run.error_message = message[:2000]
    run.progress_json = {
        **(run.progress_json or {}),
        "stage": "failed",
        "message": message[:300],
        "percent": 100.0,
        "updated_at": _iso_now(),
    }
    session.commit()


def mark_run_skipped(session: Session, run: Run, message: str = "Skipped by user.") -> None:
    run.status = "skipped"
    run.finished_at = utc_now()
    run.error_message = message[:2000]
    run.progress_json = {
        **(run.progress_json or {}),
        "stage": "skipped",
        "message": message[:300],
        "percent": 100.0,
        "updated_at": _iso_now(),
        "skip_requested": True,
    }
    session.commit()


def update_run_progress(
    session: Session,
    run: Run,
    *,
    stage: str,
    message: str = "",
    percent: float | None = None,
    current: int | None = None,
    total: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    previous = run.progress_json or {}
    changed_stage = previous.get("stage") != stage
    if percent is None and current is not None and total:
        percent = (current / total) * 100.0
    payload = {
        **previous,
        "stage": stage,
        "message": message or previous.get("message", ""),
        "percent": min(100.0, max(0.0, float(percent if percent is not None else previous.get("percent", 0.0)))),
        "updated_at": _iso_now(),
        "skip_requested": bool(previous.get("skip_requested", False)),
    }
    if changed_stage:
        payload["stage_started_at"] = payload["updated_at"]
    if current is not None:
        payload["current"] = int(current)
    if total is not None:
        payload["total"] = int(total)
    if extra:
        payload.update(extra)
    run.progress_json = payload
    session.commit()


def is_skip_requested(session: Session, run_id: str) -> bool:
    progress = session.scalar(select(Run.progress_json).where(Run.id == run_id)) or {}
    return bool(progress.get("skip_requested"))


def request_run_skip(session: Session, run_id: str) -> Run | None:
    run = get_run(session, run_id)
    if run is None:
        return None
    progress = run.progress_json or _initial_progress()
    if run.status == "queued":
        mark_run_skipped(session, run, "Skipped before the worker started.")
        return run
    if run.status == "running":
        run.progress_json = {
            **progress,
            "skip_requested": True,
            "message": "Skip requested; the worker will stop at the next checkpoint.",
            "updated_at": _iso_now(),
        }
        session.commit()
        return run
    return run


def hide_run_from_view(session: Session, run_id: str) -> Run | None:
    run = get_run(session, run_id)
    if run is None:
        return None
    if run.status in ACTIVE_STATUSES:
        raise ValueError("Queued or running runs must be skipped before they can be deleted from view.")
    run.hidden_at = utc_now()
    progress = run.progress_json or _initial_progress()
    run.progress_json = {
        **progress,
        "hidden_at": run.hidden_at.isoformat(),
        "updated_at": _iso_now(),
    }
    session.commit()
    return run


def _primary_analysis(run: Run) -> dict[str, Any]:
    if not run.analysis_json:
        return {}
    first = next(iter(run.analysis_json.values()), {})
    return first.get("primary", first)


def _run_progress(run: Run) -> dict[str, Any]:
    return run.progress_json or _initial_progress()


def _estimate_eta_seconds(
    run: Run,
    progress: dict[str, Any],
    average_completed_seconds: float | None,
) -> float | None:
    if run.status in {"succeeded", "failed", "skipped"}:
        return 0.0
    elapsed = _seconds_between(run.started_at or run.created_at)
    if elapsed is None:
        return None
    percent = float(progress.get("percent") or 0.0)
    if percent > 1.0:
        return max(0.0, elapsed * ((100.0 - percent) / percent))
    if average_completed_seconds is not None:
        return max(0.0, average_completed_seconds - elapsed)
    return None


def summarize_run(run: Run, average_completed_seconds: float | None = None) -> RunSummary:
    analysis = _primary_analysis(run)
    ranking_items = (run.ranking_json or {}).get("items", [])
    risk = ranking_items[0].get("replication_risk") if ranking_items else None
    progress = _run_progress(run)
    return RunSummary(
        id=run.id,
        status=run.status,
        title=run.paper.title,
        source_kind=run.paper.source_kind,
        created_at=run.created_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        risk=risk,
        p_value=analysis.get("p_value"),
        effect_size=analysis.get("effect_size"),
        progress_stage=str(progress.get("stage", run.status)),
        progress_message=str(progress.get("message", "")),
        progress_percent=float(progress.get("percent") or 0.0),
        elapsed_seconds=_seconds_between(run.started_at or run.created_at, run.finished_at),
        eta_seconds=_estimate_eta_seconds(run, progress, average_completed_seconds),
        skip_requested=bool(progress.get("skip_requested", False)),
    )


def total_recorded_spend(session: Session) -> float:
    total = 0.0
    for run in session.scalars(select(Run).where(Run.budget_json.is_not(None))):
        total += float((run.budget_json or {}).get("spent_total_usd", 0.0))
    return total


def total_reserved_spend(session: Session) -> float:
    total = 0.0
    for run in session.scalars(
        select(Run).where(Run.status.in_(ACTIVE_STATUSES), Run.hidden_at.is_(None), Run.budget_json.is_not(None))
    ):
        total += float((run.budget_json or {}).get("reserved_usd", 0.0))
    return total


def total_projected_spend(session: Session) -> float:
    return total_recorded_spend(session) + total_reserved_spend(session)


def average_completed_duration_seconds(session: Session, limit: int = 20) -> float | None:
    stmt = (
        select(Run)
        .where(Run.status == "succeeded", Run.started_at.is_not(None), Run.finished_at.is_not(None))
        .order_by(Run.finished_at.desc())
        .limit(limit)
    )
    durations = [
        duration
        for run in session.scalars(stmt)
        if (duration := _seconds_between(run.started_at, run.finished_at)) is not None
    ]
    if not durations:
        return None
    return sum(durations) / len(durations)
