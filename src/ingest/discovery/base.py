"""Shared helpers for discovery adapters."""

from __future__ import annotations

import re

_STOPWORDS = {"with", "from", "that", "this", "and", "the", "for", "study", "paper", "effect", "effects"}
_METHOD_TERMS = (
    "field experiment",
    "randomized",
    "randomised",
    "experiment",
    "control",
    "treatment",
    "participants",
    "condition",
    "trial",
)


def normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi.strip().lower()


def dedupe_key(*, doi: str = "", title: str = "", source_uri: str = "") -> str:
    norm_doi = normalize_doi(doi)
    if norm_doi:
        return f"doi:{norm_doi}"
    norm_title = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    if norm_title:
        return f"title:{norm_title}"
    return f"uri:{(source_uri or '').strip().lower()}"


def relevance_score(query: str, title: str, abstract: str) -> float:
    text = f"{title} {abstract}".lower()
    title_text = (title or "").lower()
    tokens = [tok for tok in re.findall(r"[a-z]{4,}", query.lower()) if tok not in _STOPWORDS]
    score = 0.0
    for token in tokens:
        if token in title_text:
            score += 4.0
        if token in text:
            score += 1.0
    for term in _METHOD_TERMS:
        if term in text:
            score += 2.0
    return score
