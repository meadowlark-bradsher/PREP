"""Response parsing helpers shared across components."""

from __future__ import annotations

import json
from typing import Any


def extract_json(text: str) -> Any:
    """Parse JSON from a model response, tolerating markdown code fences.

    The prompts ask for raw JSON, but models occasionally wrap responses in
    ```json ... ``` blocks despite instructions. This strips the fence and
    parses what remains. Raises json.JSONDecodeError on actual parse failure
    — the caller can decide whether to retry or surface the error.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)
