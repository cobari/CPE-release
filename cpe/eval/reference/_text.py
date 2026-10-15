"""Normalize reference input without importing production text structures."""

from dataclasses import dataclass, replace
from typing import Any

from ._dsl import Expression, Fixed, Slot


def _selector(char: str) -> bool:
    return 0xFE00 <= ord(char) <= 0xFE0F or 0xE0100 <= ord(char) <= 0xE01EF


def _deletable(char: str) -> bool:
    return char in " \t\u3000\r\n" or _selector(char)


@dataclass(frozen=True)
class Options:
    """Validated per-code-point transformations and source note syntax."""

    selectors: str
    whitespace: str
    breaks: str
    notes: str
    opening: str | None
    closing: str | None
    annotation_opening: str | None
    annotation_closing: str | None
    mapping: dict[str, str]

    def characters(self, value: str) -> str:
        return "".join(
            self.mapping.get(char, char)
            for char in value
            if not (
                (self.breaks == "delete" and char in "\r\n")
                or (self.whitespace == "delete" and char in " \t\u3000")
                or (self.selectors == "delete" and _selector(char))
            )
        )

    def literal(self, value: str) -> str:
        if (
            not self.mapping
            and (self.selectors, self.whitespace, self.breaks) == ("keep",) * 3
        ):
            return value
        normalized = self.characters(value)
        if not normalized:
            raise ValueError("normalization made a pattern literal empty")
        return normalized

    def expression(self, expression: Expression) -> Expression:
        if (
            not self.mapping
            and (self.selectors, self.whitespace, self.breaks) == ("keep",) * 3
        ):
            return expression
        fixed = []
        for element in expression.fixed:
            options = tuple(self.literal(value) for value in element.options)
            if len(set(options)) != len(options):
                raise ValueError("R5: alternatives repeat after normalization")
            fixed.append(Fixed(options))

        def slot(value: Slot) -> Slot:
            return replace(value, forbidden=tuple(map(self.literal, value.forbidden)))

        return replace(
            expression,
            fixed=tuple(fixed),
            between=tuple(
                slot(value) if isinstance(value, Slot) else value
                for value in expression.between
            ),
            tail=slot(expression.tail) if expression.tail is not None else None,
        )


def options(value: Any, delimiters: str) -> Options:
    if not isinstance(value, dict):
        raise ValueError("normalization must be an object")
    modes = []
    for key in ("variation_selectors", "whitespace", "line_breaks", "notes"):
        allowed = ("barrier", "join") if key == "notes" else ("keep", "delete")
        default = "delete" if key == "line_breaks" else allowed[0]
        mode = value.get(key, default)
        if not isinstance(mode, str) or mode not in allowed:
            raise ValueError(f"invalid normalization option {key}")
        modes.append(mode)

    def markers(kind: str) -> tuple[str | None, str | None]:
        opening, closing = value.get(kind + "_open"), value.get(kind + "_close")
        if opening is not None or closing is not None:
            if (
                not isinstance(opening, str)
                or not isinstance(closing, str)
                or not opening
                or not closing
                or opening == closing
            ):
                raise ValueError(
                    f"{kind} markers must be distinct nonempty strings together"
                )
        return opening, closing

    opening, closing = markers("note")
    annotation_opening, annotation_closing = markers("annotation")
    configured = [
        marker
        for marker in (opening, closing, annotation_opening, annotation_closing)
        if marker is not None
    ]
    if len(configured) != len(set(configured)):
        raise ValueError("annotation and note markers must all differ")
    mapping: dict[str, str] = {}
    variant = value.get("variants")
    if variant is not None:
        if not isinstance(variant, dict) or set(variant) != {"id", "version", "map"}:
            raise ValueError("variants must contain exactly id, version, map")
        for key in ("id", "version"):
            if not isinstance(variant[key], str) or not variant[key]:
                raise ValueError(f"variant {key} must be a nonempty string")
        if not isinstance(variant["map"], dict):
            raise ValueError("variant map must be an object")
        for source, output in variant["map"].items():
            if (
                not isinstance(source, str)
                or len(source) != 1
                or not isinstance(output, str)
                or not output
                or source == output
                or any(_deletable(char) for char in output)
                or any(char in delimiters for char in source + output)
            ):
                raise ValueError("invalid variant mapping")
            mapping[source] = output
    return Options(
        modes[0],
        modes[1],
        modes[2],
        modes[3],
        opening,
        closing,
        annotation_opening,
        annotation_closing,
        mapping,
    )


@dataclass(frozen=True)
class Text:
    """Final output with the source index of every retained output code point."""

    value: str
    sources: tuple[int, ...] | None
    deleted: frozenset[int]
    barriers: tuple[int, ...]
    joined: tuple[tuple[int, int, int], ...]
    annotations: tuple[tuple[int, int, int], ...]

    def boundary(self, position: int, start: bool = False) -> dict[str, Any]:
        sources = self.sources
        if sources is None:
            return {"norm": position, "src": position, "exact": True}
        if not sources:
            source = 0
        elif start:
            source = sources[position] if position < len(sources) else sources[-1] + 1
        else:
            source = sources[position - 1] + 1 if position else sources[0]
        exact = not (
            0 < position < len(sources) and sources[position - 1] == sources[position]
        )
        return {"norm": position, "src": source, "exact": exact}

    def span(self, begin: int, end: int) -> dict[str, Any]:
        left, right = self.boundary(begin, True), self.boundary(end)
        deleted: list[list[int]] = []
        for position in range(left["src"], right["src"]):
            if position in self.deleted:
                if deleted and deleted[-1][1] == position:
                    deleted[-1][1] += 1
                else:
                    deleted.append([position, position + 1])
        return {
            "src": [left["src"], right["src"]],
            "src_exact": [left["exact"], right["exact"]],
            "src_deleted": deleted,
        }

    def record(self, record: dict[str, Any]) -> None:
        if self.sources is None:
            return
        segment = record["segment"]
        mapped = self.span(*segment["norm"])
        segment["src"] = mapped["src"]
        segment["src_deleted"] = mapped["src_deleted"]
        for fixed in record["fixed"]:
            mapped = self.span(*fixed["norm"])
            fixed["src"], fixed["src_exact"] = mapped["src"], mapped["src_exact"]
        for slot in record["slots"]:
            slot.update(self.span(*slot["norm"]))
        opened = record["open_slot"]
        if opened is not None:
            begin = opened["b_start"]["norm"]
            opened["b_start"] = self.boundary(begin, True)
            opened["ends"] = [self.boundary(end["norm"]) for end in opened["ends"]]
            observed = opened["observed_end"]["norm"]
            opened["observed_end"] = self.boundary(observed, observed == begin)
            for representative in opened["representatives"]:
                representative.update(self.boundary(representative["norm"]))
        if record["fixed"]:
            begin = record["fixed"][0]["norm"][0]
            end = (
                max(e["norm"] for e in opened["ends"])
                if opened is not None and opened["ends"]
                else record["fixed"][-1]["norm"][1]
            )
            record["joined_notes"] = [
                {"norm": q, "src": [start, stop]}
                for q, start, stop in self.joined
                if begin < q < end
            ]
            record["joined_annotations"] = [
                {"norm": q, "src": [start, stop]}
                for q, start, stop in self.annotations
                if begin < q < end
            ]


def normalize(reference: str, cfg: Options) -> Text:
    if (
        cfg.opening is None
        and cfg.annotation_opening is None
        and not cfg.mapping
        and (cfg.selectors, cfg.whitespace, cfg.breaks) == ("keep",) * 3
    ):
        return Text(reference, None, frozenset(), (), (), ())
    output: list[str] = []
    sources: list[int] = []
    deleted: set[int] = set()
    notes: list[tuple[int, int, int]] = []
    annotations: list[tuple[int, int, int]] = []
    kinds = [
        (opening, closing, removed)
        for opening, closing, removed in (
            (cfg.opening, cfg.closing, notes),
            (cfg.annotation_opening, cfg.annotation_closing, annotations),
        )
        if opening is not None and closing is not None
    ]
    position = 0
    while position < len(reference):
        for opening, closing, removed in kinds:
            if reference.startswith(opening, position):
                found = reference.find(closing, position + len(opening))
                if found < 0:
                    raise ValueError("unclosed note or annotation marker")
                end = found + len(closing)
                removed.append((len(output), position, end))
                deleted.update(range(position, end))
                position = end
                break
        else:
            if kinds and any(
                reference.startswith(closing, position) for _, closing, _ in kinds
            ):
                raise ValueError("closing marker outside a note or annotation")
            value = cfg.characters(reference[position])
            if not value:
                deleted.add(position)
            output.extend(value)
            sources.extend([position] * len(value))
            position += 1
    return Text(
        "".join(output),
        tuple(sources),
        frozenset(deleted),
        tuple(sorted({q for q, _, _ in notes})) if cfg.notes == "barrier" else (),
        tuple(sorted(notes)) if cfg.notes == "join" else (),
        tuple(sorted(annotations)),
    )
