"""AdCP v2 backward-compatibility transform for ``get_products`` responses.

For pre-3.0 clients the wire response must include the legacy v2 field
names alongside the v3 ones: each pricing option needs ``is_fixed``,
``rate`` (mirror of ``fixed_price``), and ``price_guidance.floor`` (mirror
of top-level ``floor_price``). This module provides a single function,
:func:`add_get_products_v2_compat`, that walks an already-serialized
response dict and adds those keys when ``adcp_version`` predates 3.0.

Why dict-in/dict-out: every transport boundary (``products.py``,
``api_v1.py``, ``adcp_a2a_server.py``) calls ``response.model_dump(mode="json")``
before applying compat — model_dump flattens the adcp library's
``PricingOption(RootModel)`` wrapper, so the resulting dict has v3 fields
at the top level of each pricing-option entry, exactly where this transform
expects to find them. Working in dict space keeps callers simple and avoids
re-deriving fields that ``model_dump`` already produced.

History: this module replaced an earlier "registry" abstraction
(``apply_version_compat(tool_name, ...)``) that only ever held one entry
(``"get_products"``) yet kept ``tool_name`` as a parameter. The rename to
``add_get_products_v2_compat`` retires the dead generality. See PR #1081's
squash for the silent-no-op regression that motivated the rewrite, and
issue #1246 for the broader v2/v3 field-rename audit.
"""

from typing import Any

from src.core.product_conversion import needs_v2_compat


def add_get_products_v2_compat(response: dict[str, Any], adcp_version: str | None) -> dict[str, Any]:
    """Mutate a serialized ``get_products`` response dict to add v2 fields.

    No-ops for v3+ clients. For pre-3.0 clients, walks every pricing option
    in every product and adds ``is_fixed``, ``rate``, and
    ``price_guidance.floor`` derived from the v3 fields already present.

    Args:
        response: Serialized response dict (typically produced by
            ``GetProductsResponse.model_dump(mode="json")`` plus any A2A
            envelope keys like ``message`` / ``success``). Mutated in place.
        adcp_version: Buyer-declared AdCP version, or None.

    Returns:
        The same dict (returned for caller ergonomics).
    """
    if not needs_v2_compat(adcp_version):
        return response
    for product in response.get("products", []):
        for pricing_option in product.get("pricing_options", []):
            _add_v2_compat_keys(pricing_option)
    return response


def _add_v2_compat_keys(pricing_option: dict[str, Any]) -> None:
    """Add v2 backward-compat keys to a single serialized pricing-option dict.

    Mutates ``pricing_option`` in place. The caller is responsible for ensuring
    the input is the model_dump output of a single pricing option (so the v3
    fields ``fixed_price`` / ``floor_price`` are at this dict's top level —
    the RootModel wrapper has already been flattened).

    v2 keys derived:
        ``is_fixed``                      ← True iff ``fixed_price`` is set
        ``rate``                          ← ``fixed_price`` (mirror)
        ``price_guidance.floor``          ← ``floor_price`` (mirror)
    """
    fixed_price = pricing_option.get("fixed_price")
    floor_price = pricing_option.get("floor_price")
    pricing_option["is_fixed"] = fixed_price is not None
    if fixed_price is not None:
        pricing_option["rate"] = fixed_price
    if floor_price is not None:
        # Defensive: callers normally pass model_dump(mode="json") output, which
        # omits None-valued optional fields, so price_guidance is either absent
        # or a real dict. A user-constructed dict could carry an explicit None,
        # which `setdefault` would return verbatim and fail on item assignment.
        # Coalesce explicitly to avoid that contract surprise.
        price_guidance = pricing_option.get("price_guidance") or {}
        pricing_option["price_guidance"] = price_guidance
        price_guidance["floor"] = floor_price
