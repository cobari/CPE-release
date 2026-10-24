"""Evaluate standard-library regular expressions in a terminable worker."""

from __future__ import annotations

import atexit
import multiprocessing
import re
import threading
from multiprocessing.process import BaseProcess
from typing import Any

_LOCK = threading.Lock()
_PROCESS: BaseProcess | None = None
_CONNECTION: Any = None
TIMEOUT = 2.0


def _serve(connection: Any) -> None:
    try:
        while True:
            try:
                pattern, text = connection.recv()
            except EOFError:
                return
            try:
                result = [
                    match.span()
                    for match in pattern.finditer(text)
                    if match.end() > match.start()
                ]
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


def spans(
    pattern: re.Pattern[str], text: str, timeout: float = TIMEOUT
) -> list[tuple[int, int]]:
    """Preserve Python re semantics while bounding time spent on a source string."""
    global _PROCESS, _CONNECTION
    with _LOCK:
        try:
            if _PROCESS is None:
                context = multiprocessing.get_context("spawn")
                parent, child = context.Pipe()
                _CONNECTION = parent
                _PROCESS = context.Process(target=_serve, args=(child,), daemon=True)
                _PROCESS.start()
                child.close()
            _CONNECTION.send((pattern, text))
            if not _CONNECTION.poll(timeout):
                raise TimeoutError("regular expression exceeded its time limit")
            ok, result = _CONNECTION.recv()
            if not ok:
                raise ValueError(str(result))
            return list(result)
        except (OSError, EOFError, TimeoutError, ValueError):
            _stop()
            raise


atexit.register(_stop)
