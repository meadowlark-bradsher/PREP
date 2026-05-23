"""Provider-agnostic LLM seam.

v1 is Anthropic-only; this Protocol exists so adding another provider
later is a drop-in. Each role (selector / reading / judge / dialogue)
calls the client with its own system prompt and message list — the
client itself holds no state between calls. Independence between roles
is enforced by callers passing fresh inputs, not by the client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    model_id: str


class LLMClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
    ) -> Completion: ...
