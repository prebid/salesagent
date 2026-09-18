"""Regression tests for two review findings on the signing relation rules (#1291 D1).

salesagent-z6nr.20 "## Refine (salesagent-js3z.76)" fixed two MEDIUM findings from the
architect-review pass on ``src/core/schemas/capability_declarations.py``:

1. The bucket-relation refusal built its ``AdCPConfigurationError`` without ``field=``, so
   the top-level wire envelope ``field`` key (``adcp_error.field`` / ``errors[0].field``)
   was always ``null`` -- the offending field only existed in prose and buried in
   ``details``.
2. ``_validate_bucket_monotonicity``'s disjointness check (``warn_for`` vs
   ``required_for``) concatenated BOTH namespaces into one list and unconditionally named
   ``request_signing.warn_for`` / ``request_signing.required_for`` in the rejection --
   even when the overlap was entirely in the ``protocol_methods_*`` namespace, naming
   fields the operator never wrote.

WHAT #1721 CHANGED HERE, AND WHAT IT DID NOT
--------------------------------------------
``AdCPSalesAgentError`` takes no ``message=`` parameter: buyer-facing text is a read-only
property over ``CODE_TABLE`` keyed by the code, so ``exc.message`` is the table's
CONFIGURATION_ERROR sentence and is identical for every refusal in this module. The
original finding 2 was graded by asserting on that authored sentence
(``"protocol_methods_required_for" in exc.message``, ``"request_signing.warn_for" not in
exc.message``). That oracle's SUBJECT was deleted, not its obligation: WHICH namespace the
refusal names is still the contract, and under #1721 it is carried by the structured
positions -- ``field`` (the offending bucket, at the protocol top level of both envelope
layers) and typed ``ConfigurationDetails`` (``rejected_value``, the pin's canonical
rejection-set key, carrying the operations that overlapped). So the positive half is
re-expressed on ``field``/``details``, and the negative half -- "must not name a bucket the
operator never wrote" -- is asserted over the WHOLE serialized envelope, which is strictly
stronger than the old ``not in exc.message``: it catches the wrong spelling wherever it
lands, including in ``details``.

Both tests call ``CapabilityDeclarations.from_tenant`` directly (pure business logic, no
DB/network) and assert on the real raised exception's wire envelope via ``envelope_for``
(``to_wire(AdcpErrorResponse.of(exc))`` -- the two calls every transport boundary makes on
a failure) + ``assert_envelope_shape`` -- per tests/CLAUDE.md's error-verification policy
for the unit/IMPL layer.
"""

from __future__ import annotations

import json

import pytest

from src.core.exceptions import AdCPConfigurationError
from src.core.schemas.capability_declarations import CapabilityDeclarations
from tests.helpers.envelope_assertions import assert_envelope_shape, envelope_for


def _wire_text(envelope: dict) -> str:
    """The whole envelope as one string, for asserting a spelling is ABSENT everywhere.

    A bucket name the operator never declared must not reach the buyer at any position --
    ``field``, ``details``, or the envelope-level mirror. Searching the serialized body is
    the only assertion that covers all three at once, and it is what the deleted
    ``not in exc.message`` checks meant before authored messages went away.
    """
    return json.dumps(envelope, default=str)


class TestBucketOverlapRejectionPopulatesWireField:
    """Bug 1: the refusal must pass ``field=`` so the wire envelope's top-level
    ``field`` key names the offending field, not just ``details``.
    """

    def test_request_signing_overlap_field_on_wire_envelope(self):
        """A request_signing warn_for/required_for overlap rejects with the
        top-level wire envelope field populated (not null)."""
        declared = {
            "request_signing": {
                "supported": True,
                "warn_for": ["create_media_buy"],
                "required_for": ["create_media_buy"],
            }
        }

        with pytest.raises(AdCPConfigurationError) as exc_info:
            CapabilityDeclarations.from_tenant(declared)

        exc = exc_info.value

        # The exception itself must carry the field -- this is the attribute the
        # pre-fix refusal never set (no `field=` kwarg was passed at all).
        assert exc.field == "capability_declarations.request_signing.warn_for"

        # And the wire envelope built from it must carry the SAME value at the
        # protocol top level of BOTH layers (not buried in `details`, which is what
        # the pre-fix code did instead).
        assert_envelope_shape(
            envelope_for(exc),
            "CONFIGURATION_ERROR",
            recovery="terminal",
            field="capability_declarations.request_signing.warn_for",
            details={"rejected_value": ["create_media_buy"]},
        )


class TestDisjointnessNamesTheDeclaredNamespace:
    """Bug 2: the warn_for/required_for disjointness check must run PER NAMESPACE,
    naming ``protocol_methods_warn_for`` / ``protocol_methods_required_for`` when
    the overlap is in the protocol-methods namespace -- not the request_signing
    namespace's own field names.
    """

    def test_protocol_methods_only_overlap_names_protocol_methods_fields(self):
        """A declaration that overlaps ONLY in protocol_methods_warn_for /
        protocol_methods_required_for (no request_signing.warn_for/required_for at
        all) must be rejected naming the protocol_methods_* fields, not
        request_signing.warn_for/required_for."""
        declared = {
            "request_signing": {
                "supported": True,
                "protocol_methods_warn_for": ["tasks/cancel"],
                "protocol_methods_required_for": ["tasks/cancel"],
            }
        }

        with pytest.raises(AdCPConfigurationError) as exc_info:
            CapabilityDeclarations.from_tenant(declared)

        exc = exc_info.value

        # The pre-fix code always named "request_signing.warn_for" here, regardless
        # of which namespace actually overlapped -- that is exactly the bug.
        assert exc.field == "capability_declarations.request_signing.protocol_methods_warn_for"

        envelope = envelope_for(exc)
        assert_envelope_shape(
            envelope,
            "CONFIGURATION_ERROR",
            recovery="terminal",
            field="capability_declarations.request_signing.protocol_methods_warn_for",
            details={"rejected_value": ["tasks/cancel"]},
        )

        # The negative half of the finding, at every wire position rather than only
        # in the authored sentence #1721 deleted: neither AdCP-namespace bucket was
        # declared, so neither may be named back to the operator.
        wire = _wire_text(envelope)
        assert "request_signing.warn_for" not in wire
        assert "request_signing.required_for" not in wire
