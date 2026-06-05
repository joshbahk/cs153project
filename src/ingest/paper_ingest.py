"""User-facing paper ingestion for pasted text and PDF uploads."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class PaperIngestError(ValueError):
    pass


@dataclass(slots=True)
class PaperInput:
    title: str
    source_kind: str
    source_uri: str
    text: str
    source_sha256: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_paper_text(text: str, max_chars: int) -> str:
    normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines()).strip()
    if not normalized:
        raise PaperIngestError("No paper text was provided.")
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars].rsplit("\n", 1)[0].strip() or normalized[:max_chars].strip()
    if len(normalized) < 80:
        raise PaperIngestError("Paper text is too short to extract a study protocol.")
    return normalized


def extract_pdf_text(pdf_bytes: bytes, max_upload_mb: int, max_chars: int) -> str:
    max_bytes = max_upload_mb * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        raise PaperIngestError(f"PDF is larger than the {max_upload_mb} MB upload limit.")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise PaperIngestError("Could not read the uploaded PDF.") from exc

    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
    text = normalize_paper_text("\n".join(chunks), max_chars=max_chars)
    if len(text) < 120:
        raise PaperIngestError("The PDF appears to be scanned or empty. Paste OCR text instead.")
    return text


def build_paper_input(
    title: str,
    pasted_text: str,
    upload_bytes: bytes | None,
    upload_filename: str,
    max_upload_mb: int = 15,
    max_chars: int = 120_000,
) -> PaperInput:
    clean_title = title.strip()
    pasted_text = pasted_text.strip()

    if pasted_text:
        text = normalize_paper_text(pasted_text, max_chars=max_chars)
        source_kind = "text"
        source_uri = "user-pasted-text"
    elif upload_bytes:
        text = extract_pdf_text(upload_bytes, max_upload_mb=max_upload_mb, max_chars=max_chars)
        source_kind = "pdf"
        source_uri = upload_filename or "uploaded.pdf"
    else:
        raise PaperIngestError("Upload a PDF or paste paper text.")

    if not clean_title:
        clean_title = Path(upload_filename).stem.replace("_", " ").replace("-", " ").strip()
    if not clean_title:
        clean_title = text.splitlines()[0][:90].strip() or "Untitled study"

    return PaperInput(
        title=clean_title,
        source_kind=source_kind,
        source_uri=source_uri,
        text=text,
        source_sha256=sha256_text(text),
    )
