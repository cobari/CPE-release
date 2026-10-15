"""Two-box structure search: FIRST {A} SECOND {B} (spec 14.1)."""

from __future__ import annotations

from cpe.match.dsl import DslError
from cpe.match.pattern_file import PatternSpec, from_mapping

PAIR_ID = "pair"
_FORBIDDEN = frozenset("{}()@")


def _box(value: str, label: str) -> str:
    """One input box as a DSL fixed element: a literal or an alternative."""
    options = value.split("|")
    for option in options:
        if not option or any(c.isspace() or c in _FORBIDDEN for c in option):
            raise DslError(
                "syntax",
                f"{label} box: give a word, or words separated by |, without spaces "
                "or { } ( ) @",
            )
    return options[0] if len(options) == 1 else "(" + "|".join(options) + ")"


def expression(
    first: str,
    second: str,
    a: tuple[int, int] = (1, 20),
    b: tuple[int, int] = (1, 20),
    scope: str = "sentence",
) -> str:
    """The pattern the two boxes stand for; FIRST always precedes SECOND."""
    return (
        f"{_box(first, 'first')} {{A:{a[0]}..{a[1]}}} "
        f"{_box(second, 'second')} {{B:{b[0]}..{b[1]}}} @{scope}"
    )


def pair_spec(
    first: str,
    second: str,
    a: tuple[int, int] = (1, 20),
    b: tuple[int, int] = (1, 20),
    scope: str = "sentence",
) -> PatternSpec:
    # The adjacent_fixed diagnostic shows FIRST immediately followed by SECOND
    # (such as 以爲) in its own layer instead of dropping it (docs/decisions.md
    # "Related candidates are shown, labelled, not hidden").
    return from_mapping(
        {
            "id": PAIR_ID,
            "expr": expression(first, second, a, b, scope),
            "diagnostics": [{"between_slot": "A", "require_tail": False}],
        },
        "<pair>",
    )
