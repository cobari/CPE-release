"""Evaluate standard-library regular expressions in a terminable worker."""

from __future__ import annotations

import atexit
import multiprocessing
import re
import threading
import time
from collections.abc import Sequence
from multiprocessing.process import BaseProcess
from typing import Any

from cpe.cancellation import Checkpoint

_LOCK = threading.Lock()
_PROCESS: BaseProcess | None = None
_CONNECTION: Any = None
TIMEOUT = 2.0
_BATCH_MAX_ITEMS = 256
_BATCH_MAX_CHARS = 65_536


def _match_spans(pattern: re.Pattern[str], text: str) -> list[tuple[int, int]]:
    return [
        match.span() for match in pattern.finditer(text) if match.end() > match.start()
    ]


def _serve(connection: Any) -> None:
    try:
        while True:
            try:
                request = connection.recv()
            except EOFError:
                return
            if len(request) == 3 and request[0] == "batch":
                _, pattern, texts = request
                for item in texts:
                    try:
                        item_result = _match_spans(pattern, item)
                    except (re.error, RecursionError) as error:
                        connection.send((False, str(error)))
                        break
                    else:
                        connection.send((True, item_result))
                continue
            try:
                pattern, text = request
                result: Any = _match_spans(pattern, text)
            except (re.error, RecursionError) as error:
                connection.send((False, str(error)))
            else:
                connection.send((True, result))
    finally:
        connection.close()


def _stop() -> None:
    global _PROCESS, _CONNECTION
    if _CONNECTION is not None:
        _CONNECTION.close()
        _CONNECTION = None
    if _PROCESS is not None:
        if _PROCESS.pid is not None:
            if _PROCESS.is_alive():
                _PROCESS.terminate()
            _PROCESS.join(timeout=1)
            if _PROCESS.is_alive():
                _PROCESS.kill()
                _PROCESS.join(timeout=1)
        _PROCESS.close()
        _PROCESS = None


def _ensure_worker() -> Any:
    global _PROCESS, _CONNECTION
    if _PROCESS is None:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        _CONNECTION = parent
        _PROCESS = context.Process(target=_serve, args=(child,), daemon=True)
        _PROCESS.start()
        child.close()
    return _CONNECTION


def spans(
    pattern: re.Pattern[str], text: str, timeout: float = TIMEOUT
) -> list[tuple[int, int]]:
    """Preserve Python re semantics while bounding time spent on a source string."""
    with _LOCK:
        try:
            connection = _ensure_worker()
            connection.send((pattern, text))
            if not connection.poll(timeout):
                raise TimeoutError("regular expression exceeded its time limit")
            ok, result = connection.recv()
            if not ok:
                raise ValueError(str(result))
            return list(result)
        except (OSError, EOFError, TimeoutError, ValueError):
            _stop()
            raise


_ORIG_SPANS = spans


def spans_batch(
    pattern: re.Pattern[str],
    texts: Sequence[str],
    timeout: float = TIMEOUT,
    checkpoint: Checkpoint = None,
) -> list[list[tuple[int, int]]]:
    """Evaluate one regex across many segments with bounded chunk IPC overhead."""
    if not texts:
        return []
    chunks: list[list[str]] = []
    current: list[str] = []
    chars = 0
    for text in texts:
        if current and (
            len(current) >= _BATCH_MAX_ITEMS or chars + len(text) > _BATCH_MAX_CHARS
        ):
            chunks.append(current)
            current = []
            chars = 0
        current.append(text)
        chars += len(text)
    if current:
        chunks.append(current)

    results: list[list[tuple[int, int]]] = []
    for chunk in chunks:
        if checkpoint:
            checkpoint()
        with _LOCK:
            try:
                connection = _ensure_worker()
                connection.send(("batch", pattern, chunk))
                for _ in range(len(chunk)):
                    if checkpoint:
                        checkpoint()
                    deadline = time.monotonic() + timeout
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(
                                "regular expression exceeded its time limit"
                            )
                        if connection.poll(min(0.05, remaining)):
                            break
                        if checkpoint:
                            checkpoint()
                    ok, item_result = connection.recv()
                    if not ok:
                        raise ValueError(str(item_result))
                    results.append(list(item_result))
            except BaseException:
                _stop()
                raise
    return results


atexit.register(_stop)
