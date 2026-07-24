"""Lossless AdCP application-context serialization at transport boundaries.

AdCP ``context`` is an opaque JSON object.  Generated SDK models default to
``exclude_none=True`` when dumped, which is appropriate for schema-owned
optional fields but not for caller-owned context: an explicitly supplied JSON
``null`` is data and must survive the request/response round trip.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_CONTEXT_UNSET = object()

# Deepest buyer-supplied context nesting this agent will detach and echo.
#
# ``context`` is opaque per ``core/context.json`` (v3.1.1) and the schema sets
# no depth ceiling, so the bound exists purely to keep serialization inside the
# interpreter's stack: ``copy.deepcopy`` exhausts the default recursion limit at
# roughly 500 levels and ``json.dumps`` (the response renderer) at roughly
# 25,000. Both run INSIDE exception handlers, where a ``RecursionError`` would
# escape as a bare 500 with no envelope. 100 levels is far beyond any real
# correlation payload while staying an order of magnitude clear of the copy
# ceiling, so conformant traffic is echoed unchanged and only pathological
# nesting degrades — to a dropped context, never to a masked error.
MAX_CONTEXT_DEPTH = 100


class _ContextTooDeepError(Exception):
    """Internal signal that a context exceeds ``MAX_CONTEXT_DEPTH``."""


def _detach_bounded(value: Any, depth: int) -> Any:
    """Recursively detach JSON containers, refusing to descend past the bound.

    Recursion here is safe precisely because it is bounded: at most
    ``MAX_CONTEXT_DEPTH`` frames, versus the unbounded descent of ``deepcopy``
    that this replaces. Non-container leaves are deep-copied individually so a
    later mutation of the request object still cannot change an emitted
    response.
    """
    if isinstance(value, dict | list):
        if depth > MAX_CONTEXT_DEPTH:
            raise _ContextTooDeepError
        if isinstance(value, dict):
            return {key: _detach_bounded(item, depth + 1) for key, item in value.items()}
        return [_detach_bounded(item, depth + 1) for item in value]
    return deepcopy(value)


def serialize_application_context(context: Any) -> dict[str, Any] | None:
    """Return a detached, JSON-safe context without deleting explicit nulls.

    ``exclude_unset=True`` omits fields the generated ``ContextObject`` merely
    declares, while ``exclude_none=False`` preserves fields the buyer actually
    supplied with a JSON-null value. Plain dictionaries are detached so a
    later mutation of the request object cannot change an emitted response.
    Invalid non-object values return ``None``; callers use this function while
    already handling another result/error and must not mask it with a secondary
    serialization failure.

    That last guarantee is why nesting deeper than ``MAX_CONTEXT_DEPTH`` — and
    a ``RecursionError`` from dumping a deeply nested model — degrade to a
    logged ``None`` rather than propagating: every caller runs inside an
    exception handler or a response builder, so raising here would replace the
    buyer's envelope with an unhandled 500.
    """
    if context is None:
        return None
    if isinstance(context, dict):
        raw: dict[str, Any] = context
    elif isinstance(context, BaseModel):
        try:
            raw = context.model_dump(
                mode="json",
                exclude_unset=True,
                exclude_none=False,
            )
        except (RecursionError, ValueError):
            # Pydantic reports runaway nesting as ValueError("Circular reference
            # detected (depth exceeded)") rather than RecursionError, so both
            # spellings of "too deep to dump" degrade the same way.
            logger.warning("application context model is too deeply nested to serialize; dropping context")
            return None
    else:
        return None

    try:
        return dict(_detach_bounded(raw, 1))
    except _ContextTooDeepError:
        logger.warning(
            "application context nests deeper than %d levels; dropping context",
            MAX_CONTEXT_DEPTH,
        )
        return None


def _response_context(response: Any) -> Any:
    """Find a direct or flattened-wrapper application context."""
    direct = getattr(response, "context", None)
    if direct is not None:
        return direct

    # create/update result wrappers flatten their typed ``response`` model at
    # the wire boundary, so the application context lives one level down.
    nested_response = getattr(response, "response", None)
    return getattr(nested_response, "context", None)


def dump_adcp_response(
    response: Any,
    *,
    context: Any = _CONTEXT_UNSET,
    mode: str = "json",
    **kwargs: Any,
) -> dict[str, Any]:
    """Serialize a response and restore its opaque application context exactly.

    The response model keeps its canonical serializer for every schema-owned
    field. Only the root ``context`` member is overwritten with the lossless
    application-context representation. ``context=`` may be supplied by a
    transport wrapper; otherwise a direct response context (or the context on a
    flattened result wrapper's inner response) is used.
    """
    if isinstance(response, dict):
        data = deepcopy(response)
    else:
        data = response.model_dump(mode=mode, **kwargs)

    candidate = _response_context(response) if context is _CONTEXT_UNSET else context
    serialized = serialize_application_context(candidate)
    if serialized is not None:
        data["context"] = serialized
    return data


__all__ = ["dump_adcp_response", "serialize_application_context"]
