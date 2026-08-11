"""Meta-tests pinning the canonical wire-assertion helpers' own contract.

These grade the ASSERTION HELPERS, not production. They exist because the
helpers are about to absorb ~47 hand-rolled call sites (salesagent-hu31 /
-wmx1 / -ot32) whose private predecessors carried semantics a naive
"just index the dotted path" replacement would silently drop:

* ``_require`` failed on a path that was absent **or** carried a JSON null;
* ``_assert_absent`` treated a JSON null as PRESENT (only a missing key is
  absent).

Losing either direction turns a real assertion into a vacuous pass, which is
the exact defect class the migration exists to remove. So the null-rejection
tests below are the hard gate: they must pass BEFORE any call site migrates
onto :func:`wire_field` / :func:`wire_absent`.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from tests.bdd.steps._outcome_helpers import WIRE_MISSING, wire_absent, wire_dict, wire_field, wire_lookup
from tests.harness.transport import Transport, TransportResult
from tests.helpers.envelope_assertions import assert_envelope_shape


def _ctx(wire: dict, transport: Transport = Transport.REST) -> dict:
    """A minimal ctx carrying a captured success-path wire, as dispatch_request writes it."""
    return {"wire_response": wire, "transport": transport}


_WIRE = {
    "adcp_version": "3.1.1",
    "media_buy": {"features": {"sandbox": True, "targeting": None}, "pricing": {}},
    "packages": [{"id": "pkg_1"}],
    "context_id": None,
}


class TestWireFieldResolution:
    def test_top_level_key_resolves(self):
        assert wire_field(_ctx(_WIRE), "adcp_version") == "3.1.1"

    def test_dotted_path_resolves_nested_value(self):
        assert wire_field(_ctx(_WIRE), "media_buy.features.sandbox") is True

    def test_dotted_path_resolves_nested_object(self):
        assert wire_field(_ctx(_WIRE), "media_buy.features") == {"sandbox": True, "targeting": None}

    def test_falsy_but_present_value_is_returned_not_rejected(self):
        """An empty object is a populated-but-empty section, not an absence."""
        assert wire_field(_ctx(_WIRE), "media_buy.pricing") == {}

    def test_absent_path_raises_and_names_top_level_keys(self):
        with pytest.raises(AssertionError, match="absent from wire response"):
            wire_field(_ctx(_WIRE), "media_buy.nonexistent")

    def test_hop_through_non_dict_is_an_absence_not_a_crash(self):
        with pytest.raises(AssertionError, match="absent from wire response"):
            wire_field(_ctx(_WIRE), "packages.id")


class TestWireFieldRejectsJsonNull:
    """The hard gate: a present-but-null path is a defect, never a value.

    Replaces ``uc010_capabilities._require``'s second assert. Without these, the
    migration would downgrade ~27 dual asserts to presence-only.
    """

    def test_null_at_dotted_path_raises(self):
        with pytest.raises(AssertionError, match="schema-invalid serialization"):
            wire_field(_ctx(_WIRE), "media_buy.features.targeting")

    def test_null_at_top_level_raises(self):
        with pytest.raises(AssertionError, match="schema-invalid serialization"):
            wire_field(_ctx(_WIRE), "context_id")


class TestWireAbsent:
    def test_missing_path_is_absent(self):
        wire_absent(_ctx(_WIRE), "media_buy.brand")

    def test_missing_top_level_key_is_absent(self):
        wire_absent(_ctx(_WIRE), "account")

    def test_present_value_is_not_absent(self):
        with pytest.raises(AssertionError, match="unexpectedly present"):
            wire_absent(_ctx(_WIRE), "media_buy.features.sandbox")

    def test_json_null_counts_as_present_and_fails(self):
        """The hard gate's mirror: ``null`` on the wire is an emission, not an omission.

        An unset optional section must not be serialized at all. If ``wire_absent``
        passed here, every "should not advertise X" scenario would go green against
        a wire that advertises ``X: null``.
        """
        with pytest.raises(AssertionError, match="unexpectedly present"):
            wire_absent(_ctx(_WIRE), "media_buy.features.targeting")


class TestWireLookup:
    """The tri-state primitive: absent vs present-with-a-value, asserting nothing.

    Exists for the two ``uc010`` oracles whose contract is genuinely three-valued
    (``absent or false`` outline columns; a conditional Then that grades a block
    only when the seller emits it). Every OTHER caller must use the asserting
    helpers — this returning a sentinel is what makes it a primitive rather than a
    second assertion mechanism.
    """

    def test_present_value_is_returned(self):
        assert wire_lookup(_ctx(_WIRE), "media_buy.features.sandbox") is True

    def test_absent_path_returns_the_sentinel(self):
        assert wire_lookup(_ctx(_WIRE), "media_buy.brand") is WIRE_MISSING

    def test_json_null_is_returned_as_none_not_the_sentinel(self):
        """The whole point of the sentinel: null is a VALUE here, absence is not."""
        assert wire_lookup(_ctx(_WIRE), "media_buy.features.targeting") is None

    def test_sentinel_repr_reads_as_an_absence(self):
        assert repr(WIRE_MISSING) == "<absent from wire>"

    def test_inherits_the_loud_guard(self):
        with pytest.raises(AssertionError, match="does not stash success-path wire"):
            wire_lookup({"transport": Transport.REST}, "adcp_version")


class TestWireDict:
    def test_no_path_returns_the_whole_body(self):
        assert wire_dict(_ctx(_WIRE)) == _WIRE

    def test_path_returns_the_nested_object(self):
        assert wire_dict(_ctx(_WIRE), "media_buy.features") == {"sandbox": True, "targeting": None}

    def test_path_to_a_scalar_raises(self):
        with pytest.raises(AssertionError, match="not a JSON object"):
            wire_dict(_ctx(_WIRE), "adcp_version")

    def test_path_inherits_the_null_rejection(self):
        with pytest.raises(AssertionError, match="schema-invalid serialization"):
            wire_dict(_ctx(_WIRE), "media_buy.features.targeting")


class TestLoudGuardSurvivesTheExtension:
    """A real-wire transport that stashed nothing must still raise, not fall back.

    The dotted-path rewrite routes all three helpers through one ``_wire_body``;
    if that refactor lost the guard, every helper would silently serialize the
    typed payload instead of grading the wire.
    """

    @pytest.mark.parametrize("helper", [wire_field, wire_absent])
    def test_missing_wire_on_real_transport_raises(self, helper):
        with pytest.raises(AssertionError, match="does not stash success-path wire"):
            helper({"transport": Transport.MCP}, "adcp_version")

    def test_wire_dict_missing_wire_on_real_transport_raises(self):
        with pytest.raises(AssertionError, match="does not stash success-path wire"):
            wire_dict({"transport": Transport.A2A})

    def test_an_errored_scenario_reports_the_error_not_a_missing_wire(self):
        """Preserves ``uc010._wire``'s error-first diagnostic through the migration.

        Blaming the env for a non-stashing wire when the request simply failed sends
        the reader hunting a harness bug that isn't there.
        """
        ctx = {"transport": Transport.REST, "error": RuntimeError("VERSION_UNSUPPORTED")}
        with pytest.raises(AssertionError, match="expected a success response, got error"):
            wire_field(ctx, "adcp_version")

    def test_a_captured_wire_still_wins_over_a_stale_error(self):
        ctx = {"transport": Transport.REST, "wire_response": _WIRE, "error": RuntimeError("stale")}
        assert wire_field(ctx, "adcp_version") == "3.1.1"


class _TypedPayload(BaseModel):
    """Stand-in for a typed response payload, as ctx["response"] carries one."""

    adcp_version: str = "3.1.1"


class TestUnsetTransportIsNotImpl:
    """``transport=None`` (unset) must raise loudly, never silently serialize.

    The model_dump fallback is legitimate ONLY for an EXPLICIT ``Transport.IMPL``
    — the caller declaring "there is no wire here, grade the serializer". An
    unset transport is a non-parametrized caller (transport-tagged BDD scenarios
    get an empty ctx) that never told the helper whether a real wire must exist;
    falling back silently turns its wire assertion into a serializer round-trip
    (GH #1744).
    """

    @pytest.mark.parametrize("base_ctx", [{}, {"transport": None}], ids=["key-absent", "explicit-none"])
    @pytest.mark.parametrize("helper", [wire_field, wire_absent])
    def test_unset_transport_raises_and_names_the_fix(self, base_ctx, helper):
        ctx = {**base_ctx, "response": _TypedPayload()}
        with pytest.raises(AssertionError, match=r"transport unset.*ctx\['transport'\].*Transport\.IMPL"):
            helper(ctx, "adcp_version")

    @pytest.mark.parametrize("base_ctx", [{}, {"transport": None}], ids=["key-absent", "explicit-none"])
    def test_wire_dict_unset_transport_raises(self, base_ctx):
        ctx = {**base_ctx, "response": _TypedPayload()}
        with pytest.raises(AssertionError, match=r"transport unset.*ctx\['transport'\].*Transport\.IMPL"):
            wire_dict(ctx)

    def test_explicit_impl_still_serializes_the_typed_payload(self):
        """The sanctioned no-wire path: IMPL declared explicitly grades the serializer."""
        ctx = {"transport": Transport.IMPL, "response": _TypedPayload()}
        assert wire_field(ctx, "adcp_version") == "3.1.1"

    def test_a_captured_wire_wins_regardless_of_unset_transport(self):
        """A stashed wire is a real wire — the guard is about the MISSING-wire path only."""
        assert wire_field({"wire_response": _WIRE}, "adcp_version") == "3.1.1"


def _error_envelope(field: str | None) -> dict:
    error: dict = {"code": "VALIDATION_ERROR", "message": "budget must be positive", "recovery": "correctable"}
    if field is not None:
        error["field"] = field
    return {"adcp_error": dict(error), "errors": [error]}


class TestEnvelopeFieldPointer:
    """``field=`` on the ONE sanctioned error surface, not a parallel helper."""

    def test_matching_field_passes(self):
        assert_envelope_shape(_error_envelope("budget"), "VALIDATION_ERROR", recovery="correctable", field="budget")

    def test_mismatched_field_fails(self):
        with pytest.raises(AssertionError, match=r"errors\[0\].field='budget', expected 'flight_dates'"):
            assert_envelope_shape(
                _error_envelope("budget"), "VALIDATION_ERROR", recovery="correctable", field="flight_dates"
            )

    def test_absent_field_fails(self):
        with pytest.raises(AssertionError, match=r"errors\[0\].field=None"):
            assert_envelope_shape(_error_envelope(None), "VALIDATION_ERROR", recovery="correctable", field="budget")

    def test_field_buried_in_details_does_not_satisfy_the_protocol_position(self):
        envelope = _error_envelope(None)
        envelope["errors"][0]["details"] = {"field": "budget"}
        with pytest.raises(AssertionError, match=r"errors\[0\].field=None"):
            assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable", field="budget")

    def test_omitting_field_leaves_the_assertion_unchanged(self):
        assert_envelope_shape(_error_envelope(None), "VALIDATION_ERROR", recovery="correctable")

    def test_assert_wire_error_forwards_field(self):
        result = TransportResult(payload=None, envelope={}, wire_error_envelope=_error_envelope("budget"))
        result.assert_wire_error("VALIDATION_ERROR", field="budget")
        with pytest.raises(AssertionError, match=r"errors\[0\].field='budget'"):
            result.assert_wire_error("VALIDATION_ERROR", field="promoted_offering")
