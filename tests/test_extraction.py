from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from extraction.protocol_extractor import extract_protocol
from ingest.study_loader import StudyDocument


class ExtractionTest(unittest.TestCase):
    def test_extract_protocol_returns_study_spec(self) -> None:
        doc = StudyDocument(
            study_id="s2",
            title="title",
            source_uri="u",
            text="Hypothesis: x improves y.\nN = 120 participants\nCondition control prompt: A\nCondition treatment prompt: B",
            source_sha256="h",
        )
        result = extract_protocol(doc, min_confidence=0.6)
        self.assertEqual(result.study_spec.study_id, "s2")
        self.assertGreaterEqual(result.confidence, 0.6)
        self.assertEqual(len(result.study_spec.arms), 2)


if __name__ == "__main__":
    unittest.main()
