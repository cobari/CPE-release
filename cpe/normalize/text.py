"""Normalized text with its source map (docs/spec/m0-spec.md sections 1 and 2)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    """A source span `[start, end)` and the deleted source spans inside it."""

    start: int
    end: int
    deleted: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class NormalizedText:
    """Text that matching runs on, mapped back to the unchanged reference text.

    `spans[i]` is the source span of `text[i]`. Code points of one one-to-many
    replacement share a span; deleted source code points belong to no span.
    `barriers` are normalized positions that no placement may cross (spec 2.5).
    """

    reference: str
    text: str
    spans: tuple[tuple[int, int], ...]
    barriers: frozenset[int] = frozenset()
    # (normalized removal position, source start, source end) per removed note.
    notes: tuple[tuple[int, int, int], ...] = ()
    # The same for attached annotations (spec 2.9); they never make a barrier.
    annotations: tuple[tuple[int, int, int], ...] = ()

    def __post_init__(self) -> None:
        if len(self.spans) != len(self.text):
            raise ValueError("every normalized code point needs one source span")

    def src_start(self, position: int) -> int:
        """Source position of a normalized boundary used as a start (spec 1.7)."""
        if position < len(self.text):
            return self.spans[position][0]
        return self.spans[-1][1] if self.spans else 0

    def src_end(self, position: int) -> int:
        """Source position of a normalized boundary used as an end (spec 1.7)."""
        if position > 0:
            return self.spans[position - 1][1]
        return self.spans[0][0] if self.spans else 0

    def is_exact(self, position: int) -> bool:
        """Whether a boundary falls between different source spans (spec 1.7)."""
        if position <= 0 or position >= len(self.text):
            return True
        return self.spans[position - 1] != self.spans[position]

    def src_span(self, start: int, end: int) -> SourceSpan:
        """Source span of a non-empty normalized span, with deletions (spec 1.6)."""
        if not 0 <= start < end <= len(self.text):
            raise ValueError(f"not a non-empty normalized span: [{start}, {end})")
        src_start, src_end = self.spans[start][0], self.spans[end - 1][1]
        deleted: list[tuple[int, int]] = []
        cursor = src_start
        for index in range(start, end):
            span_start, span_end = self.spans[index]
            if span_start > cursor:
                deleted.append((cursor, span_start))
            cursor = max(cursor, span_end)
        return SourceSpan(src_start, src_end, tuple(deleted))


def identity(reference: str) -> NormalizedText:
    """Identity normalization: matching text equals the reference text (spec 2.1)."""
    return NormalizedText(
        reference=reference,
        text=reference,
        spans=tuple((index, index + 1) for index in range(len(reference))),
    )
