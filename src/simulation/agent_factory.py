"""Synthetic participant generation from demographic priors."""

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
    region: str
    latent_trait: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "age_bucket": self.age_bucket,
            "gender": self.gender,
            "education": self.education,
            "region": self.region,
            "latent_trait": self.latent_trait,
        }


class AgentFactory:
    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def build(self, count: int) -> list[SyntheticAgent]:
        agents: list[SyntheticAgent] = []
        for i in range(count):
            agent = SyntheticAgent(
                agent_id=f"agent_{i:05d}",
                age_bucket=self._rng.choices(["18-29", "30-44", "45-60", "60+"], weights=[0.28, 0.31, 0.25, 0.16], k=1)[0],
                gender=self._rng.choices(["female", "male", "nonbinary"], weights=[0.49, 0.48, 0.03], k=1)[0],
                education=self._rng.choices(
                    ["high_school", "college", "graduate"],
                    weights=[0.34, 0.45, 0.21],
                    k=1,
                )[0],
                region=self._rng.choices(["urban", "suburban", "rural"], weights=[0.41, 0.38, 0.21], k=1)[0],
                latent_trait=self._rng.gauss(0.0, 1.0),
            )
            agents.append(agent)
        return agents
