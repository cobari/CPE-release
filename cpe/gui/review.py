"""Local persistence for selected candidates and saved search conditions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from cpe.export.run_file import inputs_section
from cpe.gui.service import Library
from cpe.match.engine import Settings
from cpe.normalize.options import options_from_mapping

STATUSES = ("unreviewed", "keep", "exclude")
REFERENCE_FIELDS = (
    "id",
    "result_id",
    "text_id",
    "title",
    "link",
    "era",
    "group",
    "kind",
    "evidence_scope",
    "citation",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def library_fingerprint(library: Library) -> str:
    entries = inputs_section([text.local for text in library.texts])
    for entry, text in zip(entries, library.texts, strict=True):
        entry["era"] = text.era
    return fingerprint({"texts": entries, "skipped": library.skipped})


def capture_settings(settings: Settings) -> dict[str, Any]:
    data = asdict(settings)
    for key in ("sentence_delimiters", "clause_delimiters"):
        data[key] = sorted(data[key])
    return data


def restore_settings(data: dict[str, Any]) -> Settings:
    return Settings(
        sentence_delimiters=frozenset(data["sentence_delimiters"]),
        clause_delimiters=frozenset(data["clause_delimiters"]),
        state_limit=data["state_limit"],
        result_limit=data["result_limit"],
        long_segment_threshold=data["long_segment_threshold"],
        normalization=options_from_mapping(data["normalization"]),
    )


def candidate(
    snapshot: Mapping[str, Any], row: dict[str, Any], *, local: bool
) -> dict[str, Any]:
    if local:
        result = row
    else:
        result = {key: row[key] for key in REFERENCE_FIELDS if key in row}
        citation = result.get("citation")
        if isinstance(citation, Mapping):
            cleaned = {
                key: value
                for key, value in citation.items()
                if isinstance(key, str) and isinstance(value, (str, int, float, bool))
            }
            if cleaned:
                result["citation"] = cleaned
            else:
                result.pop("citation", None)
        elif "citation" in result:
            result.pop("citation", None)
    query = {
        key: snapshot["query"][key]
        for key in (
            "mode",
            "query",
            "first",
            "second",
            "expression",
            "pattern_file",
            "site",
            "item",
            "database",
        )
        if key in snapshot["query"]
    }
    provenance: dict[str, Any] = {}
    if local:
        provenance = {
            key: snapshot[key]
            for key in (
                "normalization",
                "settings",
                "engine_version",
                "pattern_file_sha256",
            )
            if key in snapshot
        }
        provenance["inputs"] = [
            entry
            for entry in snapshot.get("inputs", ())
            if entry["text_id"] == row.get("text_id")
        ]
    return {
        "source_id": snapshot["source_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "query": query,
        "reference_only": not local,
        "result": result,
        "provenance": provenance,
    }


class ReviewStore:
    """Atomic per-item writes shared by desktop windows using the same profile."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
        if not write and not self.path.is_file():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            if write:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS review (
                       id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                       status TEXT NOT NULL DEFAULT 'unreviewed',
                       note TEXT NOT NULL DEFAULT ''
                    )"""
                )
            yield connection

    def put(self, id_: str, kind: str, payload: dict[str, Any]) -> None:
        with self._connect(write=True) as connection:
            assert connection is not None
            connection.execute(
                "INSERT OR IGNORE INTO review (id, kind, payload) VALUES (?, ?, ?)",
                (id_, kind, _json(payload)),
            )

    def items(self, kind: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if connection is None:
                return []
            query = "SELECT id, payload, status, note FROM review WHERE kind = ?"
            args = [kind]
            if ids is not None:
                query += " AND id IN (" + ",".join("?" for _ in ids) + ")"
                args.extend(ids)
            records = connection.execute(query + " ORDER BY rowid DESC", args)
            return [
                {**json.loads(payload), "id": id_, "status": status, "note": note}
                for id_, payload, status, note in records
            ]

    def update(self, id_: str, status: str, note: str) -> bool:
        with self._connect() as connection:
            return (
                connection is not None
                and connection.execute(
                    "UPDATE review SET status = ?, note = ?"
                    " WHERE id = ? AND kind = 'candidate'",
                    (status, note, id_),
                ).rowcount
                == 1
            )

    def remove(self, id_: str) -> bool:
        with self._connect() as connection:
            return (
                connection is not None
                and connection.execute(
                    "DELETE FROM review WHERE id = ?", (id_,)
                ).rowcount
                == 1
            )
