"""Shared ASGI helper: wrap a ``send`` callable to set headers on ``http.response.start``.

Canonical DRY extraction for admin middleware header-mutation. Before this
module, ``RequestIDMiddleware``, ``ServedByMiddleware``, and
``SecurityHeadersMiddleware`` each reimplemented the same send-wrapping
logic. A 4th caller (``LegacyAdminRedirectMiddleware`` at L1c) would have
been a 4th copy. Per CLAUDE.md DRY policy: "three copies is a defect."

Modes
-----

``"replace"`` — strip any existing headers whose name matches one in
    ``to_set``, then append. The wrapper wins (latest-write semantics).
    Used by ``RequestIDMiddleware`` and ``ServedByMiddleware`` — both want
    to be the single source of truth for their header even if an inner
    middleware already stamped one.

``"append_if_missing"`` — skip any name already present on the response;
    the inner handler/middleware wins. Used by ``SecurityHeadersMiddleware``
    because an individual handler (e.g., a PDF endpoint with a tighter
    ``default-src 'none'`` CSP) may override our default.

Pass-through guarantees
-----------------------

- Non-HTTP scopes are unaffected — this is a callable wrapper over ``send``,
  not a scope gate. The caller decides whether to invoke it at all.
- Only ``http.response.start`` is mutated. ``http.response.body``,
  ``http.response.trailers``, and ``websocket.*`` messages pass through
  unchanged.

``to_set`` accepts either a static iterable or a zero-argument thunk.
The thunk form lets callers defer header computation until request time
without precomputing (e.g., conditional HSTS gated by ``https_only``).
Header names MUST be lowercase bytes per the ASGI spec.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Literal

from starlette.types import Message, Send

HeaderPair = tuple[bytes, bytes]
HeadersSource = Iterable[HeaderPair] | Callable[[], Iterable[HeaderPair]]


def build_header_wrapper(
    send: Send,
    *,
    to_set: HeadersSource,
    mode: Literal["replace", "append_if_missing"],
) -> Send:
    """Return a new ``send`` that applies ``to_set`` on the first ``http.response.start``.

    Arguments
    ---------
    send
        The downstream ASGI ``send`` callable to wrap.
    to_set
        Either an iterable of ``(name_bytes_lowercase, value_bytes)`` pairs,
        or a zero-arg callable returning such an iterable. Callable form is
        resolved lazily at response time so request-scoped state can inform
        the list.
    mode
        ``"replace"`` — strip any existing response headers matching the
        names in ``to_set``, then append; the wrapper wins. ``"append_if_missing"``
        — skip any name already present on the response; the handler wins.

    Returns
    -------
    A new ``Send`` callable. Non-``http.response.start`` messages pass through
    unchanged. Empty ``to_set`` is a no-op.
    """

    async def wrapped(message: Message) -> None:
        if message["type"] != "http.response.start":
            await send(message)
            return

        resolved = to_set() if callable(to_set) else to_set
        pairs: list[HeaderPair] = list(resolved)
        if not pairs:
            await send(message)
            return

        current = list(message.get("headers", []))
        names_to_set = {name for name, _ in pairs}

        if mode == "replace":
            # Strip by name (the wrapper owns these names), then append the
            # new values. `pairs` is trusted — if it contains duplicate
            # names, the last occurrence wins (consistent with dict-like
            # overwrite semantics).
            current = [(n, v) for (n, v) in current if n not in names_to_set]
            current.extend(pairs)
        else:  # append_if_missing
            existing = {n for n, _ in current}
            for name, value in pairs:
                if name not in existing:
                    current.append((name, value))
                    existing.add(name)  # guards against dup `pairs` re-adding

        await send({**message, "headers": current})

    return wrapped


__all__ = ["HeaderPair", "HeadersSource", "build_header_wrapper"]
