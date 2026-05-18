"""Execution engine for simulation and sequential sampling."""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean

from contracts.study_spec import StudySpec
from simulation.agent_factory import SyntheticAgent


@dataclass(slots=True)
class TrialResult:
    study_id: str
    arm: str
    agent_id: str
    response_score: float


@dataclass(slots=True)
class SimulationBatch:
    results: list[TrialResult]
    sampled_n: int
    effect_estimate: float


class SimulationRunner:
    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def _assign_arm(self, spec: StudySpec) -> str:
        return self._rng.choice([arm.arm_id for arm in spec.arms])

    def _simulate_response(self, agent: SyntheticAgent, arm_id: str) -> float:
        base = 50.0 + 8.0 * agent.latent_trait
        treatment_shift = 3.0 if arm_id == "treatment" else 0.0
        demographic_shift = {
            "high_school": -1.0,
            "college": 0.0,
            "graduate": 1.0,
        }.get(agent.education, 0.0)
        noise = self._rng.gauss(0.0, 6.5)
        return base + treatment_shift + demographic_shift + noise

    def run_batch(self, spec: StudySpec, agents: list[SyntheticAgent], batch_size: int) -> SimulationBatch:
        picked = self._rng.sample(agents, k=min(batch_size, len(agents)))
        results: list[TrialResult] = []
        by_arm: dict[str, list[float]] = {"control": [], "treatment": []}
        for agent in picked:
            arm_id = self._assign_arm(spec)
            response = self._simulate_response(agent, arm_id)
            results.append(
                TrialResult(
                    study_id=spec.study_id,
                    arm=arm_id,
                    agent_id=agent.agent_id,
                    response_score=response,
                )
            )
            by_arm.setdefault(arm_id, []).append(response)
        control_mean = mean(by_arm.get("control", [0.0]))
        treatment_mean = mean(by_arm.get("treatment", [0.0]))
        return SimulationBatch(
            results=results,
            sampled_n=len(picked),
            effect_estimate=treatment_mean - control_mean,
        )

    def run_sequential(
        self,
        spec: StudySpec,
        agents: list[SyntheticAgent],
        batch_size: int,
        max_n: int,
        stop_width_threshold: float,
        ci_fn,
    ) -> list[TrialResult]:
        all_results: list[TrialResult] = []
        consumed = 0
        while consumed < max_n:
            batch = self.run_batch(spec, agents, batch_size=batch_size)
            all_results.extend(batch.results)
            consumed += batch.sampled_n
            if consumed >= batch_size * 2:
                low, high = ci_fn(all_results)
                if (high - low) <= stop_width_threshold:
                    break
        return all_results
