# The freshness rule

This function decides whether a member is still describing the code it was
written about. It is the single point where PREP and any producer must agree
byte for byte.

Four normalisations, and each is stated rather than left to be inferred. A
leading UTF-8 BOM is stripped, because an editor adding one has not changed the
code. CRLF and bare CR both fold to LF, because a line ending is an artifact of
how the file was checked out. Trailing whitespace is kept, because it is a real
edit to a real byte. A missing final newline is invisible: the trailing newline
terminates a line rather than beginning an empty one, so it is dropped before
slicing and the selected lines are joined with LF with no terminator appended.
A range ending at the last line therefore hashes identically whether or not the
file ends in a newline.

The BOM and bare-CR rules ran the other way here until a second implementation
of this contract turned up folding both. Neither repository could produce such a
file, so the disagreement was invisible — it would have been discovered by
whoever first anchored something an editor had touched on Windows, arriving as a
hash mismatch with no diff to account for it. The rules that are dangerous are
the ones nobody wrote down, not the ones anyone argued about.

Because the hash covers only the anchored lines, a file edited elsewhere leaves
its members fresh, and two members anchored to different ranges of one file have
independent freshness.
