"""Bulk paper discovery and extraction for low-touch batch runs."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from ingest.paper_ingest import PaperIngestError, PaperInput, extract_pdf_text, normalize_paper_text, sha256_text


class BatchImportError(ValueError):
    pass


@dataclass(slots=True)
class ImportCandidate:
    title: str
    source_uri: str
    text: str
    source_kind: str


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._capture = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer", "header", "aside"}:
            self._skip_depth += 1
        if tag in {"title", "h1", "h2", "h3", "p", "li", "td", "th", "figcaption"}:
            self._capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer", "header", "aside"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"title", "h1", "h2", "h3", "p", "li", "td", "th", "figcaption"}:
            self._capture = False
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._capture:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self._chunks.append(cleaned + " ")

    def text(self) -> str:
        return "\n".join(chunk.strip() for chunk in self._chunks if chunk.strip())


def _request_json(url: str, timeout: int = 20) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "replication-triage/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_bytes(url: str, max_bytes: int, timeout: int = 25) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "replication-triage/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("content-type", "")
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            raise PaperIngestError(f"Remote file is larger than the {max_bytes // (1024 * 1024)} MB limit.")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise PaperIngestError(f"Remote file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
            chunks.append(chunk)
        return b"".join(chunks), content_type


def _abstract_from_inverted_index(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions:
            words.append((int(pos), word))
    return " ".join(word for _, word in sorted(words))


def _best_open_url(work: dict[str, Any]) -> str:
    open_access = work.get("open_access") or {}
    if open_access.get("oa_url"):
        return str(open_access["oa_url"])

    primary = work.get("primary_location") or {}
    if primary.get("pdf_url"):
        return str(primary["pdf_url"])
    if primary.get("landing_page_url"):
        return str(primary["landing_page_url"])

    for location in work.get("locations") or []:
        if location.get("pdf_url"):
            return str(location["pdf_url"])
    for location in work.get("locations") or []:
        if location.get("landing_page_url"):
            return str(location["landing_page_url"])
    return str(work.get("doi") or work.get("id") or "")


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


def discover_openalex(query: str, limit: int, mailto: str = "") -> list[ImportCandidate]:
    query = query.strip()
    if not query:
        raise BatchImportError("Enter a search query for batch discovery.")

    params = {
        "search": query,
        "filter": "open_access.is_oa:true,type:article",
        "per-page": str(min(max(limit * 3, 10), 100)),
        "select": "id,doi,display_name,publication_year,open_access,primary_location,locations,abstract_inverted_index",
    }
    if mailto:
        params["mailto"] = mailto
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    payload = _request_json(url)
    candidates: list[tuple[int, ImportCandidate]] = []
    for work in payload.get("results", []):
        title = str(work.get("display_name") or "Untitled paper").strip()
        abstract = _abstract_from_inverted_index(work.get("abstract_inverted_index"))
        relevance_text = f"{title} {abstract}".lower()
        if not any(term in relevance_text for term in ["experiment", "random", "control", "treatment", "participant"]):
            continue
        source_uri = _best_open_url(work)
        citation = f"Title: {title}\nOpenAlex: {work.get('id', '')}\nDOI: {work.get('doi', '')}\nYear: {work.get('publication_year', '')}\nSource: {source_uri}"
        text = "\n\n".join(part for part in [citation, f"Abstract:\n{abstract}" if abstract else ""] if part).strip()
        if len(text) >= 80 and source_uri:
            candidates.append(
                (
                    _relevance_score(query, title, abstract),
                    ImportCandidate(title=title, source_uri=source_uri, text=text, source_kind="openalex"),
                )
            )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in candidates[:limit]]


def _html_to_text(html: bytes) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(html.decode("utf-8", errors="ignore"))
    return parser.text()


def extract_url_text(url: str, max_upload_mb: int, max_chars: int) -> str:
    raw, content_type = _request_bytes(url, max_bytes=max_upload_mb * 1024 * 1024)
    looks_pdf = "pdf" in content_type.lower() or urllib.parse.urlparse(url).path.lower().endswith(".pdf")
    if looks_pdf:
        return extract_pdf_text(raw, max_upload_mb=max_upload_mb, max_chars=max_chars)
    return normalize_paper_text(_html_to_text(raw), max_chars=max_chars)


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
                    text = (
                        f"Title: {candidate.title}\n"
                        f"Source: {candidate.source_uri}\n\n"
                        f"{extracted}"
                    )
            except Exception as exc:
                text = f"{text}\n\nImport note: Full-text extraction failed; using metadata/abstract fallback ({exc})."
        hydrated.append(
            ImportCandidate(
                title=candidate.title,
                source_uri=candidate.source_uri,
                text=text,
                source_kind=candidate.source_kind,
            )
        )
    return hydrated


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
        text = (
            f"Title: Imported source {idx}\n"
            f"Source: {url}\n"
            "Import note: Full text will be fetched by the worker before simulation."
        )
        candidates.append(
            ImportCandidate(
                title=f"Imported source {idx}",
                source_uri=url,
                text=text,
                source_kind="url",
            )
        )
    return candidates


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


def candidates_to_paper_inputs(candidates: list[ImportCandidate], max_chars: int) -> list[PaperInput]:
    papers: list[PaperInput] = []
    for candidate in candidates:
        try:
            text = normalize_paper_text(candidate.text, max_chars=max_chars)
        except PaperIngestError:
            continue
        papers.append(
            PaperInput(
                title=candidate.title[:240],
                source_kind=candidate.source_kind,
                source_uri=candidate.source_uri,
                text=text,
                source_sha256=sha256_text(text),
            )
        )
    return papers
