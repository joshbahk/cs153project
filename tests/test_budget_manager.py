from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from contracts.study_spec import BudgetState
from cost.budget_manager import BudgetEvent, BudgetExceededError, BudgetManager


class BudgetManagerTest(unittest.TestCase):
    def test_charge_updates_ledgers(self) -> None:
        state = BudgetState(
            max_total_usd=10.0,
            max_per_study_usd=5.0,
            max_stage_usd={"extraction": 3.0},
        )
        mgr = BudgetManager(state)
        mgr.charge(BudgetEvent(study_id="s1", stage="extraction", cost_usd=1.2, reason="test"))

        self.assertAlmostEqual(state.spent_total_usd, 1.2)
        self.assertAlmostEqual(state.spent_by_study_usd["s1"], 1.2)
        self.assertAlmostEqual(state.spent_by_stage_usd["extraction"], 1.2)

    def test_stage_cap_enforced(self) -> None:
        state = BudgetState(
            max_total_usd=10.0,
            max_per_study_usd=8.0,
            max_stage_usd={"simulation": 1.0},
        )
        mgr = BudgetManager(state)
        mgr.charge(BudgetEvent(study_id="s1", stage="simulation", cost_usd=0.8, reason="ok"))
        with self.assertRaises(BudgetExceededError):
            mgr.charge(BudgetEvent(study_id="s1", stage="simulation", cost_usd=0.3, reason="overflow"))


if __name__ == "__main__":
    unittest.main()
