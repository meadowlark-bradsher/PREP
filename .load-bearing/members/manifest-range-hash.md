# The freshness rule

This function decides whether a member is still describing the code it was
written about. It is the single point where PREP and any producer must agree
byte for byte.

The rule is deliberately small. Bytes are read, CRLF is normalised to LF, and
the requested lines are hashed. Nothing else happens: no trailing whitespace is
stripped, no byte-order mark is special-cased, no encoding is guessed. Every
additional normalisation rule would be another chance for two independent
implementations to diverge, and a divergence does not look like a bug — every
member simply reads as stale, as though the code had moved.

One detail is not derivable from the specification and is fixed here. A file's
trailing newline terminates its last line rather than beginning an empty one, so
it is dropped before slicing, and the selected lines are joined with LF with no
terminator appended. The consequence is that a range ending at the last line
hashes identically whether or not the file ends in a newline.

Because the hash covers only the anchored lines, a file edited elsewhere leaves
its members fresh, and two members anchored to different ranges of one file have
independent freshness.
