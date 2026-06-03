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
    AdCPGamUpdateError,
    AdCPInventoryUnavailableError,
    AdCPLineItemError,
    AdCPMediaBuyRejectedError,
    AdCPWorkflowError,
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
