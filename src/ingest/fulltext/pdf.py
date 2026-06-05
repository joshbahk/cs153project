"""PDF body extraction that returns ``None`` instead of raising on failure."""

from __future__ import annotations

from ingest.paper_ingest import PaperIngestError, extract_pdf_text


def pdf_bytes_to_text(pdf_bytes: bytes, *, max_upload_mb: int, max_chars: int) -> str | None:
    try:
        return extract_pdf_text(pdf_bytes, max_upload_mb=max_upload_mb, max_chars=max_chars)
    except PaperIngestError:
        return None
    except Exception:
        return None
