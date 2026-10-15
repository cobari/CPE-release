"""Reference word search with an independent parser and exhaustive leaf spans.

The wire has query, mode (sentence or clause), texts and settings. Text and
normalization fields use the structural reference wire's names and defaults;
only delimiter sets and long_segment_threshold apply to this search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ._text import Options, Text, normalize, options


@dataclass(frozen=True)
class Query:
    """A normalized word, raw regex, or Boolean/proximity expression."""

    kind: str
    value: str = ""
    children: tuple[Query, ...] = ()
    distance: int = 0


def _tokens(query: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    i = 0
    while i < len(query):
        char = query[i]
        if char.isspace():
            i += 1
            continue
        if char in "()":
            result.append((char, char))
            i += 1
            continue
        if char == '"':
            end = query.find('"', i + 1)
            if end < 0:
                raise ValueError("unclosed phrase")
            result.append(("word", query[i + 1 : end]))
            i = end + 1
            continue
        if char == "-" and i + 1 < len(query) and not query[i + 1].isspace():
            result.append(("NOT", "-"))
            i += 1
            continue
        if char == "/":
            end = i + 1
            while end < len(query):
                if query[end] == "\\":
                    end += 2
                elif query[end] == "/":
                    break
                else:
                    end += 1
            if i + 1 < end < len(query):
                value = query[i + 1 : end]
                try:
                    re.compile(value)
                except re.error as error:
                    raise ValueError("invalid regex") from error
                result.append(("regex", value))
                i = end + 1
                continue
        end = i
        while end < len(query) and not query[end].isspace() and query[end] not in '()"':
            end += 1
        value = query[i:end]
        if not value:
            raise ValueError("invalid token")
        kind = (
            value
            if value in ("AND", "OR", "NOT")
            else "NEAR"
            if re.fullmatch(r"NEAR/\d+", value)
            else "word"
        )
        result.append((kind, value))
        i = end
    return result


def parse(query: str, cfg: Options) -> Query:
    """Precedence climbing: NEAR > NOT > AND (also implicit) > OR."""
    tokens = _tokens(query)
    position = 0

    def expression(minimum: int) -> Query:
        nonlocal position
        if position == len(tokens):
            raise ValueError("query ended unexpectedly")
        kind, value = tokens[position]
        position += 1
        if kind == "NOT":
            node = Query("not", children=(expression(30),))
        elif kind == "(":
            node = expression(0)
            if position == len(tokens) or tokens[position][0] != ")":
                raise ValueError("unclosed parenthesis")
            position += 1
        elif kind in ("word", "regex"):
            if kind == "word":
                value = cfg.characters(value)
                if not value:
                    raise ValueError("empty normalized query term")
            node = Query(kind, value)
        else:
            raise ValueError("expected a query term")
        while position < len(tokens):
            kind, value = tokens[position]
            implicit = kind in ("word", "regex", "NOT", "(")
            precedence = {"OR": 10, "AND": 20, "NEAR": 40}.get(
                kind, 20 if implicit else -1
            )
            if precedence < minimum:
                break
            if not implicit:
                position += 1
            right = expression(precedence + 1)
            if kind == "NEAR":
                if node.kind not in ("word", "regex") or right.kind not in (
                    "word",
                    "regex",
                ):
                    raise ValueError("NEAR requires two leaves")
                try:
                    distance = int(value[5:])
                except ValueError as error:
                    raise ValueError("invalid NEAR distance") from error
                node = Query("near", children=(node, right), distance=distance)
            else:
                node = Query("or" if kind == "OR" else "and", children=(node, right))
        return node

    tree = expression(0)
    if position != len(tokens):
        raise ValueError("unexpected query token")
    return tree


def _crosses(text: Text, start: int, end: int) -> bool:
    return any(start < barrier < end for barrier in text.barriers)


def _leaf_spans(
    leaf: Query, text: Text, start: int, end: int
) -> tuple[tuple[int, int], ...]:
    body = text.value[start:end]
    if leaf.kind == "word":
        found = (
            (i, i + len(leaf.value))
            for i in range(len(body) - len(leaf.value) + 1)
            if body[i : i + len(leaf.value)] == leaf.value
        )
    else:
        found = (m.span() for m in re.finditer(leaf.value, body) if m.start() < m.end())
    return tuple(
        (start + a, start + b)
        for a, b in found
        if not _crosses(text, start + a, start + b)
    )


def _evaluate(
    tree: Query, text: Text, leaves: dict[Query, tuple[tuple[int, int], ...]]
) -> bool:
    if tree.kind in ("word", "regex"):
        return bool(leaves[tree])
    if tree.kind == "not":
        return not _evaluate(tree.children[0], text, leaves)
    if tree.kind == "and":
        return all(_evaluate(child, text, leaves) for child in tree.children)
    if tree.kind == "or":
        return any(_evaluate(child, text, leaves) for child in tree.children)
    left, right = tree.children
    return any(
        max(a, c) - min(b, d) <= tree.distance
        and not _crosses(text, min(a, c), max(b, d))
        for a, b in leaves[left]
        for c, d in leaves[right]
    )


def _leaves(tree: Query, positive: bool | None = None) -> set[Query]:
    if tree.kind in ("word", "regex"):
        return {tree} if positive is not False else set()
    if tree.kind == "not" and positive is not None:
        return _leaves(tree.children[0], not positive)
    return set().union(*(_leaves(child, positive) for child in tree.children))


def _segments(value: str, delimiters: str) -> list[tuple[int, int]]:
    result = []
    start: int | None = None
    after_delimiter = False
    for i, char in enumerate(value):
        excluded = char in delimiters or (after_delimiter and char in "」』”’")
        after_delimiter = excluded
        if excluded:
            if start is not None:
                result.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        result.append((start, len(value)))
    return result


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _choice(value: Any, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {name}")
    return _string(value, name)


def search(wire: dict[str, Any]) -> list[dict[str, Any]]:
    """Produce ordered occurrence records without importing any production code."""
    query = _string(wire["query"], "query")
    mode = _choice(wire.get("mode", "sentence"), ("sentence", "clause"), "mode")
    raw = wire.get("settings", {})
    if not isinstance(raw, dict) or not isinstance(wire.get("texts"), list):
        raise ValueError("settings/texts must be an object/array")
    sentence = _string(raw.get("sentence_delimiters", "。！？"), "sentence delimiters")
    clause = _string(raw.get("clause_delimiters", "。！？，；："), "clause delimiters")
    threshold = raw.get("long_segment_threshold", 200)
    if type(threshold) is not int or threshold < 0:
        raise ValueError("long segment threshold must be a nonnegative integer")
    texts = wire["texts"]
    for item in texts:
        if not isinstance(item, dict):
            raise ValueError("text must be an object")
        _choice(item.get("punctuation", "modern"), ("modern", "pause"), "punctuation")
        _choice(item.get("full_stop", "sentence"), ("sentence", "reading"), "full_stop")
    cfg = options(
        raw.get("normalization", {}),
        sentence
        + clause
        + ("、" if any(t.get("punctuation") == "pause" for t in texts) else ""),
    )
    tree = parse(query, cfg)
    all_leaves, positives = _leaves(tree), _leaves(tree, True)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in texts:
        identifier = _string(item["text_id"], "text_id")
        if not identifier or identifier in seen:
            raise ValueError("text_id must be nonempty and unique")
        seen.add(identifier)
        if item.get("text_type", "original") != "original":
            raise ValueError("only original texts are searched")
        extent = _choice(
            item.get("range", "excerpt"), ("excerpt", "volume", "whole_work"), "range"
        )
        edges = tuple(
            _choice(item.get(key, "truncated"), ("verified", "truncated"), key)
            for key in ("boundary_start", "boundary_end")
        )
        text = normalize(_string(item["text"], "text"), cfg)
        text_sentence = (
            sentence.replace("。", "")
            if item.get("full_stop") == "reading"
            else sentence
        )
        delimiters = (
            text_sentence
            if mode == "sentence"
            else clause + ("、" if item.get("punctuation") == "pause" else "")
        )
        segments = _segments(text.value, delimiters)
        warned = bool(segments) and sum(b - a for a, b in segments) > threshold * len(
            segments
        )
        for start, end in segments:
            leaves = {leaf: _leaf_spans(leaf, text, start, end) for leaf in all_leaves}
            if not _evaluate(tree, text, leaves):
                continue
            left = edges[0] if start == 0 else "editorial"
            right = edges[1] if end == len(text.value) else "editorial"
            effective = tuple(
                "inferred" if warned and edge == "editorial" else edge
                for edge in (left, right)
            )
            evidence = (
                "S1"
                if any(e in ("truncated", "inferred") for e in effective)
                else "S3"
                if extent != "excerpt"
                else "S2"
            )
            context = (
                "snippet"
                if "truncated" in effective
                else "window"
                if "inferred" in effective
                else "full"
            )
            hits = []
            for a, b in sorted({span for leaf in positives for span in leaves[leaf]}):
                hits.append(
                    {
                        "text": text.value[a:b],
                        "norm": [a, b],
                        **text.span(a, b),
                        "joined_notes": [
                            {"norm": q, "src": [s, t]}
                            for q, s, t in text.joined
                            if a < q < b
                        ],
                        "joined_annotations": [
                            {"norm": q, "src": [s, t]}
                            for q, s, t in text.annotations
                            if a < q < b
                        ],
                    }
                )
            mapped = text.span(start, end)
            result.append(
                {
                    "schema": "cpe.occurrence/1",
                    "result_id": f"r{len(result) + 1:06d}",
                    "kind": "occurrence",
                    "query": query,
                    "source": "local",
                    "text_id": identifier,
                    "mode": mode,
                    "segment": {
                        "norm": [start, end],
                        "src": mapped["src"],
                        "src_deleted": mapped["src_deleted"],
                        "boundary_start": left,
                        "boundary_end": right,
                    },
                    "evidence": evidence,
                    "context_status": context,
                    "grade": "record_candidate",
                    "editorial_boundaries": "editorial" in (left, right),
                    "long_segment_warning": warned,
                    "hits": hits,
                    "review_status": "unreviewed",
                }
            )
    return result
