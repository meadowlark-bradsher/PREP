"""Concrete LLMClient implementation against the Anthropic SDK.

The Anthropic SDK is the only production provider in v1. Adding a second
provider means writing another class that satisfies the LLMClient Protocol
and registering it in production wiring; nothing else changes.

The client holds no per-request state. Each `complete()` call is a fresh
Messages API invocation — that is how the role-independence properties
from the PM doc are preserved: a separate call per role gives a separate
conversational context per role.
"""

from __future__ import annotations

from anthropic import Anthropic

from ..config import Settings, get_settings
from .client import Completion, Message


class AnthropicClient:
    """Wraps anthropic.Anthropic with the LLMClient Protocol shape.

    Construct directly with an Anthropic client for tests; use from_settings
    in production wiring so the API key flows from the Settings object.
    """

    def __init__(self, client: Anthropic) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "AnthropicClient":
        s = settings or get_settings()
        if not s.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; cannot construct AnthropicClient"
            )
        return cls(Anthropic(api_key=s.anthropic_api_key))

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
    ) -> Completion:
        if not model:
            raise ValueError("model id is required (per-role config missing)")

        response = self._client.messages.create(
            model=model,
            system=system,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
        )

        text_parts = [
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ]
        if not text_parts:
            raise RuntimeError(
                f"Anthropic response contained no text blocks; got types: "
                f"{[getattr(b, 'type', '?') for b in response.content]}"
            )

        return Completion(text="\n".join(text_parts), model_id=response.model)
