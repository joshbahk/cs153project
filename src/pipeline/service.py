"""Shared pipeline execution for CLI, web workers, and tests."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from contracts.study_spec import BudgetState, CreditStatus, RunManifest, StudySpec
from cost.budget_manager import BudgetEvent, BudgetManager
from cost.credit_validation import validate_credit_statuses
from extraction.protocol_extractor import extract_protocol
from ingest.study_loader import StudyDocument, load_study_document
from ranking.risk_ranker import rank_replication_risk
from simulation.agent_factory import AgentFactory
from simulation.agent_factory import SyntheticAgent
from simulation.runner import SimulationRunner, TrialResult
from stats.analyzer import AnalysisResult, analyze_for_study, bootstrap_ci
from storage.cache import JsonCache

EXTRACTION_CACHE_VERSION = "v3_methodology_guardrails"

ProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


class PipelineCancelled(RuntimeError):
    pass


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
    agents: dict[str, list[dict[str, Any]]]

    def artifacts(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "extraction": self.extraction,
            "analysis": self.analysis,
            "ranking": self.ranking,
            "trials": self.trials,
            "agents": self.agents,
        }


def estimate_run_cost(max_sample_size: int, config: PipelineConfig | None = None) -> float:
    config = config or PipelineConfig(max_sample_size=max_sample_size)
    scenario_count = len(config.sensitivity_shifts)
    return (
        config.cost_extract
        + (max_sample_size * config.cost_per_trial * scenario_count)
        + (config.cost_analysis * scenario_count)
        + config.cost_reporting
    )


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
        "method": result.method,
        "matched_original_method": result.matched_original_method,
        "method_note": result.method_note,
    }


def _cap_sample_size(spec: StudySpec, max_sample_size: int) -> StudySpec:
    if spec.sample_size_target <= max_sample_size:
        return spec
    spec.sample_size_target = max_sample_size
    return spec


def _run_one_scenario(
    spec: StudySpec,
    agents: list[SyntheticAgent],
    simulation_seed: int,
    analysis_seed: int,
    batch_size: int,
    stop_width_threshold: float,
    scenario: str,
    treatment_shift: float,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    progress_start: float = 20.0,
    progress_end: float = 80.0,
) -> tuple[list[TrialResult], AnalysisResult]:
    runner = SimulationRunner(seed=simulation_seed, treatment_shift=treatment_shift, scenario=scenario)

    def report_batch(consumed: int, total: int) -> None:
        _checkpoint(should_cancel)
        fraction = consumed / total if total else 0.0
        _emit_progress(
            progress_callback,
            stage="simulating",
            message=f"Simulating {scenario} scenario ({consumed}/{total} agents).",
            percent=progress_start + ((progress_end - progress_start) * fraction),
            current=consumed,
            total=total,
            scenario=scenario,
        )

    results = runner.run_sequential(
        spec=spec,
        agents=agents,
        batch_size=batch_size,
        max_n=spec.sample_size_target,
        stop_width_threshold=stop_width_threshold,
        ci_fn=lambda rows: bootstrap_ci(rows, seed=analysis_seed, rounds=150),
        progress_callback=report_batch,
    )
    _checkpoint(should_cancel)
    return results, analyze_for_study(spec, results, seed=analysis_seed)


def _has_blocking_methodology_gap(spec: StudySpec) -> bool:
    blocking = {"study_arms_inferred_from_defaults", "fewer_than_two_conditions_detected"}
    return any(warning in blocking for warning in spec.methodology.extraction_warnings)


def _checkpoint(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise PipelineCancelled("Skipped by user request.")


def _emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    stage: str,
    message: str,
    percent: float,
    current: int | None = None,
    total: int | None = None,
    **extra: Any,
) -> None:
    if progress_callback is None:
        return
    payload: dict[str, Any] = {
        "stage": stage,
        "message": message,
        "percent": percent,
    }
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    payload.update(extra)
    progress_callback(payload)


def execute_documents(
    docs: list[StudyDocument],
    run_id: str,
    budget_state: BudgetState,
    credits: list[CreditStatus],
    cache: JsonCache | None = None,
    config: PipelineConfig | None = None,
    require_credit_validation: bool = True,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> PipelineOutput:
    if not docs:
        raise ValueError("at least one study document is required")

    config = config or PipelineConfig()
    _emit_progress(progress_callback, stage="validating", message="Validating credits and budget.", percent=2.0)
    _checkpoint(should_cancel)
    if require_credit_validation:
        credit_check = validate_credit_statuses(credits)
        if not credit_check.ok:
            raise ValueError("credit validation failed: " + "; ".join(credit_check.messages))
    _checkpoint(should_cancel)

    budget = BudgetManager(budget_state)
    studies: list[StudySpec] = []
    extraction_items: list[dict[str, Any]] = []
    for doc in docs:
        cached = None
        cache_key = f"extract::{EXTRACTION_CACHE_VERSION}::{doc.study_id}::{doc.source_sha256}"
        if cache is not None:
            cached = cache.get(cache_key)

        _emit_progress(
            progress_callback,
            stage="extracting_protocol",
            message=f"Extracting methodology for {doc.title}.",
            percent=8.0,
        )
        _checkpoint(should_cancel)
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
        _emit_progress(
            progress_callback,
            stage="checking_methodology",
            message="Checking whether the extracted methods are simulatable.",
            percent=15.0,
        )
        _checkpoint(should_cancel)
        if _has_blocking_methodology_gap(spec):
            warnings = ", ".join(spec.methodology.extraction_warnings)
            raise ValueError(
                "Insufficient methodology detail for automated simulation. "
                f"Detected warnings: {warnings}. Paste the methods/conditions text or use a PDF with extractable text."
            )
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
        _checkpoint(should_cancel)

    analysis_by_study: dict[str, AnalysisResult] = {}
    analysis_payload: dict[str, Any] = {}
    all_trials: dict[str, list[dict[str, Any]]] = {}
    all_agents: dict[str, list[dict[str, Any]]] = {}

    for idx, spec in enumerate(studies):
        scenario_payload: dict[str, Any] = {}
        primary_trials: list[TrialResult] = []
        base_seed = config.seed + (idx * 1000)
        _emit_progress(
            progress_callback,
            stage="generating_agents",
            message=f"Generating {spec.sample_size_target} stratified synthetic agents.",
            percent=18.0,
            current=0,
            total=spec.sample_size_target,
        )
        _checkpoint(should_cancel)
        agents = AgentFactory(seed=base_seed, population_hint=spec.methodology.participant_population).build(
            spec.sample_size_target
        )
        agent_profiles = [agent.to_dict() for agent in agents]
        scenario_items = list(config.sensitivity_shifts.items())
        scenario_count = len(scenario_items)
        for scenario_idx, (scenario, shift) in enumerate(scenario_items):
            scenario_start = 20.0 + (60.0 * (scenario_idx / scenario_count))
            scenario_end = 20.0 + (60.0 * ((scenario_idx + 1) / scenario_count))
            _emit_progress(
                progress_callback,
                stage="simulating",
                message=f"Starting {scenario} sensitivity scenario.",
                percent=scenario_start,
                current=0,
                total=spec.sample_size_target,
                scenario=scenario,
            )
            _checkpoint(should_cancel)
            trials, analyzed = _run_one_scenario(
                spec=spec,
                agents=agents,
                simulation_seed=base_seed + 1,
                analysis_seed=base_seed + 2 + scenario_idx,
                batch_size=config.batch_size,
                stop_width_threshold=config.stop_width_threshold,
                scenario=scenario,
                treatment_shift=shift,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                progress_start=scenario_start,
                progress_end=scenario_end,
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
                all_agents[spec.study_id] = agent_profiles
            _checkpoint(should_cancel)

        analysis_payload[spec.study_id] = {
            "primary": scenario_payload["primary"],
            "sensitivity": scenario_payload,
            "methodology": asdict(spec.methodology),
            "interpretation": "Simulation-based triage result; not evidence that the original human study replicated.",
        }
        all_trials[spec.study_id] = [asdict(row) for row in primary_trials]

    _emit_progress(progress_callback, stage="ranking", message="Ranking replication risk.", percent=88.0)
    _checkpoint(should_cancel)
    ranked = rank_replication_risk(analysis_by_study)
    _emit_progress(progress_callback, stage="reporting", message="Writing run artifacts.", percent=94.0)
    _checkpoint(should_cancel)
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
        "agents.json",
    ]

    output = PipelineOutput(
        manifest=manifest.to_dict(),
        extraction={"items": extraction_items},
        analysis=analysis_payload,
        ranking={"items": [asdict(item) for item in ranked]},
        trials=all_trials,
        agents=all_agents,
    )
    _emit_progress(progress_callback, stage="saving_results", message="Saving completed results.", percent=98.0)
    return output


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
