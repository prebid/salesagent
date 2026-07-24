"""AdCP application context is an opaque, lossless JSON object."""

from typing import Any

from adcp.types import ContextObject

from src.core.application_context import dump_adcp_response, serialize_application_context
from src.core.schemas.product import GetProductsResponse


def _nested_context(depth: int) -> dict[str, Any]:
    """Build a context nested exactly ``depth`` objects deep."""
    root: dict[str, Any] = {}
    cursor = root
    for _ in range(depth - 1):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    cursor["leaf"] = "value"
    return root


def test_typed_context_preserves_explicit_nulls_without_synthesizing_fields() -> None:
    raw = {
        "correlation_id": "ctx-null",
        "nullable": None,
        "nested": {"value": None},
    }
    context = ContextObject.model_validate(raw)

    assert serialize_application_context(context) == raw
    assert serialize_application_context(ContextObject.model_validate({})) == {}


def test_plain_context_is_detached_recursively() -> None:
    raw = {"nested": {"value": None}}
    serialized = serialize_application_context(raw)

    raw["nested"]["value"] = "mutated"
    assert serialized == {"nested": {"value": None}}


def test_deeply_nested_plain_context_survives_intact() -> None:
    """No depth ceiling: ``core/context.json`` sets none, so none is enforced.

    ``copy.deepcopy`` exhausts CPython's call stack around 500 levels — an
    earlier version of this function used it and silently dropped context past
    a 100-level bound, violating the normative echo contract for perfectly
    schema-valid input. The iterative detach in ``_detach`` has no such limit:
    a context nested 5,000 objects deep — an order of magnitude past both the
    old bound and the recursion ceiling it was avoiding — is echoed exactly.
    """
    raw = _nested_context(5000)

    assert serialize_application_context(raw) == raw


def test_deeply_nested_typed_context_survives_intact() -> None:
    """The ``ContextObject`` branch must not hand a deep structure to Pydantic's
    own serializer, whose internal recursion guard trips independently of
    Python's — reading ``model_extra`` directly and detaching it ourselves
    sidesteps that guard entirely.
    """
    raw = _nested_context(5000)
    context = ContextObject.model_validate(raw)

    assert serialize_application_context(context) == raw


def test_response_dump_restores_lossless_context_and_omits_absence() -> None:
    raw = {"nullable": None, "nested": {"value": None}}
    with_context = GetProductsResponse(
        products=[],
        context=ContextObject.model_validate(raw),
    )
    without_context = GetProductsResponse(products=[])

    assert dump_adcp_response(with_context)["context"] == raw
    assert "context" not in dump_adcp_response(without_context)
