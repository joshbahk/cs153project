"""Stratified synthetic participant generation from transparent demographic priors."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SyntheticAgent:
    agent_id: str
    age_bucket: str
    gender: str
    education: str
    income_bucket: str
    political_orientation: str
    region: str
    baseline_compliance: float
    prosociality: float
    risk_preference: float
    latent_trait: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "age_bucket": self.age_bucket,
            "gender": self.gender,
            "education": self.education,
            "income_bucket": self.income_bucket,
            "political_orientation": self.political_orientation,
            "region": self.region,
            "baseline_compliance": self.baseline_compliance,
            "prosociality": self.prosociality,
            "risk_preference": self.risk_preference,
            "latent_trait": self.latent_trait,
        }


US_ADULT_PRIORS: dict[str, list[tuple[str, float]]] = {
    "age_bucket": [("18-29", 0.22), ("30-44", 0.28), ("45-60", 0.25), ("60+", 0.25)],
    "gender": [("female", 0.50), ("male", 0.49), ("nonbinary", 0.01)],
    "education": [("high_school", 0.38), ("college", 0.41), ("graduate", 0.21)],
    "income_bucket": [("low", 0.30), ("middle", 0.48), ("high", 0.22)],
    "political_orientation": [("left", 0.34), ("center", 0.32), ("right", 0.34)],
    "region": [("urban", 0.34), ("suburban", 0.46), ("rural", 0.20)],
}

COLLEGE_STUDENT_PRIORS: dict[str, list[tuple[str, float]]] = {
    "age_bucket": [("18-29", 0.94), ("30-44", 0.05), ("45-60", 0.01), ("60+", 0.0)],
    "gender": [("female", 0.52), ("male", 0.46), ("nonbinary", 0.02)],
    "education": [("high_school", 0.03), ("college", 0.87), ("graduate", 0.10)],
    "income_bucket": [("low", 0.44), ("middle", 0.42), ("high", 0.14)],
    "political_orientation": [("left", 0.48), ("center", 0.32), ("right", 0.20)],
    "region": [("urban", 0.45), ("suburban", 0.45), ("rural", 0.10)],
}


def _expand_strata(priors: dict[str, list[tuple[str, float]]], field: str, count: int) -> list[str]:
    weighted = priors[field]
    values: list[str] = []
    running = 0
    for idx, (label, weight) in enumerate(weighted):
        if idx == len(weighted) - 1:
            n = count - running
        else:
            n = int(round(count * weight))
            running += n
        values.extend([label] * max(0, n))
    return values[:count] + [weighted[-1][0]] * max(0, count - len(values))


def _priors_for_population(population_hint: str) -> dict[str, list[tuple[str, float]]]:
    lower = population_hint.lower()
    if "student" in lower or "university" in lower or "college" in lower:
        return COLLEGE_STUDENT_PRIORS
    return US_ADULT_PRIORS


class AgentFactory:
    def __init__(self, seed: int, population_hint: str = "us adult sample") -> None:
        self._rng = random.Random(seed)
        self._priors = _priors_for_population(population_hint)

    def build(self, count: int) -> list[SyntheticAgent]:
        strata = {
            field: _expand_strata(self._priors, field, count)
            for field in ["age_bucket", "gender", "education", "income_bucket", "political_orientation", "region"]
        }
        for values in strata.values():
            self._rng.shuffle(values)

        agents: list[SyntheticAgent] = []
        for i in range(count):
            baseline_compliance = min(1.0, max(0.0, self._rng.betavariate(5, 3)))
            prosociality = min(1.0, max(0.0, self._rng.betavariate(4, 4)))
            risk_preference = min(1.0, max(0.0, self._rng.betavariate(2.5, 4)))
            latent_trait = (
                self._rng.gauss(0.0, 0.75)
                + (baseline_compliance - 0.5)
                + (prosociality - 0.5) * 0.8
                - (risk_preference - 0.5) * 0.4
            )
            agents.append(
                SyntheticAgent(
                    agent_id=f"agent_{i:05d}",
                    age_bucket=strata["age_bucket"][i],
                    gender=strata["gender"][i],
                    education=strata["education"][i],
                    income_bucket=strata["income_bucket"][i],
                    political_orientation=strata["political_orientation"][i],
                    region=strata["region"][i],
                    baseline_compliance=baseline_compliance,
                    prosociality=prosociality,
                    risk_preference=risk_preference,
                    latent_trait=latent_trait,
                )
            )
        return agents
