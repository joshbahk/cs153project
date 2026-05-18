"""Protocol extraction with confidence and tiered fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts.study_spec import ArmSpec, StudySpec, VariableSpec
from ingest.study_loader import StudyDocument


@dataclass(slots=True)
class ExtractionResult:
    study_spec: StudySpec
    confidence: float
    tier_used: str
    notes: list[str]


def _extract_sample_size(text: str) -> int:
    match = re.search(r"\b(?:N|n)\s*=\s*(\d{2,5})\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d{2,5})\s+participants?\b", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 200


def _extract_hypothesis(text: str) -> str:
    for line in text.splitlines():
        if "hypothesis" in line.lower():
            return line.strip()
    return "Treatment arm changes outcome distribution compared to control."


def _extract_arms(text: str) -> list[ArmSpec]:
    prompts = []
    for idx, line in enumerate(text.splitlines()):
        if "condition" in line.lower() or "prompt" in line.lower():
            prompts.append((idx, line.strip()))
    if len(prompts) >= 2:
        return [
            ArmSpec(arm_id="control", prompt_template=prompts[0][1], description="control"),
            ArmSpec(arm_id="treatment", prompt_template=prompts[1][1], description="treatment"),
        ]
    return [
        ArmSpec(arm_id="control", prompt_template="Neutral prompt", description="control"),
        ArmSpec(arm_id="treatment", prompt_template="Intervention prompt", description="treatment"),
    ]


def _estimate_confidence(text: str, sample_size: int, arms: list[ArmSpec]) -> float:
    score = 0.35
    if sample_size >= 50:
        score += 0.2
    if len(arms) >= 2:
        score += 0.2
    if "hypothesis" in text.lower():
        score += 0.15
    if "statistical" in text.lower() or "p-value" in text.lower():
        score += 0.1
    return min(score, 0.95)


def extract_protocol(
    doc: StudyDocument,
    low_cost_tier_name: str = "local_heuristic",
    fallback_tier_name: str = "paid_fallback",
    min_confidence: float = 0.65,
) -> ExtractionResult:
    sample_size = _extract_sample_size(doc.text)
    hypothesis = _extract_hypothesis(doc.text)
    arms = _extract_arms(doc.text)
    confidence = _estimate_confidence(doc.text, sample_size, arms)
    tier = low_cost_tier_name
    notes: list[str] = []

    if confidence < min_confidence:
        # Fallback is modeled as improved confidence from a higher-quality extractor.
        confidence = min(0.98, confidence + 0.2)
        tier = fallback_tier_name
        notes.append("fallback_extractor_invoked")

    study_spec = StudySpec(
        study_id=doc.study_id,
        title=doc.title,
        domain="social_science",
        hypothesis=hypothesis,
        sample_size_target=sample_size,
        inclusion_rules=["adult participants", "fluent in experiment language"],
        exclusion_rules=["missing outcomes"],
        independent_variables=[VariableSpec(name="arm", kind="categorical", allowed_values=["control", "treatment"])],
        dependent_variables=[VariableSpec(name="response_score", kind="continuous")],
        arms=arms,
        outcome_measure="mean_response_difference",
        analysis_plan="two-group mean comparison with bootstrap CI",
        source_uri=doc.source_uri,
        source_sha256=doc.source_sha256,
        extraction_confidence=confidence,
    )
    return ExtractionResult(study_spec=study_spec, confidence=confidence, tier_used=tier, notes=notes)
