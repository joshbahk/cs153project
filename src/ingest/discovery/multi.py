"""Secondary multi-source discovery orchestration.

This intentionally excludes OpenAlex: OpenAlex discovery lives in
``batch_importer.discover_openalex`` so the existing, unit-tested
``_request_json`` patch point keeps working. ``discover_secondary`` fans out to
the remaining sources and is only invoked when multi-source discovery is enabled
via config.
"""

from __future__ import annotations

from ingest.config import IngestConfig
from ingest.discovery import arxiv, europepmc, semantic_scholar
from ingest.discovery.base import dedupe_key
from ingest.http import HttpClient
from ingest.types import ImportCandidate


def discover_secondary(
    query: str,
    limit: int,
    http: HttpClient,
    config: IngestConfig,
) -> list[ImportCandidate]:
    fetched: list[ImportCandidate] = []
    per_source = max(limit * config.overfetch_factor, limit)

    if config.enable_europepmc:
        fetched.extend(europepmc.search(query, per_source, http))
    if config.enable_semantic_scholar:
        fetched.extend(
            semantic_scholar.search(query, per_source, http, api_key=config.semantic_scholar_api_key)
        )
    if config.enable_arxiv:
        fetched.extend(arxiv.search(query, per_source, http))

    return merge_candidates(fetched)


def merge_candidates(candidates: list[ImportCandidate]) -> list[ImportCandidate]:
    """De-duplicate by DOI/title and sort by descending relevance."""

    seen: dict[str, ImportCandidate] = {}
    for candidate in candidates:
        key = dedupe_key(
            doi=candidate.doi, title=candidate.title, source_uri=candidate.source_uri
        )
        existing = seen.get(key)
        if existing is None or candidate.relevance > existing.relevance:
            seen[key] = candidate
    return sorted(seen.values(), key=lambda c: c.relevance, reverse=True)
