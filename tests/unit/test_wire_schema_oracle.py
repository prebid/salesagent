"""The shared wire-vs-pinned-schema oracle grades both arms, and its
known-missing-required tolerance cannot silently outlive the gap it documents.

``tests.helpers.wire_schema.assert_wire_items_schema_valid`` replaced an inline
copy of this logic in a UC-019 BDD step. The step's own scenario only ever
exercises the PASSING direction, so without these tests every arm of the oracle
could be vacuous — a validator built against the wrong subschema, a framing
guard that never rejects, a tolerance list that quietly matched nothing — and
the scenario would stay green. Each test below drives the FAILING direction.
"""

from __future__ import annotations

import pytest

from src.core.schemas._base import GetMediaBuysResponse
from tests.helpers.pinned_schema import PinnedSchemaError, array_item_validator_for
from tests.helpers.wire_schema import assert_wire_items_schema_valid

SCHEMA_REF = "media-buy/get-media-buys-response.json"

# The two fields the repo's GetMediaBuysMediaBuy does not model; the pinned
# schema marks both required. Mirrors uc019_query_media_buys._UNMODELED_REQUIRED.
UNMODELED_REQUIRED = frozenset({"confirmed_at", "revision"})


def _media_buy(**overrides):
    """A media-buy item satisfying every pinned required field except the two
    unmodeled ones — the shape production actually puts on the wire."""
    return {
        "media_buy_id": "mb-1",
        "status": "pending_creatives",
        "currency": "USD",
        "total_budget": 1000.0,
        "packages": [],
        **overrides,
    }


def _validate(wire, *, known_missing_required=UNMODELED_REQUIRED):
    assert_wire_items_schema_valid(
        wire,
        schema_ref=SCHEMA_REF,
        item_key="media_buys",
        model=GetMediaBuysResponse,
        known_missing_required=known_missing_required,
    )


class TestPayloadArm:
    def test_conformant_wire_passes(self):
        _validate({"media_buys": [_media_buy()], "status": "completed"})

    def test_retyped_id_fails(self):
        """A field whose TYPE regressed must fail — proves the item validator is
        wired to the real item subschema, not an empty/permissive one."""
        with pytest.raises(AssertionError, match="is not of type 'string'"):
            _validate({"media_buys": [_media_buy(media_buy_id=5)]})

    def test_dropped_required_field_fails(self):
        """A required field OUTSIDE the tolerance still grades — the tolerance
        is not a blanket 'ignore required'."""
        item = _media_buy()
        del item["status"]
        with pytest.raises(AssertionError, match="'status' is a required property"):
            _validate({"media_buys": [item]})

    def test_cross_file_ref_resolves(self):
        """``account`` is a ``$ref`` into ../core/account.json. If the parent's
        ``$id`` were not carried onto the item subschema the ref would be
        unresolvable and this violation would never surface."""
        with pytest.raises(AssertionError, match="'name' is a required property"):
            _validate({"media_buys": [_media_buy(account={"account_id": "a1"})]})

    def test_tolerated_fields_are_the_only_ones_tolerated(self):
        """Without the tolerance the same conformant wire fails on exactly the
        two unmodeled fields — proving the tolerance is load-bearing, and that
        the wire really is non-conformant on those two."""
        with pytest.raises(AssertionError) as exc:
            _validate({"media_buys": [_media_buy()]}, known_missing_required=frozenset())
        message = str(exc.value)
        assert "'confirmed_at' is a required property" in message
        assert "'revision' is a required property" in message

    def test_missing_array_fails(self):
        with pytest.raises(AssertionError, match="wire carries no media_buys array"):
            _validate({"status": "completed"})


class TestFramingArm:
    def test_envelope_framing_keys_are_accepted(self):
        _validate(
            {
                "media_buys": [_media_buy()],
                "status": "completed",
                "task_id": "t-1",
                "context_id": "c-1",
                "message": "ok",
                "success": True,
            }
        )

    def test_bogus_top_level_key_is_rejected(self):
        """The pinned schema sets ``additionalProperties: true`` at the top
        level, so jsonschema alone cannot catch this — the framing allowlist is
        the only thing that does."""
        with pytest.raises(AssertionError, match=r"unexpected top-level wire keys \['mediabuys'\]"):
            _validate({"media_buys": [_media_buy()], "mediabuys": []})


class TestToleranceStaleness:
    def test_name_not_required_by_the_pin_raises(self):
        """A tolerance naming a field the pinned schema does NOT require is a
        no-op that would silently persist after the gap closed."""
        with pytest.raises(PinnedSchemaError, match="the tolerance is a no-op"):
            array_item_validator_for(SCHEMA_REF, "media_buys", ignore_required=frozenset({"currency", "not_a_field"}))

    def test_unknown_array_property_raises(self):
        with pytest.raises(PinnedSchemaError, match="has no array property 'nope'"):
            array_item_validator_for(SCHEMA_REF, "nope")
