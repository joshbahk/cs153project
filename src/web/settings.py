"""Runtime settings for the dashboard and worker."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class AppSettings:
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL"))
    budget_path: Path = field(default_factory=lambda: Path(os.getenv("BUDGET_PATH", "configs/budget.json")))
    credits_path: Path = field(default_factory=lambda: Path(os.getenv("CREDITS_PATH", "configs/credits.demo.json")))
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("CACHE_DIR", "artifacts/cache")))
    max_total_usd: float = field(default_factory=lambda: float(os.getenv("MAX_TOTAL_USD", "250")))
    max_sample_size: int = field(default_factory=lambda: int(os.getenv("MAX_SAMPLE_SIZE", "500")))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "15")))
    max_text_chars: int = field(default_factory=lambda: int(os.getenv("MAX_TEXT_CHARS", "120000")))
    max_queued_jobs: int = field(default_factory=lambda: int(os.getenv("MAX_QUEUED_JOBS", "50")))
    max_batch_import: int = field(default_factory=lambda: int(os.getenv("MAX_BATCH_IMPORT", "50")))
    openalex_mailto: str = field(default_factory=lambda: os.getenv("OPENALEX_MAILTO", ""))
    seed: int = field(default_factory=lambda: int(os.getenv("RUN_SEED", "153")))
    auto_process_on_submit: bool = field(default_factory=lambda: _bool_env("AUTO_PROCESS_ON_SUBMIT", True))
    require_credit_validation: bool = field(default_factory=lambda: _bool_env("REQUIRE_CREDIT_VALIDATION", True))
    worker_poll_seconds: float = field(default_factory=lambda: float(os.getenv("WORKER_POLL_SECONDS", "2")))
    llm_simulation_enabled: bool = field(default_factory=lambda: _bool_env("LLM_SIMULATION_ENABLED", False))
    llm_api_key: str = field(
        default_factory=lambda: (
            os.getenv("GRADIENT_MODEL_ACCESS_KEY")
            or os.getenv("DIGITALOCEAN_INFERENCE_KEY")
            or os.getenv("DIGITALOCEAN_TOKEN")
            or os.getenv("DIGITALOCEAN_ACCESS_TOKEN")
            or os.getenv("LLM_API_KEY")
            or ""
        )
    )
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://inference.do-ai.run"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "llama3.3-70b-instruct"))
    llm_sample_size: int = field(default_factory=lambda: int(os.getenv("LLM_SAMPLE_SIZE", "40")))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.2")))
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "180")))
    llm_max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))
    llm_primary_only: bool = field(default_factory=lambda: _bool_env("LLM_PRIMARY_ONLY", True))
    llm_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))
    llm_estimated_input_tokens_per_trial: int = field(
        default_factory=lambda: int(os.getenv("LLM_ESTIMATED_INPUT_TOKENS", "900"))
    )
    llm_estimated_output_tokens_per_trial: int = field(
        default_factory=lambda: int(os.getenv("LLM_ESTIMATED_OUTPUT_TOKENS", "160"))
    )
    llm_input_cost_per_1m_tokens: float = field(
        default_factory=lambda: float(os.getenv("LLM_INPUT_COST_PER_1M", "0.50"))
    )
    llm_output_cost_per_1m_tokens: float = field(
        default_factory=lambda: float(os.getenv("LLM_OUTPUT_COST_PER_1M", "0.50"))
    )


def get_settings() -> AppSettings:
    return AppSettings()
