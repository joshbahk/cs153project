"""Ordered full-text resolution for an import candidate.

The resolver tries the cheapest, most reliable open-access routes first and
returns the first body that looks like real full text:

  1. Europe PMC machine-readable full text (via PMCID, or PMCID looked up by DOI)
  2. Unpaywall best open-access PDF/HTML (via DOI)
  3. arXiv PDF (via arXiv id)
  4. Candidate-provided OA PDF / OA HTML locations
  5. Candidate landing-page HTML (last resort)

Every step is best-effort: network/parse failures are swallowed so a single bad
source never aborts the batch.
"""

from __future__ import annotations

import urllib.parse

from ingest.config import IngestConfig
from ingest.fulltext import europepmc_fulltext, unpaywall
from ingest.fulltext.html import html_to_text
from ingest.fulltext.pdf import pdf_bytes_to_text
from ingest.fulltext.quality import is_probably_full_text
from ingest.http import HttpClient, HttpError
from ingest.types import ImportCandidate


def resolve_full_text(
    candidate: ImportCandidate,
    http: HttpClient,
    config: IngestConfig,
    *,
    max_chars: int = 120_000,
) -> tuple[str, str] | None:
    """Return ``(full_text, source_label)`` or ``None`` when nothing resolves."""

    max_bytes = config.max_resolution_bytes
    min_chars = config.min_full_text_chars

    def accept(text: str | None) -> bool:
        return bool(text) and is_probably_full_text(text, min_chars=min_chars)

    # 1. Europe PMC full text.
    if config.enable_europepmc:
        pmcid = candidate.pmcid or (europepmc_fulltext.pmcid_from_doi(candidate.doi, http) if candidate.doi else "")
        if pmcid:
            text = europepmc_fulltext.fetch_full_text(pmcid, http, max_bytes=max_bytes, max_chars=max_chars)
            if accept(text):
                return text, "europepmc_fulltext"

    # 2. Unpaywall OA locations.
    if candidate.doi and config.unpaywall_email:
        pdf_url, html_url = unpaywall.best_oa_locations(candidate.doi, config.unpaywall_email, http)
        for url, kind in ((pdf_url, "pdf"), (html_url, "html")):
            text = _fetch_body(url, kind, http, config, max_chars=max_chars)
            if accept(text):
                return text, f"unpaywall_{kind}"

    # 3. arXiv PDF.
    if candidate.arxiv_id and config.enable_arxiv:
        arxiv_pdf = f"https://arxiv.org/pdf/{candidate.arxiv_id}.pdf"
        text = _fetch_body(arxiv_pdf, "pdf", http, config, max_chars=max_chars)
        if accept(text):
            return text, "arxiv_pdf"

    # 4. Candidate-declared OA locations.
    for url, kind in ((candidate.oa_pdf_url, "pdf"), (candidate.oa_html_url, "html")):
        text = _fetch_body(url, kind, http, config, max_chars=max_chars)
        if accept(text):
            return text, f"oa_{kind}"

    # 5. Landing page as a last resort.
    text = _fetch_body(candidate.source_uri, "auto", http, config, max_chars=max_chars)
    if accept(text):
        return text, "landing_page"

    return None


def _fetch_body(
    url: str,
    kind: str,
    http: HttpClient,
    config: IngestConfig,
    *,
    max_chars: int,
) -> str | None:
    if not url:
        return None
    try:
        response = http.get_bytes(url, max_bytes=config.max_resolution_bytes)
    except HttpError:
        return None

    looks_pdf = (
        kind == "pdf"
        or "pdf" in response.content_type.lower()
        or urllib.parse.urlparse(url).path.lower().endswith(".pdf")
    )
    if looks_pdf:
        max_mb = max(1, config.max_resolution_bytes // (1024 * 1024))
        return pdf_bytes_to_text(response.content, max_upload_mb=max_mb, max_chars=max_chars)
    return html_to_text(response.content)[:max_chars]
