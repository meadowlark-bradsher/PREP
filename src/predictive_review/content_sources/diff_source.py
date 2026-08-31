"""Diff-backed content source.

The adapter for the original producer: a unified diff, parsed into hunks,
each widened into `RegionContent` with `kind="code_hunk"`. This is what
every session used before the port existed, and it stays the default for
the web route and for `prep run` without `--source manifest`.

The parse happens once, in the constructor, so a malformed diff fails at
the boundary where the caller can still report it usefully rather than
midway through `submit`.
"""

from __future__ import annotations

from ..domain.content import RegionContent
from ..domain.diff import Diff, parse_diff


class DiffSource:
    """Produces one `code_hunk` region per hunk, in diff order."""

    def __init__(self, diff_text: str) -> None:
        self.raw_text = diff_text
        self._diff: Diff = parse_diff(diff_text)

    @property
    def diff(self) -> Diff:
        """The parsed diff.

        Exposed for callers that legitimately need diff geometry — not for
        the core, which reads `produce()` only. Nothing in the session
        pipeline consults this.
        """
        return self._diff

    def produce(self) -> list[RegionContent]:
        return [hunk.to_content() for hunk in self._diff.hunks]
