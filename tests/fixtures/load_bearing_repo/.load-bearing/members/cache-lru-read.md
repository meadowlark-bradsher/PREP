# LRU read path

`get` is not a pure read. A hit promotes the key to the most-recent end
of the ordering, which is what makes the structure an LRU rather than a
FIFO, and it is the reason reads cannot be served concurrently without
synchronisation.

A miss returns `None` and does not insert a tombstone, so a hot miss
does not consume capacity. The distinction matters to callers that use
`None` as a legitimate cached value: they must wrap it, because this
layer cannot tell a stored `None` from an absent key.
