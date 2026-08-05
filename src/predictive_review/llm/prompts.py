"""Prompt loading from disk.

Prompts live as .md files in llm/prompts/. Each role (selector, reading,
judge, dialogue) reads its prompt from a file rather than carrying it as
a Python string so that prompt iteration in weeks 4-5 is a text edit
without a code change. Each component records its prompt_version with
every artifact, so historical sessions stay comparable across iterations
when a new prompt is saved alongside the old one (e.g. selector_v2.md).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")
