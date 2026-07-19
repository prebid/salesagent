"""Lossless AdCP application-context serialization at transport boundaries.

AdCP ``context`` is an opaque JSON object.  Generated SDK models default to
``exclude_none=True`` when dumped, which is appropriate for schema-owned
optional fields but not for caller-owned context: an explicitly supplied JSON
``null`` is data and must survive the request/response round trip.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

_CONTEXT_UNSET = object()


def serialize_application_context(context: Any) -> dict[str, Any] | None:
    """Return a detached, JSON-safe context without deleting explicit nulls.

    ``exclude_unset=True`` omits fields the generated ``ContextObject`` merely
    declares, while ``exclude_none=False`` preserves fields the buyer actually
    supplied with a JSON-null value. Plain dictionaries are deep-copied so a
    later mutation of the request object cannot change an emitted response.
    Invalid non-object values return ``None``; callers use this function while
    already handling another result/error and must not mask it with a secondary
    serialization failure.
    """
    if context is None:
        return None
    if isinstance(context, dict):
        return deepcopy(context)
    if isinstance(context, BaseModel):
        return context.model_dump(
            mode="json",
            exclude_unset=True,
            exclude_none=False,
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
