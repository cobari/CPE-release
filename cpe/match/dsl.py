"""Parse pattern expressions (docs/spec/m0-spec.md section 4.1 and 4.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

DEFAULT_SLOT_MIN = 1
DEFAULT_SLOT_MAX = 20

# Characters that can never be part of a literal: they delimit DSL syntax.
_SYNTAX_CHARS = frozenset("{}()|@")
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_INT_RE = re.compile(r"[0-9]+\Z")


class DslError(ValueError):
    """A pattern expression that the MVP grammar rejects.

    `rule` is the restriction id from the spec ("R1".."R8") or "syntax".
    """

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule


@dataclass(frozen=True)
class Fixed:
    """A fixed element; `options` has one literal, or several for an alternative."""

    options: tuple[str, ...]

    @property
    def is_alt(self) -> bool:
        return len(self.options) > 1


@dataclass(frozen=True)
class Slot:
    name: str
    min: int
    max: int
    nots: tuple[str, ...]


@dataclass(frozen=True)
class Gap:
    max: int


Element = Fixed | Slot | Gap
Mode = Literal["sentence", "clause", "window"]


@dataclass(frozen=True)
class Scope:
    mode: Mode
    window: int | None = None


@dataclass(frozen=True)
class Pattern:
    """A parsed pattern.

    `fixed[i]` is F(i+1) of the spec; `between[i]` is the element between
    `fixed[i]` and `fixed[i + 1]` (None when they are adjacent).
    """

    fixed: tuple[Fixed, ...]
    between: tuple[Slot | Gap | None, ...]
    open_slot: Slot | None
    scope: Scope

    def canonical(self) -> str:
        parts: list[str] = []
        for index, fixed in enumerate(self.fixed):
            parts.append(_format_fixed(fixed))
            if index < len(self.between):
                gap_or_slot = self.between[index]
                if gap_or_slot is not None:
                    parts.append(_format_element(gap_or_slot))
        if self.open_slot is not None:
            parts.append(_format_element(self.open_slot))
        if self.scope.mode == "window":
            parts.append(f"@window:{self.scope.window}")
        else:
            parts.append(f"@{self.scope.mode}")
        return " ".join(parts)


def parse(expression: str) -> Pattern:
    tokens = _tokenize(expression)
    scopes = [index for index, token in enumerate(tokens) if token.startswith("@")]
    if len(scopes) > 1 or (scopes and scopes[0] != len(tokens) - 1):
        raise DslError("R7", "use at most one scope, as the last element")
    scope = Scope("sentence")
    if scopes:
        scope = _parse_scope(tokens.pop())
    elements = [_parse_element(token) for token in tokens]
    if not elements or not isinstance(elements[0], Fixed):
        raise DslError("R1", "a pattern must start with a fixed element")

    fixed: list[Fixed] = []
    between: list[Slot | Gap | None] = []
    pending: Slot | Gap | None = None
    for element in elements:
        if isinstance(element, Fixed):
            if fixed:
                between.append(pending)
            fixed.append(element)
            pending = None
        elif pending is not None:
            raise DslError(
                "R2", "at most one slot or gap may stand between fixed elements"
            )
        else:
            pending = element
    if isinstance(pending, Gap):
        raise DslError("R4", "a pattern cannot end with a gap")

    names = [s.name for s in [*between, pending] if isinstance(s, Slot)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise DslError("R3", f"slot name used more than once: {', '.join(duplicates)}")
    return Pattern(tuple(fixed), tuple(between), pending, scope)


def _tokenize(expression: str) -> list[str]:
    # Only ASCII spaces separate elements, except inside braces where they separate
    # `not` clauses. Other whitespace is left in the token and rejected by
    # `_check_literal`, so a U+3000 pasted into a pattern cannot become a literal.
    tokens: list[str] = []
    index = 0
    while index < len(expression):
        if expression[index] == " ":
            index += 1
            continue
        if expression[index] == "{":
            close = expression.find("}", index)
            if close < 0:
                raise DslError("syntax", f"unclosed slot: {expression[index:]}")
            end = close + 1
            if end < len(expression) and expression[end] != " ":
                raise DslError(
                    "syntax", f"missing space after: {expression[index:end]}"
                )
        else:
            end = expression.find(" ", index)
            end = len(expression) if end < 0 else end
        tokens.append(expression[index:end])
        index = end
    return tokens


def _parse_scope(token: str) -> Scope:
    if token == "@sentence":
        return Scope("sentence")
    if token == "@clause":
        return Scope("clause")
    if token.startswith("@window:"):
        size = _parse_int(token.removeprefix("@window:"), token)
        if size < 1:
            raise DslError("R6", f"window size must be at least 1: {token}")
        return Scope("window", size)
    raise DslError("syntax", f"unknown scope: {token}")


def _parse_element(token: str) -> Element:
    if token.startswith("{"):
        if not token.endswith("}"):
            raise DslError("syntax", f"unclosed slot: {token}")
        return _parse_braced(token[1:-1], token)
    if token.startswith("("):
        if not token.endswith(")"):
            raise DslError("syntax", f"unclosed alternative: {token}")
        options = token[1:-1].split("|")
        if len(options) < 2:
            raise DslError(
                "syntax", f"an alternative needs at least two options: {token}"
            )
        if any(option == "" for option in options):
            raise DslError("R5", f"empty option in alternative: {token}")
        if len(set(options)) != len(options):
            raise DslError("R5", f"repeated option in alternative: {token}")
        return Fixed(tuple(_check_literal(option, token) for option in options))
    return Fixed((_check_literal(token, token),))


def _parse_braced(body: str, token: str) -> Slot | Gap:
    if body.startswith(".."):
        if " " in body:
            raise DslError("R8", f"a gap cannot have constraints: {{{body}}}")
        size = _parse_int(body[2:], token)
        if size < 1:
            raise DslError("R6", f"gap maximum must be at least 1: {token}")
        return Gap(size)
    words = [word for word in body.split(" ") if word]
    if not words:
        raise DslError("syntax", f"empty slot: {token}")
    head, rest = words[0], words[1:]
    name, _, length = head.partition(":")
    if not _NAME_RE.match(name):
        raise DslError("syntax", f"invalid slot name: {token}")
    low, high = DEFAULT_SLOT_MIN, DEFAULT_SLOT_MAX
    if length:
        low_text, separator, high_text = length.partition("..")
        if not separator:
            raise DslError("syntax", f"slot length must be min..max: {token}")
        low, high = _parse_int(low_text, token), _parse_int(high_text, token)
    if low < 1 or high < low:
        raise DslError("R6", f"slot length must satisfy 1 <= min <= max: {token}")
    nots: list[str] = []
    while rest:
        if rest[0] != "not":
            raise DslError("syntax", f"expected 'not' in slot: {token}")
        if len(rest) < 2:
            raise DslError("R8", f"empty 'not' literal: {token}")
        nots.append(_check_literal(rest[1], token))
        rest = rest[2:]
    return Slot(name, low, high, tuple(nots))


def _parse_int(text: str, token: str) -> int:
    if not _INT_RE.match(text):
        raise DslError("syntax", f"expected a decimal number in {token}")
    return int(text)


def _check_literal(literal: str, token: str) -> str:
    if any(char in _SYNTAX_CHARS or char.isspace() for char in literal):
        raise DslError("syntax", f"invalid character in literal: {token}")
    return literal


def _format_fixed(fixed: Fixed) -> str:
    if fixed.is_alt:
        return "(" + "|".join(fixed.options) + ")"
    return fixed.options[0]


def _format_element(element: Slot | Gap) -> str:
    if isinstance(element, Gap):
        return f"{{..{element.max}}}"
    nots = "".join(f" not {literal}" for literal in element.nots)
    return f"{{{element.name}:{element.min}..{element.max}{nots}}}"
