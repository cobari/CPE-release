"""Boolean, phrase, proximity and regex queries over returned text.

Ported from the original `itkc_search.py` with the same grammar and precedence:

    or_expr  := and_expr ('OR' and_expr)*
    and_expr := unary (['AND'] unary)*          AND may be omitted
    unary    := ('NOT' | '-') unary | near
    near     := primary ['NEAR/n' primary]
    primary  := word | "phrase" | /regex/ | '(' or_expr ')'

Precedence from strongest: NEAR > NOT > AND > OR. This is a record filter for
API results; it is separate from the pattern DSL in cpe.match.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    text: str

    def __str__(self) -> str:
        return f'"{self.text}"'


@dataclass(frozen=True)
class Regex:
    pattern: re.Pattern[str]

    def __str__(self) -> str:
        return f"/{self.pattern.pattern}/"


@dataclass(frozen=True)
class Not:
    child: Node

    def __str__(self) -> str:
        return f"NOT {self.child}"


@dataclass(frozen=True)
class And:
    children: tuple[Node, ...]

    def __str__(self) -> str:
        return "(" + " AND ".join(map(str, self.children)) + ")"


@dataclass(frozen=True)
class Or:
    children: tuple[Node, ...]

    def __str__(self) -> str:
        return "(" + " OR ".join(map(str, self.children)) + ")"


@dataclass(frozen=True)
class Near:
    left: Term | Regex
    right: Term | Regex
    dist: int

    def __str__(self) -> str:
        return f"({self.left} NEAR/{self.dist} {self.right})"


Node = Term | Regex | Not | And | Or | Near
Leaf = Term | Regex

_TOKEN_RE = re.compile(
    r"""
      (?P<lp>\()
    | (?P<rp>\))
    | (?P<near>NEAR/\d+)(?=[\s()]|$)
    | (?P<op>AND|OR|NOT)(?=[\s()]|$)
    | (?P<phrase>"[^"]*")
    | (?P<regex>/(?:\\.|[^/\\])+/)
    | (?P<minus>-)(?=\S)
    | (?P<term>[^\s()"]+)
""",
    re.X,
)
_STARTS = {"lp", "phrase", "regex", "term", "minus"}


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            return tokens
        match = _TOKEN_RE.match(text, position)
        if not match:
            raise QueryError(
                f"cannot parse {text[position:]!r} (check that quotes are closed)"
            )
        kind = next(k for k, v in match.groupdict().items() if v is not None)
        tokens.append((kind, match.group(kind)))
        position = match.end()


def parse(text: str) -> Node:
    return _Parser(text).parse()


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = tokenize(text)
        self.index = 0

    def peek(self) -> tuple[str | None, str | None]:
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return None, None

    def take(self) -> tuple[str | None, str | None]:
        token = self.peek()
        self.index += 1
        return token

    def parse(self) -> Node:
        if not self.tokens:
            raise QueryError("empty query")
        node = self.parse_or()
        if self.index < len(self.tokens):
            raise QueryError(f"unexpected {self.peek()[1]!r}")
        return node

    def parse_or(self) -> Node:
        parts = [self.parse_and()]
        while self.peek() == ("op", "OR"):
            self.take()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def parse_and(self) -> Node:
        parts = [self.parse_unary()]
        while True:
            kind, value = self.peek()
            if (kind, value) == ("op", "AND"):
                self.take()
                parts.append(self.parse_unary())
            elif kind in _STARTS or (kind, value) == ("op", "NOT"):
                parts.append(self.parse_unary())
            else:
                break
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def parse_unary(self) -> Node:
        kind, value = self.peek()
        if kind == "minus" or (kind, value) == ("op", "NOT"):
            self.take()
            return Not(self.parse_unary())
        return self.parse_near()

    def parse_near(self) -> Node:
        left = self.parse_primary()
        kind, value = self.peek()
        if kind != "near":
            return left
        self.take()
        right = self.parse_primary()
        if not isinstance(left, Term | Regex) or not isinstance(right, Term | Regex):
            raise QueryError("NEAR takes only words, phrases or regexes on each side")
        assert value is not None
        return Near(left, right, int(value.split("/")[1]))

    def parse_primary(self) -> Node:
        kind, value = self.take()
        if kind == "lp":
            node = self.parse_or()
            if self.take()[0] != "rp":
                raise QueryError("unclosed parenthesis")
            return node
        if kind == "term" and value is not None:
            return Term(value)
        if kind == "phrase" and value is not None:
            if len(value) <= 2:
                raise QueryError('an empty phrase ("") is not allowed')
            return Term(value[1:-1])
        if kind == "regex" and value is not None:
            try:
                return Regex(re.compile(value[1:-1]))
            except re.error as error:
                raise QueryError(f"invalid regex {value}: {error}") from None
        if value:
            raise QueryError(f"{value!r} cannot appear here")
        raise QueryError("query ended unexpectedly")


def spans(leaf: Leaf, text: str) -> list[tuple[int, int]]:
    if isinstance(leaf, Term):
        found = []
        start = 0
        while (index := text.find(leaf.text, start)) != -1:
            found.append((index, index + len(leaf.text)))
            start = index + 1
        return found
    return [m.span() for m in leaf.pattern.finditer(text) if m.end() > m.start()]


def evaluate(
    node: Node, text: str, allowed: Callable[[int, int], bool] | None = None
) -> bool:
    """Whether `text` satisfies the query.

    `allowed(start, end)` rejects spans that may not be formed, such as spans
    crossing a note barrier in local search (spec 14.2): a leaf hit and the
    whole stretch of a NEAR pair must be allowed. Independent terms joined by
    AND are tested separately, so they may lie on both sides of a barrier.
    """
    if isinstance(node, Term | Regex):
        return bool(leaf_spans(node, text, allowed))
    if isinstance(node, Not):
        return not evaluate(node.child, text, allowed)
    if isinstance(node, And):
        return all(evaluate(child, text, allowed) for child in node.children)
    if isinstance(node, Or):
        return any(evaluate(child, text, allowed) for child in node.children)
    left = leaf_spans(node.left, text, allowed)
    right = leaf_spans(node.right, text, allowed)
    # Gap = code points between the two matches; overlapping matches give <= 0.
    return any(
        max(s1, s2) - min(e1, e2) <= node.dist
        and (allowed is None or allowed(min(s1, s2), max(e1, e2)))
        for s1, e1 in left
        for s2, e2 in right
    )


def leaf_spans(
    leaf: Leaf, text: str, allowed: Callable[[int, int], bool] | None = None
) -> list[tuple[int, int]]:
    found = spans(leaf, text)
    return found if allowed is None else [s for s in found if allowed(*s)]


def positive_leaves(node: Node, negated: bool = False) -> list[Leaf]:
    if isinstance(node, Term | Regex):
        return [] if negated else [node]
    if isinstance(node, Not):
        return positive_leaves(node.child, not negated)
    if isinstance(node, Near):
        return positive_leaves(node.left, negated) + positive_leaves(
            node.right, negated
        )
    return [leaf for child in node.children for leaf in positive_leaves(child, negated)]


def highlight(text: str, leaves: list[Leaf]) -> str:
    marks = sorted(span for leaf in leaves for span in spans(leaf, text))
    merged: list[list[int]] = []
    for start, end in marks:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts: list[str] = []
    position = 0
    for start, end in merged:
        parts += [text[position:start], "【", text[start:end], "】"]
        position = end
    parts.append(text[position:])
    return "".join(parts)


def plan_keywords(node: Node, count: Callable[[str], int]) -> list[str] | None:
    """Keywords to send to the API; the union of their results contains every hit.

    None when no keyword can be chosen (only NOT or regex). For AND and NEAR the
    branch with the fewest results is sent; for OR every branch is sent.
    """
    if isinstance(node, Term):
        return [node.text]
    if isinstance(node, Regex | Not):
        return None
    if isinstance(node, Or):
        branches = [plan_keywords(child, count) for child in node.children]
        if any(branch is None for branch in branches):
            return None
        return list(dict.fromkeys(k for branch in branches if branch for k in branch))
    children: tuple[Node, ...] = (
        (node.left, node.right) if isinstance(node, Near) else node.children
    )
    options = [o for o in (plan_keywords(child, count) for child in children) if o]
    if not options:
        return None
    return min(options, key=lambda keywords: sum(count(k) for k in keywords))
