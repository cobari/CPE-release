"""Parse the oracle's DSL independently of production code."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Fixed:
    """A literal or its ordered alternatives."""

    options: tuple[str, ...]


@dataclass(frozen=True)
class Slot:
    """A named interval and its literal exclusions."""

    name: str
    minimum: int
    maximum: int
    forbidden: tuple[str, ...]


@dataclass(frozen=True)
class Gap:
    """An unnamed interval that permits zero code points."""

    maximum: int


@dataclass(frozen=True)
class Expression:
    """The fixed elements and the independent constraints between them."""

    canonical: str
    mode: str
    window: int | None
    fixed: tuple[Fixed, ...]
    between: tuple[Slot | Gap | None, ...]
    tail: Slot | None


def _literal(value: str) -> str:
    if not value or any(c.isspace() or c in "{}()|@" for c in value):
        raise ValueError("syntax: invalid literal")
    return value


def _tokens(expr: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while position < len(expr):
        start = position
        char = expr[position]
        if char == " ":
            raise ValueError("syntax: unexpected space")
        if char in "{(":
            closing = "}" if char == "{" else ")"
            end = expr.find(closing, position + 1)
            if end < 0:
                raise ValueError("syntax: unclosed element")
            position = end + 1
        else:
            while position < len(expr) and expr[position] != " ":
                position += 1
        tokens.append(expr[start:position])
        if position < len(expr):
            if expr[position] != " ":
                raise ValueError("syntax: elements require ASCII spaces")
            while position < len(expr) and expr[position] == " ":
                position += 1
            if position == len(expr):
                raise ValueError("syntax: trailing space")
    if not tokens:
        raise ValueError("syntax: empty pattern")
    return tokens


def _element(token: str) -> Fixed | Slot | Gap:
    if token.startswith("("):
        options = tuple(token[1:-1].split("|"))
        if "" in options or len(set(options)) != len(options):
            raise ValueError("R5: alternatives must be nonempty and distinct")
        if len(options) < 2:
            raise ValueError("syntax: alternatives require at least two literals")
        return Fixed(tuple(_literal(option) for option in options))
    if not token.startswith("{"):
        return Fixed((_literal(token),))
    body = token[1:-1]
    if body.startswith(".."):
        if "not" in body:
            raise ValueError("R8: gaps cannot contain not constraints")
        if re.fullmatch(r"\.\.[0-9]+", body) is None:
            raise ValueError("syntax: invalid gap")
        maximum = int(body[2:])
        if maximum < 1:
            raise ValueError("R6: gap maximum must be positive")
        return Gap(maximum)
    parts = re.split(" +", body)
    head = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)(?::([0-9]+)\.\.([0-9]+))?", parts[0])
    if head is None:
        raise ValueError("syntax: invalid slot")
    minimum = int(head[2]) if head[2] is not None else 1
    maximum = int(head[3]) if head[3] is not None else 20
    if minimum < 1 or maximum < minimum:
        raise ValueError("R6: invalid slot length range")
    forbidden: list[str] = []
    for index in range(1, len(parts), 2):
        if parts[index] != "not" or index + 1 >= len(parts) or not parts[index + 1]:
            raise ValueError("R8: not requires a nonempty literal")
        forbidden.append(_literal(parts[index + 1]))
    return Slot(head[1], minimum, maximum, tuple(forbidden))


def parse(expr: str) -> Expression:
    """Parse the MVP grammar and reject violations with their R1–R8 names."""
    tokens = _tokens(expr)
    scopes = [index for index, token in enumerate(tokens) if token.startswith("@")]
    mode = "sentence"
    window: int | None = None
    if scopes:
        if len(scopes) != 1 or scopes[0] != len(tokens) - 1:
            raise ValueError("R7: a single scope must be last")
        scope = tokens.pop()
        if scope in ("@sentence", "@clause"):
            mode = scope[1:]
        elif re.fullmatch(r"@window:[0-9]+", scope):
            mode = "window"
            window = int(scope.partition(":")[2])
            if window < 1:
                raise ValueError("R6: window width must be positive")
        else:
            raise ValueError("R7: invalid scope")
    elements = [_element(token) for token in tokens]
    if not elements or not isinstance(elements[0], Fixed):
        raise ValueError("R1: the first element must be fixed")
    names: set[str] = set()
    for index, element in enumerate(elements):
        if (
            index
            and not isinstance(element, Fixed)
            and not isinstance(elements[index - 1], Fixed)
        ):
            raise ValueError("R2: variable elements cannot be adjacent")
        if isinstance(element, Slot):
            if element.name in names:
                raise ValueError("R3: slot names must be unique")
            names.add(element.name)
    if isinstance(elements[-1], Gap):
        raise ValueError("R4: a pattern cannot end with a gap")
    fixed: list[Fixed] = []
    between: list[Slot | Gap | None] = []
    pending: Slot | Gap | None = None
    canonical: list[str] = []
    for element in elements:
        if isinstance(element, Fixed):
            if fixed:
                between.append(pending)
            fixed.append(element)
            pending = None
            canonical.append(
                element.options[0]
                if len(element.options) == 1
                else f"({'|'.join(element.options)})"
            )
        elif isinstance(element, Slot):
            pending = element
            nots = "".join(f" not {value}" for value in element.forbidden)
            canonical.append(
                f"{{{element.name}:{element.minimum}..{element.maximum}{nots}}}"
            )
        else:
            pending = element
            canonical.append(f"{{..{element.maximum}}}")
    canonical.append(f"@window:{window}" if mode == "window" else f"@{mode}")
    return Expression(
        " ".join(canonical),
        mode,
        window,
        tuple(fixed),
        tuple(between),
        pending if isinstance(pending, Slot) else None,
    )
