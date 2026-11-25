"""The advertising channels an operator can tag a product with: the picker's options and the write rule.

Kept out of ``src.core.helpers``: that package's ``__init__`` loads the adapters, which import
the models, and both readers of this module sit below them -- ``Product.channels``' validator
(``src/core/json_validators.py``) and the admin app's context processor import it at module
top. So it imports only ``MediaChannel`` and the refusal it raises (precedent:
``src/core/enum_helpers.py``). GH #1532.
"""

from __future__ import annotations

from collections.abc import Iterable

from adcp.types._generated import MediaChannel

from src.core.operator_errors import FormIssue, InvalidFormInput

#: The values ``Product.channels`` accepts. ``parse_media_channels`` decides membership with it,
#: and a caller that branches on membership (the UC-010 harness) imports it rather than
#: rebuilding the set.
MEDIA_CHANNEL_VALUES: frozenset[str] = frozenset(member.value for member in MediaChannel)

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
    unknown = [value for value in posted if value not in MEDIA_CHANNEL_VALUES]
    if unknown:
        raise InvalidFormInput(
            *(FormIssue("channels", f"channels: '{value}' is not an AdCP media channel") for value in unknown)
        )
    return [member.value for member in MediaChannel if member.value in posted] or None
