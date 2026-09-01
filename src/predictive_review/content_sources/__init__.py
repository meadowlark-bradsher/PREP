"""Producers of review material.

Each adapter turns some upstream artifact — a diff, a `.load-bearing/`
manifest — into the `RegionContent` list a selector ranks. See
`base.ContentSource` for the contract.
"""

from .base import ContentSource
from .diff_source import DiffSource
from .manifest import ManifestError, ManifestSource

__all__ = ["ContentSource", "DiffSource", "ManifestError", "ManifestSource"]
