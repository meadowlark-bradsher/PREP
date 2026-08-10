"""Abstract region content.

The widened carrier for whatever a region asks the engineer to reason
about. The core pipeline (reading, dialogue, judge) needs exactly two
things from content: a ``body`` to put in a prompt or render, and a
``kind`` discriminator for provenance, rendering, and prompt selection.

Everything domain-specific — diff geometry for a code hunk, a source
citation for a glossary term — rides in ``metadata`` and is opaque to the
core. Diff selectors keep typed access to geometry via `diff.Hunk`; they
convert to RegionContent only at the point they emit a Region, so the
geometry never leaks into the core type that provably only reads ``body``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RegionContent:
    kind: str
    body: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "body": self.body, "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RegionContent":
        return cls(
            kind=data["kind"],
            body=data["body"],
            metadata=dict(data.get("metadata") or {}),
        )
