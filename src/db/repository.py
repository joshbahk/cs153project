"""Repository helpers for papers and queued runs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def create_paper_run(session: Session, paper_input: PaperInput, seed: int) -> Run:
    paper = Paper(
        id=_new_id("paper"),
        title=paper_input.title,
        source_kind=paper_input.source_kind,
        source_uri=paper_input.source_uri,
        extracted_text=paper_input.text,
        source_sha256=paper_input.source_sha256,
    )
    run = Run(id=_new_id("run"), paper=paper, status="queued", seed=seed)
    session.add_all([paper, run])
    session.commit()
    return run


def get_run(session: Session, run_id: str) -> Run | None:
    stmt = select(Run).options(joinedload(Run.paper)).where(Run.id == run_id)
    return session.scalar(stmt)


def list_runs(session: Session, query: str = "", limit: int = 50) -> list[Run]:
    stmt = select(Run).options(joinedload(Run.paper)).order_by(Run.created_at.desc()).limit(limit)
    if query:
        pattern = f"%{query.lower()}%"
        stmt = (
            select(Run)
            .join(Run.paper)
            .options(joinedload(Run.paper))
            .where(or_(func.lower(Paper.title).like(pattern), func.lower(Paper.extracted_text).like(pattern)))
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
    return list(session.scalars(stmt))


def count_active_runs(session: Session) -> int:
    stmt = select(func.count()).select_from(Run).where(Run.status.in_(ACTIVE_STATUSES))
    return int(session.scalar(stmt) or 0)


def claim_next_run(session: Session) -> Run | None:
    stmt = (
        select(Run)
        .options(joinedload(Run.paper))
        .where(Run.status == "queued")
        .order_by(Run.created_at.asc())
        .limit(1)
    )
    run = session.scalar(stmt)
    if run is None:
        return None
    run.status = "running"
    run.started_at = utc_now()
    run.error_message = None
    session.commit()
    return run


def mark_run_running(session: Session, run_id: str) -> Run | None:
    run = get_run(session, run_id)
    if run is None or run.status != "queued":
        return None
    run.status = "running"
    run.started_at = utc_now()
    run.error_message = None
    session.commit()
    return run


def mark_run_succeeded(session: Session, run: Run, artifacts: dict[str, Any]) -> None:
    run.status = "succeeded"
    run.finished_at = utc_now()
    run.error_message = None
    run.extraction_json = artifacts["extraction"]
    run.analysis_json = artifacts["analysis"]
    run.ranking_json = artifacts["ranking"]
    run.trials_json = artifacts["trials"]
    run.budget_json = artifacts["manifest"].get("budget", {})
    extraction_items = artifacts["extraction"].get("items", [])
    if extraction_items:
        run.sample_size_target = extraction_items[0].get("sample_size_target")
    session.commit()


def mark_run_failed(session: Session, run: Run, message: str) -> None:
    run.status = "failed"
    run.finished_at = utc_now()
    run.error_message = message[:2000]
    session.commit()


def _primary_analysis(run: Run) -> dict[str, Any]:
    if not run.analysis_json:
        return {}
    first = next(iter(run.analysis_json.values()), {})
    return first.get("primary", first)


def summarize_run(run: Run) -> RunSummary:
    analysis = _primary_analysis(run)
    ranking_items = (run.ranking_json or {}).get("items", [])
    risk = ranking_items[0].get("replication_risk") if ranking_items else None
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
    )


def total_recorded_spend(session: Session) -> float:
    total = 0.0
    for run in session.scalars(select(Run).where(Run.budget_json.is_not(None))):
        total += float((run.budget_json or {}).get("spent_total_usd", 0.0))
    return total
