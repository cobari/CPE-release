"""Shared HTTP safety helpers: Content-Length bounds and restricted redirects."""

from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import weakref
from collections.abc import Iterable
from typing import Any

_ORIG_URLOPEN = urllib.request.urlopen


class UnsafeRedirectError(urllib.error.URLError):
    """Raised when a URL or HTTP redirect violates host, scheme or syntax rules."""


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


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Restrict redirects by host, scheme, userinfo, port, and redirect count."""

    max_redirections = 5
    max_repeats = 4

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        require_https: bool = True,
        max_redirects: int = 5,
    ) -> None:
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._require_https = require_https
        self._max_redirects = max_redirects
        self._hops: weakref.WeakKeyDictionary[urllib.request.Request, int] = (
            weakref.WeakKeyDictionary()
        )

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        visited = getattr(req, "redirect_dict", None)
        dict_count = sum(visited.values()) if isinstance(visited, dict) else 0
        hops = max(self._hops.get(req, 0), dict_count)
        if hops >= self._max_redirects:
            raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
        try:
            current_scheme = urllib.parse.urlsplit(req.full_url).scheme.lower()
            parts = urllib.parse.urlsplit(newurl)
            port = parts.port
        except ValueError:
            raise UnsafeRedirectError(f"malformed redirect URL: {newurl}") from None
        scheme = parts.scheme.lower()
        allowed_schemes = (
            ("https",)
            if self._require_https or current_scheme == "https"
            else ("http", "https")
        )
        host = (parts.hostname or "").lower()
        allowed_port = (None, 443) if scheme == "https" else (None, 80)
        if (
            scheme not in allowed_schemes
            or host not in self._allowed_hosts
            or parts.username is not None
            or parts.password is not None
            or port not in allowed_port
        ):
            raise UnsafeRedirectError(f"disallowed redirect: {newurl}")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            self._hops[req] = hops + 1
            self._hops[redirected] = hops + 1
        return redirected


def build_safe_opener(
    *,
    allowed_hosts: Iterable[str],
    require_https: bool = True,
    max_redirects: int = 5,
    context: ssl.SSLContext | None = None,
    extra_handlers: Iterable[urllib.request.BaseHandler] = (),
) -> urllib.request.OpenerDirector:
    """Build an opener with SafeRedirectHandler and optional TLS/cookie handlers."""
    handlers: list[urllib.request.BaseHandler] = list(extra_handlers)
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    handlers.append(
        SafeRedirectHandler(
            allowed_hosts=allowed_hosts,
            require_https=require_https,
            max_redirects=max_redirects,
        )
    )
    return urllib.request.build_opener(*handlers)


def open_url(
    request: urllib.request.Request | str,
    *,
    timeout: float,
    allowed_hosts: Iterable[str],
    require_https: bool = True,
    max_redirects: int = 5,
    context: ssl.SSLContext | None = None,
    extra_handlers: Iterable[urllib.request.BaseHandler] = (),
) -> Any:
    """Open an HTTP(S) request enforcing redirect restrictions on host/scheme."""
    target = (
        request.full_url if isinstance(request, urllib.request.Request) else request
    )
    try:
        parts = urllib.parse.urlsplit(target)
        port = parts.port
    except ValueError:
        raise UnsafeRedirectError(f"malformed URL: {target}") from None
    scheme = parts.scheme.lower()
    allowed_schemes = ("https",) if require_https else ("http", "https")
    host = (parts.hostname or "").lower()
    allowed_port = (None, 443) if scheme == "https" else (None, 80)
    if (
        scheme not in allowed_schemes
        or host not in frozenset(item.lower() for item in allowed_hosts)
        or parts.username is not None
        or parts.password is not None
        or port not in allowed_port
    ):
        raise UnsafeRedirectError(f"disallowed URL: {target}")
    if urllib.request.urlopen is not _ORIG_URLOPEN:
        if context is not None:
            return urllib.request.urlopen(request, timeout=timeout, context=context)
        return urllib.request.urlopen(request, timeout=timeout)
    opener = build_safe_opener(
        allowed_hosts=allowed_hosts,
        require_https=require_https,
        max_redirects=max_redirects,
        context=context,
        extra_handlers=extra_handlers,
    )
    return opener.open(request, timeout=timeout)
