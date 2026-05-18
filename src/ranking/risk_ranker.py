"""Rank studies by replication risk and uncertainty."""

from __future__ import annotations

from dataclasses import dataclass

from stats.analyzer import AnalysisResult


@dataclass(slots=True)
class StudyRisk:
    study_id: str
    replication_risk: float
    uncertainty: float
    rationale: str


def _risk_from_analysis(analysis: AnalysisResult) -> tuple[float, float]:
    ci_width = analysis.ci_high - analysis.ci_low
    p_component = min(1.0, analysis.p_value / 0.10)
    magnitude_component = 1.0 if abs(analysis.effect_size) < 0.5 else 0.5
    uncertainty = min(1.0, ci_width / 8.0)
    risk = min(1.0, 0.5 * p_component + 0.3 * magnitude_component + 0.2 * uncertainty)
    return risk, uncertainty


def rank_replication_risk(results: dict[str, AnalysisResult]) -> list[StudyRisk]:
    ranked: list[StudyRisk] = []
    for study_id, analysis in results.items():
        risk, uncertainty = _risk_from_analysis(analysis)
        rationale = (
            f"p={analysis.p_value:.4f}, effect={analysis.effect_size:.3f}, "
            f"CI=[{analysis.ci_low:.3f}, {analysis.ci_high:.3f}]"
        )
        ranked.append(
            StudyRisk(
                study_id=study_id,
                replication_risk=risk,
                uncertainty=uncertainty,
                rationale=rationale,
            )
        )
    ranked.sort(key=lambda item: item.replication_risk, reverse=True)
    return ranked
