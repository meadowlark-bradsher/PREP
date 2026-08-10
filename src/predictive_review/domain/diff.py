"""Pure value types for parsed unified diffs.

The ORM stores diffs as text and hunks as serialized references; this module
gives the rest of the application a typed in-memory view to work with.
"""

from __future__ import annotations

from dataclasses import dataclass

from unidiff import PatchSet

from .content import RegionContent


@dataclass(frozen=True)
class Hunk:
    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    text: str

    @property
    def ref(self) -> str:
        end = self.new_start + max(self.new_count, 1) - 1
        return f"{self.file_path}@{self.new_start}-{end}"

    def to_content(self) -> RegionContent:
        """Widen this diff hunk into the abstract carrier a Region holds.

        Diff geometry becomes opaque metadata; the core only reads ``body``.
        """
        return RegionContent(
            kind="code_hunk",
            body=self.text,
            metadata={
                "file_path": self.file_path,
                "ref": self.ref,
                "old_start": self.old_start,
                "old_count": self.old_count,
                "new_start": self.new_start,
                "new_count": self.new_count,
            },
        )


@dataclass(frozen=True)
class Diff:
    raw_text: str
    hunks: tuple[Hunk, ...]


def parse_diff(diff_text: str) -> Diff:
    patch = PatchSet(diff_text)
    hunks: list[Hunk] = []
    for patched_file in patch:
        file_path = patched_file.target_file or patched_file.source_file or ""
        if file_path.startswith(("a/", "b/")):
            file_path = file_path[2:]
        for hunk in patched_file:
            hunks.append(
                Hunk(
                    file_path=file_path,
                    old_start=hunk.source_start,
                    old_count=hunk.source_length,
                    new_start=hunk.target_start,
                    new_count=hunk.target_length,
                    text=str(hunk),
                )
            )
    return Diff(raw_text=diff_text, hunks=tuple(hunks))
