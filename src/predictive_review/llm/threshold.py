"""Engagement-threshold guidance text for selector and judge prompts.

The threshold is a session-level coarse knob: load_bearing_only,
default, or thorough. Each role (selector, judge) reads a per-role
description of what its threshold means and substitutes that text
into its prompt via `$engagement_threshold`.

Keeping the descriptions here (rather than inlining them in the .md
prompts) means iteration on what each threshold tells each role is
a single-file edit. The PM doc explicitly defers prompt tuning to
weeks 4-5; this module is where that tuning will live for the
threshold variable.
"""

from __future__ import annotations

from ..storage.models import EngagementThreshold


_SELECTOR_GUIDANCE: dict[EngagementThreshold, str] = {
    EngagementThreshold.LOAD_BEARING_ONLY: (
        "Session is calibrated to LOAD-BEARING ONLY. Select 1-2 regions, "
        "only when they reflect a genuinely load-bearing engineering choice "
        "where a different reasonable engineer would have made a different "
        "decision. Always select at least one region — the engineer needs a "
        "calibration anchor even on trivial diffs."
    ),
    EngagementThreshold.DEFAULT: (
        "Session is calibrated to DEFAULT engagement. Select 2-4 regions "
        "reflecting design choices and notable details where the engineer "
        "would benefit from being asked to predict the model's reading."
    ),
    EngagementThreshold.THOROUGH: (
        "Session is calibrated to THOROUGH engagement. Select 3-4 regions, "
        "including secondary details worth comprehending. Lean into "
        "regions where the engineer might have multiple plausible "
        "interpretations of what the code is doing or why."
    ),
}


_JUDGE_GUIDANCE: dict[EngagementThreshold, str] = {
    EngagementThreshold.LOAD_BEARING_ONLY: (
        "Session is calibrated to LOAD-BEARING ONLY coverage. PASS the "
        "teach-back if it covers the core load-bearing claim of the "
        "reading, even if it misses peripheral details. FAIL only when "
        "the teach-back is silent on something fundamental to the choice."
    ),
    EngagementThreshold.DEFAULT: (
        "Session is calibrated to DEFAULT coverage. PASS the teach-back if "
        "it covers the substantive content of the reading. FAIL when the "
        "teach-back misses something the engineer would want to know they "
        "missed — be calibrated, not exhaustive."
    ),
    EngagementThreshold.THOROUGH: (
        "Session is calibrated to THOROUGH coverage. PASS the teach-back "
        "only when it covers the substantive content of the reading "
        "including secondary details and stated tradeoffs. FAIL when any "
        "non-trivial claim from the reading is uncovered."
    ),
}


def selector_guidance(threshold: EngagementThreshold) -> str:
    return _SELECTOR_GUIDANCE[threshold]


def judge_guidance(threshold: EngagementThreshold) -> str:
    return _JUDGE_GUIDANCE[threshold]
