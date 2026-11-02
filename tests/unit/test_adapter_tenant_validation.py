"""Regression tests for tenant_id validation at system boundaries.

: Adapters using `tenant_id or ""` silently coerce None to empty
string, causing all tenant-scoped queries to return empty results instead of
raising an error.  The fix is to validate tenant_id at the adapter boundary
and raise AdCPConfigurationError for None or empty string.

: Same pattern in admin blueprint _call_webhook_for_creative_status —
`tenant_id or ""` coerces None to empty string, causing AdminCreativeUoW to
silently return empty results.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AdCPConfigurationError
from src.core.schemas import Principal


def _make_principal() -> Principal:
    """Create a minimal Principal for adapter construction."""
    return Principal(
        principal_id="test_principal",
        name="Test Principal",
        platform_mappings={},
    )


class TestAdapterTenantIdValidation:
    """Adapter must reject None or empty tenant_id at construction time.

    EXPECTATION REVERSED by salesagent-7et3j. These asserted a bare ValueError, which
    adcp_error_for maps by PYTHON TYPE to AdCPValidationError -- telling the buyer their
    request is malformed for a fault entirely on the seller's side. The raise sites now
    name AdCPConfigurationError, so the buyer reads CONFIGURATION_ERROR (terminal): the
    seller must fix its own configuration and no retry will help.

    The match= substrings went with the message: AdCPSalesAgentError has no message parameter, so
    the sentence is a function of the code through CODE_TABLE and cannot be asserted at
    a raise site. Provenance now rides internal_detail, which the boundary logs
    server-side and never puts on the wire -- which is what makes acceptance 2 ("no
    converted site carries upstream text in message") hold by construction.
    """

    def test_gam_adapter_rejects_none_tenant_id(self):
        """GoogleAdManager with tenant_id=None must raise, not silently use ''."""
        from src.adapters.google_ad_manager import GoogleAdManager

        with pytest.raises(AdCPConfigurationError):
            GoogleAdManager(
                config={"service_account_json": "{}"},
                principal=_make_principal(),
                network_code="12345",
                tenant_id=None,
            )

    def test_gam_adapter_rejects_empty_tenant_id(self):
        """GoogleAdManager with tenant_id='' must raise, not proceed silently."""
        from src.adapters.google_ad_manager import GoogleAdManager

        with pytest.raises(AdCPConfigurationError):
            GoogleAdManager(
                config={"service_account_json": "{}"},
                principal=_make_principal(),
                network_code="12345",
                tenant_id="",
            )

    def test_mock_adapter_rejects_none_tenant_id(self):
        """MockAdServer with tenant_id=None must raise, not silently use ''."""
        from src.adapters.mock_ad_server import MockAdServer

        with pytest.raises(AdCPConfigurationError):
            MockAdServer(
                config={},
                principal=_make_principal(),
                tenant_id=None,
            )

    def test_mock_adapter_rejects_empty_tenant_id(self):
        """MockAdServer with tenant_id='' must raise, not proceed silently."""
        from src.adapters.mock_ad_server import MockAdServer

        with pytest.raises(AdCPConfigurationError):
            MockAdServer(
                config={},
                principal=_make_principal(),
                tenant_id="",
            )

    def test_gam_adapter_accepts_valid_tenant_id(self):
        """GoogleAdManager with valid tenant_id should initialize without error.

        The service account document has to be one google.auth can parse: the adapter
        builds its credentials at construction, and the ``"{}"`` that stood here was only
        ever accepted because a dry-run adapter skipped that step (no adapter carries that
        flag now). The refusal cases above still pass ``"{}"`` -- they raise on the tenant
        id before any credential is read.
        """
        from src.adapters.google_ad_manager import GoogleAdManager
        from tests.helpers.gam_credentials import service_account_json

        adapter = GoogleAdManager(
            config={"service_account_json": service_account_json()},
            principal=_make_principal(),
            network_code="12345",
            tenant_id="valid_tenant",
        )
        assert adapter.tenant_id == "valid_tenant"

    def test_mock_adapter_accepts_valid_tenant_id(self):
        """MockAdServer with valid tenant_id should initialize without error."""
        from src.adapters.mock_ad_server import MockAdServer

        adapter = MockAdServer(
            config={},
            principal=_make_principal(),
            tenant_id="valid_tenant",
        )
        assert adapter.tenant_id == "valid_tenant"


class TestBlueprintTenantIdValidation:
    """Admin blueprint functions must reject None/empty tenant_id explicitly.

    NOT reversed by salesagent-7et3j, deliberately. This class targets
    src/admin/blueprints/creatives.py, which is outside that ticket's scope
    (src/adapters/ only). The bare ValueError there is untouched debt, not a
    site this pass converted.
    """

    @pytest.mark.asyncio
    async def test_call_webhook_rejects_none_tenant_id(self):
        """_call_webhook_for_creative_status with tenant_id=None must raise, not use ''."""
        from src.admin.blueprints.creatives import _call_webhook_for_creative_status

        with pytest.raises(ValueError, match="tenant_id"):
            await _call_webhook_for_creative_status(creative_id="cr_123", tenant_id=None)

    @pytest.mark.asyncio
    async def test_call_webhook_rejects_empty_tenant_id(self):
        """_call_webhook_for_creative_status with tenant_id='' must raise, not proceed."""
        from src.admin.blueprints.creatives import _call_webhook_for_creative_status

        with pytest.raises(ValueError, match="tenant_id"):
            await _call_webhook_for_creative_status(creative_id="cr_123", tenant_id="")
