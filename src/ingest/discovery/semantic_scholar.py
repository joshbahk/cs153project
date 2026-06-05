"""Semantic Scholar Graph API discovery adapter.

Works without a key (shared low rate limit); a ``SEMANTIC_SCHOLAR_API_KEY``
raises the limit substantially.
"""

from __future__ import annotations

from ingest.discovery.base import relevance_score
from ingest.http import HttpClient, HttpError
from ingest.types import ImportCandidate

_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"


def search(query: str, limit: int, http: HttpClient, api_key: str = "") -> list[ImportCandidate]:
    headers = {"x-api-key": api_key} if api_key else None
    try:
        payload = http.get_json(
            _SEARCH,
            params={
                "query": query,
                "limit": str(min(max(limit, 10), 100)),
                "fields": "title,abstract,year,externalIds,openAccessPdf",
            },
            headers=headers,
        )
    except (HttpError, ValueError):
        return []

    data = (payload or {}).get("data") or []
    candidates: list[ImportCandidate] = []
    for item in data:
        title = str(item.get("title") or "Untitled paper").strip()
        abstract = str(item.get("abstract") or "").strip()
        external = item.get("externalIds") or {}
        doi = str(external.get("DOI") or "")
        arxiv_id = str(external.get("ArXiv") or "")
        year = str(item.get("year") or "")
        oa = item.get("openAccessPdf") or {}
        oa_pdf = str(oa.get("url") or "") if isinstance(oa, dict) else ""
        source_uri = f"https://doi.org/{doi}" if doi else (oa_pdf or "")
        citation = f"Title: {title}\nDOI: {doi}\narXiv: {arxiv_id}\nYear: {year}\nSource: {source_uri}"
        text = "\n\n".join(p for p in [citation, f"Abstract:\n{abstract}" if abstract else ""] if p)
        candidates.append(
            ImportCandidate(
                title=title,
                source_uri=source_uri,
                text=text,
                source_kind="semantic_scholar",
                doi=doi,
                arxiv_id=arxiv_id,
                abstract=abstract,
                oa_pdf_url=oa_pdf,
                year=year,
                relevance=relevance_score(query, title, abstract),
            )
        )
    return candidates
