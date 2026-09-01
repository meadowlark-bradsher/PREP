# Token refresh

The refresher exchanges a stored refresh token for a fresh access token
and writes the result back to the store.

Two bounds govern the loop. The attempt counter caps total tries at
`MAX_ATTEMPTS`, and backoff grows exponentially between them. A 401 is
treated as terminal rather than transient: the refresh token itself has
expired, so no number of retries will produce a different answer. The
handler evicts the dead token from the store before raising, which keeps
a subsequent caller from retrying against a credential already known to
be bad.

Exhausting the attempt budget and receiving a 401 are distinct failures
and raise distinct exceptions, because callers respond differently:
exhaustion is worth retrying later, expiry requires re-authentication.
