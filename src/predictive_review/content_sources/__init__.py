"""Producers of review material.

Each adapter turns some upstream artifact — a diff today, a
`.load-bearing/` manifest next — into the `RegionContent` list a selector
ranks. See `base.ContentSource` for the contract.
"""

from .base import ContentSource
from .diff_source import DiffSource

__all__ = ["ContentSource", "DiffSource"]
