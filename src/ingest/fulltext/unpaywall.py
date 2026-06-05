"""Unpaywall lookup: DOI -> best open-access PDF/HTML location.

Unpaywall requires a contact email. When no email is configured the lookup is
skipped (returns ``None``) rather than calling the API anonymously.
"""

from __future__ import annotations

from ingest.http import HttpClient, HttpError


def best_oa_locations(doi: str, email: str, http: HttpClient) -> tuple[str, str]:
    """Return ``(pdf_url, html_url)`` for a DOI, empty strings when unavailable."""

    doi = _normalize_doi(doi)
    if not doi or not email:
        return "", ""
    url = f"https://api.unpaywall.org/v2/{doi}"
    try:
        payload = http.get_json(url, params={"email": email})
    except (HttpError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""

    locations = []
    best = payload.get("best_oa_location")
    if isinstance(best, dict):
        locations.append(best)
    extra = payload.get("oa_locations")
    if isinstance(extra, list):
        locations.extend(loc for loc in extra if isinstance(loc, dict))

    pdf_url = ""
    html_url = ""
    for loc in locations:
        if not pdf_url and loc.get("url_for_pdf"):
            pdf_url = str(loc["url_for_pdf"])
        if not html_url and loc.get("url"):
            html_url = str(loc["url"])
    return pdf_url, html_url


def _normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi.strip()
