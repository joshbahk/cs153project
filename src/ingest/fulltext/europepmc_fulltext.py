"""Europe PMC open-access full-text retrieval.

Europe PMC exposes machine-readable full text for open-access articles via a
PMCID. We can also resolve a PMCID from a DOI through its search endpoint.
"""

from __future__ import annotations

import re

from ingest.http import HttpClient, HttpError

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def pmcid_from_doi(doi: str, http: HttpClient) -> str:
    doi = _normalize_doi(doi)
    if not doi:
        return ""
    try:
        payload = http.get_json(
            f"{_BASE}/search",
            params={"query": f'DOI:"{doi}"', "format": "json", "pageSize": "1", "resultType": "core"},
        )
    except (HttpError, ValueError):
        return ""
    results = (((payload or {}).get("resultList") or {}).get("result")) or []
    for result in results:
        pmcid = result.get("pmcid")
        if pmcid:
            return str(pmcid)
    return ""


def fetch_full_text(pmcid: str, http: HttpClient, *, max_bytes: int, max_chars: int) -> str:
    pmcid = (pmcid or "").strip()
    if not pmcid:
        return ""
    try:
        response = http.get_text(f"{_BASE}/{pmcid}/fullTextXML", max_bytes=max_bytes)
    except HttpError:
        return ""
    return _xml_to_text(response.text)[:max_chars]


def _xml_to_text(xml: str) -> str:
    if not xml:
        return ""
    # Prefer the <body> section; fall back to the whole document.
    body_match = re.search(r"<body[^>]*>(.*?)</body>", xml, flags=re.DOTALL | re.IGNORECASE)
    fragment = body_match.group(1) if body_match else xml
    # Drop tables/figures graphics references but keep textual content.
    fragment = re.sub(r"<(xref|graphic|inline-formula|disp-formula)[^>]*?/>", " ", fragment)
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    lines = [line.strip() for line in re.split(r"\n+", text)]
    collapsed = "\n".join(" ".join(line.split()) for line in lines if line.strip())
    return collapsed.strip()


def _normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi.strip()
