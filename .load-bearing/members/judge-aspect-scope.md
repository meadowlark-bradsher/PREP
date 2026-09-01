# Narrowing aspects to the session's criterion

A member may declare aspects serving several different criteria. This function
decides which of them the judge is allowed to see, given the criterion the
session is ordering by.

An aspect with an empty `criteria` list applies under every criterion; one that
names criteria applies only when the session is ordering by a criterion it
names. The judge then scores against exactly that narrowed set, which is what
makes a PASS mean "covered at this scope" rather than "covered entirely" — and
why the attempt records the criterion alongside the verdict.

Two different situations collapse to the same answer of None, which puts the
judge into prose mode. A region that declares no aspects at all — every diff
hunk, always. And a region whose aspects all belong to other criteria. They look
different but are the same thing: there is no declared claim to score against at
this scope. Returning an empty list instead would be worse than useless, because
a structured failure must name at least one aspect from the set it was given,
so a judge handed nothing could never fail.

Aspects are rebuilt from the persisted content rather than read from a live
manifest, so a session's scope stays fixed even if the manifest changes under it.
