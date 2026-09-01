# Cache eviction

A bounded LRU over an `OrderedDict`, where insertion order is recency
order and `move_to_end` promotes on both read and write.

The ordering of eviction against insertion is the load-bearing detail.
Eviction runs *before* the new entry goes in, so occupancy never
transiently exceeds capacity. A caller that sizes a downstream buffer
from `len()` can therefore rely on the bound at every observable moment,
not merely at rest.

Updating a key already present promotes it instead of evicting, so a
hot key rewritten in a loop cannot push itself out.
