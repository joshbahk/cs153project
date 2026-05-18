from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from contracts.study_spec import ArmSpec, StudySpec, VariableSpec
from ranking.risk_ranker import rank_replication_risk
from simulation.agent_factory import AgentFactory
from simulation.runner import SimulationRunner
from stats.analyzer import analyze_results


class SimulationStatsRankingTest(unittest.TestCase):
    def test_pipeline_components_work_together(self) -> None:
        spec = StudySpec(
            study_id="s3",
            title="demo",
            domain="social",
            hypothesis="h",
            sample_size_target=120,
            inclusion_rules=[],
            exclusion_rules=[],
            independent_variables=[VariableSpec(name="arm", kind="categorical")],
            dependent_variables=[VariableSpec(name="score", kind="continuous")],
            arms=[ArmSpec(arm_id="control", prompt_template="A"), ArmSpec(arm_id="treatment", prompt_template="B")],
            outcome_measure="delta",
            analysis_plan="bootstrap",
            extraction_confidence=0.8,
        )
        agents = AgentFactory(seed=1).build(200)
        results = SimulationRunner(seed=2).run_sequential(
            spec=spec,
            agents=agents,
            batch_size=40,
            max_n=120,
            stop_width_threshold=4.0,
            ci_fn=lambda rows: (-1.0, 1.0),
        )
        analysis = analyze_results(results, seed=3)
        ranked = rank_replication_risk({"s3": analysis})
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].study_id, "s3")


if __name__ == "__main__":
    unittest.main()
