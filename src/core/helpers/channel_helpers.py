"""Which advertising channels a product actually offers.

One rule, two readers. ``get_products`` applies it per product to answer a
buyer's ``filters.channels``; ``get_adcp_capabilities`` unions it across the
tenant's catalog to answer ``media_buy.portfolio.primary_channels``. The rule was
written inline in the filter first, and the capabilities path did not apply it at
all -- it reported the adapter class's constant, so a seller whose catalog
declared its channels was described by its ad server's defaults instead.

The rule must be shared rather than re-spelled, and not only for tidiness: the two
sites would disagree the moment one of them treated "this product declares nothing"
differently, and a buyer would then be offered a channel the filter would refuse.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


def effective_channel_names(declared: Iterable[Any] | None, *, adapter_defaults: Sequence[str]) -> set[str]:
    """The lowercase channel names a product effectively offers.

    A product that declares channels offers exactly those. A product that declares
    none inherits what the tenant's ad server reports, because an undeclared
    catalog entry is not a claim of "no channels" -- it is the absence of a claim,
    and the ad server is the next-best answer.

    Args:
        declared: The product's ``channels``, or None. Accepts ``MediaChannel``
            members (the Pydantic model's type) and plain strings (the ORM
            column's), because the two readers hold the product in different
            forms and normalizing here is what lets them share the rule.
        adapter_defaults: Channel names the tenant's adapter reports, from
            ``get_adapter_default_channels``.

    Returns:
        Lowercase names. EMPTY means "no basis to answer" -- the product declares
        nothing and the adapter reports nothing. It does NOT mean "offers no
        channels", and a caller must not read it as an exclusion: the filter
        treats an empty set as "do not narrow", which is the behaviour it had
        before this rule was extracted.
    """
    if declared:
        return {str(getattr(channel, "value", channel)).lower() for channel in declared}
    return {name.lower() for name in adapter_defaults}
