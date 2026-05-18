"""CLI orchestration for the AI replication MVP."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from contracts.study_spec import BudgetState, CreditStatus, RunManifest, StudySpec
from cost.budget_manager import BudgetEvent, BudgetManager
from cost.credit_validation import validate_credit_statuses
from extraction.protocol_extractor import extract_protocol
from ingest.study_loader import load_study_document
from ranking.risk_ranker import rank_replication_risk
from simulation.agent_factory import AgentFactory
from simulation.runner import SimulationRunner
from stats.analyzer import AnalysisResult, analyze_results, bootstrap_ci
from storage.cache import JsonCache


def _git_commit_short() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True)
        return out.strip()
    except Exception:
        return "unknown"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_credits(path: Path) -> list[CreditStatus]:
    data = _load_json(path)
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


def _load_budget(path: Path) -> BudgetState:
    data = _load_json(path)
    return BudgetState(
        max_total_usd=float(data["max_total_usd"]),
        max_per_study_usd=float(data["max_per_study_usd"]),
        max_stage_usd={k: float(v) for k, v in data.get("max_stage_usd", {}).items()},
    )


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run_pipeline(args: argparse.Namespace) -> None:
    docs_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    cache = JsonCache(Path(args.cache_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    credits = _load_credits(Path(args.credits))
    credit_check = validate_credit_statuses(credits)
    if not credit_check.ok:
        raise SystemExit("credit validation failed: " + "; ".join(credit_check.messages))

    budget_state = _load_budget(Path(args.budget))
    budget = BudgetManager(budget_state)

    studies: list[StudySpec] = []
    extraction_meta: list[dict[str, Any]] = []
    for input_path in sorted(docs_dir.glob("*.json")):
        doc = load_study_document(input_path)
        cache_key = f"extract::{doc.study_id}::{doc.source_sha256}"
        cached = cache.get(cache_key)
        if cached:
            result_spec = StudySpec.from_dict(cached["study_spec"])
            tier_used = cached["tier_used"]
            confidence = cached["confidence"]
        else:
            extraction = extract_protocol(doc, min_confidence=args.min_confidence)
            result_spec = extraction.study_spec
            tier_used = extraction.tier_used
            confidence = extraction.confidence
            cache.set(
                cache_key,
                {
                    "study_spec": result_spec.to_dict(),
                    "tier_used": tier_used,
                    "confidence": confidence,
                },
            )
        budget.charge(BudgetEvent(study_id=doc.study_id, stage="extraction", cost_usd=args.cost_extract))
        studies.append(result_spec)
        extraction_meta.append(
            {"study_id": doc.study_id, "tier_used": tier_used, "confidence": confidence}
        )

    factory = AgentFactory(seed=args.seed)
    runner = SimulationRunner(seed=args.seed + 1)
    analysis_by_study: dict[str, AnalysisResult] = {}
    all_results: dict[str, list[dict[str, Any]]] = {}

    for spec in studies:
        agents = factory.build(spec.sample_size_target)
        results = runner.run_sequential(
            spec=spec,
            agents=agents,
            batch_size=args.batch_size,
            max_n=spec.sample_size_target,
            stop_width_threshold=args.stop_width_threshold,
            ci_fn=lambda rows: bootstrap_ci(rows, seed=args.seed + 2, rounds=150),
        )
        for _ in results:
            budget.charge(BudgetEvent(study_id=spec.study_id, stage="simulation", cost_usd=args.cost_per_trial))
        analyzed = analyze_results(results, seed=args.seed + 3)
        budget.charge(BudgetEvent(study_id=spec.study_id, stage="analysis", cost_usd=args.cost_analysis))
        analysis_by_study[spec.study_id] = analyzed
        all_results[spec.study_id] = [asdict(row) for row in results]

    ranked = rank_replication_risk(analysis_by_study)
    budget.charge(BudgetEvent(study_id="global", stage="reporting", cost_usd=args.cost_reporting))

    manifest = RunManifest.create(
        run_id=args.run_id,
        git_commit=_git_commit_short(),
        random_seed=args.seed,
        model_tier="credit_first_mixed",
        study_ids=[s.study_id for s in studies],
        budget=budget.state,
        credit_status=credits,
    )
    manifest.artifacts = [
        str(out_dir / "manifest.json"),
        str(out_dir / "extraction.json"),
        str(out_dir / "analysis.json"),
        str(out_dir / "ranking.json"),
        str(out_dir / "trials.json"),
    ]

    _save_json(out_dir / "manifest.json", manifest.to_dict())
    _save_json(out_dir / "extraction.json", {"items": extraction_meta})
    _save_json(
        out_dir / "analysis.json",
        {
            k: {
                "n_control": v.n_control,
                "n_treatment": v.n_treatment,
                "effect_size": v.effect_size,
                "ci_low": v.ci_low,
                "ci_high": v.ci_high,
                "p_value": v.p_value,
                "replicated": v.replicated,
            }
            for k, v in analysis_by_study.items()
        },
    )
    _save_json(
        out_dir / "ranking.json",
        {"items": [asdict(item) for item in ranked]},
    )
    _save_json(out_dir / "trials.json", all_results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI replication MVP pipeline")
    parser.add_argument("--input-dir", default="data/studies")
    parser.add_argument("--output-dir", default="artifacts/latest")
    parser.add_argument("--cache-dir", default="artifacts/cache")
    parser.add_argument("--budget", default="configs/budget.json")
    parser.add_argument("--credits", default="configs/credits.example.json")
    parser.add_argument("--run-id", default="pilot_run_001")
    parser.add_argument("--seed", type=int, default=153)
    parser.add_argument("--min-confidence", type=float, default=0.65)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--stop-width-threshold", type=float, default=2.5)
    parser.add_argument("--cost-extract", type=float, default=0.04)
    parser.add_argument("--cost-per-trial", type=float, default=0.002)
    parser.add_argument("--cost-analysis", type=float, default=0.10)
    parser.add_argument("--cost-reporting", type=float, default=0.20)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _run_pipeline(args)


if __name__ == "__main__":
    main()
