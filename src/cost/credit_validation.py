"""Startup-credit validation checks before execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from contracts.study_spec import CreditStatus


@dataclass(slots=True)
class CreditValidationResult:
    ok: bool
    messages: list[str]


def _is_expired(expires_at: str) -> bool:
    expires_dt = datetime.fromisoformat(expires_at)
    now = datetime.now(timezone.utc)
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    return expires_dt <= now


def validate_credit_statuses(statuses: list[CreditStatus]) -> CreditValidationResult:
    messages: list[str] = []
    if not statuses:
        return CreditValidationResult(False, ["no credit validation status provided"])

    for status in statuses:
        if not status.validated:
            messages.append(f"{status.provider}: not validated in billing dashboard")
        if _is_expired(status.expires_at):
            messages.append(f"{status.provider}: credits expired ({status.expires_at})")
        if not status.eligible_products:
            messages.append(f"{status.provider}: eligible_products cannot be empty")

    return CreditValidationResult(ok=not messages, messages=messages)
