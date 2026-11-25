"""Normalized text with its source map (docs/spec/m0-spec.md sections 1 and 2)."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import cached_property


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
    # True when every span is exact 1-to-1 with no gaps anywhere (e.g. identity).
    _contiguous: bool = field(default=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.spans) != len(self.text):
            raise ValueError("every normalized code point needs one source span")

    @cached_property
    def _deletion_index(
        self,
    ) -> tuple[bool, tuple[int, ...], tuple[tuple[int, int], ...]]:
        """Sparse index of source gaps between consecutive normalized code points."""
        spans = self.spans
        if len(spans) <= 1:
            return True, (), ()
        positions: list[int] = []
        gaps: list[tuple[int, int]] = []
        prev_start, cursor = spans[0]
        for index in range(1, len(spans)):
            span_start, span_end = spans[index]
            if span_start < prev_start or span_end < cursor:
                return False, (), ()
            if span_start > cursor:
                positions.append(index)
                gaps.append((cursor, span_start))
            prev_start = span_start
            if span_end > cursor:
                cursor = span_end
        return True, tuple(positions), tuple(gaps)

    @cached_property
    def _removal_index(self) -> dict[str, tuple[tuple[int, int, int], ...]]:
        return {
            name: tuple(sorted(getattr(self, name)))
            for name in ("notes", "annotations")
        }

    @cached_property
    def sorted_barriers(self) -> tuple[int, ...]:
        """Sorted barrier positions reused across patterns matching this text."""
        return tuple(sorted(self.barriers))

    def removals_between(
        self, name: str, start: int, end: int
    ) -> tuple[tuple[int, int, int], ...]:
        """Removed metadata strictly inside a normalized span, including ties."""
        items = self._removal_index[name]
        first = bisect_right(items, start, key=lambda item: item[0])
        last = bisect_left(items, end, key=lambda item: item[0])
        return items[first:last]

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
        if self._contiguous or end - start <= 1:
            return SourceSpan(src_start, src_end, ())
        if end - start > 64:
            monotonic, positions, gaps = self._deletion_index
            if monotonic:
                if not positions:
                    return SourceSpan(src_start, src_end, ())
                lo = bisect_right(positions, start)
                hi = bisect_left(positions, end)
                return SourceSpan(src_start, src_end, gaps[lo:hi])
        deleted: list[tuple[int, int]] = []
        cursor = self.spans[start][1]
        for index in range(start + 1, end):
            span_start, span_end = self.spans[index]
            if span_start > cursor:
                deleted.append((cursor, span_start))
            if span_end > cursor:
                cursor = span_end
        return SourceSpan(src_start, src_end, tuple(deleted))


def identity(reference: str) -> NormalizedText:
    """Identity normalization: matching text equals the reference text (spec 2.1)."""
    n = len(reference)
    return NormalizedText(
        reference=reference,
        text=reference,
        spans=tuple(zip(range(n), range(1, n + 1), strict=True)),
        _contiguous=True,
    )
