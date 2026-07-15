"""Shared assertions for the AdCP v3.1 format_id federation contract.

A serialized ``format_id`` MUST be an object carrying both ``agent_url`` and
``id`` — never a bare string. This is the **schema** contract
(``core/format-id.json``: ``required: [agent_url, id]``, never a plain string).
The storyboard (``creative/index.yaml`` discover_formats / list_formats) only
grades ``field_present`` on ``formats[0]``; this helper is intentionally
stricter — every entry, never a bare string.

Used by the UC-005 ``format-id-shape`` BDD steps and their falsifiability unit
test; reusable by the ``roundtrip-from-products`` / ``third-party-agent``
sibling scenarios.
"""

from __future__ import annotations

from typing import Any


def assert_wire_format_id_is_object(fid: Any) -> None:
    """Assert a single serialized ``format_id`` is an object with ``agent_url`` + ``id``.

    ``isinstance(fid, dict)`` is the falsifiable check: a regression that flattens
    the structured object to its ``id`` string on the wire serializes as ``str``
    and fails here. Asserts *presence* of ``agent_url`` and ``id``, not an exact
    key set — adcp 5.7.0 adds optional ``width``/``height``/``duration_ms``, and
    ``agent_url`` normalizes with a trailing slash so its value is not asserted.

    Args:
        fid: A single ``format_id`` value as it appears on the serialized wire.

    Raises:
        AssertionError: if ``fid`` is not an object, or is missing either key.
    """
    assert isinstance(fid, dict), f"format_id must serialize as an object, got {type(fid).__name__}: {fid!r}"
    assert "agent_url" in fid, f"format_id missing agent_url: {fid!r}"
    assert "id" in fid, f"format_id missing id: {fid!r}"


def capture_advertised_format_id(env, *, product_id=None, brief="format_id roundtrip"):
    """Capture the seller's advertised ``format_id`` via a real get_products call.

    The shared capture core for the UC-005/UC-006 roundtrip Givens: calls
    ``_get_products_impl`` with the env identity and returns
    ``products[].format_ids[0]`` verbatim as an ``{"agent_url", "id"}`` dict.
    Callers keep their own product seeding; ``product_id`` narrows the capture
    to the seeded product (required on shared e2e_rest server DBs).
    """
    import asyncio

    from src.core.schemas import GetProductsRequest
    from src.core.tools.products import _get_products_impl

    response = asyncio.run(_get_products_impl(GetProductsRequest(brief=brief), env.identity))
    assert response.products, "get_products returned no products — cannot capture format_id"
    products = response.products
    if product_id is not None:
        products = [p for p in products if p.product_id == product_id]
        assert products, f"seeded product {product_id!r} not in get_products response"
    assert products[0].format_ids, "product has no format_ids — cannot capture format_id"
    fid = products[0].format_ids[0]
    return {"agent_url": str(fid.agent_url), "id": str(fid.id)}
