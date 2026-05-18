from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from contracts.study_spec import ArmSpec, StudySpec, VariableSpec


class ContractsTest(unittest.TestCase):
    def test_study_spec_round_trip(self) -> None:
        spec = StudySpec(
            study_id="s1",
            title="t",
            domain="psych",
            hypothesis="h",
            sample_size_target=100,
            inclusion_rules=["a"],
            exclusion_rules=["b"],
            independent_variables=[VariableSpec(name="arm", kind="categorical", allowed_values=["control", "treatment"])],
            dependent_variables=[VariableSpec(name="score", kind="continuous")],
            arms=[ArmSpec(arm_id="control", prompt_template="c"), ArmSpec(arm_id="treatment", prompt_template="t")],
            outcome_measure="delta",
            analysis_plan="plan",
            extraction_confidence=0.8,
        )
        raw = spec.to_dict()
        out = StudySpec.from_dict(raw)
        self.assertEqual(out.study_id, "s1")
        self.assertEqual(len(out.arms), 2)


if __name__ == "__main__":
    unittest.main()
