"""Budget guardrails and accounting."""

from __future__ import annotations

from dataclasses import dataclass

from contracts.study_spec import BudgetState


@dataclass(slots=True)
class BudgetEvent:
    study_id: str
    stage: str
    cost_usd: float
    reason: str = ""


class BudgetExceededError(RuntimeError):
    pass


class BudgetManager:
    def __init__(self, state: BudgetState) -> None:
        self.state = state

    def can_spend(self, study_id: str, stage: str, amount_usd: float) -> tuple[bool, str]:
        if amount_usd < 0:
            return False, "amount must be >= 0"
        projected_total = self.state.spent_total_usd + amount_usd
        if projected_total > self.state.max_total_usd:
            return False, "global budget exceeded"

        spent_study = self.state.spent_by_study_usd.get(study_id, 0.0)
        if spent_study + amount_usd > self.state.max_per_study_usd:
            return False, f"per-study budget exceeded ({study_id})"

        stage_cap = self.state.max_stage_usd.get(stage)
        if stage_cap is not None:
            stage_spent = self.state.spent_by_stage_usd.get(stage, 0.0)
            if stage_spent + amount_usd > stage_cap:
                return False, f"stage budget exceeded ({stage})"

        return True, "ok"

    def charge(self, event: BudgetEvent) -> None:
        allowed, reason = self.can_spend(event.study_id, event.stage, event.cost_usd)
        if not allowed:
            raise BudgetExceededError(
                f"cannot charge ${event.cost_usd:.4f} for {event.study_id}/{event.stage}: {reason}"
            )
        self.state.spent_total_usd += event.cost_usd
        self.state.spent_by_study_usd[event.study_id] = (
            self.state.spent_by_study_usd.get(event.study_id, 0.0) + event.cost_usd
        )
        self.state.spent_by_stage_usd[event.stage] = (
            self.state.spent_by_stage_usd.get(event.stage, 0.0) + event.cost_usd
        )
