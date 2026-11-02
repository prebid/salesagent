"""Which pinned AdCP request schema a registered tool's request DTO implements — DERIVED.

This file used to hold ``REQUEST_SCHEMA_BY_TOOL``, thirteen hand-written rows of
tool -> schema path, and its own docstring argued that the binding is ONE fact about
the spec that no second table may re-state. That argument was right about the second
table and wrong about the first: a hand-written row is a copy too, and the thing it
copies is already written down in the artifact. ``adcp``'s generated request types are
one module per pinned schema file, named after it — ``create_media_buy_request`` for
``media-buy/create-media-buy-request.json`` — so a DTO grounded in the SDK vocabulary
already NAMES the schema it implements, in the only place that cannot drift from it.

So the rule is:

* A DTO whose ancestry reaches an ``adcp`` generated request type takes that type's
  module path as its schema ref. Twelve of the thirteen rows resolved this way, byte
  for byte, including ``list_creative_formats`` -> ``media-buy/`` rather than
  ``creative/`` — the one row the old table argued for at length, now answered by the
  SDK instead of by a comment.
* A DTO with no such ancestry, or one whose schema is named differently from its type,
  declares :data:`~src.core.schemas._base.WireSerializerMixin._PINNED_SCHEMA_REF`
  ITSELF. ``GetTaskStatusRequest`` is the only live case: the tool is ``get_task_status`` and the
  schema is ``protocol/get-task-status-status-request.json``, which no derivation from either
  name produces.

COVERAGE, and the half of it that is currently missing
------------------------------------------------------
The old table was keyed by TOOL, not by model, and its docstring defended that: "a
request DTO that quietly stops being graded is the failure this pairing exists to
prevent, and a model-keyed map cannot notice a tool it was never given." Half of that
still holds here and is stronger than a table: membership is not a key set anyone can
decline to add to, because callers parametrize over :func:`graded_request_schemas`,
whose tool set comes from the LIVE MCP registry.

The other half is GONE. A tool that resolves NO ref is simply absent from that dict,
and the test that made such a tool prove the pinned tree holds no request schema for it
lived in the alignment suite, deleted whole (docs/development/building-tools.md). Nothing
replaces it here: a tool whose binding is dropped now falls out of the grading silently.
Its helper — a token-subset candidate search that found ``get-task-status-status-request.json``
for ``get_task_status``, where an exact-filename probe would have reported "no schema exists" —
was deleted with it.
"""

from __future__ import annotations

from pydantic import BaseModel

#: The package every generated SDK request/response type lives under, one module per
#: pinned schema file. ``adcp.types.generated_poc.media_buy.create_media_buy_request``
#: is ``media-buy/create-media-buy-request.json``: the two components after this marker
#: are the schema's category directory and its file stem, with ``_`` for the ``-`` a
#: Python module name cannot carry.
_GENERATED_PACKAGE = "generated_poc."


def _ref_from_generated_module(module: str) -> str | None:
    """The pinned schema ref a generated SDK type's module path names, if it names one."""
    _, marker, tail = module.partition(_GENERATED_PACKAGE)
    if not marker:
        return None
    category, _, stem = tail.partition(".")
    if not category or not stem:
        return None
    return f"{category.replace('_', '-')}/{stem.replace('_', '-')}.json"


def pinned_request_schema_ref(model: type[BaseModel]) -> str | None:
    """The pinned request schema *model* implements, or None when it implements none.

    DERIVED, with no declaration to prefer. A request DTO used to be able to name its own
    ref on ``_PINNED_SCHEMA_REF``, for "a schema named differently from its type, or a DTO
    with no SDK ancestry at all". GetTaskStatusRequest was the only user and was BOTH of those --
    the spec calls the operation get-task-status-status while the tool is get_task_status, and the model
    was hand-written. It now extends the SDK's GetTaskStatusRequest, so the module path
    names the schema and the derivation reaches it unaided.

    The declaration is gone rather than kept unused: a DTO that can name its own grading
    schema can name the wrong one, and the hand-written parallel it existed to serve is
    exactly what a derivation-only rule prevents. A request DTO with no SDK ancestry now
    resolves no ref and is simply not graded here -- the honest answer for complete_task,
    whose operation the pin does not define at all.
    """
    from src.core.tools._announced_shape import sdk_grounding

    grounding = sdk_grounding(model)
    if grounding is None:
        return None
    return _ref_from_generated_module(grounding.__module__)


def graded_request_schemas() -> dict[str, tuple[str, type[BaseModel]]]:
    """``tool -> (schema ref, request DTO)`` for every registered tool that resolves one.

    The tool set is the LIVE MCP registry, so a tool cannot sit outside the grading by
    being left out of something. A tool that resolves no ref is absent here, and nothing
    currently makes it prove the spec defines no request schema for it — see the module
    docstring.
    """
    from tests.helpers.registered_tools import registered_request_dtos

    graded = {}
    for tool_name, model in registered_request_dtos().items():
        ref = pinned_request_schema_ref(model)
        if ref is not None:
            graded[tool_name] = (ref, model)
    return graded
