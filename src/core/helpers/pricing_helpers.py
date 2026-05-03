"""Pricing option helper utilities.

Centralizes the heuristic that gates the anonymous-vs-authenticated message
branch in :class:`GetProductsResponse.__str__`. The codebase has historically
trapped on this helper when AdCP renamed pricing fields — see issue #1246.
"""

from typing import Any


def pricing_option_is_priced(pricing_option: Any) -> bool:
    """True if the pricing option exposes any rate-bearing value to the buyer.

    Used by :class:`GetProductsResponse.__str__` to decide whether to append
    the "Please connect through an authorized buying agent" suffix. The
    function answers "does this option reveal pricing to the buyer?", not "is
    it fixed-rate?" — so any of the four AdCP rate-bearing fields counts:

      - ``rate``           — v2 legacy field name (still on the ORM column)
      - ``fixed_price``    — v3 spec name (replaces v2 ``rate``)
      - ``floor_price``    — v3 auction floor (was v2 ``price_guidance.floor``)
      - ``price_guidance`` — v3 percentile hints; spec-legal as the sole
        rate-bearing field for an auction option that publishes percentiles
        but no hard floor.

    Recognized across the five input shapes used by the codebase: JSON dict,
    adcp library ``PricingOption`` RootModel (with ``.root`` proxy), library
    typed option (``CpmPricingOption`` etc.), internal Pydantic
    ``PricingOption``, and SQLAlchemy ORM ``PricingOption`` (whose column is
    literally named ``rate`` for legacy reasons).

    Long-term: replace this string-message heuristic with a structured
    ``errors[]`` entry (AdCP v3 ``AUTH_REQUIRED``) set at the
    ``_get_products_impl`` layer where ``principal_id is None`` is already
    known. See issue #1246 for the migration plan.

    When AdCP renames or adds a pricing field, update the local tuple below
    AND extend ``tests/unit/test_pricing_option_is_priced.py``. The structural
    guard ``test_architecture_pricing_helper_completeness.py`` enforces that
    this function (and any sibling pricing accessor) keeps the v2 and v3
    field names in lockstep.

    Note for test authors: callers must pass real instances or
    ``Mock(spec=[...])``; plain ``MagicMock()`` causes spurious True returns
    because every attribute access produces a non-None Mock.

    Args:
        pricing_option: A pricing option in any supported shape, or None.

    Returns:
        True if any of (rate, fixed_price, floor_price, price_guidance) is set
        and not None; False otherwise (including for None input).
    """
    if pricing_option is None:
        return False

    rate_bearing_fields = ("rate", "fixed_price", "floor_price", "price_guidance")

    if isinstance(pricing_option, dict):
        return any(pricing_option.get(field) is not None for field in rate_bearing_fields)

    # The defensive `hasattr` is required here (and is the documented exception
    # to the rootmodel-access lint rule) because this helper accepts five
    # heterogeneous input shapes — dict, adcp library RootModel, library typed
    # option, internal Pydantic PricingOption, ORM model — and the caller's
    # type is genuinely unknown. `hasattr` rather than `or` also avoids the
    # truthiness pitfall if `.root` is ever a legitimately falsy value.
    target = pricing_option.root if hasattr(pricing_option, "root") else pricing_option  # noqa: rootmodel
    return any(getattr(target, field, None) is not None for field in rate_bearing_fields)
