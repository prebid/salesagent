"""Internal-code -> wire-code mapping for the adapter / mock error taxonomy.

Each taxonomy subclass carries a distinct internal ``error_code`` for logs and
audit; the boundary translator (``build_two_layer_error_envelope``) maps it to
the buyer-facing wire code. A class-swap or an ERROR_CODE_MAPPING change that
silently flips the wire code is caught here — the adapter classes must stay on
SERVICE_UNAVAILABLE, and the mock business-outcome classes must keep their
distinct buyer-facing codes. The GAM raise sites for two of these are
additionally exercised end-to-end in test_gam_workflow_packages.py.
"""

import pytest

from src.core.exceptions import (
    AdCPActivationWorkflowError,
    AdCPBulkUpdateError,
    AdCPConfigurationError,
    AdCPGamUpdateError,
    AdCPInventoryUnavailableError,
    AdCPLineItemError,
    AdCPMediaBuyRejectedError,
    AdCPWorkflowError,
    adcp_adapter_error_for_http_status,
    build_two_layer_error_envelope,
)


@pytest.mark.parametrize(
    "exc_class,internal_code,wire_code",
    [
        # Adapter-taxonomy classes: distinct internal codes, all collapse to
        # SERVICE_UNAVAILABLE on the wire (the buyer retries the same way).
        (AdCPLineItemError, "LINE_ITEM_CREATION_FAILED", "SERVICE_UNAVAILABLE"),
        (AdCPBulkUpdateError, "PARTIAL_FAILURE", "SERVICE_UNAVAILABLE"),
        (AdCPActivationWorkflowError, "ACTIVATION_WORKFLOW_FAILED", "SERVICE_UNAVAILABLE"),
        (AdCPGamUpdateError, "GAM_UPDATE_FAILED", "SERVICE_UNAVAILABLE"),
        (AdCPWorkflowError, "WORKFLOW_CREATION_FAILED", "SERVICE_UNAVAILABLE"),
        # The ad-server 403 (operator credential denied): internal CONFIGURATION_ERROR
        # is overloaded with the secret-decrypt path, so it MUST translate to the
        # leak-safe SERVICE_UNAVAILABLE on the wire (never the raw internal code).
        (AdCPConfigurationError, "CONFIGURATION_ERROR", "SERVICE_UNAVAILABLE"),
        # Mock-adapter business outcomes: distinct buyer-facing wire codes.
        (AdCPMediaBuyRejectedError, "MEDIA_BUY_REJECTED", "POLICY_VIOLATION"),
        (AdCPInventoryUnavailableError, "INVENTORY_UNAVAILABLE", "PRODUCT_UNAVAILABLE"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_taxonomy_class_maps_internal_code_to_wire_code(exc_class, internal_code, wire_code):
    exc = exc_class("boom")

    # The class identity carries the internal taxonomy code (logs / audit).
    assert exc.error_code == internal_code

    # The boundary translator maps it to the buyer-facing wire code on both
    # layers of the envelope.
    envelope = build_two_layer_error_envelope(exc)
    assert envelope["adcp_error"]["code"] == wire_code
    assert envelope["errors"][0]["code"] == wire_code


def test_ad_server_403_wire_envelope_is_service_unavailable_terminal():
    """The ad-server 403 (operator credential denied) wire contract, end-to-end through
    the boundary translator: the buyer receives the leak-safe ``SERVICE_UNAVAILABLE``
    code AND the ``terminal`` recovery carrier (the spec's authoritative "requires human
    action" signal). A class-swap, an ERROR_CODE_MAPPING edit, or a recovery regression
    in the envelope builder reddens here — the gap the attribute-only factory tests miss.
    """
    exc = adcp_adapter_error_for_http_status(403, "Kevel flight POST denied (HTTP 403)")

    envelope = build_two_layer_error_envelope(exc)
    assert envelope["adcp_error"]["code"] == "SERVICE_UNAVAILABLE"
    assert envelope["errors"][0]["code"] == "SERVICE_UNAVAILABLE"
    assert envelope["errors"][0]["recovery"] == "terminal"
    # Leak-safe: the raw internal code (also used by the secret-decrypt path) never
    # reaches the wire on either layer.
    assert "CONFIGURATION_ERROR" not in (envelope["adcp_error"]["code"], envelope["errors"][0]["code"])
