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

from adcp.types._generated import MediaChannel

from src.core.operator_errors import FormIssue, InvalidFormInput

#: Labels for the admin picker where title-casing the value is unreadable ("Olv", "Dooh");
#: every other member title-cases its value. Keyed by member, so mypy refuses a non-member.
MEDIA_CHANNEL_LABELS: dict[MediaChannel, str] = {
    MediaChannel.olv: "OLV (Online Video)",
    MediaChannel.ctv: "CTV (Connected TV)",
    MediaChannel.dooh: "DOOH (Digital Out-of-Home)",
    MediaChannel.ooh: "OOH (Out-of-Home)",
    MediaChannel.linear_tv: "Linear TV",
}


def media_channel_options() -> list[tuple[str, str]]:
    """``(value, label)`` per ``MediaChannel`` member, in enum order, for the admin channel picker.

    The picker iterates this list, so it holds no channel literal of its own and cannot
    offer a value the enum does not have (GH #1532).
    """
    return [
        (member.value, MEDIA_CHANNEL_LABELS.get(member) or member.value.replace("_", " ").title())
        for member in MediaChannel
    ]


def parse_media_channels(values: Iterable[str]) -> list[str] | None:
    """``Product.channels`` as stored: *values* in enum order without duplicates, or None for none.

    The column's validator (``JSONValidatorMixin.validate_channels``) calls this on every
    write, so it is the one rule. Refuses with :class:`InvalidFormInput`, one issue per
    non-member, which the admin error handler answers. A non-member can only come from a form
    rendered before the picker became enum-driven or from a crafted request, and both are
    refused rather than translated: an alias table here would be a second channel vocabulary
    beside the enum. Enum order is the order the picker posts, the repair migration writes and
    ``get_adcp_capabilities`` reports, so saving a product unchanged writes the same array;
    ``Product.channels`` is ``uniqueItems`` in the pinned schema, and None is what the column
    stores for "no channels declared".
    """
    posted = dict.fromkeys(values)
    members = {member.value for member in MediaChannel}
    unknown = [value for value in posted if value not in members]
    if unknown:
        raise InvalidFormInput(
            *(FormIssue("channels", f"channels: '{value}' is not an AdCP media channel") for value in unknown)
        )
    return [member.value for member in MediaChannel if member.value in posted] or None


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
