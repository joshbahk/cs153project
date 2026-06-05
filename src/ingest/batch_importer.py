"""Bulk paper discovery and full-text resolution for batch runs.

Pipeline overview
-----------------
1. ``discover_openalex`` queries OpenAlex (paginated, relevance-ranked, polite
   pool when a mailto is configured) and, when ``BULK_IMPORT_MULTISOURCE`` is
   enabled, merges in Europe PMC / Semantic Scholar / arXiv results.
2. ``candidates_to_paper_inputs`` enforces a **full-text-only** policy: a
   candidate is kept only if real study conditions can be extracted from its
   text. Candidates that only carry an abstract are sent through the full-text
   resolver chain (Europe PMC, Unpaywall, arXiv, OA PDFs, landing pages) and
   re-checked. Anything still lacking method/conditions text is dropped, with a
   reason recorded in an :class:`ImportReport`.

Backward-compatibility contracts (relied on by existing tests):
  * ``_request_json`` / ``_request_bytes`` stay urllib-based and patchable.
  * ``ImportCandidate`` keeps its original four positional fields.
  * ``discover_openalex`` / ``candidates_to_paper_inputs`` keep their signatures.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ingest.config import get_ingest_config
from ingest.fulltext.html import html_to_text
from ingest.fulltext.resolver import resolve_full_text
from ingest.http import HttpClient
from ingest.paper_ingest import (
    PaperIngestError,
    PaperInput,
    extract_pdf_text,
    normalize_paper_text,
    sha256_text,
)
from ingest.suitability import assess_text
from ingest.types import CandidateDisposition, ImportCandidate, ImportReport

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; replication-triage/0.1; +https://github.com/cs153project)"
)
_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


class BatchImportError(ValueError):
    pass


def _browser_headers(mailto: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": _USER_AGENT + (f" (mailto:{mailto})" if mailto else ""),
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    return headers


def _request_json(url: str, timeout: int = 20, retries: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=_browser_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
            last_error = exc
            if exc.code in _RETRYABLE_HTTP and attempt < retries:
                _sleep_backoff(attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:  # type: ignore[attr-defined]
            last_error = exc
            if attempt < retries:
                _sleep_backoff(attempt)
                continue
            raise
    raise BatchImportError(f"Could not reach {url}: {last_error}")


def _request_bytes(url: str, max_bytes: int, timeout: int = 25, retries: int = 2) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=_browser_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content_type = response.headers.get("content-type", "")
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise PaperIngestError(
                        f"Remote file is larger than the {max_bytes // (1024 * 1024)} MB limit."
                    )

                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise PaperIngestError(
                            f"Remote file is larger than the {max_bytes // (1024 * 1024)} MB limit."
                        )
                    chunks.append(chunk)
                return b"".join(chunks), content_type
        except PaperIngestError:
            raise
        except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
            last_error = exc
            if exc.code in _RETRYABLE_HTTP and attempt < retries:
                _sleep_backoff(attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:  # type: ignore[attr-defined]
            last_error = exc
            if attempt < retries:
                _sleep_backoff(attempt)
                continue
            raise
    raise PaperIngestError(f"Could not download {url}: {last_error}")


def _sleep_backoff(attempt: int) -> None:
    import random
    import time

    time.sleep(min(0.5 * (2 ** attempt), 6.0) + random.uniform(0, 0.2))


def _abstract_from_inverted_index(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions:
            words.append((int(pos), word))
    return " ".join(word for _, word in sorted(words))


def _open_locations(work: dict[str, Any]) -> tuple[str, str]:
    """Return ``(pdf_url, html_url)`` from an OpenAlex work record."""

    pdf_url = ""
    html_url = ""
    open_access = work.get("open_access") or {}
    if open_access.get("oa_url"):
        html_url = str(open_access["oa_url"])

    locations = [work.get("primary_location") or {}, *(work.get("locations") or [])]
    for location in locations:
        if not isinstance(location, dict):
            continue
        if not pdf_url and location.get("pdf_url"):
            pdf_url = str(location["pdf_url"])
        if not html_url and location.get("landing_page_url"):
            html_url = str(location["landing_page_url"])
    return pdf_url, html_url


def _best_open_url(work: dict[str, Any]) -> str:
    pdf_url, html_url = _open_locations(work)
    return html_url or pdf_url or str(work.get("doi") or work.get("id") or "")


def _pmcid_from_ids(work: dict[str, Any]) -> str:
    ids = work.get("ids") or {}
    pmcid = ids.get("pmcid") if isinstance(ids, dict) else ""
    if not pmcid:
        return ""
    match = re.search(r"PMC\d+", str(pmcid))
    return match.group(0) if match else str(pmcid)


def _relevance_score(query: str, title: str, abstract: str) -> int:
    text = f"{title} {abstract}".lower()
    title_text = title.lower()
    stop = {"with", "from", "that", "this", "and", "the", "for", "study", "paper"}
    tokens = [token for token in re.findall(r"[a-z]{4,}", query.lower()) if token not in stop]
    score = 0
    for token in tokens:
        if token in title_text:
            score += 4
        if token in text:
            score += 1
    for term in ["field experiment", "randomized", "experiment", "control", "treatment", "participants"]:
        if term in text:
            score += 2
    return score


def _openalex_candidates(query: str, limit: int, mailto: str, max_pages: int, overfetch: int) -> list[ImportCandidate]:
    target = max(limit * overfetch, limit)
    per_page = min(max(target, 25), 200)
    collected: dict[str, ImportCandidate] = {}

    for page in range(1, max_pages + 1):
        params = {
            "search": query,
            "filter": "open_access.is_oa:true,type:article",
            "per-page": str(per_page),
            "page": str(page),
            "select": "id,doi,display_name,publication_year,open_access,primary_location,locations,ids,abstract_inverted_index",
        }
        if mailto:
            params["mailto"] = mailto
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        payload = _request_json(url)
        results = payload.get("results", []) or []
        for work in results:
            title = str(work.get("display_name") or "Untitled paper").strip()
            abstract = _abstract_from_inverted_index(work.get("abstract_inverted_index"))
            source_uri = _best_open_url(work)
            doi = str(work.get("doi") or "")
            pdf_url, html_url = _open_locations(work)
            citation = (
                f"Title: {title}\nOpenAlex: {work.get('id', '')}\nDOI: {doi}\n"
                f"Year: {work.get('publication_year', '')}\nSource: {source_uri}"
            )
            text = "\n\n".join(
                part for part in [citation, f"Abstract:\n{abstract}" if abstract else ""] if part
            ).strip()
            if len(text) < 80 or not source_uri:
                continue
            key = (doi or work.get("id") or source_uri or title).lower()
            if key in collected:
                continue
            collected[key] = ImportCandidate(
                title=title,
                source_uri=source_uri,
                text=text,
                source_kind="openalex",
                doi=doi,
                pmcid=_pmcid_from_ids(work),
                abstract=abstract,
                oa_pdf_url=pdf_url,
                oa_html_url=html_url,
                year=str(work.get("publication_year", "")),
                relevance=float(_relevance_score(query, title, abstract)),
            )
        if len(results) < per_page or len(collected) >= target:
            break

    return sorted(collected.values(), key=lambda c: c.relevance, reverse=True)


def discover_openalex(query: str, limit: int, mailto: str = "") -> list[ImportCandidate]:
    query = query.strip()
    if not query:
        raise BatchImportError("Enter a search query for batch discovery.")

    config = get_ingest_config().with_overrides(mailto=mailto)
    candidates = _openalex_candidates(
        query,
        limit,
        mailto or config.openalex_mailto,
        max_pages=config.max_discovery_pages,
        overfetch=config.overfetch_factor,
    )

    if config.multisource_discovery:
        candidates = _augment_with_secondary(query, limit, candidates, config)

    return candidates[:limit]


def _augment_with_secondary(
    query: str,
    limit: int,
    primary: list[ImportCandidate],
    config: Any,
) -> list[ImportCandidate]:
    # Imported lazily so the discovery package never participates in an import
    # cycle with this module.
    from ingest.discovery.multi import discover_secondary, merge_candidates

    http = HttpClient(
        timeout=config.http_timeout,
        max_retries=config.http_max_retries,
        min_host_interval=config.min_host_interval,
        mailto=config.openalex_mailto,
    )
    try:
        secondary = discover_secondary(query, limit, http, config)
    except Exception as exc:  # pragma: no cover - network defensive
        logger.warning("secondary discovery failed: %s", exc)
        secondary = []
    finally:
        http.close()
    return merge_candidates([*primary, *secondary])


def extract_url_text(url: str, max_upload_mb: int, max_chars: int) -> str:
    raw, content_type = _request_bytes(url, max_bytes=max_upload_mb * 1024 * 1024)
    looks_pdf = "pdf" in content_type.lower() or urllib.parse.urlparse(url).path.lower().endswith(".pdf")
    if looks_pdf:
        return extract_pdf_text(raw, max_upload_mb=max_upload_mb, max_chars=max_chars)
    return normalize_paper_text(html_to_text(raw), max_chars=max_chars)


def _line_to_url(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    if line.startswith("http://") or line.startswith("https://"):
        return line
    doi_match = re.search(r"\b10\.\d{4,9}/\S+\b", line)
    if doi_match:
        return "https://doi.org/" + doi_match.group(0).rstrip(".,)")
    return ""


def source_candidates_from_lines(source_lines: str) -> list[ImportCandidate]:
    candidates: list[ImportCandidate] = []
    for idx, line in enumerate(source_lines.splitlines(), start=1):
        url = _line_to_url(line)
        if not url:
            continue
        doi_match = re.search(r"\b10\.\d{4,9}/\S+\b", url)
        doi = ("https://doi.org/" + doi_match.group(0).rstrip(".,)")) if doi_match else ""
        text = (
            f"Title: Imported source {idx}\n"
            f"Source: {url}\n"
            "Import note: Full text will be fetched and validated before a run is created."
        )
        candidates.append(
            ImportCandidate(
                title=f"Imported source {idx}",
                source_uri=url,
                text=text,
                source_kind="url",
                doi=doi,
                oa_html_url=url,
            )
        )
    return candidates


def hydrate_candidates(
    candidates: list[ImportCandidate],
    max_upload_mb: int,
    max_chars: int,
) -> list[ImportCandidate]:
    hydrated: list[ImportCandidate] = []
    for candidate in candidates:
        text = candidate.text
        if candidate.source_uri:
            try:
                extracted = extract_url_text(
                    candidate.source_uri,
                    max_upload_mb=max_upload_mb,
                    max_chars=max_chars,
                )
                if len(extracted) > len(text):
                    text = f"Title: {candidate.title}\nSource: {candidate.source_uri}\n\n{extracted}"
            except Exception as exc:
                text = f"{text}\n\nImport note: Full-text extraction failed; using metadata/abstract fallback ({exc})."
        hydrated.append(
            ImportCandidate(
                title=candidate.title,
                source_uri=candidate.source_uri,
                text=text,
                source_kind=candidate.source_kind,
                doi=candidate.doi,
                pmcid=candidate.pmcid,
                arxiv_id=candidate.arxiv_id,
            )
        )
    return hydrated


def import_from_sources(source_lines: str, max_upload_mb: int, max_chars: int) -> list[ImportCandidate]:
    candidates: list[ImportCandidate] = []
    for idx, line in enumerate(source_lines.splitlines(), start=1):
        url = _line_to_url(line)
        if not url:
            continue
        try:
            text = extract_url_text(url, max_upload_mb=max_upload_mb, max_chars=max_chars)
        except Exception as exc:
            text = f"Title: Imported source {idx}\nSource: {url}\nImport note: Could not extract full text automatically ({exc})."
        candidates.append(
            ImportCandidate(
                title=f"Imported source {idx}",
                source_uri=url,
                text=text,
                source_kind="url",
            )
        )
    return candidates


def resolve_candidates_to_paper_inputs(
    candidates: list[ImportCandidate],
    max_chars: int,
) -> tuple[list[PaperInput], ImportReport]:
    """Full-text-only conversion with a structured report.

    A candidate is emitted only if real study conditions can be extracted from
    its text. Abstract-only candidates are first sent through the full-text
    resolver chain; if resolution still does not surface method/conditions text
    the candidate is dropped (never raised), keeping a single bad source from
    failing the whole batch.
    """

    config = get_ingest_config()
    report = ImportReport(requested=len(candidates), discovered=len(candidates))
    papers: list[PaperInput] = []
    http: HttpClient | None = None

    try:
        for candidate in candidates:
            text = candidate.best_text
            full_text_source = candidate.full_text_source
            verdict = assess_text(text, title=candidate.title, source_uri=candidate.source_uri)

            if not verdict.suitable and config.resolve_full_text:
                if http is None:
                    http = HttpClient(
                        timeout=config.http_timeout,
                        max_retries=config.http_max_retries,
                        min_host_interval=config.min_host_interval,
                        mailto=config.openalex_mailto,
                    )
                try:
                    resolved = resolve_full_text(candidate, http, config, max_chars=max_chars)
                except Exception as exc:  # pragma: no cover - network defensive
                    logger.debug("resolution error for %s: %s", candidate.source_uri, exc)
                    resolved = None
                if resolved is not None:
                    text, full_text_source = resolved
                    report.resolved_full_text += 1
                    verdict = assess_text(text, title=candidate.title, source_uri=candidate.source_uri)

            if not verdict.suitable:
                status = verdict.reason if text.strip() else "no_full_text"
                report.add(
                    CandidateDisposition(
                        title=candidate.title,
                        source_kind=candidate.source_kind,
                        doi=candidate.doi,
                        status=status,
                        detail=",".join(verdict.warnings),
                    )
                )
                continue

            try:
                normalized = normalize_paper_text(text, max_chars=max_chars)
            except PaperIngestError as exc:
                report.add(
                    CandidateDisposition(
                        title=candidate.title,
                        source_kind=candidate.source_kind,
                        doi=candidate.doi,
                        status="empty_text",
                        detail=str(exc),
                    )
                )
                continue

            papers.append(
                PaperInput(
                    title=candidate.title[:240],
                    source_kind=candidate.source_kind,
                    source_uri=candidate.source_uri,
                    text=normalized,
                    source_sha256=sha256_text(normalized),
                )
            )
            report.suitable += 1
            report.created += 1
            report.add(
                CandidateDisposition(
                    title=candidate.title,
                    source_kind=candidate.source_kind,
                    doi=candidate.doi,
                    status="created",
                    detail=full_text_source,
                )
            )
    finally:
        if http is not None:
            http.close()

    logger.info("bulk import report: %s", report.summary())
    return papers, report


def candidates_to_paper_inputs(candidates: list[ImportCandidate], max_chars: int) -> list[PaperInput]:
    papers, _report = resolve_candidates_to_paper_inputs(candidates, max_chars)
    return papers
