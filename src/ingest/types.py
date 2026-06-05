"""Shared data contracts for the bulk import subsystem.

``ImportCandidate`` is intentionally backward compatible: the first four fields
match the legacy definition so existing callers and tests that build
``ImportCandidate(title=..., source_uri=..., text=..., source_kind=...)`` keep
working. Everything added afterwards is optional metadata used by the discovery
and full-text resolution stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ImportCandidate:
    title: str
    source_uri: str
    text: str
    source_kind: str
    doi: str = ""
    pmcid: str = ""
    arxiv_id: str = ""
    abstract: str = ""
    oa_pdf_url: str = ""
    oa_html_url: str = ""
    year: str = ""
    relevance: float = 0.0
    full_text: str = ""
    full_text_source: str = ""

    @property
    def best_text(self) -> str:
        return self.full_text or self.text


@dataclass(slots=True)
class CandidateDisposition:
    title: str
    source_kind: str
    doi: str
    status: str  # created | resolved | no_full_text | fetch_failed | empty_text | unsuitable_methodology | duplicate | error
    detail: str = ""


@dataclass(slots=True)
class ImportReport:
    requested: int = 0
    discovered: int = 0
    deduped: int = 0
    resolved_full_text: int = 0
    suitable: int = 0
    created: int = 0
    dispositions: list[CandidateDisposition] = field(default_factory=list)

    def add(self, disp: CandidateDisposition) -> None:
        self.dispositions.append(disp)

    def summary(self) -> dict[str, object]:
        by_status: dict[str, int] = {}
        for disp in self.dispositions:
            by_status[disp.status] = by_status.get(disp.status, 0) + 1
        return {
            "requested": self.requested,
            "discovered": self.discovered,
            "deduped": self.deduped,
            "resolved_full_text": self.resolved_full_text,
            "suitable": self.suitable,
            "created": self.created,
            "by_status": by_status,
        }
