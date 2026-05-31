"""Markdown rendering for LLM-produced and engineer-written text.

Readings, dialogue turns, reconciliations, and hypotheses are all
free-form prose that may contain code references, fenced code blocks,
lists, and emphasis. Rendering them as raw text loses the structure
the writer intended (and that the LLM emits by default).

`mistune.create_markdown(escape=True)` escapes raw HTML in the input,
so the only HTML in the output is what mistune itself generates
(<p>, <code>, <pre>, <em>, etc.) — safe to mark Jinja-safe at the
template boundary. Engineer-typed text gets the same treatment;
anything that looks like an HTML tag in their writing renders as
literal text.
"""

from __future__ import annotations

import mistune
from markupsafe import Markup

_render = mistune.create_markdown(escape=True)


def render_markdown(text: str | None) -> Markup:
    if not text:
        return Markup("")
    return Markup(_render(text))
