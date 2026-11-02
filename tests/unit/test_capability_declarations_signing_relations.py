"""Regression tests for two review findings on the signing relation rules (#1291 D1).

salesagent-z6nr.20 "## Refine (salesagent-js3z.76)" fixed two MEDIUM findings from the
architect-review pass on ``src/core/schemas/capability_declarations.py``:

1. The bucket-relation refusal built its ``AdCPConfigurationError`` without ``field=``, so
   the top-level wire envelope ``field`` key (``adcp_error.field`` / ``errors[0].field``)
   was always ``null`` -- the offending field only existed in prose and buried in
   ``details``.
2. A refusal named a bucket the operator never wrote.

WHAT THE SUBSET NARROWING CHANGED HERE
--------------------------------------
Finding 2 was originally graded on the ``warn_for`` / ``required_for`` disjointness rule,
across two namespaces. Both are gone: ``warn_for`` and the ``protocol_methods_*`` trio are
UNDECLARABLE (docs/design/request-signing-subset.md), so there is no second namespace to
name wrongly and no disjointness rule to run. The OBLIGATIONS outlived their subject and
are re-homed onto what this seller still does: the surviving ``required_for`` subset rule
carries ``field`` on both envelope layers, and the refusal of an undeclarable field names
the fields and discloses nothing else.

``AdCPSalesAgentError`` takes no ``message=`` parameter: buyer-facing text is a read-only
property over ``CODE_TABLE`` keyed by the code, so ``exc.message`` is the table's
CONFIGURATION_ERROR sentence and is identical for every refusal in this module. The
negative half -- "must not name something the operator never wrote, and must not disclose
the operator sentence" -- is therefore asserted over the WHOLE serialized envelope, which
catches the wrong spelling wherever it lands, including in ``details``.

Every test calls ``CapabilityDeclarations.from_tenant`` directly (pure business logic, no
DB/network) and asserts on the real raised exception's wire envelope via ``envelope_for``
(``to_wire(AdcpErrorResponse.of(exc))`` -- the two calls every transport boundary makes on
a failure) + ``assert_envelope_shape`` -- per tests/CLAUDE.md's error-verification policy.
"""

from __future__ import annotations

import json

import pytest

from src.core.exceptions import AdCPConfigurationError
from src.core.schemas.capability_declarations import (
    _DERIVED_BLOCKS,
    _UNBACKED_BLOCKS,
    _UNDECLARABLE_POSTURE_FIELDS,
    CapabilityDeclarations,
)
from tests.helpers.envelope_assertions import assert_envelope_shape, envelope_for


def _wire_text(envelope: dict) -> str:
    """The whole envelope as one string, for asserting a spelling is ABSENT everywhere.

    A bucket name the operator never declared must not reach the buyer at any position --
    ``field``, ``details``, or the envelope-level mirror. Searching the serialized body is
    the only assertion that covers all three at once, and it is what the deleted
    ``not in exc.message`` checks meant before authored messages went away.
    """
    return json.dumps(envelope, default=str)


class TestBucketRelationRejectionPopulatesWireField:
    """Bug 1: the refusal must pass ``field=`` so the wire envelope's top-level
    ``field`` key names the offending field, not just ``details``.
    """

    def test_request_signing_subset_violation_field_on_wire_envelope(self):
        """A ``required_for`` naming what ``supported_for`` does not rejects with the
        top-level wire envelope field populated (not null)."""
        declared = {
            "request_signing": {
                "supported": True,
                "supported_for": ["create_media_buy"],
                "required_for": ["create_media_buy", "update_media_buy"],
            }
        }

        with pytest.raises(AdCPConfigurationError) as exc_info:
            CapabilityDeclarations.from_tenant(declared)

        exc = exc_info.value

        # The exception itself must carry the field -- this is the attribute the
        # pre-fix refusal never set (no `field=` kwarg was passed at all).
        assert exc.field == "capability_declarations.request_signing.required_for"

        # And the wire envelope built from it must carry the SAME value at the
        # protocol top level of BOTH layers (not buried in `details`, which is what
        # the pre-fix code did instead).
        assert_envelope_shape(
            envelope_for(exc),
            "CONFIGURATION_ERROR",
            recovery="terminal",
            field="capability_declarations.request_signing.required_for",
            details={"rejected_value": ["update_media_buy"], "accepted_values": ["create_media_buy"]},
        )


#: Every ``request_signing`` property the pinned schema defines and this seller refuses,
#: read off the production table so a field added to it is covered the day it appears.
_UNDECLARABLE = sorted(_UNDECLARABLE_POSTURE_FIELDS)


class TestUndeclarablePostureFieldsAreRefused:
    """A posture naming a field this seller does not implement is refused, and says so.

    The refusal is what keeps those fields off the wire: they are inherited from the
    pinned library type (Pattern #1 forbids redeclaring them away), so "no tenant can
    store one" is the whole mechanism by which no response echoes one.
    """

    @pytest.mark.parametrize("field", _UNDECLARABLE)
    def test_each_undeclarable_field_is_refused_naming_itself(self, field: str) -> None:
        """Declared alone, each field is refused with itself in ``rejected_value``."""
        with pytest.raises(AdCPConfigurationError) as exc_info:
            CapabilityDeclarations.from_tenant({"request_signing": {"supported": True, field: ["create_media_buy"]}})

        assert_envelope_shape(
            envelope_for(exc_info.value),
            "CONFIGURATION_ERROR",
            recovery="terminal",
            field="capability_declarations.request_signing",
            details={"rejected_value": [field], "accepted_values": ["required_for", "supported_for"]},
        )

    def test_an_empty_list_is_refused_too(self) -> None:
        """Present-but-empty is not inert: the axis is refused, not just its values.

        An explicit ``[]`` is a narrowing the pin gives meaning to, so accepting it would
        mean accepting the axis while refusing every value a tenant could put in it.
        """
        with pytest.raises(AdCPConfigurationError) as exc_info:
            CapabilityDeclarations.from_tenant({"request_signing": {"supported": True, "warn_for": []}})

        assert exc_info.value.field == "capability_declarations.request_signing"

    def test_every_named_field_is_reported_at_once(self) -> None:
        """All four together, so an operator fixes one declaration rather than four."""
        posture: dict[str, object] = {"supported": True}
        posture.update({field: [] for field in _UNDECLARABLE})

        with pytest.raises(AdCPConfigurationError) as exc_info:
            CapabilityDeclarations.from_tenant({"request_signing": posture})

        assert_envelope_shape(
            envelope_for(exc_info.value),
            "CONFIGURATION_ERROR",
            recovery="terminal",
            field="capability_declarations.request_signing",
            details={"rejected_value": _UNDECLARABLE, "accepted_values": ["required_for", "supported_for"]},
        )

    @pytest.mark.parametrize("field", _UNDECLARABLE)
    def test_the_refusal_discloses_no_operator_sentence(self, field: str) -> None:
        """The reason is LOGGED; the envelope carries the field names and nothing else.

        ``from_tenant`` is the parse boundary of the REQUEST path, so anything in
        ``details`` reaches an unauthenticated caller. The sentences in
        ``_UNDECLARABLE_POSTURE_FIELDS`` explain which platform behaviour this deployment
        lacks, which is operator remediation and reconnaissance both.

        The word-level half is scanned over ``details`` ALONE, because that is the object
        this refusal authors. ``code``, ``message``, ``suggestion`` and ``recovery`` are
        read-only properties over ``CODE_TABLE`` keyed by the code, identical for every
        CONFIGURATION_ERROR, and scanning them matches the table's own vocabulary rather
        than a leak.

        SCHEMA FIELD NAMES are exempt, and they are the reason this is a word scan and not
        a substring one. ``warn_for``'s sentence points the operator at ``supported_for``
        and ``required_for``, which is also what ``accepted_values`` carries -- that
        overlap is the actionable half arriving where the pin says to put it. What must not
        arrive is the EXPLANATION: which behaviour this deployment lacks, and why.
        """
        with pytest.raises(AdCPConfigurationError) as exc_info:
            CapabilityDeclarations.from_tenant({"request_signing": {"supported": True, field: []}})

        envelope = envelope_for(exc_info.value)
        sentence = _UNDECLARABLE_POSTURE_FIELDS[field]
        wire = _wire_text(envelope)
        assert sentence not in wire, f"the operator sentence for {field!r} reached the buyer verbatim: {wire}"

        authored = _wire_text(envelope["adcp_error"]["details"])
        field_names = set(_UNDECLARABLE) | {"required_for", "supported_for"}
        leaked = [
            word
            for word in sentence.split()
            if len(word) > 8 and word.strip("(),.") not in field_names and word in authored
        ]
        assert not leaked, f"{field!r} leaked {leaked!r} from its operator sentence into {authored}"


#: Both refusal tables, as ``(block, operator sentence)``. ONE test over both, because the
#: disclosure rule does not care WHY a block is undeclarable: an unbacked block's sentence
#: names an internal issue and what this deployment does not implement, a derived one's names
#: the platform state the value comes from, and both are operator remediation reaching an
#: unauthenticated caller if they land in ``details``.
_UNDECLARABLE_BLOCKS = sorted({**_UNBACKED_BLOCKS, **_DERIVED_BLOCKS}.items())


@pytest.mark.parametrize("block, operator_sentence", _UNDECLARABLE_BLOCKS)
def test_an_undeclarable_block_refusal_discloses_no_internal_tracking(block: str, operator_sentence: str) -> None:
    """The refusal names the block and nothing else this deployment knows about itself.

    ``from_tenant`` is the parse boundary of the REQUEST path -- ``posture_for_tenant``
    calls it on every request and ``get_adcp_capabilities`` parses the same store -- so
    whatever lands in ``details`` here is served to an UNAUTHENTICATED caller. The
    sentences in both tables are operator remediation: an issue number plus an enumeration
    of what this deployment does not implement, or the platform state a derived value comes
    from. Both are reconnaissance, and an issue number resolves to a tracker the buyer
    cannot read.

    So the sentence is LOGGED at the refusal and the envelope carries ``block`` alone,
    which is the only part a buyer can act on -- they removed a block this seller refuses.
    ``src/core/signing/provider.py`` quotes the spec section forbidding the rest, and
    before this the two arms of one change disagreed about it.

    Parametrized over the tables rather than over literals: a block added to either one is
    covered by construction, which is the whole reason the tables exist.
    """
    with pytest.raises(AdCPConfigurationError) as caught:
        CapabilityDeclarations.from_tenant({block: {"anything": True}})

    envelope = envelope_for(caught.value)
    assert_envelope_shape(
        envelope,
        "CONFIGURATION_ERROR",
        recovery="terminal",
        field=f"capability_declarations.{block}",
        details={"block": block},
    )

    wire = _wire_text(envelope)
    assert operator_sentence not in wire, f"the operator sentence for {block!r} reached the buyer verbatim: {wire}"
    for fragment in ("#", "no ", "implement", "derive"):
        leaked = [part for part in operator_sentence.split() if fragment in part and part in wire and len(part) > 3]
        assert not leaked, f"{block!r} leaked {leaked!r} from its operator sentence into {wire}"
