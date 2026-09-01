"""Content source contract.

Selection used to begin at a parsed diff: `SessionService.submit` took
diff text, called `parse_diff`, and handed the result to a selector. That
wired one *producer* of review material into the orchestrator, and there
is now a second one (`.load-bearing/` member manifests) that has no diff
anywhere in it.

A ContentSource is that producer, generalized. It answers one question —
"what are the candidate regions for this session?" — and answers it in
the widened carrier the core already speaks, `RegionContent`. Everything
domain-specific about how the material was obtained (diff geometry, git
provenance, manifest anchors) is resolved inside the adapter and travels
onward only as opaque `RegionContent.metadata`.

CONTRACT:
  - `produce()` returns the ordered candidate set. Order is the source's
    natural order; ranking is the selector's job, not the source's.
  - `raw_text` is the verbatim material the session was launched from,
    persisted for provenance and display. It is never parsed downstream.
  - Side-effect free with respect to the database. A source may read the
    filesystem or shell out to git; it never persists anything.

The port is deliberately narrow. It does not know about selectors,
sessions, or phases, and it carries no per-user state — a source produces
content, never a judgement about content.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.content import RegionContent


@runtime_checkable
class ContentSource(Protocol):
    raw_text: str

    def produce(self) -> list[RegionContent]: ...
