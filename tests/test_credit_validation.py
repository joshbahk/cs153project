from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from contracts.study_spec import CreditStatus
from cost.credit_validation import validate_credit_statuses


class CreditValidationTest(unittest.TestCase):
    def test_detects_unvalidated_provider(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        statuses = [
            CreditStatus(
                provider="cloudflare",
                validated=False,
                expires_at=future,
                eligible_products=["workers_ai"],
                blocked_products=[],
            )
        ]
        result = validate_credit_statuses(statuses)
        self.assertFalse(result.ok)
        self.assertTrue(any("not validated" in m for m in result.messages))


if __name__ == "__main__":
    unittest.main()
