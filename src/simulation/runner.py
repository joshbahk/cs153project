"""Execution engine for simulation and sequential sampling."""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean
from typing import Callable

from contracts.study_spec import StudySpec
from simulation.agent_factory import SyntheticAgent


@dataclass(slots=True)
class TrialResult:
    study_id: str
    arm: str
    agent_id: str
    response_score: float
    scenario: str = "primary"


@dataclass(slots=True)
class SimulationBatch:
    results: list[TrialResult]
    sampled_n: int
    effect_estimate: float


class SimulationRunner:
    def __init__(self, seed: int, treatment_shift: float = 3.0, scenario: str = "primary") -> None:
        self._rng = random.Random(seed)
        self._treatment_shift = treatment_shift
        self._scenario = scenario

    def _assign_arm(self, spec: StudySpec) -> str:
        return self._rng.choice([arm.arm_id for arm in spec.arms])

    def _simulate_response(self, agent: SyntheticAgent, arm_id: str) -> float:
        base = 50.0 + 8.0 * agent.latent_trait
        treatment_shift = self._treatment_shift if arm_id == "treatment" else 0.0
        demographic_shift = {
            "high_school": -1.0,
            "college": 0.0,
            "graduate": 1.0,
        }.get(agent.education, 0.0)
        context_shift = (
            3.0 * (agent.baseline_compliance - 0.5)
            + 2.0 * (agent.prosociality - 0.5)
            - 1.5 * (agent.risk_preference - 0.5)
        )
        orientation_shift = {
            "left": 0.4,
            "center": 0.0,
            "right": -0.2,
        }.get(agent.political_orientation, 0.0)
        noise = self._rng.gauss(0.0, 6.5)
        return base + treatment_shift + demographic_shift + context_shift + orientation_shift + noise

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
                    scenario=self._scenario,
                )
            )
            by_arm.setdefault(arm_id, []).append(response)
        control_scores = by_arm.get("control", [])
        treatment_scores = by_arm.get("treatment", [])
        control_mean = mean(control_scores) if control_scores else 0.0
        treatment_mean = mean(treatment_scores) if treatment_scores else 0.0
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
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[TrialResult]:
        all_results: list[TrialResult] = []
        consumed = 0
        remaining = list(agents)
        if progress_callback is not None:
            progress_callback(consumed, max_n)
        while consumed < max_n and remaining:
            batch_n = min(batch_size, max_n - consumed, len(remaining))
            batch = self.run_batch(spec, remaining, batch_size=batch_n)
            used_agent_ids = {row.agent_id for row in batch.results}
            remaining = [agent for agent in remaining if agent.agent_id not in used_agent_ids]
            all_results.extend(batch.results)
            consumed += batch.sampled_n
            if progress_callback is not None:
                progress_callback(consumed, max_n)
            if consumed >= batch_size * 2:
                low, high = ci_fn(all_results)
                if (high - low) <= stop_width_threshold:
                    break
        return all_results
