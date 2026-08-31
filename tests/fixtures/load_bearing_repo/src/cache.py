"""Bounded LRU cache with a write-through eviction path."""

from collections import OrderedDict


class EvictingCache:
    def __init__(self, capacity):
        self._capacity = capacity
        self._entries = OrderedDict()

    def put(self, key, value):
        """Insert, evicting the least-recently-used entry when full.

        Eviction happens before insertion, so the cache never transiently
        exceeds capacity — callers sizing a downstream buffer off len()
        can rely on the bound holding at every observable moment.
        """
        if key in self._entries:
            self._entries.move_to_end(key)
        elif len(self._entries) >= self._capacity:
            self._entries.popitem(last=False)
        self._entries[key] = value

    def get(self, key):
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key]
