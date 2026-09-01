# Structured verdict parsing

When the judge is given an aspect list, its answer must be a list of ids drawn
from that list. This function is where that is enforced, and every deviation is
treated the same way: as a parse failure, indistinguishable from malformed JSON.

Four things are rejected. A `missing_aspects` that is not a list at all — the
judge answered in prose when it was asked for structure. An empty list on FAIL,
which would be a failure that names nothing. A non-string element. And an id
that was not in the set supplied, which is the one that matters most: a judge
naming an aspect nobody gave it has not answered the question that was asked.

Nothing is salvaged. There is no attempt to keep the recognisable ids and drop
the rest, because a verdict that was partly invented is not a verdict whose
remainder can be trusted. Duplicates are the single exception — they are
collapsed rather than rejected, since repeating an id is a formatting artefact
rather than a claim about a different aspect.

On PASS the result is None regardless of what the judge returned, so a model
that fills the field on success cannot cause a spurious record.
