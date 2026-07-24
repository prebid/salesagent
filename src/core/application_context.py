"""Lossless AdCP application-context serialization at transport boundaries.

AdCP ``context`` is an opaque JSON object.  Generated SDK models default to
``exclude_none=True`` when dumped, which is appropriate for schema-owned
optional fields but not for caller-owned context: an explicitly supplied JSON
``null`` is data and must survive the request/response round trip.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_CONTEXT_UNSET = object()


def _detach(value: Any) -> Any:
    """Deep-copy JSON containers with an explicit heap stack, never Python recursion.

    ``context`` is opaque per ``core/context.json`` (v3.1.1) and the schema sets
    no depth ceiling, so this agent must be able to echo a context of ANY
    nesting depth unchanged — the normative echo contract
    (``context-sessions.mdx``) requires accepted context to survive the round
    trip exactly, and every caller of this module runs inside an exception
    handler or a response builder, where silently dropping the buyer's context
    (or raising) would violate that contract or mask the original error.

    ``copy.deepcopy`` and Pydantic's own JSON serializer both recurse per
    nesting level and exhaust their respective recursion guards on a
    pathologically deep structure (~500 levels for CPython's call stack;
    pydantic-core's cycle detector separately caps out around the same order).
    This function instead walks the structure with an explicit Python list as
    the traversal stack — heap-allocated, so its capacity is bounded by
    available memory, not a fixed recursion limit. JSON scalars (str / int /
    float / bool / None) are immutable, so they are assigned directly rather
    than copied; only dict/list containers are cloned, which is sufficient to
    guarantee a later mutation of the source object cannot change an already-
    emitted response.
    """
    if isinstance(value, dict):
        root: Any = {}
    elif isinstance(value, list):
        root = []
    else:
        return value

    stack: list[tuple[Any, Any]] = [(value, root)]
    while stack:
        source, dest = stack.pop()
        items = source.items() if isinstance(source, dict) else enumerate(source)
        for key, item in items:
            if isinstance(item, dict):
                child: Any = {}
                stack.append((item, child))
            elif isinstance(item, list):
                child = []
                stack.append((item, child))
            else:
                child = item
            if isinstance(dest, dict):
                dest[key] = child
            else:
                dest.append(child)
    return root


def serialize_application_context(context: Any) -> dict[str, Any] | None:
    """Return a detached, JSON-safe context without deleting explicit nulls.

    Plain dictionaries are detached directly. A ``ContextObject`` declares no
    schema-owned fields at all — ``core/context.json`` types it as a bare
    opaque object — so every buyer-supplied key lives in ``model_extra`` as
    plain JSON containers already; reading it directly and detaching it
    ourselves (rather than calling ``context.model_dump()``) avoids handing a
    deeply nested structure to Pydantic's serializer, whose own internal
    recursion guard is exactly the failure mode this function exists to avoid.
    ``exclude=`` the extra keys so a future schema revision's declared fields
    (currently none) still dump through Pydantic's ordinary — and shallow —
    path, then merge the two: declared fields can never collide with extras by
    construction. ``exclude_unset=True`` omits fields the model merely
    declares; ``exclude_none=False`` preserves an explicit JSON ``null`` the
    buyer actually supplied. Invalid non-object values return ``None``;
    callers use this function while already handling another result/error and
    must not mask it with a secondary serialization failure.
    """
    if context is None:
        return None
    if isinstance(context, dict):
        return _detach(context)
    if isinstance(context, BaseModel):
        extra = context.model_extra or {}
        declared = context.model_dump(
            mode="json",
            exclude=set(extra),
            exclude_unset=True,
            exclude_none=False,
        )
        return _detach({**declared, **extra})
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
        data = _detach(response)
    else:
        data = response.model_dump(mode=mode, **kwargs)

    candidate = _response_context(response) if context is _CONTEXT_UNSET else context
    serialized = serialize_application_context(candidate)
    if serialized is not None:
        data["context"] = serialized
    return data


__all__ = ["dump_adcp_response", "serialize_application_context"]
