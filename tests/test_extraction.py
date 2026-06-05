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
        self.assertEqual(result.study_spec.methodology.original_statistical_test, "unspecified")
        self.assertIn("participants", result.study_spec.methodology.participant_population.lower())

    def test_extract_arms_ignores_hypothesis_prompt_language(self) -> None:
        doc = StudyDocument(
            study_id="s_prompt",
            title="prompt framing",
            source_uri="u",
            text=(
                "Hypothesis: framed prompts increase compliance.\n"
                "N = 100 participants.\n"
                "Condition control prompt: Please answer the question.\n"
                "Condition treatment prompt: Most people answer this question.\n"
                "We report p-value = 0.03."
            ),
            source_sha256="h",
        )
        result = extract_protocol(doc, min_confidence=0.6)
        prompts = [arm.prompt_template for arm in result.study_spec.arms]
        self.assertEqual(prompts[0], "Condition control prompt: Please answer the question.")
        self.assertEqual(prompts[1], "Condition treatment prompt: Most people answer this question.")
        self.assertNotIn("study_arms_inferred_from_defaults", result.study_spec.methodology.extraction_warnings)


if __name__ == "__main__":
    unittest.main()
