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


def _response_with_context_cleared(response: Any) -> Any:
    """Return a shallow copy of ``response`` with any (nested) context value blanked.

    ``model_dump()`` on a response carrying a pathologically deep context
    trips Pydantic's own serializer recursion guard — ``core/context.json``
    sets no depth ceiling, and ``_detach`` (this module's own iterative
    serializer) has none either, so nothing upstream bounds how deep a buyer
    can nest it. Left unhandled, the crash happens before this module's own
    safe iterative serialization ever runs, because ``model_dump()`` walks the
    whole model, context included. Clearing the field first (via
    ``model_copy``, which does not re-run validators) removes the one
    unbounded value from Pydantic's walk;
    the real serialized context is injected back into the dumped dict
    afterward by the caller. ``exclude={"context": ...}`` was considered and
    rejected: ``CreateMediaBuyResult``/``UpdateMediaBuyResult`` flatten their
    inner ``response`` via a custom ``model_serializer(mode="wrap")`` that
    calls ``self.response.model_dump()`` directly, without forwarding an
    externally supplied ``exclude=`` into that nested call — so ``exclude``
    cannot reliably reach a flattened wrapper's context, while replacing the
    field value up front works uniformly for direct and wrapped shapes alike.
    A response with no context field, or an already-None one, is returned
    unchanged (``getattr`` degrades to ``None``, and clearing ``None`` to
    ``None`` would be a no-op copy anyway).

    Only engages for genuine ``BaseModel`` instances. A test double (e.g. a
    bare ``unittest.mock.MagicMock()`` standing in for a response, common
    across this codebase's transport-wrapper tests) auto-creates ANY attribute
    access as a truthy child mock — ``getattr(mock, "context", None)`` never
    legitimately returns ``None`` — so the unguarded version of this check
    always took the "clear" branch, called the mock's own auto-mocked
    ``model_copy()``, and returned a DIFFERENT child mock whose
    ``model_dump()`` no longer carries whatever return value the test
    configured. Non-``BaseModel`` responses fall through unchanged, matching
    this function's pre-existing behavior for them.
    """
    if not isinstance(response, BaseModel):
        return response
    if getattr(response, "context", None) is not None:
        return response.model_copy(update={"context": None})
    nested_response = getattr(response, "response", None)
    if (
        nested_response is not None
        and isinstance(nested_response, BaseModel)
        and getattr(nested_response, "context", None) is not None
    ):
        cleared_nested = nested_response.model_copy(update={"context": None})
        return response.model_copy(update={"response": cleared_nested})
    return response


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

    The model is dumped with its context (direct or flattened-wrapper) blanked
    first — see ``_response_with_context_cleared`` — so a pathologically deep
    context cannot crash Pydantic's own serializer before the safe iterative
    path below ever runs; the real context is restored afterward.
    """
    if isinstance(response, dict):
        data = _detach(response)
    else:
        data = _response_with_context_cleared(response).model_dump(mode=mode, **kwargs)

    candidate = _response_context(response) if context is _CONTEXT_UNSET else context
    serialized = serialize_application_context(candidate)
    if serialized is not None:
        data["context"] = serialized
    return data


__all__ = ["dump_adcp_response", "serialize_application_context"]
