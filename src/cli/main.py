"""CLI entrypoint for the replication-triage pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline.service import PipelineConfig, execute_study_files, save_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulation-based replication triage pipeline")
    parser.add_argument("--input-dir", default="data/studies")
    parser.add_argument("--output-dir", default="artifacts/latest")
    parser.add_argument("--cache-dir", default="artifacts/cache")
    parser.add_argument("--budget", default="configs/budget.json")
    parser.add_argument("--credits", default="configs/credits.demo.json")
    parser.add_argument("--run-id", default="pilot_run_001")
    parser.add_argument("--seed", type=int, default=153)
    parser.add_argument("--min-confidence", type=float, default=0.65)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--stop-width-threshold", type=float, default=2.5)
    parser.add_argument("--max-sample-size", type=int, default=500)
    parser.add_argument("--cost-extract", type=float, default=0.04)
    parser.add_argument("--cost-per-trial", type=float, default=0.002)
    parser.add_argument("--cost-analysis", type=float, default=0.10)
    parser.add_argument("--cost-reporting", type=float, default=0.20)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = PipelineConfig(
        seed=args.seed,
        min_confidence=args.min_confidence,
        batch_size=args.batch_size,
        stop_width_threshold=args.stop_width_threshold,
        max_sample_size=args.max_sample_size,
        cost_extract=args.cost_extract,
        cost_per_trial=args.cost_per_trial,
        cost_analysis=args.cost_analysis,
        cost_reporting=args.cost_reporting,
    )
    output = execute_study_files(
        input_dir=Path(args.input_dir),
        run_id=args.run_id,
        budget_path=Path(args.budget),
        credits_path=Path(args.credits),
        cache_dir=Path(args.cache_dir),
        config=config,
    )
    save_artifacts(Path(args.output_dir), output)


if __name__ == "__main__":
    main()
