"""Load and normalize text-based study sources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class StudyDocument:
    study_id: str
    title: str
    source_uri: str
    text: str
    source_sha256: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_study_document(path: Path) -> StudyDocument:
    raw = json.loads(path.read_text(encoding="utf-8"))
    text = raw["text"].strip()
    return StudyDocument(
        study_id=raw["study_id"],
        title=raw["title"],
        source_uri=raw.get("source_uri", str(path)),
        text=text,
        source_sha256=_sha256(text),
    )
