"""Behavioral pin tests for typed AdCPError subclass raises.

These tests prove that production raise sites emit the correct typed
subclass at the actual call site. They CALL production code (not just
import the classes) — distinguishing them from test_adcp_exceptions.py
(which only verifies class attributes).

Covered raise sites:
- ``AdCPBudgetTooLowError`` in ``_create_media_buy_impl`` (budget <= 0)
- ``AdCPMediaBuyNotFoundError`` in ``_update_media_buy_impl`` (lookup miss)
- ``AdCPCapabilityNotSupportedError`` in ``_get_media_buys_impl``
  (account_id filtering)

Each test is a structural pin — if the production raise site reverts to
a sibling typed exception (e.g. ``AdCPValidationError`` instead of
``AdCPBudgetTooLowError``), these tests fail at the type check, not
later at the wire envelope. The wire-envelope tests in
test_mcp_error_envelope.py cover the downstream serialization path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.exceptions import (
    AdCPBudgetTooLowError,
    AdCPCapabilityNotSupportedError,
    AdCPMediaBuyNotFoundError,
)
from src.core.schemas import CreateMediaBuyRequest, GetMediaBuysRequest, UpdateMediaBuyRequest
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_list import _get_media_buys_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.harness._idempotency import fresh_idempotency_key
from tests.helpers.adcp_factories import create_test_package_request_dict
from tests.integration.conftest import seed_error_test_tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


_TENANT_ID = "typed_raise_test"
_PRINCIPAL_ID = "typed_raise_principal"
_ACCESS_TOKEN = "typed_raise_token_789"
_PRODUCT_ID = "typed_raise_product"


@pytest.fixture
def typed_raise_setup(integration_db):
    """Tenant + principal + product fixture for the typed-raise behavioral pins.

    Seeds real DB state via factory-boy (session bound by ``IntegrationEnv``) and
    yields a ``ResolvedIdentity`` pointing at the seeded principal.
    """
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv():
        yield seed_error_test_tenant(
            tenant_id=_TENANT_ID,
            principal_id=_PRINCIPAL_ID,
            access_token=_ACCESS_TOKEN,
            product_id=_PRODUCT_ID,
            subdomain="typedraise",
            tenant_name="Typed Raise Test Tenant",
            advertiser_id="mock_adv_789",
        )["identity"]


@pytest.mark.integration
@pytest.mark.requires_db
class TestTypedAdCPErrorRaises:
    """Behavioral pins: production raise sites emit the correct typed subclass."""

    async def test_budget_too_low_raises_typed_subclass(self, typed_raise_setup):
        """The per-package budget validator raises ``AdCPBudgetTooLowError``.

        Pins the specific typed subclass so a future change that swaps it
        back to the sibling ``AdCPValidationError`` breaks here rather than
        silently losing the spec wire code ``BUDGET_TOO_LOW``.
        """
        identity = typed_raise_setup
        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=30)

        req = CreateMediaBuyRequest(
            brand={"domain": "typedraise.example"},
            packages=[
                create_test_package_request_dict(
                    product_id=_PRODUCT_ID,
                    pricing_option_id="cpm_usd_fixed",
                    budget=0,  # triggers BUDGET_TOO_LOW
                )
            ],
            start_time=future_start.isoformat(),
            end_time=future_end.isoformat(),
            idempotency_key=fresh_idempotency_key("int-key"),
        )

        with pytest.raises(AdCPBudgetTooLowError) as exc_info:
            await _create_media_buy_impl(req=req, identity=identity)

        assert exc_info.value.error_code == "BUDGET_TOO_LOW"
        assert exc_info.value.recovery == "correctable"
        assert "budget" in exc_info.value.message.lower()

    def test_media_buy_not_found_raises_typed_subclass(self, typed_raise_setup):
        """``_verify_principal`` raises ``AdCPMediaBuyNotFoundError`` on lookup miss.

        Pins the specific subclass so the wire code stays
        ``MEDIA_BUY_NOT_FOUND`` (not the generic ``NOT_FOUND``) and
        recovery stays ``correctable`` for buyer-correctable cases.
        """
        identity = typed_raise_setup
        # update_media_buy needs ≥1 updatable field; ``paused`` passes pre-lookup validation.
        req = UpdateMediaBuyRequest(media_buy_id="mb_nonexistent_typed_raise_pin", paused=True)

        with pytest.raises(AdCPMediaBuyNotFoundError) as exc_info:
            _update_media_buy_impl(req=req, identity=identity, context_id=None)

        assert exc_info.value.error_code == "MEDIA_BUY_NOT_FOUND"
        # AdCPMediaBuyNotFoundError overrides AdCPNotFoundError's terminal default
        # because the buyer can correct by supplying the right media_buy_id.
        assert exc_info.value.recovery == "correctable"
        assert "mb_nonexistent_typed_raise_pin" in exc_info.value.message

    def test_account_filter_unsupported_raises_typed_subclass(self):
        """``_get_media_buys_impl`` raises ``AdCPCapabilityNotSupportedError``.

        Pins the specific subclass so the wire code is
        ``UNSUPPORTED_FEATURE`` (not the generic ``VALIDATION_ERROR``).
        Recovery is ``correctable`` per the documented spec divergence
        (the buyer can drop the unsupported parameter).
        """
        # No DB setup needed — the unsupported-feature check fires before any DB access.
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(
            tenant_id="any_tenant",
            principal_id="any_principal",
            protocol="mcp",
        )
        req = GetMediaBuysRequest(account_id="acc_123")

        with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
            _get_media_buys_impl(req, identity=identity)

        assert exc_info.value.error_code == "UNSUPPORTED_FEATURE"
        # Intentional spec divergence (see exceptions.py:484) — we emit
        # correctable because the buyer can drop the unsupported parameter.
        assert exc_info.value.recovery == "correctable"
        assert "account" in exc_info.value.message.lower()
