# General DSL candidate classification

**Current agreement (researcher decision pending).** The GUI's
two-element structural diagnostics have a defined scope. There is no approved
generalization of `skips_nearer` or `crosses_clause` to arbitrary multi-fixed,
multi-slot, gap or alternative patterns.

MCP therefore places candidates outside the pair-like scope in `less` with
`classification_unreviewed`. This is an explicit absence-of-review marker, not a
linguistic diagnostic or a claim that the construction is implausible. It must
not be translated into a probability or treated as a verified reading.

The existing matcher result kind, source coordinates, slots and canonical JSONL
record stay unchanged. Pair-like patterns retain their existing diagnostics.
The desktop GUI Pattern DSL mode shares `cpe/gui/patterns.py` and presents the two
groups with neutral UI headings (`Matched` and `Needs review`, with an explicit
classification note) so `classification_unreviewed` is never surfaced as a
likelihood claim. CLI canonical exports do not acquire a new grading rule. A later
researcher decision, with recorded classical-use evidence, can define a broader
policy.
