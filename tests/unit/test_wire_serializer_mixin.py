"""The ``WireSerializerMixin`` seat: what it keeps, and what it must not override.

Exercised through ``GetMediaBuysMediaBuy``, the one adopter the pin actually gives a
required-nullable field. The previous version used ``Account`` and asserted that
advertiser, rate_card and payment_terms are "required-nullable" — a premise
core/account.json contradicts on every count: all three are plain optionals, none
appear in ``required``, none are typed nullable. Those assertions could only pass
while production emitted a document that FAILED validation against that schema.

What is graded here is the seat's two rules, not the field set. The set is derived
from the pin (``_PINNED_SCHEMA_REF``), so asserting it equals the pin would be a
tautology; the full-document BDD scenarios grade what reaches the buyer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.schemas._base import GetMediaBuysMediaBuy


def _buy(**overrides) -> GetMediaBuysMediaBuy:
    """A media buy whose ``confirmed_at`` is null unless a caller sets it."""
    fields = {
        "media_buy_id": "mb-1",
        "status": "active",
        "currency": "USD",
        "total_budget": 1000.0,
        "packages": [],
        "confirmed_at": None,
        "revision": 1,
    }
    fields.update(overrides)
    return GetMediaBuysMediaBuy(**fields)


class TestRequiredNullableRetention:
    """The pin lists confirmed_at in ``required`` and types it ["string","null"]."""

    @pytest.mark.parametrize("mode", ["python", "json"])
    def test_a_null_required_nullable_field_survives_exclude_none(self, mode: str) -> None:
        dumped = _buy().model_dump(mode=mode) if mode == "json" else _buy().model_dump()
        assert "confirmed_at" in dumped, "confirmed_at was dropped by exclude_none; the pin requires it"
        assert dumped["confirmed_at"] is None

    def test_a_field_the_pin_does_not_require_is_omitted_when_null(self) -> None:
        """The derivation's whole point: only required-AND-nullable fields are kept."""
        dumped = _buy().model_dump()
        assert "buyer_campaign_ref" not in dumped, (
            "an optional field was retained as null — the always-include set is no longer "
            "derived from the pin's required-and-nullable intersection"
        )


class TestCallerSelectionIsHonoured:
    """The wrap serializer receives the caller's selection on ``info``."""

    def test_explicit_exclude_is_not_undone(self) -> None:
        dumped = _buy().model_dump(exclude={"confirmed_at"})
        assert "confirmed_at" not in dumped, "an explicitly excluded field must not be re-inserted"

    def test_include_selection_is_not_widened(self) -> None:
        dumped = _buy().model_dump(include={"media_buy_id"})
        assert set(dumped) == {"media_buy_id"}, f"retention widened an include= selection: {sorted(dumped)}"


class TestOnlyNullValuesArePutBack:
    """Nothing but ``None`` is ever written by the retention step.

    The old override re-inserted ``getattr(self, field)``, so under ``mode="json"`` a
    live ``datetime`` could land in a JSON document. Writing only ``None`` removes
    that by construction.
    """

    def test_a_populated_field_is_untouched_by_retention(self) -> None:
        stamped = _buy(confirmed_at=datetime(2026, 3, 1, tzinfo=UTC))
        assert stamped.model_dump(mode="json")["confirmed_at"] == "2026-03-01T00:00:00Z"
