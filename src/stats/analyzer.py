"""Statistical analysis for simulated replication outcomes."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import mean

from simulation.runner import TrialResult


@dataclass(slots=True)
class AnalysisResult:
    n_control: int
    n_treatment: int
    effect_size: float
    ci_low: float
    ci_high: float
    p_value: float
    replicated: bool


def _split_scores(results: list[TrialResult]) -> tuple[list[float], list[float]]:
    control = [r.response_score for r in results if r.arm == "control"]
    treatment = [r.response_score for r in results if r.arm == "treatment"]
    if not control or not treatment:
        raise ValueError("both control and treatment samples are required")
    return control, treatment


def effect_size(results: list[TrialResult]) -> float:
    control, treatment = _split_scores(results)
    return mean(treatment) - mean(control)


def bootstrap_ci(
    results: list[TrialResult],
    seed: int = 0,
    rounds: int = 300,
    alpha: float = 0.05,
) -> tuple[float, float]:
    rng = random.Random(seed)
    control, treatment = _split_scores(results)
    effects: list[float] = []
    for _ in range(rounds):
        c = [rng.choice(control) for _ in range(len(control))]
        t = [rng.choice(treatment) for _ in range(len(treatment))]
        effects.append(mean(t) - mean(c))
    effects.sort()
    low_idx = max(0, int((alpha / 2.0) * len(effects)) - 1)
    high_idx = min(len(effects) - 1, int((1 - alpha / 2.0) * len(effects)) - 1)
    return effects[low_idx], effects[high_idx]


def permutation_p_value(results: list[TrialResult], seed: int = 0, rounds: int = 500) -> float:
    rng = random.Random(seed)
    control, treatment = _split_scores(results)
    observed = abs(mean(treatment) - mean(control))
    pooled = control + treatment
    c_n = len(control)
    exceed = 0
    for _ in range(rounds):
        rng.shuffle(pooled)
        c = pooled[:c_n]
        t = pooled[c_n:]
        delta = abs(mean(t) - mean(c))
        if delta >= observed:
            exceed += 1
    return (exceed + 1) / (rounds + 1)


def analyze_results(results: list[TrialResult], alpha: float = 0.05, seed: int = 0) -> AnalysisResult:
    control, treatment = _split_scores(results)
    delta = mean(treatment) - mean(control)
    ci_low, ci_high = bootstrap_ci(results, seed=seed, rounds=400, alpha=alpha)
    p_value = permutation_p_value(results, seed=seed, rounds=600)
    replicated = (p_value <= alpha) and (not math.isclose(delta, 0.0, abs_tol=0.01))
    return AnalysisResult(
        n_control=len(control),
        n_treatment=len(treatment),
        effect_size=delta,
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=p_value,
        replicated=replicated,
    )
