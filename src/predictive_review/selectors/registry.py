"""Selector registry.

The PM doc requires each session to record which selector produced its
regions so historical artifacts remain comparable across selector
versions. Selectors register themselves by a stable (name, version)
pair, and the orchestration layer looks them up by name.

The registry is deliberately simple: no capability negotiation, no
composition. Sophistication here is explicitly deferred per the
'Premature optimization of the selector interface' risk in the PM doc.
"""

from __future__ import annotations

from typing import Callable

from .base import RegionSelector


class SelectorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], RegionSelector]] = {}

    def register(self, name: str, factory: Callable[[], RegionSelector]) -> None:
        if name in self._factories:
            raise ValueError(f"selector already registered: {name}")
        self._factories[name] = factory

    def get(self, name: str) -> RegionSelector:
        if name not in self._factories:
            raise KeyError(f"no selector registered: {name}")
        return self._factories[name]()

    def names(self) -> list[str]:
        return sorted(self._factories.keys())


default_registry = SelectorRegistry()


def _register_defaults() -> None:
    from .development import FirstNHunksSelector

    default_registry.register("first_n_hunks", FirstNHunksSelector)
    # LLMJudgmentSelector is not registered here because it requires an
    # LLMClient; production wiring registers it with the configured client.


_register_defaults()
