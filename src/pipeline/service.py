"""Shared pipeline execution for CLI, web workers, and tests."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from contracts.study_spec import BudgetState, CreditStatus, RunManifest, StudySpec
from cost.budget_manager import BudgetEvent, BudgetManager
from cost.credit_validation import validate_credit_statuses
from extraction.protocol_extractor import extract_protocol
from ingest.study_loader import StudyDocument, load_study_document
from ranking.risk_ranker import rank_replication_risk
from simulation.agent_factory import AgentFactory
from simulation.runner import SimulationRunner, TrialResult
from stats.analyzer import AnalysisResult, analyze_results, bootstrap_ci
from storage.cache import JsonCache


@dataclass(slots=True)
class PipelineConfig:
    seed: int = 153
    min_confidence: float = 0.65
    batch_size: int = 50
    stop_width_threshold: float = 2.5
    max_sample_size: int = 500
    cost_extract: float = 0.04
    cost_per_trial: float = 0.002
    cost_analysis: float = 0.10
    cost_reporting: float = 0.20
    model_tier: str = "digitalocean_cpu_transparent_simulator"
    sensitivity_shifts: dict[str, float] = field(
        default_factory=lambda: {
            "primary": 3.0,
            "small_effect": 1.0,
            "null_effect": 0.0,
        }
    )


@dataclass(slots=True)
class PipelineOutput:
    manifest: dict[str, Any]
    extraction: dict[str, Any]
    analysis: dict[str, Any]
    ranking: dict[str, Any]
    trials: dict[str, list[dict[str, Any]]]

    def artifacts(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "extraction": self.extraction,
            "analysis": self.analysis,
            "ranking": self.ranking,
            "trials": self.trials,
        }


def git_commit_short() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True)
        return out.strip()
    except Exception:
        return "unknown"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_credits(path: Path) -> list[CreditStatus]:
    data = load_json(path)
    return [
        CreditStatus(
            provider=item["provider"],
            validated=bool(item["validated"]),
            expires_at=item["expires_at"],
            eligible_products=list(item.get("eligible_products", [])),
            blocked_products=list(item.get("blocked_products", [])),
            notes=item.get("notes", ""),
        )
        for item in data.get("providers", [])
    ]


def load_budget(path: Path) -> BudgetState:
    data = load_json(path)
    return BudgetState(
        max_total_usd=float(data["max_total_usd"]),
        max_per_study_usd=float(data["max_per_study_usd"]),
        max_stage_usd={k: float(v) for k, v in data.get("max_stage_usd", {}).items()},
    )


def save_artifacts(out_dir: Path, output: PipelineOutput) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in output.artifacts().items():
        (out_dir / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _analysis_to_dict(result: AnalysisResult) -> dict[str, Any]:
    return {
        "n_control": result.n_control,
        "n_treatment": result.n_treatment,
        "effect_size": result.effect_size,
        "ci_low": result.ci_low,
        "ci_high": result.ci_high,
        "p_value": result.p_value,
        "replicated": result.replicated,
    }


def _cap_sample_size(spec: StudySpec, max_sample_size: int) -> StudySpec:
    if spec.sample_size_target <= max_sample_size:
        return spec
    spec.sample_size_target = max_sample_size
    return spec


def _run_one_scenario(
    spec: StudySpec,
    seed: int,
    batch_size: int,
    stop_width_threshold: float,
    scenario: str,
    treatment_shift: float,
) -> tuple[list[TrialResult], AnalysisResult]:
    agents = AgentFactory(seed=seed).build(spec.sample_size_target)
    runner = SimulationRunner(seed=seed + 1, treatment_shift=treatment_shift, scenario=scenario)
    results = runner.run_sequential(
        spec=spec,
        agents=agents,
        batch_size=batch_size,
        max_n=spec.sample_size_target,
        stop_width_threshold=stop_width_threshold,
        ci_fn=lambda rows: bootstrap_ci(rows, seed=seed + 2, rounds=150),
    )
    return results, analyze_results(results, seed=seed + 3)


def execute_documents(
    docs: list[StudyDocument],
    run_id: str,
    budget_state: BudgetState,
    credits: list[CreditStatus],
    cache: JsonCache | None = None,
    config: PipelineConfig | None = None,
    require_credit_validation: bool = True,
) -> PipelineOutput:
    if not docs:
        raise ValueError("at least one study document is required")

    config = config or PipelineConfig()
    if require_credit_validation:
        credit_check = validate_credit_statuses(credits)
        if not credit_check.ok:
            raise ValueError("credit validation failed: " + "; ".join(credit_check.messages))

    budget = BudgetManager(budget_state)
    studies: list[StudySpec] = []
    extraction_items: list[dict[str, Any]] = []
    for doc in docs:
        cached = None
        cache_key = f"extract::{doc.study_id}::{doc.source_sha256}"
        if cache is not None:
            cached = cache.get(cache_key)

        if cached:
            spec = StudySpec.from_dict(cached["study_spec"])
            tier_used = cached["tier_used"]
            confidence = cached["confidence"]
            notes = cached.get("notes", [])
        else:
            extraction = extract_protocol(doc, min_confidence=config.min_confidence)
            spec = extraction.study_spec
            tier_used = extraction.tier_used
            confidence = extraction.confidence
            notes = extraction.notes
            if cache is not None:
                cache.set(
                    cache_key,
                    {
                        "study_spec": spec.to_dict(),
                        "tier_used": tier_used,
                        "confidence": confidence,
                        "notes": notes,
                    },
                )

        spec = _cap_sample_size(spec, config.max_sample_size)
        budget.charge(BudgetEvent(study_id=doc.study_id, stage="extraction", cost_usd=config.cost_extract))
        studies.append(spec)
        extraction_items.append(
            {
                "study_id": doc.study_id,
                "tier_used": tier_used,
                "confidence": confidence,
                "notes": notes,
                "sample_size_target": spec.sample_size_target,
                "source_sha256": doc.source_sha256,
            }
        )

    analysis_by_study: dict[str, AnalysisResult] = {}
    analysis_payload: dict[str, Any] = {}
    all_trials: dict[str, list[dict[str, Any]]] = {}

    for idx, spec in enumerate(studies):
        scenario_payload: dict[str, Any] = {}
        primary_trials: list[TrialResult] = []
        for scenario_idx, (scenario, shift) in enumerate(config.sensitivity_shifts.items()):
            scenario_seed = config.seed + (idx * 1000) + (scenario_idx * 100)
            trials, analyzed = _run_one_scenario(
                spec=spec,
                seed=scenario_seed,
                batch_size=config.batch_size,
                stop_width_threshold=config.stop_width_threshold,
                scenario=scenario,
                treatment_shift=shift,
            )
            for _ in trials:
                budget.charge(
                    BudgetEvent(
                        study_id=spec.study_id,
                        stage="simulation",
                        cost_usd=config.cost_per_trial,
                        reason=scenario,
                    )
                )
            budget.charge(BudgetEvent(study_id=spec.study_id, stage="analysis", cost_usd=config.cost_analysis))
            scenario_payload[scenario] = {
                **_analysis_to_dict(analyzed),
                "treatment_shift": shift,
            }
            if scenario == "primary":
                analysis_by_study[spec.study_id] = analyzed
                primary_trials = trials

        analysis_payload[spec.study_id] = {
            "primary": scenario_payload["primary"],
            "sensitivity": scenario_payload,
            "interpretation": "Simulation-based triage result; not evidence that the original human study replicated.",
        }
        all_trials[spec.study_id] = [asdict(row) for row in primary_trials]

    ranked = rank_replication_risk(analysis_by_study)
    budget.charge(BudgetEvent(study_id="global", stage="reporting", cost_usd=config.cost_reporting))

    manifest = RunManifest.create(
        run_id=run_id,
        git_commit=git_commit_short(),
        random_seed=config.seed,
        model_tier=config.model_tier,
        study_ids=[s.study_id for s in studies],
        budget=budget.state,
        credit_status=credits,
    )
    manifest.artifacts = [
        "manifest.json",
        "extraction.json",
        "analysis.json",
        "ranking.json",
        "trials.json",
    ]

    return PipelineOutput(
        manifest=manifest.to_dict(),
        extraction={"items": extraction_items},
        analysis=analysis_payload,
        ranking={"items": [asdict(item) for item in ranked]},
        trials=all_trials,
    )


def execute_study_files(
    input_dir: Path,
    run_id: str,
    budget_path: Path,
    credits_path: Path,
    cache_dir: Path | None = None,
    config: PipelineConfig | None = None,
    require_credit_validation: bool = True,
) -> PipelineOutput:
    docs = [load_study_document(path) for path in sorted(input_dir.glob("*.json"))]
    cache = JsonCache(cache_dir) if cache_dir else None
    return execute_documents(
        docs=docs,
        run_id=run_id,
        budget_state=load_budget(budget_path),
        credits=load_credits(credits_path),
        cache=cache,
        config=config,
        require_credit_validation=require_credit_validation,
    )
