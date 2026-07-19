"""AdCP application context is an opaque, lossless JSON object."""

from adcp.types import ContextObject

from src.core.application_context import dump_adcp_response, serialize_application_context
from src.core.schemas.product import GetProductsResponse


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


def test_response_dump_restores_lossless_context_and_omits_absence() -> None:
    raw = {"nullable": None, "nested": {"value": None}}
    with_context = GetProductsResponse(
        products=[],
        context=ContextObject.model_validate(raw),
    )
    without_context = GetProductsResponse(products=[])

    assert dump_adcp_response(with_context)["context"] == raw
    assert "context" not in dump_adcp_response(without_context)
