"""SQLAlchemy models for paper submissions and runs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    runs: Mapped[list["Run"]] = relationship(back_populates="paper")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ranking_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trials_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agents_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    llm_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    budget_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    paper: Mapped[Paper] = relationship(back_populates="runs")
