"""Full-text-only suitability gate for imported candidates.

A candidate is "suitable" only when the protocol extractor can recover at least
two genuine study conditions from its text. Abstracts and landing pages fail
this gate because they lack explicit control/treatment arm language, which is
exactly the behavior we want: only papers whose method/conditions are present
in the imported text proceed to run creation.

The blocking warning set mirrors ``pipeline.service`` so the ingest gate and the
pipeline's methodology guardrail stay aligned. Keep these in sync if either
side changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from extraction.protocol_extractor import extract_protocol
from ingest.study_loader import StudyDocument
from ingest.paper_ingest import sha256_text

# Mirror of pipeline.service._has_blocking_methodology_gap's blocking set.
BLOCKING_WARNINGS = {
    "study_arms_inferred_from_defaults",
    "fewer_than_two_conditions_detected",
}


@dataclass(slots=True)
class SuitabilityResult:
    suitable: bool
    reason: str
    warnings: list[str]


def assess_text(text: str, *, title: str = "candidate", source_uri: str = "") -> SuitabilityResult:
    text = (text or "").strip()
    if len(text) < 80:
        return SuitabilityResult(False, "empty_text", [])

    doc = StudyDocument(
        study_id="suitability_probe",
        title=title or "candidate",
        source_uri=source_uri,
        text=text,
        source_sha256=sha256_text(text),
    )
    result = extract_protocol(doc)
    warnings = list(result.study_spec.methodology.extraction_warnings)
    blocking = sorted(BLOCKING_WARNINGS.intersection(warnings))
    if blocking:
        return SuitabilityResult(False, "unsuitable_methodology", warnings)
    return SuitabilityResult(True, "suitable", warnings)
