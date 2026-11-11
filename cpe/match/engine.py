"""Backtracking pattern matcher (spec sections 4, 5, 6, 8)."""

from __future__ import annotations

import bisect
import heapq
import itertools
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace

from cpe.cancellation import Checkpoint
from cpe.match import segment as seg
from cpe.match.dsl import Fixed, Gap, Pattern, Slot
from cpe.match.pattern_file import (
    Diagnostic,
    Heuristics,
    PatternFileError,
    PatternSpec,
)
from cpe.match.result import (
    Adjacency,
    Boundary,
    FixedHit,
    OpenSlotHit,
    Representative,
    Result,
    SlotHit,
)
from cpe.normalize.options import NormalizationError, Options
from cpe.normalize.text import NormalizedText

KIND_ORDER = {"matched": 0, "adjacent": 1, "indeterminate": 2}


@dataclass(frozen=True)
class Settings:
    sentence_delimiters: frozenset[str] = seg.SENTENCE_DELIMITERS
    clause_delimiters: frozenset[str] = seg.CLAUSE_DELIMITERS
    state_limit: int = 100_000
    result_limit: int = 200
    long_segment_threshold: int = 200
    # Applied to fixed and `not` literals; the caller normalizes the text with
    # the same options (spec 2.6).
    normalization: Options = field(default_factory=Options)

    def __post_init__(self) -> None:
        table = self.normalization.variants
        if table is None:
            return
        delimiters = self.sentence_delimiters | self.clause_delimiters
        for key, value in table.map.items():
            # Spec 2.7: a mapping that adds, moves or removes a delimiter would
            # make boundary provenance claim punctuation the source lacks.
            if key in delimiters or any(char in delimiters for char in value):
                raise NormalizationError(
                    f"variant {key!r} -> {value!r} involves a delimiter"
                )


@dataclass(frozen=True)
class TextInput:
    text_id: str
    normalized: NormalizedText
    range: seg.Range = "excerpt"
    # Text-edge evidence comes from the manifest, never from the range (spec 3.3):
    # a complete volume can still start or end mid-sentence.
    boundary_start: seg.TextEdge = "truncated"
    boundary_end: seg.TextEdge = "truncated"
    # How the text uses U+3001 (spec 3.7): under "pause" it separates clauses.
    punctuation: seg.Punctuation = "modern"
    # How the text uses U+3002 (spec 3.8): under "reading" it does not end a
    # sentence, so a pattern can span it.
    full_stop: seg.FullStop = "sentence"
    # Whether halfwidth . ? ! , ; : are punctuation in this text (spec 3.9).
    halfwidth: bool = False

    _segments: dict[frozenset[str], tuple[seg.Segment, ...]] = field(
        default_factory=dict, compare=False, repr=False, hash=False
    )

    _segment_means: dict[frozenset[str], float] = field(
        default_factory=dict, compare=False, repr=False, hash=False
    )

    def mean_segment_length(self, delimiters: frozenset[str]) -> float:
        if delimiters not in self._segment_means:
            segments = self.segments(delimiters)
            self._segment_means[delimiters] = (
                sum(item.end - item.start for item in segments) / len(segments)
                if segments
                else 0
            )
        return self._segment_means[delimiters]

    def segments(self, delimiters: frozenset[str]) -> tuple[seg.Segment, ...]:
        """Reuse segmentation only within this unchanged text and edge policy."""
        if delimiters not in self._segments:
            self._segments[delimiters] = tuple(
                seg.split(
                    self.normalized.text,
                    delimiters,
                    self.boundary_start,
                    self.boundary_end,
                )
            )
        return self._segments[delimiters]


class _BudgetExceeded(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


@dataclass
class _Branch:
    """The normal branch, or one diagnostic branch with its slot made adjacent."""

    kind: str
    between: tuple[Slot | Gap | None, ...]
    diagnostic: Diagnostic | None = None
    require_tail: bool = True


@dataclass
class _Search:
    """One pattern's search of one segment; branches share the budget (spec 8.1)."""

    segment: seg.Segment
    branch: _Branch
    states: int = 0
    found: list[Result] = field(default_factory=list)


def match_text(
    spec: PatternSpec,
    text: TextInput,
    settings: Settings,
    *,
    checkpoint: Checkpoint = None,
) -> list[Result]:
    """All results of one pattern in one text, unsorted and without ids."""
    pattern = normalized_pattern(spec, settings.normalization)
    norm = text.normalized
    long_warning = long_segment_warning(
        spec,
        norm.text,
        settings,
        text.punctuation,
        text.full_stop,
        text.halfwidth,
        prepared=text,
    )
    segments: Iterable[seg.Segment]
    if pattern.scope.mode == "window":
        size = pattern.scope.window
        assert size is not None
        anchors = itertools.groupby(
            _occurrences(norm.text, pattern.fixed[0].options), key=lambda hit: hit[0]
        )
        segments = (
            seg.window(position, size, len(norm.text), text.boundary_end)
            for position, _ in anchors
        )
    else:
        delimiters = (
            sentence_delimiters(settings, text.full_stop, text.halfwidth)
            if pattern.scope.mode == "sentence"
            else clause_delimiters(settings, text.punctuation, text.halfwidth)
        )
        segments = text.segments(delimiters)

    branches = [_Branch("matched", pattern.between)]
    for diagnostic in spec.diagnostics:
        between = tuple(
            None if isinstance(s, Slot) and s.name == diagnostic.between_slot else s
            for s in pattern.between
        )
        branches.append(
            _Branch("adjacent", between, diagnostic, diagnostic.require_tail)
        )

    matcher = _Matcher(spec, pattern, text, settings, long_warning)
    matcher.checkpoint = checkpoint
    results: list[Result] = []
    for segment in segments:
        if checkpoint:
            checkpoint()
        results.extend(matcher.search(segment, branches))
    _set_alt_counts(results)
    return results


def normalized_pattern(spec: PatternSpec, options: Options) -> Pattern:
    """The pattern with fixed and `not` literals normalized like the text (2.6)."""

    def literal(text: str) -> str:
        normalized = options.literal(text)
        if not normalized:
            raise PatternFileError(
                f"{spec.id}: literal {text!r} is empty after normalization"
            )
        return normalized

    def slot(element: Slot | Gap | None) -> Slot | Gap | None:
        if isinstance(element, Slot):
            return replace(element, nots=tuple(literal(n) for n in element.nots))
        return element

    fixed = []
    for element in spec.pattern.fixed:
        options_ = tuple(literal(option) for option in element.options)
        if len(set(options_)) != len(options_):
            raise PatternFileError(
                f"{spec.id}: alternative options become equal after normalization"
            )
        fixed.append(Fixed(options_))
    open_slot = slot(spec.pattern.open_slot)
    assert not isinstance(open_slot, Gap)
    return replace(
        spec.pattern,
        fixed=tuple(fixed),
        between=tuple(slot(element) for element in spec.pattern.between),
        open_slot=open_slot,
    )


def normalized_heuristics(spec: PatternSpec, options: Options) -> Heuristics:
    """Connectives and final particles normalized like the text (spec 2.6)."""

    def strings(values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(options.literal(value) for value in values)
        if not all(normalized):
            raise PatternFileError(f"{spec.id}: a heuristic string becomes empty")
        return normalized

    heuristics = spec.heuristics
    return replace(
        heuristics,
        connectives=strings(heuristics.connectives),
        final_particles=strings(heuristics.final_particles),
    )


def clause_delimiters(
    settings: Settings, punctuation: seg.Punctuation, halfwidth: bool = False
) -> frozenset[str]:
    """The run's clause delimiters, plus U+3001 for a `pause` text (spec 3.7) and the
    halfwidth forms for a text that declares them (spec 3.9)."""
    found = settings.clause_delimiters
    if punctuation == "pause":
        found = found | {seg.IDEOGRAPHIC_COMMA}
    if halfwidth:
        found = found | seg.HALFWIDTH_CLAUSE
    return found


def sentence_delimiters(
    settings: Settings, full_stop: seg.FullStop, halfwidth: bool = False
) -> frozenset[str]:
    """The run's sentence delimiters, without U+3002 for a `reading` text (3.8).

    U+3002 stays a clause delimiter: a reading mark is still a pause.
    """
    found = settings.sentence_delimiters
    if halfwidth:
        # The halfwidth full stop takes the role the text gives 。 (spec 3.9).
        found = found | seg.HALFWIDTH_SENTENCE
        if full_stop == "reading":
            found = found - seg.HALFWIDTH_FULL_STOPS
    if full_stop == "reading":
        return found - {seg.FULL_STOP}
    return found


def check_variants(
    settings: Settings,
    texts: Iterable[tuple[seg.Punctuation, seg.FullStop, bool]],
) -> None:
    """Reject a variant mapping that touches a delimiter of any input text (2.7).

    A mapping like 甲 -> "." would create a sentence boundary the source does not
    have in a halfwidth text, so every text's actual delimiters are checked:
    punctuation convention (U+3001 for pause), halfwidth marks and the full-stop
    role. Closing quotation marks are not included: their boundary role depends on
    a following delimiter and is a researcher rule (spec 3.2).
    """
    table = settings.normalization.variants
    if table is None:
        return
    for punctuation, full_stop, halfwidth in texts:
        delimiters = clause_delimiters(settings, punctuation, halfwidth) | (
            sentence_delimiters(settings, full_stop, halfwidth)
        )
        for key, value in table.map.items():
            for char in key + value:
                if char in delimiters:
                    raise NormalizationError(
                        f"variant {key!r} -> {value!r} involves {char!r}, a "
                        "delimiter in this text"
                    )


def long_segment_warning(
    spec: PatternSpec,
    text: str,
    settings: Settings,
    punctuation: seg.Punctuation = "modern",
    full_stop: seg.FullStop = "sentence",
    halfwidth: bool = False,
    *,
    prepared: TextInput | None = None,
) -> bool:
    """Whether the text's mean segment length exceeds the threshold (spec 3.5).

    Depends on the text and the pattern's mode only, not on matches, so a long
    text without any hit is still reported in the run file.
    """
    mode = spec.pattern.scope.mode
    if mode == "window":
        return False
    delimiters = (
        sentence_delimiters(settings, full_stop, halfwidth)
        if mode == "sentence"
        else clause_delimiters(settings, punctuation, halfwidth)
    )
    if prepared is not None:
        return (
            prepared.mean_segment_length(delimiters) > settings.long_segment_threshold
        )
    segments = seg.split(text, delimiters)
    if not segments:
        return False
    mean = sum(s.end - s.start for s in segments) / len(segments)
    return mean > settings.long_segment_threshold


def assign_ids(results: Iterable[tuple[int, int, Result]]) -> list[Result]:
    """Sort `(input index, pattern index, result)` and number them (spec 8.6)."""
    ordered = sorted(
        results,
        key=lambda item: (
            item[0],
            item[1],
            item[2].anchor,
            KIND_ORDER[item[2].kind],
            [(f.start, f.end) for f in item[2].fixed],
            (item[2].segment.start, item[2].segment.end),
        ),
    )
    numbered = []
    for rank, (_, _, result) in enumerate(ordered, start=1):
        result.result_id = f"r{rank:06d}"
        numbered.append(result)
    return numbered


def _occurrences(
    text: str,
    options: Sequence[str],
    low: int = 0,
    high: int | None = None,
    end: int | None = None,
) -> Iterator[tuple[int, str]]:
    """Every `(position, literal)`, ascending, alt options in declared order (8.5)."""
    high = len(text) if high is None else high
    end = len(text) if end is None else end

    def matches(order: int, literal: str) -> Iterator[tuple[int, int, str]]:
        start = text.find(literal, low, end)
        while 0 <= start <= high:
            yield start, order, literal
            start = text.find(literal, start + 1, end)

    streams = [matches(order, literal) for order, literal in enumerate(options)]
    for position, _, literal in heapq.merge(*streams):
        yield position, literal


def _set_alt_counts(results: list[Result]) -> None:
    keys = [(r.kind, r.anchor) for r in results if r.fixed]
    counts = Counter(keys)
    for result in results:
        if result.fixed:
            result.alt_count = counts[(result.kind, result.anchor)] - 1


class _Matcher:
    checkpoint: Checkpoint = None

    def __init__(
        self,
        spec: PatternSpec,
        pattern: Pattern,
        text: TextInput,
        settings: Settings,
        long_warning: bool,
    ) -> None:
        self.spec = spec
        self.pattern = pattern
        self.heuristics = normalized_heuristics(spec, settings.normalization)
        self.text = text
        self.norm = text.normalized
        self.settings = settings
        self.barriers = sorted(self.norm.barriers)
        self.long_warning = long_warning
        self.canonical = spec.pattern.canonical()

    def search(self, segment: seg.Segment, branches: list[_Branch]) -> list[Result]:
        search = _Search(segment, branches[0])
        if self.pattern.scope.mode == "window":
            firsts = self._in_range(0, segment.start, segment.start, segment)
        else:
            firsts = self._in_range(0, segment.start, segment.end, segment)
        try:
            for branch in branches:
                search.branch = branch
                self._count(search, len(firsts))
                for position, literal in firsts:
                    if self._crosses(position, position, position + len(literal)):
                        continue
                    self._extend(search, [(position, literal)])
        except _BudgetExceeded as stop:
            # Spec 8.1 and 8.3: the segment is atomic for the pattern. Rows already
            # found by any branch are dropped; committing them would present an
            # unfinished search as complete.
            return [self._interruption(segment, stop.reason)]
        return search.found

    def _in_range(
        self, index: int, low: int, high: int, segment: seg.Segment
    ) -> list[tuple[int, str]]:
        """Occurrences of element `index` starting in `[low, high]`, inside segment."""
        found = _occurrences(
            self.norm.text,
            self.pattern.fixed[index].options,
            max(low, segment.start),
            high,
            segment.end,
        )
        # One extra candidate is enough to trigger the existing segment budget;
        # materializing the full occurrence list would bypass that memory guard.
        return list(itertools.islice(found, self.settings.state_limit + 1))

    def _count(self, search: _Search, added: int) -> None:
        previous = search.states
        search.states += added
        if self.checkpoint and previous // 256 != search.states // 256:
            self.checkpoint()
        if search.states > self.settings.state_limit:
            raise _BudgetExceeded("state_budget")

    def _crosses(self, anchor: int, start: int, end: int) -> bool:
        """Whether `[anchor, end)` crosses a barrier at a position in `(start, end)`."""
        index = bisect.bisect_right(self.barriers, max(anchor, start))
        return index < len(self.barriers) and self.barriers[index] < end

    def _extend(self, search: _Search, initial_placed: list[tuple[int, str]]) -> None:
        pattern = self.pattern
        num_fixed = len(pattern.fixed)
        stack: list[list[tuple[int, str]]] = [initial_placed]
        while stack:
            placed = stack.pop()
            if len(placed) == num_fixed:
                self._complete(search, placed)
                continue
            index = len(placed)
            previous_end = placed[-1][0] + len(placed[-1][1])
            element = search.branch.between[index - 1]
            low, high = _distance_range(element)
            candidates = self._in_range(
                index, previous_end + low, previous_end + high, search.segment
            )
            self._count(search, len(candidates))
            anchor = placed[0][0]
            for position, literal in reversed(candidates):
                if isinstance(element, Slot) and _contains_any(
                    self.norm.text[previous_end:position], element.nots
                ):
                    continue
                if self._crosses(anchor, previous_end - 1, position + len(literal)):
                    continue
                stack.append([*placed, (position, literal)])

    def _complete(self, search: _Search, placed: list[tuple[int, str]]) -> None:
        pattern = self.pattern
        segment = search.segment
        branch = search.branch
        b = placed[-1][0] + len(placed[-1][1])
        open_slot = None
        kind = branch.kind
        reason = None
        if pattern.open_slot is not None:
            ends, observed, could_extend = self._tail(b, segment)
            if not ends:
                truncated_edge = segment.boundary_end == "truncated"
                if branch.kind == "adjacent" and not branch.require_tail:
                    pass
                elif truncated_edge and could_extend:
                    kind, reason = "indeterminate", "empty_tail_truncated"
                else:
                    return
            if kind != "indeterminate":
                open_slot = self._open_slot_hit(b, ends, observed, segment)
        if len(search.found) >= self.settings.result_limit:
            raise _BudgetExceeded("result_budget")
        search.found.append(
            self._result(kind, segment, placed, branch, open_slot, reason)
        )

    def _tail(self, b: int, segment: seg.Segment) -> tuple[range, int, bool]:
        """E as a contiguous range, the observed end, and whether more text could help.

        E is contiguous because every bound in spec 4.4 is an upper bound on `e`
        except `b + min`: a forbidden literal inside `N[b:e]` stays inside every
        longer slot, and a barrier in `[b, e)` stays in every longer one.
        """
        open_slot = self.pattern.open_slot
        assert open_slot is not None
        text = self.norm.text
        t = segment.end
        barrier_index = bisect.bisect_left(self.barriers, b)
        first_barrier = (
            self.barriers[barrier_index] if barrier_index < len(self.barriers) else None
        )
        observed = min(b + open_slot.max, t)
        if first_barrier is not None:
            observed = min(observed, first_barrier)
        upper = observed
        forbidden_in_text = False
        for literal in open_slot.nots:
            # Only occurrences ending by t can lower `upper` (never above t) or
            # make the literal forbidden in this segment, so the search stops at t
            # instead of scanning the rest of the text for every placement.
            found = text.find(literal, b, t)
            if found >= 0:
                # Ends from found + len(literal) on contain the literal.
                upper = min(upper, found + len(literal) - 1)
                if found + len(literal) <= t:
                    forbidden_in_text = True
        # A barrier exactly at t also blocks extension (spec 4.6): text added
        # after t would lie on the far side of it.
        could_extend = (
            b + open_slot.max > t
            and (first_barrier is None or first_barrier > t)
            and not forbidden_in_text
        )
        return range(b + open_slot.min, upper + 1), observed, could_extend

    def _open_slot_hit(
        self, b: int, ends: range, observed: int, segment: seg.Segment
    ) -> OpenSlotHit:
        open_slot = self.pattern.open_slot
        assert open_slot is not None
        norm = self.norm
        representatives = tuple(
            Representative(self._end(e), selected_by, rules)
            for e, selected_by, rules in _representatives(
                norm.text,
                b,
                ends,
                segment.end,
                self.heuristics,
                clause_delimiters(
                    self.settings, self.text.punctuation, self.text.halfwidth
                ),
            )
        )
        b_start = Boundary(b, norm.src_start(b), norm.is_exact(b))
        return OpenSlotHit(
            name=open_slot.name,
            b_start=b_start,
            ends=tuple(self._end(e) for e in ends),
            # An empty observable range maps like b_start (spec 1.7 "Roles"):
            # mapping it as an end could put it before b_start in the source.
            observed_end=b_start if observed == b else self._end(observed),
            truncated=segment.boundary_end == "truncated" and observed == segment.end,
            representatives=representatives,
        )

    def _end(self, position: int) -> Boundary:
        norm = self.norm
        return Boundary(position, norm.src_end(position), norm.is_exact(position))

    def _result(
        self,
        kind: str,
        segment: seg.Segment,
        placed: list[tuple[int, str]],
        branch: _Branch,
        open_slot: OpenSlotHit | None,
        reason: str | None,
    ) -> Result:
        norm = self.norm
        fixed = tuple(
            FixedHit(
                literal,
                p,
                p + len(literal),
                norm.src_span(p, p + len(literal)),
                (norm.is_exact(p), norm.is_exact(p + len(literal))),
            )
            for p, literal in placed
        )
        slots = []
        adjacency = None
        for index, element in enumerate(self.pattern.between):
            start, end = fixed[index].end, fixed[index + 1].start
            if branch.between[index] is None and isinstance(element, Slot):
                diagnostic = branch.diagnostic
                assert diagnostic is not None
                source_adjacent = fixed[index].src.end == fixed[index + 1].src.start
                # Spec 10: adjacency describes `adjacent` results only. An
                # empty_tail_truncated record from a diagnostic branch is an
                # indeterminate observation and carries no adjacency claim.
                if kind == "adjacent":
                    adjacency = Adjacency(
                        diagnostic.between_slot,
                        "source" if source_adjacent else "normalized",
                    )
            elif isinstance(element, Slot):
                slots.append(
                    SlotHit(
                        element.name,
                        start,
                        end,
                        norm.src_span(start, end),
                        (norm.is_exact(start), norm.is_exact(end)),
                    )
                )
        coverage_end = (
            open_slot.ends[-1].norm
            if open_slot is not None and open_slot.ends
            else fixed[-1].end
        )
        joined = norm.removals_between("notes", fixed[0].start, coverage_end)
        annotations = norm.removals_between("annotations", fixed[0].start, coverage_end)
        return self._base(
            kind,
            segment,
            fixed=fixed,
            slots=tuple(slots),
            open_slot=open_slot,
            adjacency=adjacency,
            indeterminate_reason=reason,
            joined_notes=joined,
            joined_annotations=annotations,
        )

    def _interruption(self, segment: seg.Segment, reason: str) -> Result:
        """The single record of a stopped pattern search in a segment (spec 8.7)."""
        return self._base("indeterminate", segment, indeterminate_reason=reason)

    def _base(self, kind: str, segment: seg.Segment, **fields: object) -> Result:
        # Recorded edges stay factual; grading uses the lowered edges (spec 3.5).
        graded = seg.effective(segment) if self.long_warning else segment
        context = seg.context_status(graded)
        mode = self.pattern.scope.mode
        return Result(
            kind=kind,
            pattern_id=self.spec.id,
            pattern=self.canonical,
            text_id=self.text.text_id,
            mode=mode,
            segment=segment,
            segment_src=self.norm.src_span(segment.start, segment.end),
            evidence=seg.evidence(graded, self.text.range),
            context_status=context,
            grade=seg.grade(kind, context, mode),
            editorial_boundaries="editorial"
            in (segment.boundary_start, segment.boundary_end),
            long_segment_warning=self.long_warning,
            **fields,  # type: ignore[arg-type]
        )


def _distance_range(element: Slot | Gap | None) -> tuple[int, int]:
    if element is None:
        return 0, 0
    if isinstance(element, Slot):
        return element.min, element.max
    return 0, element.max


def _contains_any(text: str, literals: Sequence[str]) -> bool:
    return any(literal in text for literal in literals)


def _representatives(
    text: str,
    b: int,
    ends: range,
    t: int,
    heuristics: Heuristics,
    clause_delimiters: frozenset[str],
) -> list[tuple[int, str, tuple[str, ...]]]:
    """Up to three representative ends, ascending (spec 6.2)."""
    if not ends:
        return []
    proposals: dict[str, list[int]] = {
        "clause_boundary": [e for e in ends if e < t and text[e] in clause_delimiters],
        "connective": [
            e
            for e in ends
            if any(
                text.startswith(c, e) and e + len(c) <= t
                for c in heuristics.connectives
            )
        ],
        "final_particle": [
            e
            for e in ends
            if any(
                e - len(f) >= b and text[e - len(f) : e] == f
                for f in heuristics.final_particles
            )
        ],
        "segment_end": [t] if t in ends else [],
    }
    selected: dict[int, str] = {}
    progress = True
    while len(selected) < 3 and progress:
        progress = False
        for rule in heuristics.order:
            if len(selected) >= 3:
                break
            fresh = [e for e in proposals[rule] if e not in selected]
            if fresh:
                selected[fresh[0]] = rule
                progress = True
    maximum = ends[-1]
    if len(selected) < 3 and maximum not in selected:
        selected[maximum] = "max_end"
    output = []
    for e in sorted(selected):
        rules = [rule for rule in heuristics.order if e in proposals[rule]]
        if e == maximum:
            rules.append("max_end")
        output.append((e, selected[e], tuple(rules)))
    return output
