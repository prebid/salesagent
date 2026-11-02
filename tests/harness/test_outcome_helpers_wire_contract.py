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
from tests.harness.wire_fixtures import wire_error_result
from tests.helpers.envelope_assertions import assert_envelope_shape

# ``has_wire`` is declared PER SITE on the fixtures below, exactly as at a real
# dispatch site: True wherever the fixture carries the envelope a real wire
# produced — which is the state these readers exist to be graded against — and
# False on the two sites that model a dispatch which captured nothing, where
# nothing (no synthesized envelope) stands in for one either. False is also the
# reader's most permissive branch, so a read that still comes back empty there
# is graded on real absence rather than on a structurally closed-off fallback.


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


class TestMissingWireRaisesLoudly:
    """A missing wire raises, whatever the transport — there is no no-wire case.

    This class used to be ``TestUnsetTransportIsNotImpl`` and demanded a message
    naming ``Transport.IMPL``. Both halves of that premise are gone: the IMPL
    pseudo-transport is deleted, and so is the ``model_dump`` fallback it was the
    only legitimate caller of. With no fallback left to take silently, an unset
    transport and a set one with no captured wire are the SAME defect, and the
    guard is that neither serializes its way out of it.

    What survives from the original intent (GH #1744) is the part that still
    holds: a helper must never turn a wire assertion into a serializer
    round-trip. It now cannot, because the branch that did is deleted.
    """

    @pytest.mark.parametrize("base_ctx", [{}, {"transport": None}], ids=["key-absent", "explicit-none"])
    @pytest.mark.parametrize("helper", [wire_field, wire_absent])
    def test_missing_wire_raises_and_names_the_defect(self, base_ctx, helper):
        ctx = {**base_ctx, "response": _TypedPayload()}
        with pytest.raises(AssertionError, match=r"wire_response missing.*no no-wire fallback"):
            helper(ctx, "adcp_version")

    @pytest.mark.parametrize("base_ctx", [{}, {"transport": None}], ids=["key-absent", "explicit-none"])
    def test_wire_dict_missing_wire_raises(self, base_ctx):
        ctx = {**base_ctx, "response": _TypedPayload()}
        with pytest.raises(AssertionError, match=r"wire_response missing.*no no-wire fallback"):
            wire_dict(ctx)

    def test_a_set_transport_does_not_excuse_a_missing_wire(self):
        """The guard is about the MISSING wire, not about which transport asked."""
        ctx = {"transport": Transport.MCP, "response": _TypedPayload()}
        with pytest.raises(AssertionError, match=r"wire_response missing"):
            wire_dict(ctx)

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
        result = wire_error_result(_error_envelope("budget"))
        result.assert_wire_error("VALIDATION_ERROR", field="budget")
        with pytest.raises(AssertionError, match=r"errors\[0\].field='budget'"):
            result.assert_wire_error("VALIDATION_ERROR", field="promoted_offering")


def _details_envelope(errors_details: dict | None, *, adcp_details: dict | None = None) -> dict:
    """A VERSION_UNSUPPORTED envelope carrying ``details`` on the named layer(s) only."""
    payload_error: dict = {
        "code": "VERSION_UNSUPPORTED",
        "message": "adcp 2.9 is not supported",
        "recovery": "correctable",
    }
    envelope_error: dict = dict(payload_error)
    if errors_details is not None:
        payload_error["details"] = errors_details
    if adcp_details is not None:
        envelope_error["details"] = adcp_details
    return {"adcp_error": envelope_error, "errors": [payload_error]}


_SUPPORTED = {"supported_versions": ["3.0", "3.1"], "build_version": "3.1.1"}


class TestEnvelopeDetailsSubset:
    """``details=`` on the ONE sanctioned error surface, mirroring ``field=``.

    ``details`` is an OPEN object in the pinned core/error.json (the error object
    declares ``additionalProperties: true``; only ``code`` and ``message`` are
    required), so the contract is a SUBSET check — each expected key present and
    equal — never dict equality, which would break the moment production adds a
    diagnostic key. The protocol position is ``errors[0].details``: a block that
    lives only on the envelope-level mirror, or nested one level deeper, is not
    at the position the schema defines and does not satisfy the assertion (same
    burial rule as ``field`` and ``extract_wire_suggestion``).
    """

    def test_matching_subset_passes(self):
        assert_envelope_shape(
            _details_envelope(_SUPPORTED),
            "VERSION_UNSUPPORTED",
            recovery="correctable",
            details={"supported_versions": ["3.0", "3.1"]},
        )

    def test_extra_keys_on_the_wire_do_not_break_the_subset(self):
        """An open object: production may carry diagnostics the oracle does not name."""
        wire = {**_SUPPORTED, "requested_version": "2.9"}
        assert_envelope_shape(
            _details_envelope(wire),
            "VERSION_UNSUPPORTED",
            recovery="correctable",
            details={"build_version": "3.1.1"},
        )

    def test_mismatched_value_fails(self):
        with pytest.raises(AssertionError, match=r"details"):
            assert_envelope_shape(
                _details_envelope(_SUPPORTED),
                "VERSION_UNSUPPORTED",
                recovery="correctable",
                details={"build_version": "3.0.0"},
            )

    def test_expected_key_absent_from_the_wire_fails(self):
        with pytest.raises(AssertionError, match=r"details"):
            assert_envelope_shape(
                _details_envelope(_SUPPORTED),
                "VERSION_UNSUPPORTED",
                recovery="correctable",
                details={"requested_version": "2.9"},
            )

    def test_absent_details_block_fails(self):
        with pytest.raises(AssertionError, match=r"details"):
            assert_envelope_shape(
                _details_envelope(None),
                "VERSION_UNSUPPORTED",
                recovery="correctable",
                details={"supported_versions": ["3.0", "3.1"]},
            )

    def test_details_only_on_the_envelope_mirror_does_not_satisfy_the_protocol_position(self):
        """``errors[0].details`` is the position; the ``adcp_error`` mirror alone is not it."""
        with pytest.raises(AssertionError, match=r"details"):
            assert_envelope_shape(
                _details_envelope(None, adcp_details=_SUPPORTED),
                "VERSION_UNSUPPORTED",
                recovery="correctable",
                details={"supported_versions": ["3.0", "3.1"]},
            )

    def test_value_buried_one_level_deeper_does_not_satisfy_the_protocol_position(self):
        """The subset is over ``details``' own keys — not a recursive search."""
        with pytest.raises(AssertionError, match=r"details"):
            assert_envelope_shape(
                _details_envelope({"version": _SUPPORTED}),
                "VERSION_UNSUPPORTED",
                recovery="correctable",
                details={"supported_versions": ["3.0", "3.1"]},
            )

    def test_omitting_details_leaves_the_assertion_unchanged(self):
        assert_envelope_shape(_details_envelope(None), "VERSION_UNSUPPORTED", recovery="correctable")

    def test_assert_wire_error_forwards_details(self):
        result = wire_error_result(_details_envelope(_SUPPORTED))
        result.assert_wire_error("VERSION_UNSUPPORTED", details={"build_version": "3.1.1"})
        with pytest.raises(AssertionError, match=r"details"):
            result.assert_wire_error("VERSION_UNSUPPORTED", details={"build_version": "3.0.0"})


def _issues_envelope(errors_issues: object = None, *, adcp_issues: object = None) -> dict:
    """A VALIDATION_ERROR envelope carrying ``issues`` on the named layer(s) only.

    Mirrors :func:`_details_envelope`: ``None`` means the key is absent from that
    layer entirely, so "absent" and "present but empty" stay distinguishable.
    """
    payload_error: dict = {
        "code": "VALIDATION_ERROR",
        "message": "webhook credential value is too short",
        "recovery": "correctable",
    }
    envelope_error: dict = dict(payload_error)
    if errors_issues is not None:
        payload_error["issues"] = errors_issues
    if adcp_issues is not None:
        envelope_error["issues"] = adcp_issues
    return {"adcp_error": envelope_error, "errors": [payload_error]}


# core/error.json 3.1.1 requires pointer/message/keyword on every emitted item.
_TOKEN_ISSUE = {
    "pointer": "/webhook/credentials/0/value",
    "message": "String should have at least 8 characters",
    "keyword": "minLength",
}
_URL_ISSUE = {
    "pointer": "/webhook/url",
    "message": "Field required",
    "keyword": "required",
}


class TestEnvelopeIssuesSubset:
    """``issues=`` on the ONE sanctioned error surface, mirroring ``details=``.

    ``issues`` is the pin's field-level rejection channel — the map ``field``
    (singular) cannot carry — and until this kwarg exists a step can only grade
    it by hand-indexing the envelope or by reaching for the reader. So the
    contract is the LIST analogue of what ``details=`` does per key:

    * ``errors[0].issues`` must be a non-empty list — absent, empty, or not a
      list fails, because a channel that is not there cannot be graded;
    * each expected item is matched by MEMBERSHIP: at least ONE wire item must
      carry every key in that expected item with an equal value. Per item and
      order-independent — two expected keys satisfied by two DIFFERENT wire
      items do NOT satisfy one expected item;
    * a matched wire item may carry keys the oracle does not name (the pinned
      item is ``additionalProperties: true``), the same openness rule
      ``details`` follows.

    The protocol position is ``errors[0].issues``, resolved through the single
    ``locate_envelope_error`` locator: a block living only on the envelope-level
    ``adcp_error`` mirror, or a value nested one level deeper inside an item, is
    not at the position the schema defines (the same burial rule ``field`` and
    ``details`` carry). Non-binary oracles (find-by-pointer, regex-per-entry)
    keep using ``TransportResult.wire_error_issues``, exactly as
    ``wire_error_details`` stays beside ``details=``.
    """

    def test_matching_item_passes(self):
        assert_envelope_shape(
            _issues_envelope([_TOKEN_ISSUE]),
            "VALIDATION_ERROR",
            recovery="correctable",
            issues=[{"keyword": "minLength"}],
        )

    def test_extra_keys_on_the_matched_item_do_not_break_the_subset(self):
        """An open item: production may carry ``keyword_value`` no oracle names."""
        assert_envelope_shape(
            _issues_envelope([{**_TOKEN_ISSUE, "keyword_value": 8}]),
            "VALIDATION_ERROR",
            recovery="correctable",
            issues=[{"pointer": "/webhook/credentials/0/value", "keyword": "minLength"}],
        )

    def test_a_second_unnamed_wire_item_does_not_break_the_match(self):
        """Membership, not positional equality: unnamed siblings are allowed."""
        assert_envelope_shape(
            _issues_envelope([_URL_ISSUE, _TOKEN_ISSUE]),
            "VALIDATION_ERROR",
            recovery="correctable",
            issues=[{"keyword": "minLength"}],
        )

    def test_mismatched_value_fails(self):
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope([_TOKEN_ISSUE]),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"keyword": "maxLength"}],
            )

    def test_expected_key_absent_from_every_item_fails(self):
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope([_TOKEN_ISSUE, _URL_ISSUE]),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"keyword_value": 8}],
            )

    def test_two_expected_keys_split_across_two_items_fails(self):
        """The defect this kwarg must not have: ONE wire item carries the whole expected item.

        ``/webhook/url`` is the pointer of the *required* issue and ``minLength``
        is the keyword of the *credential* issue. Both values are on the wire —
        but never together on one item, so the oracle is unsatisfied.
        """
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope([_TOKEN_ISSUE, _URL_ISSUE]),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"pointer": "/webhook/url", "keyword": "minLength"}],
            )

    def test_absent_issues_array_fails(self):
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope(None),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"keyword": "minLength"}],
            )

    def test_empty_issues_array_fails(self):
        """An empty channel grades nothing; passing on it is the vacuity this closes."""
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope([]),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"keyword": "minLength"}],
            )

    def test_issues_that_is_not_a_list_fails(self):
        """A single object where the pin defines an array is malformed, not a one-item match."""
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope(dict(_TOKEN_ISSUE)),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"keyword": "minLength"}],
            )

    def test_issues_only_on_the_envelope_mirror_does_not_satisfy_the_protocol_position(self):
        """``errors[0].issues`` is the position; the ``adcp_error`` mirror alone is not it."""
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope(None, adcp_issues=[_TOKEN_ISSUE]),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"keyword": "minLength"}],
            )

    def test_value_buried_one_level_deeper_does_not_satisfy_the_protocol_position(self):
        """The subset is over an item's own keys — not a recursive search."""
        with pytest.raises(AssertionError, match=r"issues"):
            assert_envelope_shape(
                _issues_envelope([{**_TOKEN_ISSUE, "context": {"keyword_value": 8}}]),
                "VALIDATION_ERROR",
                recovery="correctable",
                issues=[{"keyword_value": 8}],
            )

    def test_omitting_issues_leaves_the_assertion_unchanged(self):
        assert_envelope_shape(_issues_envelope(None), "VALIDATION_ERROR", recovery="correctable")

    def test_assert_wire_error_forwards_issues(self):
        result = wire_error_result(_issues_envelope([_TOKEN_ISSUE]))
        result.assert_wire_error("VALIDATION_ERROR", issues=[{"keyword": "minLength"}])
        with pytest.raises(AssertionError, match=r"issues"):
            result.assert_wire_error("VALIDATION_ERROR", issues=[{"keyword": "maxLength"}])


class TestWireErrorDetailsReader:
    """``wire_error_details(code, ...)`` — the escape hatch that is STRONGER than the kwarg.

    Three of the five ``uc010`` details oracles are non-binary (non-empty array;
    contains v1 and v2; every entry matches a regex), so they need a READER, not
    a second assertion surface. Taking the expected ``code`` is what keeps the
    reader from becoming a way AROUND the assertion: uc010:1309 today grades a
    details block without ever asserting which error produced it, so a wrong
    error carrying a right-shaped details block passes. The reader asserts the
    full envelope (code + recovery) FIRST, then returns the located block.
    """

    def test_returns_the_details_block_at_the_protocol_position(self):
        result = wire_error_result(_details_envelope(_SUPPORTED))
        assert result.wire_error_details("VERSION_UNSUPPORTED") == _SUPPORTED

    def test_asserts_the_code_before_returning(self):
        """A details block from the WRONG error must not be readable."""
        result = wire_error_result(_details_envelope(_SUPPORTED))
        with pytest.raises(AssertionError, match="VALIDATION_ERROR"):
            result.wire_error_details("VALIDATION_ERROR")

    def test_asserts_the_recovery_it_is_given(self):
        result = wire_error_result(_details_envelope(_SUPPORTED))
        with pytest.raises(AssertionError, match="recovery"):
            result.wire_error_details("VERSION_UNSUPPORTED", recovery="terminal")

    def test_absent_details_block_raises_rather_than_returning_none(self):
        """Required-not-optional: a caller must never have to None-check the block."""
        result = wire_error_result(_details_envelope(None))
        with pytest.raises(AssertionError, match=r"details"):
            result.wire_error_details("VERSION_UNSUPPORTED")

    def test_no_wire_envelope_raises(self):
        """No wire bytes, no read — the same hard failure ``assert_wire_error`` gives."""
        # has_wire=False — nothing was captured (see the module note above).
        result = wire_error_result(None)
        with pytest.raises(AssertionError):
            result.wire_error_details("VERSION_UNSUPPORTED")


class TestWireErrorTolerantReaders:
    """``wire_error_code`` / ``wire_error_object`` on the harness.

    The disease's root is that the harness offered ASSERTIONS but no READERS, so
    hand-rolling ``(envelope.get("errors") or [{}])[0].get(...)`` in a step module
    was the only option. These three are the sanctioned readers the step-layer
    copies delegate to. ``wire_error_message`` was a third such reader; it is gone —
    the buyer-facing sentence is a function of the error CODE through CODE_TABLE, so a
    sanctioned way to read it only enabled assertions that check the table against itself.
    They are deliberately TOLERANT of a missing envelope
    (returning ``None``) — the step helpers built on them keep their ``| None``
    contract for the no-wire branches that depend on it (then_error.py:348, :857,
    :896). Strictness lives in ``wire_error_details`` / ``assert_wire_error``.
    """

    def _errored(self) -> TransportResult:
        return wire_error_result(_details_envelope(_SUPPORTED))

    def test_wire_error_code_reads_the_wire_code(self):
        assert self._errored().wire_error_code() == "VERSION_UNSUPPORTED"

    def test_wire_error_object_returns_the_payload_layer_error(self):
        """``errors[0]`` is the layer carrying the per-error fields (field, details)."""
        assert self._errored().wire_error_object() == _details_envelope(_SUPPORTED)["errors"][0]

    @pytest.mark.parametrize("reader", ["wire_error_code", "wire_error_object"])
    def test_no_envelope_reads_as_none_not_a_raise(self, reader):
        # has_wire=False — nothing was captured (see the module note above).
        result = wire_error_result(None)
        assert getattr(result, reader)() is None


_ACCOUNTS_WIRE = {
    "adcp_version": "3.1.1",
    "accounts": [
        {"account_id": "acct_nike", "action": "created", "errors": []},
        {
            "account_id": "acct_adidas",
            "action": "failed",
            "errors": [{"code": "VALIDATION_ERROR", "message": "brand.domain is malformed"}],
        },
    ],
}


class TestWireEntry:
    """``wire_entry`` — per-entry reads inside a SUCCESS envelope.

    A partial-success response (sync-accounts-response oneOf/0) carries its
    per-entry outcomes at ``accounts[].errors[]`` — INSIDE a success envelope, so
    ``assert_wire_error`` / ``wire_error_envelope`` (which grade the error-envelope
    shape) structurally cannot serve them. Without this primitive the only way to
    reach an entry is a typed-payload read or a hand-rolled index, which is how the
    ``ctx["last_account"]`` typed stash grew 28 readers.
    """

    def test_match_locates_the_entry(self):
        from tests.bdd.steps._outcome_helpers import wire_entry

        entry = wire_entry(_ctx(_ACCOUNTS_WIRE), "accounts", account_id="acct_adidas")
        assert entry["action"] == "failed"

    def test_index_locates_the_entry(self):
        from tests.bdd.steps._outcome_helpers import wire_entry

        assert wire_entry(_ctx(_ACCOUNTS_WIRE), "accounts", index=0)["account_id"] == "acct_nike"

    def test_dotted_match_locates_a_nested_identifier(self):
        """The same dotted-path convention wire_lookup uses — a nested identifier
        (brand.domain) is how the account scenarios name their entry."""
        from tests.bdd.steps._outcome_helpers import wire_entry

        wire = {"accounts": [{"account_id": "a1", "brand": {"domain": "nike.com", "brand_id": "b1"}}]}
        assert wire_entry(_ctx(wire), "accounts", **{"brand.domain": "nike.com"})["account_id"] == "a1"

    def test_dotted_match_that_misses_names_the_entries_present(self):
        from tests.bdd.steps._outcome_helpers import wire_entry

        wire = {"accounts": [{"account_id": "acct_nike", "brand": {"domain": "nike.com"}}]}
        with pytest.raises(AssertionError, match="acct_nike"):
            wire_entry(_ctx(wire), "accounts", **{"brand.domain": "adidas.com"})

    def test_no_match_names_the_entries_actually_present(self):
        """Strict: the failure must show what the wire DID carry, not just that it missed.

        A bare "no match" sends the reader to the harness; naming the entries makes
        an off-by-one identifier or a partial-failure row obvious from the output.
        """
        from tests.bdd.steps._outcome_helpers import wire_entry

        with pytest.raises(AssertionError, match="acct_nike"):
            wire_entry(_ctx(_ACCOUNTS_WIRE), "accounts", account_id="acct_missing")

    def test_index_out_of_range_names_the_entries_actually_present(self):
        from tests.bdd.steps._outcome_helpers import wire_entry

        with pytest.raises(AssertionError, match="acct_nike"):
            wire_entry(_ctx(_ACCOUNTS_WIRE), "accounts", index=7)

    def test_absent_collection_raises(self):
        from tests.bdd.steps._outcome_helpers import wire_entry

        with pytest.raises(AssertionError, match="absent from wire response"):
            wire_entry(_ctx(_ACCOUNTS_WIRE), "creatives", index=0)


class TestWireEntryErrors:
    def test_returns_the_entry_error_array(self):
        from tests.bdd.steps._outcome_helpers import wire_entry_errors

        errors = wire_entry_errors(_ctx(_ACCOUNTS_WIRE), "accounts", account_id="acct_adidas")
        assert [e["code"] for e in errors] == ["VALIDATION_ERROR"]

    def test_locates_by_index_too(self):
        from tests.bdd.steps._outcome_helpers import wire_entry_errors

        assert wire_entry_errors(_ctx(_ACCOUNTS_WIRE), "accounts", index=1)[0]["code"] == "VALIDATION_ERROR"

    def test_strips_the_buyer_facing_message(self):
        """The one guard-blessed per-entry reader must not hand back the sentence.

        It is sanctioned in the wire-discipline guard's ``_PRIMITIVE_FUNCTIONS``, so if it
        returned ``message`` it would be the single blessed door for asserting prose — the
        very thing it exists to replace. The code survives; the sentence does not.
        """
        from tests.bdd.steps._outcome_helpers import wire_entry_errors

        errors = wire_entry_errors(_ctx(_ACCOUNTS_WIRE), "accounts", index=1)
        assert "message" not in errors[0]
        assert errors[0]["code"] == "VALIDATION_ERROR"


class TestEntryHelpersInheritTheLoudGuard:
    """The extension must not open a hole in ``_wire_body``'s guard.

    ``wire_entry``/``wire_entry_errors`` build on ``_wire_body``/``wire_lookup``
    precisely so a real-wire transport that stashed nothing raises instead of
    silently serializing the typed payload — the same property
    ``TestLoudGuardSurvivesTheExtension`` pins for the existing three helpers.
    """

    def test_wire_entry_raises_on_a_non_stashing_env(self):
        from tests.bdd.steps._outcome_helpers import wire_entry

        with pytest.raises(AssertionError, match="does not stash success-path wire"):
            wire_entry({"transport": Transport.MCP}, "accounts", index=0)

    def test_wire_entry_errors_raises_on_a_non_stashing_env(self):
        from tests.bdd.steps._outcome_helpers import wire_entry_errors

        with pytest.raises(AssertionError, match="does not stash success-path wire"):
            wire_entry_errors({"transport": Transport.A2A}, "accounts", index=0)

    def test_an_errored_scenario_reports_the_error_not_a_missing_wire(self):
        from tests.bdd.steps._outcome_helpers import wire_entry

        ctx = {"transport": Transport.REST, "error": RuntimeError("AUTH_REQUIRED")}
        with pytest.raises(AssertionError, match="expected a success response, got error"):
            wire_entry(ctx, "accounts", index=0)
