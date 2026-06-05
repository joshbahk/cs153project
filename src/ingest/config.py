"""Environment-driven configuration for bulk import.

All knobs default to safe values so the import pipeline works out of the box
without any secrets. Setting a polite ``OPENALEX_MAILTO`` / ``UNPAYWALL_EMAIL``
and (optionally) a ``SEMANTIC_SCHOLAR_API_KEY`` materially improves both yield
and rate-limit headroom.

Multi-source *discovery* (Europe PMC / Semantic Scholar / arXiv) is opt-in via
``BULK_IMPORT_MULTISOURCE=1`` to keep default behavior and existing tests
deterministic. Full-text *resolution* across providers is on by default because
it only ever runs for candidates that are otherwise unsuitable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class IngestConfig:
    openalex_mailto: str = ""
    unpaywall_email: str = ""
    semantic_scholar_api_key: str = ""

    multisource_discovery: bool = False
    resolve_full_text: bool = True

    enable_openalex: bool = True
    enable_europepmc: bool = True
    enable_semantic_scholar: bool = True
    enable_arxiv: bool = True

    http_timeout: float = 25.0
    http_max_retries: int = 3
    min_host_interval: float = 0.15

    overfetch_factor: int = 4
    max_discovery_pages: int = 5
    max_resolution_bytes: int = 20 * 1024 * 1024
    min_full_text_chars: int = 1200

    def with_overrides(self, *, mailto: str = "") -> "IngestConfig":
        if not mailto:
            return self
        return IngestConfig(
            openalex_mailto=mailto or self.openalex_mailto,
            unpaywall_email=self.unpaywall_email or mailto,
            semantic_scholar_api_key=self.semantic_scholar_api_key,
            multisource_discovery=self.multisource_discovery,
            resolve_full_text=self.resolve_full_text,
            enable_openalex=self.enable_openalex,
            enable_europepmc=self.enable_europepmc,
            enable_semantic_scholar=self.enable_semantic_scholar,
            enable_arxiv=self.enable_arxiv,
            http_timeout=self.http_timeout,
            http_max_retries=self.http_max_retries,
            min_host_interval=self.min_host_interval,
            overfetch_factor=self.overfetch_factor,
            max_discovery_pages=self.max_discovery_pages,
            max_resolution_bytes=self.max_resolution_bytes,
            min_full_text_chars=self.min_full_text_chars,
        )


def get_ingest_config() -> IngestConfig:
    mailto = os.getenv("OPENALEX_MAILTO", "").strip()
    return IngestConfig(
        openalex_mailto=mailto,
        unpaywall_email=os.getenv("UNPAYWALL_EMAIL", "").strip() or mailto,
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip(),
        multisource_discovery=_flag("BULK_IMPORT_MULTISOURCE", False),
        resolve_full_text=_flag("BULK_IMPORT_RESOLVE", True),
        enable_openalex=_flag("BULK_IMPORT_ENABLE_OPENALEX", True),
        enable_europepmc=_flag("BULK_IMPORT_ENABLE_EUROPEPMC", True),
        enable_semantic_scholar=_flag("BULK_IMPORT_ENABLE_SEMANTIC_SCHOLAR", True),
        enable_arxiv=_flag("BULK_IMPORT_ENABLE_ARXIV", True),
        http_timeout=_float("BULK_IMPORT_HTTP_TIMEOUT", 25.0),
        http_max_retries=_int("BULK_IMPORT_HTTP_RETRIES", 3),
        min_host_interval=_float("BULK_IMPORT_MIN_HOST_INTERVAL", 0.15),
        overfetch_factor=_int("BULK_IMPORT_OVERFETCH_FACTOR", 4),
        max_discovery_pages=_int("BULK_IMPORT_MAX_PAGES", 5),
        max_resolution_bytes=_int("BULK_IMPORT_MAX_BYTES", 20 * 1024 * 1024),
        min_full_text_chars=_int("BULK_IMPORT_MIN_FULLTEXT_CHARS", 1200),
    )
