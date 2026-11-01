"""Pattern definitions loaded from `patterns/*.toml` (spec sections 5 and 6)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.match.dsl import Pattern, Slot, parse

RULE_IDS = ("clause_boundary", "connective", "final_particle", "segment_end")

_TOP_KEYS = ("schema", "pattern")
_ENTRY_KEYS = ("id", "expr", "diagnostics", "heuristics")
_DIAGNOSTIC_KEYS = ("type", "between_slot", "gap", "require_tail")
_HEURISTIC_KEYS = ("connectives", "final_particles", "order")


class PatternFileError(ValueError):
    """A pattern file that does not follow the pattern file schema."""


@dataclass(frozen=True)
class Heuristics:
    """Representative end rules (spec 6.1)."""

    connectives: tuple[str, ...] = ("而", "則", "以")
    final_particles: tuple[str, ...] = ("也", "矣", "焉", "哉", "乎")
    order: tuple[str, ...] = RULE_IDS


@dataclass(frozen=True)
class Diagnostic:
    """An `adjacent_fixed` branch on one internal slot (spec 5.1)."""

    between_slot: str
    require_tail: bool = False


@dataclass(frozen=True)
class PatternSpec:
    id: str
    pattern: Pattern
    diagnostics: tuple[Diagnostic, ...] = ()
    heuristics: Heuristics = field(default_factory=Heuristics)

    def __post_init__(self) -> None:
        internal = {s.name for s in self.pattern.between if isinstance(s, Slot)}
        slots = [diagnostic.between_slot for diagnostic in self.diagnostics]
        if len(set(slots)) != len(slots):
            # Two branches on one slot would find the same placements twice.
            raise PatternFileError(f"{self.id}: repeated diagnostic on one slot")
        for diagnostic in self.diagnostics:
            if diagnostic.require_tail and self.pattern.open_slot is None:
                raise PatternFileError(
                    f"{self.id}: require_tail needs an open slot (spec 5.1)"
                )
            if diagnostic.between_slot not in internal:
                raise PatternFileError(
                    f"{self.id}: diagnostic slot {diagnostic.between_slot!r} "
                    "is not an internal slot"
                )


def load(path: Path) -> list[PatternSpec]:
    return loads(path.read_bytes(), str(path))


def _object(value: Any, path: str, label: str) -> dict[str, Any]:
    # A list where a table belongs (such as heuristics = []) must fail here
    # instead of raising AttributeError on a later .get call.
    if not isinstance(value, dict):
        raise PatternFileError(f"{path}: {label} must be a table")
    return value


def _array(value: Any, path: str, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PatternFileError(f"{path}: {label} must be an array")
    return value


def _unknown(mapping: dict[str, Any], known: tuple[str, ...], path: str) -> None:
    # A misspelled key (such as heuristic for heuristics) must fail loudly
    # instead of being silently ignored with defaults.
    extra = [key for key in mapping if key not in known]
    if extra:
        raise PatternFileError(f"{path}: unknown keys {sorted(extra)}")


def loads(content: bytes, path: str = "<input>") -> list[PatternSpec]:
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise PatternFileError(f"{path}: not a UTF-8 TOML file ({error})") from None
    _unknown(data, _TOP_KEYS, path)
    if type(data.get("schema")) is not int or data["schema"] != 1:
        raise PatternFileError(f"{path}: expected 'schema = 1'")
    entries = _array(data.get("pattern", []), path, "'pattern'")
    specs = [from_mapping(entry, str(path)) for entry in entries]
    ids = [spec.id for spec in specs]
    if len(set(ids)) != len(ids):
        raise PatternFileError(f"{path}: pattern ids must be unique")
    return specs


def from_mapping(entry: dict[str, Any], path: str = "<input>") -> PatternSpec:
    """A pattern entry of a pattern file or of the wire input (spec 12.5)."""
    entry = _object(entry, path, "pattern entry")
    _unknown(entry, _ENTRY_KEYS, path)
    try:
        pattern_id = entry["id"]
        expr = entry["expr"]
    except KeyError as missing:
        raise PatternFileError(f"{path}: pattern entry needs {missing}") from None
    if not isinstance(pattern_id, str) or not pattern_id:
        raise PatternFileError(f"{path}: pattern id must be a non-empty string")
    if not isinstance(expr, str):
        raise PatternFileError(f"{path}: {pattern_id}: pattern expr must be a string")
    pattern = parse(expr)
    diagnostics = []
    for item in _array(
        entry.get("diagnostics", []), path, f"{pattern_id}: diagnostics"
    ):
        item = _object(item, path, f"{pattern_id}: diagnostic")
        _unknown(item, _DIAGNOSTIC_KEYS, path)
        gap = item.get("gap", 0)
        # `type(...) is int` rejects booleans: in Python False == 0, so a
        # loose check would accept `gap = false` as a gap of zero.
        if item.get("type", "adjacent_fixed") != "adjacent_fixed" or not (
            type(gap) is int and gap == 0
        ):
            raise PatternFileError(
                f"{path}: {pattern_id}: only adjacent_fixed with gap = 0 is supported"
            )
        require_tail = item.get("require_tail", False)
        slot = item.get("between_slot")
        if not isinstance(require_tail, bool) or not isinstance(slot, str):
            raise PatternFileError(
                f"{path}: {pattern_id}: require_tail must be a boolean and "
                "between_slot a string"
            )
        diagnostics.append(Diagnostic(slot, require_tail))
    raw = _object(entry.get("heuristics", {}), path, f"{pattern_id}: heuristics")
    _unknown(raw, _HEURISTIC_KEYS, path)
    defaults = Heuristics()
    for key in ("connectives", "final_particles", "order"):
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise PatternFileError(
                f"{path}: {pattern_id}: heuristics {key} must be a list of "
                "non-empty strings"
            )
    order = tuple(raw.get("order", defaults.order))
    if not order or len(set(order)) != len(order):
        raise PatternFileError(
            f"{path}: {pattern_id}: rule order must be non-empty without repeats"
        )
    unknown = [rule for rule in order if rule not in RULE_IDS]
    if unknown:
        raise PatternFileError(f"{path}: {pattern_id}: unknown rules {unknown}")
    heuristics = Heuristics(
        tuple(raw.get("connectives", defaults.connectives)),
        tuple(raw.get("final_particles", defaults.final_particles)),
        order,
    )
    # The same checks live in PatternSpec.__post_init__ for programmatically
    # built specs; here they name the file and pattern id first.
    internal = {s.name for s in pattern.between if isinstance(s, Slot)}
    slots = [diagnostic.between_slot for diagnostic in diagnostics]
    if len(set(slots)) != len(slots):
        raise PatternFileError(f"{path}: {pattern_id}: repeated diagnostic on one slot")
    for diagnostic in diagnostics:
        if diagnostic.require_tail and pattern.open_slot is None:
            raise PatternFileError(
                f"{path}: {pattern_id}: require_tail needs an open slot (spec 5.1)"
            )
        if diagnostic.between_slot not in internal:
            raise PatternFileError(
                f"{path}: {pattern_id}: diagnostic slot "
                f"{diagnostic.between_slot!r} is not an internal slot"
            )
    return PatternSpec(pattern_id, pattern, tuple(diagnostics), heuristics)
