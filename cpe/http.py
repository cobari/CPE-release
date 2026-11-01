"""Bound declared response sizes before converting untrusted header values."""

import re


def content_length(value: object, maximum: int) -> int | None:
    """Return a bounded byte count, or None when no length was supplied."""
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip(" \t")):
        raise ValueError("invalid Content-Length")
    digits = value.strip(" \t").lstrip("0") or "0"
    limit = str(maximum)
    if len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit):
        raise OverflowError("declared response exceeds size limit")
    return int(digits)
