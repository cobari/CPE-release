"""Match result records (spec section 10)."""

from __future__ import annotations

from dataclasses import dataclass, field

from cpe.match.segment import Segment
from cpe.normalize.text import SourceSpan


@dataclass(frozen=True)
class FixedHit:
    literal: str
    start: int
    end: int
    src: SourceSpan
    src_exact: tuple[bool, bool] = (True, True)


@dataclass(frozen=True)
class SlotHit:
    name: str
    start: int
    end: int
    src: SourceSpan
    src_exact: tuple[bool, bool] = (True, True)


@dataclass(frozen=True)
class Boundary:
    """A normalized boundary and its source position (spec 1.7)."""

    norm: int
    src: int
    exact: bool = True


@dataclass(frozen=True)
class Representative:
    end: Boundary
    selected_by: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class OpenSlotHit:
    name: str
    b_start: Boundary
    ends: tuple[Boundary, ...]
    observed_end: Boundary
    truncated: bool
    representatives: tuple[Representative, ...]
    b_end_status: str = "open"


@dataclass(frozen=True)
class Adjacency:
    slot: str
    kind: str  # "source" or "normalized" (spec 5.4)


@dataclass
class Result:
    kind: str  # "matched", "adjacent" or "indeterminate"
    pattern_id: str
    pattern: str
    text_id: str
    mode: str
    segment: Segment
    segment_src: SourceSpan
    evidence: str
    context_status: str
    grade: str
    editorial_boundaries: bool
    fixed: tuple[FixedHit, ...] = ()
    slots: tuple[SlotHit, ...] = ()
    open_slot: OpenSlotHit | None = None
    adjacency: Adjacency | None = None
    indeterminate_reason: str | None = None
    long_segment_warning: bool = False
    alt_count: int = 0
    result_id: str = ""
    source: str = "local"
    review_status: str = "unreviewed"
    # (normalized removal position, source start, source end) per note (spec 2.5).
    joined_notes: tuple[tuple[int, int, int], ...] = field(default=())
    joined_annotations: tuple[tuple[int, int, int], ...] = field(default=())

    @property
    def ambiguous(self) -> bool:
        return self.alt_count > 0

    @property
    def anchor(self) -> int:
        """`p1`, or the segment start for a result without placement (spec 8.6)."""
        return self.fixed[0].start if self.fixed else self.segment.start
