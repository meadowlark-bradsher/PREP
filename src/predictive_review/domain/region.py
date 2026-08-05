"""In-memory Region value type.

Distinct from the ORM `Region` in storage.models: that one carries
persistence fields (session_id, ordinal, status). This one is what
selectors produce and what reading/judge/dialogue consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .diff import Hunk


@dataclass(frozen=True)
class Region:
    structural_label: str
    hunk: Hunk
    selector_rationale: dict[str, Any] = field(default_factory=dict)
