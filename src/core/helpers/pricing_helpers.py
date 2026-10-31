"""Pricing option helper utilities.

Owns the ``pricing_info`` projection a package row stores.

NO ROOTMODEL UNWRAP. Both functions below opened with
``getattr(pricing_option, "root", pricing_option)`` for the wrapper the pinned SDK shipped,
and neither kind of argument they receive has a ``root`` attribute, so both hops returned
their own argument.

``pricing_info_for`` TAKES THE ORM ROW, not a schema pricing option, and that is not
interchangeable: ``rate`` and ``is_fixed`` are COLUMNS on ``PricingOption``
(``src/core/database/models.py``) and are fields of no member of the V3 discriminated
union, so handing it a schema member raises ``AttributeError`` on ``rate``. Every caller
passes a row: the three in ``media_buy_create`` read ``product.pricing_options`` off a
``ProductModel``, and the test callers build through ``PricingOptionFactory``.
"""

from typing import Any


def pricing_info_for(pricing_option: Any, *, bid_price: float | None = None) -> dict[str, Any]:
    """The ``pricing_info`` that ``MediaPackage.package_config`` stores for a package.

    NOT a spec shape: ``pricing_info`` appears in no pinned schema and is never received
    from a buyer — ``package_config`` is an internal JSON column. What makes the shape
    binding is its READERS: the GAM order manager and ``src/adapters/utils/pricing.py``
    take a package's rate from ``pricing_info["bid_price"]``, so a writer that omits a key
    silently sends every auction package down a ``.get()`` default.

    ``bid_price`` is not a property of the option — it is what THIS package bid — so it is
    supplied by the caller rather than projected. Everything else is the option's own
    terms, read off it directly (see the module docstring on the unwrap that was removed).
    """
    return {
        "pricing_model": pricing_option.pricing_model,
        "rate": float(pricing_option.rate) if pricing_option.rate else None,
        "currency": pricing_option.currency,
        "is_fixed": pricing_option.is_fixed,
        "bid_price": bid_price,
    }


def pricing_option_has_rate(pricing_option: Any) -> bool:
    """Check if a pricing option carries a fixed rate.

    V3 renamed the fixed rate to ``fixed_price`` (auction options have no rate;
    they carry ``floor_price`` / ``price_guidance``). The pre-V3 ``rate`` key is
    still honored as a fallback for ORM rows and stored legacy dicts, whose
    column keeps that name. Before this check knew about ``fixed_price`` it only
    matched ``rate``, so every V3-shaped option counted as rate-less — the
    anonymous-pricing heuristic in GetProductsResponse.__str__ misfired for
    authenticated buyers, hidden by test fixtures that leaked ``rate`` through
    the SDK members' ``extra="allow"``.

    Handles multiple formats:
    - Dict format (from JSON/serialization): checks fixed_price, then rate
    - Direct attribute access (schema union member or SQLAlchemy row): fixed_price, then rate

    Args:
        pricing_option: A pricing option in any supported format

    Returns:
        True if the pricing option has a non-None fixed rate value
    """
    # Dict format (JSON/serialization)
    if isinstance(pricing_option, dict):
        return pricing_option.get("fixed_price", pricing_option.get("rate")) is not None

    # Model or row attributes, and BOTH getattrs are load-carrying because the two shapes
    # spell the fixed rate disjointly: ``fixed_price`` is declared by all nine union members
    # and is NOT a column, while ``rate`` is a column and is a field of no member. So each
    # read misses on exactly one of the two kinds this function accepts.
    if getattr(pricing_option, "fixed_price", None) is not None:
        return True
    return getattr(pricing_option, "rate", None) is not None
