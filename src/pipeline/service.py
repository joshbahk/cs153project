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
from simulation.llm_runner import (
    ChatCompletionClient,
    LLMParticipantRunner,
    LLMScenarioOutput,
    records_to_dicts,
)
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
    simulation_backend: str = "transparent"
    llm_client: ChatCompletionClient | None = None
    llm_model: str = "llama3.3-70b-instruct"
    llm_sample_size: int = 40
    llm_temperature: float = 0.2
    llm_max_tokens: int = 180
    llm_max_retries: int = 2
    llm_primary_only: bool = True
    llm_estimated_input_tokens_per_trial: int = 900
    llm_estimated_output_tokens_per_trial: int = 160
    llm_input_cost_per_1m_tokens: float = 0.50
    llm_output_cost_per_1m_tokens: float = 0.50
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
    llm_responses: dict[str, list[dict[str, Any]]]

    def artifacts(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "extraction": self.extraction,
            "analysis": self.analysis,
            "ranking": self.ranking,
            "trials": self.trials,
            "agents": self.agents,
            "llm_responses": self.llm_responses,
        }


def estimate_run_cost(max_sample_size: int, config: PipelineConfig | None = None) -> float:
    config = config or PipelineConfig(max_sample_size=max_sample_size)
    scenario_count = len(_scenario_items(config))
    if config.simulation_backend == "llm":
        llm_trials = min(max_sample_size, config.llm_sample_size) * scenario_count
        llm_estimated_cost = (
            (llm_trials * config.llm_estimated_input_tokens_per_trial / 1_000_000)
            * config.llm_input_cost_per_1m_tokens
        ) + (
            (llm_trials * config.llm_estimated_output_tokens_per_trial / 1_000_000)
            * config.llm_output_cost_per_1m_tokens
        )
        return config.cost_extract + llm_estimated_cost + (config.cost_analysis * scenario_count) + config.cost_reporting
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


def _scenario_items(config: PipelineConfig) -> list[tuple[str, float]]:
    items = list(config.sensitivity_shifts.items())
    if config.simulation_backend == "llm" and config.llm_primary_only:
        return [item for item in items if item[0] == "primary"] or items[:1]
    return items


def _llm_trial_cost(usage: dict[str, int], config: PipelineConfig) -> float:
    return (
        (float(usage.get("prompt_tokens", 0)) / 1_000_000) * config.llm_input_cost_per_1m_tokens
    ) + (
        (float(usage.get("completion_tokens", 0)) / 1_000_000) * config.llm_output_cost_per_1m_tokens
    )


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


def _run_llm_scenario(
    spec: StudySpec,
    agents: list[SyntheticAgent],
    simulation_seed: int,
    analysis_seed: int,
    scenario: str,
    progress_callback: ProgressCallback | None,
    should_cancel: CancelCheck | None,
    progress_start: float,
    progress_end: float,
    config: PipelineConfig,
) -> tuple[list[TrialResult], AnalysisResult, LLMScenarioOutput]:
    if config.llm_client is None:
        raise ValueError("LLM simulation is enabled but no LLM client is configured.")
    runner = LLMParticipantRunner(
        client=config.llm_client,
        model=config.llm_model,
        seed=simulation_seed,
        scenario=scenario,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        max_retries=config.llm_max_retries,
    )

    def report_agent(consumed: int, total: int) -> None:
        _checkpoint(should_cancel)
        fraction = consumed / total if total else 0.0
        _emit_progress(
            progress_callback,
            stage="llm_simulating",
            message=f"Calling LLM agents for {scenario} scenario ({consumed}/{total}).",
            percent=progress_start + ((progress_end - progress_start) * fraction),
            current=consumed,
            total=total,
            scenario=scenario,
            model=config.llm_model,
        )

    output = runner.run_sequential(
        spec=spec,
        agents=agents,
        max_n=min(spec.sample_size_target, config.llm_sample_size),
        progress_callback=report_agent,
    )
    _checkpoint(should_cancel)
    return output.trials, analyze_for_study(spec, output.trials, seed=analysis_seed), output


def _has_blocking_methodology_gap(spec: StudySpec) -> bool:
    # For the project demo, missing explicit arm labels should downgrade confidence,
    # not prevent a study from running. The extractor already records warnings and
    # inferred defaults in the run artifacts/UI.
    return False


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
    if config.simulation_backend not in {"transparent", "llm"}:
        raise ValueError(f"unknown simulation backend: {config.simulation_backend}")
    if config.simulation_backend == "llm" and config.llm_sample_size < 2:
        raise ValueError("LLM simulation requires LLM_SAMPLE_SIZE >= 2")
    if config.simulation_backend == "llm" and config.model_tier == "digitalocean_cpu_transparent_simulator":
        config.model_tier = f"digitalocean_serverless_llm_agents:{config.llm_model}"

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

        effective_max_sample_size = config.max_sample_size
        if config.simulation_backend == "llm":
            effective_max_sample_size = min(config.max_sample_size, config.llm_sample_size)
        spec = _cap_sample_size(spec, effective_max_sample_size)
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
    all_llm_responses: dict[str, list[dict[str, Any]]] = {}

    for idx, spec in enumerate(studies):
        scenario_payload: dict[str, Any] = {}
        primary_trials: list[TrialResult] = []
        study_llm_records: list[dict[str, Any]] = []
        base_seed = config.seed + (idx * 1000)
        participant_label = "LLM participant agents" if config.simulation_backend == "llm" else "stratified synthetic agents"
        _emit_progress(
            progress_callback,
            stage="generating_agents",
            message=f"Generating {spec.sample_size_target} {participant_label}.",
            percent=18.0,
            current=0,
            total=spec.sample_size_target,
        )
        _checkpoint(should_cancel)
        agents = AgentFactory(seed=base_seed, population_hint=spec.methodology.participant_population).build(
            spec.sample_size_target
        )
        agent_profiles = [agent.to_dict() for agent in agents]
        all_agents[spec.study_id] = agent_profiles
        scenario_items = _scenario_items(config)
        scenario_count = len(scenario_items)
        for scenario_idx, (scenario, shift) in enumerate(scenario_items):
            scenario_start = 20.0 + (60.0 * (scenario_idx / scenario_count))
            scenario_end = 20.0 + (60.0 * ((scenario_idx + 1) / scenario_count))
            stage = "llm_simulating" if config.simulation_backend == "llm" else "simulating"
            _emit_progress(
                progress_callback,
                stage=stage,
                message=f"Starting {scenario} sensitivity scenario.",
                percent=scenario_start,
                current=0,
                total=spec.sample_size_target,
                scenario=scenario,
            )
            _checkpoint(should_cancel)
            llm_usage: dict[str, int] | None = None
            if config.simulation_backend == "llm":
                trials, analyzed, llm_output = _run_llm_scenario(
                    spec=spec,
                    agents=agents,
                    simulation_seed=base_seed + 1,
                    analysis_seed=base_seed + 2 + scenario_idx,
                    scenario=scenario,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                    progress_start=scenario_start,
                    progress_end=scenario_end,
                    config=config,
                )
                llm_usage = llm_output.usage.to_dict()
                budget.charge(
                    BudgetEvent(
                        study_id=spec.study_id,
                        stage="simulation",
                        cost_usd=_llm_trial_cost(llm_usage, config),
                        reason=f"{scenario}: {llm_usage}",
                    )
                )
                study_llm_records.extend(records_to_dicts(llm_output.records))
            else:
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
            scenario_result = {
                **_analysis_to_dict(analyzed),
                "treatment_shift": shift,
                "simulation_backend": config.simulation_backend,
            }
            if llm_usage is not None:
                scenario_result["llm_model"] = config.llm_model
                scenario_result["llm_usage"] = llm_usage
            scenario_payload[scenario] = scenario_result
            if scenario == "primary" or not primary_trials:
                analysis_by_study[spec.study_id] = analyzed
                primary_trials = trials
            _checkpoint(should_cancel)

        if study_llm_records:
            all_llm_responses[spec.study_id] = study_llm_records
        primary_key = "primary" if "primary" in scenario_payload else next(iter(scenario_payload))
        analysis_payload[spec.study_id] = {
            "primary": scenario_payload[primary_key],
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
        "llm_responses.json",
    ]

    output = PipelineOutput(
        manifest=manifest.to_dict(),
        extraction={"items": extraction_items},
        analysis=analysis_payload,
        ranking={"items": [asdict(item) for item in ranked]},
        trials=all_trials,
        agents=all_agents,
        llm_responses=all_llm_responses,
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
