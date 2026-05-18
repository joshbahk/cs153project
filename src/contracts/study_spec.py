"""Contracts for study replication runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class VariableSpec:
    name: str
    kind: str
    allowed_values: list[str] = field(default_factory=list)
    description: str = ""


@dataclass(slots=True)
class ArmSpec:
    arm_id: str
    prompt_template: str
    description: str = ""


@dataclass(slots=True)
class StudySpec:
    """Executable representation of one text-based study."""

    study_id: str
    title: str
    domain: str
    hypothesis: str
    sample_size_target: int
    inclusion_rules: list[str]
    exclusion_rules: list[str]
    independent_variables: list[VariableSpec]
    dependent_variables: list[VariableSpec]
    arms: list[ArmSpec]
    outcome_measure: str
    analysis_plan: str
    source_uri: str = ""
    source_sha256: str = ""
    extraction_confidence: float = 0.0

    def validate(self) -> None:
        if not self.study_id:
            raise ValueError("study_id is required")
        if self.sample_size_target <= 0:
            raise ValueError("sample_size_target must be > 0")
        if not self.arms:
            raise ValueError("at least one arm is required")
        if not 0.0 <= self.extraction_confidence <= 1.0:
            raise ValueError("extraction_confidence must be within [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StudySpec":
        ivs = [VariableSpec(**item) for item in raw.get("independent_variables", [])]
        dvs = [VariableSpec(**item) for item in raw.get("dependent_variables", [])]
        arms = [ArmSpec(**item) for item in raw.get("arms", [])]
        spec = cls(
            study_id=raw["study_id"],
            title=raw["title"],
            domain=raw.get("domain", "unknown"),
            hypothesis=raw["hypothesis"],
            sample_size_target=int(raw["sample_size_target"]),
            inclusion_rules=list(raw.get("inclusion_rules", [])),
            exclusion_rules=list(raw.get("exclusion_rules", [])),
            independent_variables=ivs,
            dependent_variables=dvs,
            arms=arms,
            outcome_measure=raw["outcome_measure"],
            analysis_plan=raw["analysis_plan"],
            source_uri=raw.get("source_uri", ""),
            source_sha256=raw.get("source_sha256", ""),
            extraction_confidence=float(raw.get("extraction_confidence", 0.0)),
        )
        spec.validate()
        return spec


@dataclass(slots=True)
class BudgetState:
    max_total_usd: float
    max_per_study_usd: float
    max_stage_usd: dict[str, float]
    spent_total_usd: float = 0.0
    spent_by_study_usd: dict[str, float] = field(default_factory=dict)
    spent_by_stage_usd: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CreditStatus:
    provider: str
    validated: bool
    expires_at: str
    eligible_products: list[str]
    blocked_products: list[str]
    notes: str = ""


@dataclass(slots=True)
class RunManifest:
    run_id: str
    created_at: str
    git_commit: str
    random_seed: int
    model_tier: str
    study_ids: list[str]
    budget: BudgetState
    credit_status: list[CreditStatus]
    artifacts: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        run_id: str,
        git_commit: str,
        random_seed: int,
        model_tier: str,
        study_ids: list[str],
        budget: BudgetState,
        credit_status: list[CreditStatus],
    ) -> "RunManifest":
        return cls(
            run_id=run_id,
            created_at=_utc_now_iso(),
            git_commit=git_commit,
            random_seed=random_seed,
            model_tier=model_tier,
            study_ids=study_ids,
            budget=budget,
            credit_status=credit_status,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
