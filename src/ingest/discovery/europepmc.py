"""Europe PMC discovery adapter (no API key required)."""

from __future__ import annotations

from ingest.discovery.base import relevance_score
from ingest.http import HttpClient, HttpError
from ingest.types import ImportCandidate

_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def search(query: str, limit: int, http: HttpClient) -> list[ImportCandidate]:
    try:
        payload = http.get_json(
            _SEARCH,
            params={
                "query": f"({query}) AND (METHODS:y OR ABSTRACT:y)",
                "format": "json",
                "pageSize": str(min(max(limit, 10), 100)),
                "resultType": "core",
                "sort": "RELEVANCE",
            },
        )
    except (HttpError, ValueError):
        return []

    results = (((payload or {}).get("resultList") or {}).get("result")) or []
    candidates: list[ImportCandidate] = []
    for item in results:
        title = str(item.get("title") or "Untitled paper").strip().rstrip(".")
        abstract = str(item.get("abstractText") or "").strip()
        doi = str(item.get("doi") or "")
        pmcid = str(item.get("pmcid") or "")
        year = str(item.get("pubYear") or "")
        source_uri = f"https://doi.org/{doi}" if doi else (
            f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', '')}"
        )
        citation = f"Title: {title}\nDOI: {doi}\nPMCID: {pmcid}\nYear: {year}\nSource: {source_uri}"
        text = "\n\n".join(p for p in [citation, f"Abstract:\n{abstract}" if abstract else ""] if p)
        candidates.append(
            ImportCandidate(
                title=title,
                source_uri=source_uri,
                text=text,
                source_kind="europepmc",
                doi=doi,
                pmcid=pmcid,
                abstract=abstract,
                year=year,
                relevance=relevance_score(query, title, abstract),
            )
        )
    return candidates
