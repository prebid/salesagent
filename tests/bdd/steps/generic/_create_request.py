"""``ctx``-reading wrapper over the harness's create-request builder.

The SHAPE lives in ``tests.harness.create_request`` — see that module for why. What is
left here is the only part that is genuinely BDD: reading the seeded product and pricing
option off ``ctx`` and stashing the result back on ``ctx["request_kwargs"]``. ``ctx`` is a
pytest-bdd concept and does not belong in the harness, so the split is at that line and
not somewhere more convenient.

``pricing_option_id`` is re-exported because 34 call sites import it from here, 8 of them
outside ``tests/bdd`` — which was itself the evidence that it was in the wrong layer.
"""

from __future__ import annotations

from typing import Any

from tests.harness.create_request import build_create_request, pricing_option_id

__all__ = ["build_create_request_kwargs", "pricing_option_id"]


def build_create_request_kwargs(
    ctx: dict,
    *,
    po_number: str | None = None,
    budget: float = 5000.0,
    product_id: str | None = None,
    pricing_option: str | None = None,
) -> dict[str, Any]:
    """Assemble a valid create_media_buy request dict against the seeded product.

    Stored on ``ctx["request_kwargs"]`` and returned.

    ``product_id`` / ``pricing_option`` let a caller pass values it has already resolved.
    ``pricing_option`` is the ID STRING, not a ``PricingOption`` row — the natural name
    would shadow the ``pricing_option_id`` helper imported above, which is a small cost of
    there being exactly one such helper. Omitted, they are read from ctx, which is what the
    create-flow steps do.

    ONE package. A scenario needing more says so in its own sentence and replaces the
    array through ``build_request_packages`` — it does not pass a count here. The base
    request is assembled BEFORE the packages sentence runs (both binders order it that
    way: "a valid create_media_buy request with: <table>" comes first), so a builder that
    also owned the count would have to be re-run afterwards and would discard the table's
    account, brand and flight dates.
    """
    if product_id is None:
        product_id = ctx["default_product"].product_id
    if pricing_option is None:
        pricing_option = pricing_option_id(ctx["default_pricing_option"])

    ctx["request_kwargs"] = build_create_request([(product_id, pricing_option)], po_number=po_number, budget=budget)
    return ctx["request_kwargs"]
