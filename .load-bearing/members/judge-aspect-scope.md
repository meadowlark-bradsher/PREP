# Narrowing aspects to the session's criterion

A member may declare aspects serving several different criteria. This function
decides which of them the judge is allowed to see, given the criterion the
session is ordering by.

An aspect with an empty `criteria` list applies under every criterion. A scoped
one applies when the session's criterion is one it serves — and also when the
session's criterion is a **composite** that contains one it serves, transitively.
That last clause is what keeps a composite from covering less than its own parts.
A composite is the maintainers' account of what the software is, with each
component a narrower view a user may override to; a composite showing fewer
aspects than either component would invert that relationship, and would teach
authors to scope everything universal to work around it, which turns the whole
scoping mechanism into a no-op that still looks like it is working.

The judge then scores against exactly the narrowed set, which is what makes a
PASS mean "covered at this scope" rather than "covered entirely", and why the
attempt records the criterion alongside the verdict.

Two different situations collapse to the same answer of None, which puts the
judge into prose mode. A region that declares no aspects at all — every diff
hunk, always. And a region whose aspects all belong to unrelated criteria.
Returning an empty list instead would be worse than useless, because a structured
failure must name at least one aspect from the set it was given, so a judge
handed nothing could never fail.

Both the aspects and the criteria graph are read from the persisted content
rather than from a live manifest, so a session's scope stays fixed even if the
manifest changes under it.
