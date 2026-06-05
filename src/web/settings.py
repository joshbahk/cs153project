"""Runtime settings for the dashboard and worker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class AppSettings:
    database_url: str | None = os.getenv("DATABASE_URL")
    budget_path: Path = Path(os.getenv("BUDGET_PATH", "configs/budget.json"))
    credits_path: Path = Path(os.getenv("CREDITS_PATH", "configs/credits.demo.json"))
    cache_dir: Path = Path(os.getenv("CACHE_DIR", "artifacts/cache"))
    max_total_usd: float = float(os.getenv("MAX_TOTAL_USD", "250"))
    max_sample_size: int = int(os.getenv("MAX_SAMPLE_SIZE", "500"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "15"))
    max_text_chars: int = int(os.getenv("MAX_TEXT_CHARS", "120000"))
    max_queued_jobs: int = int(os.getenv("MAX_QUEUED_JOBS", "3"))
    seed: int = int(os.getenv("RUN_SEED", "153"))
    auto_process_on_submit: bool = _bool_env("AUTO_PROCESS_ON_SUBMIT", True)
    require_credit_validation: bool = _bool_env("REQUIRE_CREDIT_VALIDATION", True)
    worker_poll_seconds: float = float(os.getenv("WORKER_POLL_SECONDS", "2"))


def get_settings() -> AppSettings:
    return AppSettings()
