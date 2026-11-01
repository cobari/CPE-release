"""Cooperative cancellation between bounded units of local or network work."""

from collections.abc import Callable

Checkpoint = Callable[[], None] | None


def pause(
    seconds: float, sleep: Callable[[float], None], checkpoint: Checkpoint
) -> None:
    """Keep request intervals intact while allowing cancellation during a wait."""
    if checkpoint is None:
        sleep(seconds)
        return
    remaining = seconds
    while remaining > 0:
        checkpoint()
        step = min(remaining, 0.1)
        sleep(step)
        remaining -= step
    checkpoint()
