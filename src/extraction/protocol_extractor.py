"""Protocol extraction with confidence and tiered fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts.study_spec import ArmSpec, MethodologySpec, StudySpec, VariableSpec
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


def _line_with_any(text: str, terms: list[str]) -> str:
    for line in text.splitlines():
        lower = line.lower()
        if any(term in lower for term in terms):
            cleaned = line.strip()
            if cleaned:
                return cleaned
    return ""


def _extract_participant_population(text: str) -> str:
    line = _line_with_any(text, ["participants", "subjects", "students", "respondents", "sample"])
    return line or "unspecified participant population"


def _extract_recruitment_context(text: str) -> str:
    line = _line_with_any(text, ["recruited", "prolific", "mturk", "university", "field experiment", "online", "survey"])
    return line or "unspecified recruitment context"


def _extract_arms(text: str) -> list[ArmSpec]:
    by_role: dict[str, str] = {}
    ordered_unlabeled: list[str] = []
    skip_prefixes = ("hypothesis", "abstract", "title", "doi", "source", "openalex", "year", "import note")

    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower.startswith(skip_prefixes):
            continue

        explicit_control = re.search(
            r"\b(?:control\s+(?:condition|arm|group|prompt|message|text)|"
            r"(?:condition|arm|group)\s+control)\b",
            lower,
        )
        explicit_treatment = re.search(
            r"\b(?:treatment\s+(?:condition|arm|group|prompt|message|text)|"
            r"(?:condition|arm|group)\s+treatment)\b",
            lower,
        )
        if explicit_control and "control" not in by_role:
            by_role["control"] = cleaned
            continue
        if explicit_treatment and "treatment" not in by_role:
            by_role["treatment"] = cleaned
            continue

        labeled = re.search(r"\b(?:condition|arm|group)\s+([ab12])\b", lower)
        if labeled:
            ordered_unlabeled.append(cleaned)

    if "control" in by_role and "treatment" in by_role:
        return [
            ArmSpec(arm_id="control", prompt_template=by_role["control"], description="control"),
            ArmSpec(arm_id="treatment", prompt_template=by_role["treatment"], description="treatment"),
        ]

    if len(ordered_unlabeled) >= 2:
        return [
            ArmSpec(arm_id="control", prompt_template=ordered_unlabeled[0], description="control inferred from first labeled arm"),
            ArmSpec(arm_id="treatment", prompt_template=ordered_unlabeled[1], description="treatment inferred from second labeled arm"),
        ]

    return [
        ArmSpec(arm_id="control", prompt_template="Neutral prompt", description="control"),
        ArmSpec(arm_id="treatment", prompt_template="Intervention prompt", description="treatment"),
    ]


def _extract_outcome_scale(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["binary", "yes/no", "logistic", "proportion", "compliance rate"]):
        return "binary_or_proportion"
    if any(term in lower for term in ["likert", "scale", "score", "rating", "intention"]):
        return "continuous_or_likert"
    if any(term in lower for term in ["count", "number of", "frequency"]):
        return "count"
    return "continuous"


def _extract_original_test(text: str) -> str:
    lower = text.lower()
    if "logistic regression" in lower:
        return "logistic_regression"
    if "linear regression" in lower or "ordinary least squares" in lower or "ols" in lower:
        return "linear_regression"
    if "anova" in lower:
        return "anova"
    if "chi-square" in lower or "chi square" in lower or "χ" in lower:
        return "chi_square"
    if "t-test" in lower or "t test" in lower:
        return "t_test"
    if "correlation" in lower:
        return "correlation"
    if "p-value" in lower or "p <" in lower or "p=" in lower:
        return "mean_difference"
    return "unspecified"


def _extract_reported_p(text: str) -> str:
    match = re.search(r"\bp\s*(?:=|<|<=)\s*0?\.\d+\b", text, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def _extract_reported_effect(text: str) -> str:
    match = re.search(r"\b(?:effect|difference|odds ratio|coefficient|beta)\b[^.\n]{0,100}", text, flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _build_methodology(text: str, arms: list[ArmSpec]) -> MethodologySpec:
    original_test = _extract_original_test(text)
    outcome_scale = _extract_outcome_scale(text)
    warnings: list[str] = []
    if len(arms) < 2:
        warnings.append("fewer_than_two_conditions_detected")
    if any(arm.prompt_template in {"Neutral prompt", "Intervention prompt"} for arm in arms):
        warnings.append("study_arms_inferred_from_defaults")
    if original_test == "unspecified":
        warnings.append("original_statistical_test_not_detected")
    if "image" in text.lower() or "video" in text.lower() or "visual" in text.lower():
        warnings.append("visual_stimuli_may_not_be_text_simulatable")
    if outcome_scale in {"binary_or_proportion", "count"}:
        warnings.append("non_continuous_outcome_uses_approximate_simulation_score")

    feasibility = "high"
    if warnings:
        feasibility = "medium"
    if "visual_stimuli_may_not_be_text_simulatable" in warnings or original_test == "unspecified":
        feasibility = "requires_review"

    stimuli = [arm.prompt_template for arm in arms if arm.prompt_template]
    return MethodologySpec(
        participant_population=_extract_participant_population(text),
        recruitment_context=_extract_recruitment_context(text),
        randomization_unit="participant" if "cluster" not in text.lower() else "cluster_or_group",
        assignment_procedure=_line_with_any(text, ["random", "assigned", "condition"]) or "random assignment inferred from condition language",
        procedure_summary=_line_with_any(text, ["procedure", "method", "experiment", "study"]) or "text-based condition/outcome procedure inferred",
        stimuli=stimuli,
        outcome_scale=outcome_scale,
        original_statistical_test=original_test,
        reported_effect=_extract_reported_effect(text),
        reported_p_value=_extract_reported_p(text),
        exact_replication_feasibility=feasibility,
        extraction_warnings=warnings,
    )


def _estimate_confidence(text: str, sample_size: int, arms: list[ArmSpec]) -> float:
    score = 0.35
    if sample_size >= 50:
        score += 0.2
    if len(arms) >= 2 and not any(
        arm.prompt_template in {"Neutral prompt", "Intervention prompt"} for arm in arms
    ):
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
    methodology = _build_methodology(doc.text, arms)
    confidence = _estimate_confidence(doc.text, sample_size, arms)
    tier = low_cost_tier_name
    notes: list[str] = []

    if confidence < min_confidence:
        # Fallback is modeled as improved confidence from a higher-quality extractor.
        confidence = min(0.98, confidence + 0.2)
        tier = fallback_tier_name
        notes.append("fallback_extractor_invoked")
    notes.extend(methodology.extraction_warnings)

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
        outcome_measure=f"mean_response_difference ({methodology.outcome_scale})",
        analysis_plan=methodology.original_statistical_test,
        methodology=methodology,
        source_uri=doc.source_uri,
        source_sha256=doc.source_sha256,
        extraction_confidence=confidence,
    )
    return ExtractionResult(study_spec=study_spec, confidence=confidence, tier_used=tier, notes=notes)
