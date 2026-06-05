"""arXiv discovery adapter (Atom XML API)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ingest.discovery.base import relevance_score
from ingest.http import HttpClient, HttpError
from ingest.types import ImportCandidate

_API = "http://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"


def search(query: str, limit: int, http: HttpClient) -> list[ImportCandidate]:
    try:
        response = http.get_text(
            _API,
            max_bytes=4 * 1024 * 1024,
            params={
                "search_query": f"all:{query}",
                "start": "0",
                "max_results": str(min(max(limit, 10), 100)),
                "sortBy": "relevance",
            },
        )
    except HttpError:
        return []

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return []

    candidates: list[ImportCandidate] = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = _clean(_text(entry.find(f"{_ATOM}title")))
        abstract = _clean(_text(entry.find(f"{_ATOM}summary")))
        abs_url = _text(entry.find(f"{_ATOM}id"))
        arxiv_id = _arxiv_id(abs_url)
        pdf_url = ""
        for link in entry.findall(f"{_ATOM}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
        citation = f"Title: {title}\narXiv: {arxiv_id}\nSource: {abs_url}"
        text = "\n\n".join(p for p in [citation, f"Abstract:\n{abstract}" if abstract else ""] if p)
        candidates.append(
            ImportCandidate(
                title=title,
                source_uri=abs_url,
                text=text,
                source_kind="arxiv",
                arxiv_id=arxiv_id,
                abstract=abstract,
                oa_pdf_url=pdf_url,
                relevance=relevance_score(query, title, abstract),
            )
        )
    return candidates


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _clean(value: str) -> str:
    return " ".join(value.split())


def _arxiv_id(abs_url: str) -> str:
    match = re.search(r"arxiv\.org/abs/([^v\s]+(?:v\d+)?)", abs_url)
    return match.group(1) if match else ""
