"""Heuristics that distinguish a real full-text body from an abstract/landing page."""

from __future__ import annotations

_SECTION_CUES = (
    "method",
    "participant",
    "procedure",
    "materials",
    "measure",
    "results",
    "analysis",
    "discussion",
    "condition",
    "experiment",
    "regression",
    "anova",
)


def section_cue_count(text: str) -> int:
    lower = text.lower()
    return sum(1 for cue in _SECTION_CUES if cue in lower)


def is_probably_full_text(text: str, *, min_chars: int = 1200) -> bool:
    """A body is "full text" when it is long enough and reads like a methods paper."""

    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < min_chars:
        return False
    # Require at least two distinct section cues so a long abstract or a
    # navigation-heavy landing page does not masquerade as full text.
    return section_cue_count(stripped) >= 2
