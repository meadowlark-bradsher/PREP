"""Production wiring of the LLM components, registry, and session service.

This is the single place that constructs the full service graph for real
use (CLI today, web layer later). Tests do not go through here — they
construct services and components directly with the fakes in tests/.

Each invocation builds a fresh SelectorRegistry rather than mutating
default_registry so that repeated calls (e.g. from multiple CLI commands
in the same process) do not collide on registration.
"""

from __future__ import annotations

from .config import Settings, get_settings
from .dialogue import DialogueManager
from .judge import ClosureJudge
from .llm.anthropic_client import AnthropicClient
from .llm.client import LLMClient
from .reading import ReadingGenerator
from .selectors.development import FirstNHunksSelector
from .selectors.llm_judgment import LLMJudgmentSelector
from .selectors.registry import SelectorRegistry
from .sessions.service import SessionService
from .storage.database import make_session_factory


def build_default_service(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
) -> SessionService:
    s = settings or get_settings()
    client = llm or AnthropicClient.from_settings(s)

    registry = SelectorRegistry()
    registry.register("first_n_hunks", FirstNHunksSelector)
    registry.register(
        "llm_judgment",
        lambda: LLMJudgmentSelector(llm=client, model=_require(s.selector_model, "SELECTOR_MODEL")),
    )

    return SessionService(
        session_factory=make_session_factory(s.database_url),
        selector_registry=registry,
        reading_generator=ReadingGenerator(
            llm=client, model=_require(s.reading_model, "READING_MODEL")
        ),
        closure_judge=ClosureJudge(
            llm=client, model=_require(s.judge_model, "JUDGE_MODEL")
        ),
        dialogue_manager=DialogueManager(
            llm=client, model=_require(s.dialogue_model, "DIALOGUE_MODEL")
        ),
    )


def _require(value: str, env_name: str) -> str:
    if not value:
        raise RuntimeError(
            f"{env_name} is not set; configure it in .env before running"
        )
    return value
