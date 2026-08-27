"""Regression tests for two review findings on the signing relation rules (#1291 D1).

salesagent-z6nr.20 "## Refine (salesagent-js3z.76)" fixed two MEDIUM findings from the
architect-review pass on ``src/core/schemas/capability_declarations.py``:

1. ``_reject`` built the ``AdCPConfigurationError`` without the ``field=`` kwarg, so the
   top-level wire envelope ``field`` key (``adcp_error.field`` / ``errors[0].field``) was
   always ``null`` -- the offending field only existed in prose and buried in ``details``.
2. ``_validate_bucket_monotonicity``'s disjointness check (``warn_for`` vs
   ``required_for``) concatenated BOTH namespaces into one list and unconditionally named
   ``request_signing.warn_for`` / ``request_signing.required_for`` in the rejection --
   even when the overlap was entirely in the ``protocol_methods_*`` namespace, naming
   fields the operator never wrote.

Both tests call ``CapabilityDeclarations.from_tenant`` directly (pure business logic, no
DB/network) and assert on the real raised exception's wire envelope via
``build_two_layer_error_envelope`` + ``assert_envelope_shape`` -- per tests/CLAUDE.md's
error-verification policy for the unit/IMPL layer.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AdCPConfigurationError, build_two_layer_error_envelope
from src.core.schemas.capability_declarations import CapabilityDeclarations
from tests.helpers import assert_envelope_shape


class TestBucketOverlapRejectionPopulatesWireField:
    """Bug 1: ``_reject`` must pass ``field=`` so the wire envelope's top-level
    ``field`` key names the offending field, not just prose + ``details``.
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
        # pre-fix `_reject` never set (no `field=` kwarg was passed at all).
        assert exc.field == "capability_declarations.request_signing.warn_for"

        # And the wire envelope built from it must carry the SAME value at the
        # protocol top level (not buried in `details`, which is what the pre-fix
        # code did instead).
        envelope = build_two_layer_error_envelope(exc)
        assert_envelope_shape(
            envelope,
            "CONFIGURATION_ERROR",
            recovery="terminal",
            field="capability_declarations.request_signing.warn_for",
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
        assert "protocol_methods_required_for" in exc.message
        assert "request_signing.warn_for" not in exc.message
        assert "request_signing.required_for" not in exc.message

        envelope = build_two_layer_error_envelope(exc)
        assert_envelope_shape(
            envelope,
            "CONFIGURATION_ERROR",
            recovery="terminal",
            field="capability_declarations.request_signing.protocol_methods_warn_for",
            message_substr="protocol_methods_required_for",
        )
