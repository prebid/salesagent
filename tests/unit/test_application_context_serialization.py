"""AdCP application context is an opaque, lossless JSON object."""

from typing import Any

from adcp.types import ContextObject

from src.core.application_context import (
    MAX_CONTEXT_DEPTH,
    dump_adcp_response,
    serialize_application_context,
)
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


def test_context_at_the_depth_bound_is_echoed_unchanged() -> None:
    """Everything a conformant buyer can send survives the detach untouched."""
    raw = _nested_context(MAX_CONTEXT_DEPTH)

    assert serialize_application_context(raw) == raw


def test_context_past_the_depth_bound_is_dropped_not_raised(caplog) -> None:
    """Callers run inside exception handlers; a secondary failure must not escape.

    ``copy.deepcopy`` exhausts the interpreter stack around 500 levels, so an
    unbounded detach turned a buyer's error envelope into a bare 500.
    """
    raw = _nested_context(MAX_CONTEXT_DEPTH + 1)

    with caplog.at_level("WARNING", logger="src.core.application_context"):
        assert serialize_application_context(raw) is None

    assert "nests deeper than" in caplog.text


def test_deeply_nested_typed_context_is_dropped_not_raised() -> None:
    """The ``ContextObject`` branch dumps opaque extras and must degrade too."""
    context = ContextObject.model_validate(_nested_context(MAX_CONTEXT_DEPTH * 8))

    assert serialize_application_context(context) is None


def test_response_dump_restores_lossless_context_and_omits_absence() -> None:
    raw = {"nullable": None, "nested": {"value": None}}
    with_context = GetProductsResponse(
        products=[],
        context=ContextObject.model_validate(raw),
    )
    without_context = GetProductsResponse(products=[])

    assert dump_adcp_response(with_context)["context"] == raw
    assert "context" not in dump_adcp_response(without_context)
