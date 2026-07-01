"""Integration tests for account resolution error codes in create_media_buy context.

Verifies that account resolution errors return spec-compliant error codes
(ACCOUNT_NOT_FOUND, ACCOUNT_AMBIGUOUS) rather than generic codes (NOT_FOUND).

beads: salesagent-2rq, salesagent-l9wn
"""

import pytest
from adcp.types import (
    AccountReference,
    AccountReferenceById,
    AccountReferenceByNaturalKey,
    BrandReference,
)

from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import AdCPAccountNotFoundError, AdCPNotFoundError
from src.core.helpers.account_helpers import resolve_account
from src.core.resolved_identity import ResolvedIdentity
from tests.harness._base import IntegrationEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _AccountResolutionEnv(IntegrationEnv):
    """Bare integration env for account resolution tests."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


def _make_identity(tenant_id: str, principal_id: str = "agent_001") -> ResolvedIdentity:
    return ResolvedIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        auth_token="test-token",
    )


class TestAccountResolutionErrorCodes:
    """Account resolution errors must use spec-compliant error codes."""

    def test_not_found_by_id_returns_account_not_found(self, integration_db):
        """resolve_account with nonexistent account_id → ACCOUNT_NOT_FOUND."""
        from tests.factories import TenantFactory

        with _AccountResolutionEnv() as env:
            tenant = TenantFactory(tenant_id="acct_err_t1")
            env.get_session()  # commit factory data

            identity = _make_identity(tenant.tenant_id)
            ref = AccountReference(root=AccountReferenceById(account_id="nonexistent_acc"))

            with AccountUoW(tenant.tenant_id) as uow:
                with pytest.raises(AdCPAccountNotFoundError) as exc_info:
                    resolve_account(ref, identity, uow.accounts)

            # AdCPAccountNotFoundError is a subclass of AdCPNotFoundError (still 404)
            assert isinstance(exc_info.value, AdCPNotFoundError)
            assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"

    def test_not_found_by_natural_key_returns_account_not_found(self, integration_db):
        """resolve_account with nonexistent natural key → AdCPAccountNotFoundError."""
        from tests.factories import TenantFactory

        with _AccountResolutionEnv() as env:
            tenant = TenantFactory(tenant_id="acct_err_t2")
            env.get_session()

            identity = _make_identity(tenant.tenant_id)
            ref = AccountReference(
                root=AccountReferenceByNaturalKey(
                    brand=BrandReference(domain="nonexistent.com"),
                    operator="nobody.com",
                )
            )

            with AccountUoW(tenant.tenant_id) as uow:
                with pytest.raises(AdCPAccountNotFoundError) as exc_info:
                    resolve_account(ref, identity, uow.accounts)

            assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"


class TestAccountNotFoundViaTransports:
    """ACCOUNT_NOT_FOUND error surfaces through transport wrappers (l9wn regression).

    Verifies that a nonexistent account reference in a create_media_buy request
    produces ACCOUNT_NOT_FOUND via A2A and MCP transports — not a success or a
    different error. Before l9wn, the harness stripped 'account' from the flat
    dict before dispatch, so account resolution was never invoked.
    """

    @pytest.fixture
    def env_with_data(self, integration_db):
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()
            yield env

    def _nonexistent_account_req(self):
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from src.core.schemas import CreateMediaBuyRequest

        now = datetime.now(UTC)
        return CreateMediaBuyRequest(
            account=AccountReference(root=AccountReferenceById(account_id="nonexistent-acc-l9wn")),
            brand={"domain": "testbrand.com"},
            start_time=(now + timedelta(days=1)).isoformat(),
            end_time=(now + timedelta(days=8)).isoformat(),
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            # idempotency_key is REQUIRED on CreateMediaBuyRequest (16-255 chars, #1312).
            # Tests going through the harness get a default via _ensure_idempotency_key;
            # this helper builds the request directly, so supply one explicitly.
            idempotency_key=f"test-acct-notfound-{uuid4().hex}",
        )

    def test_account_not_found_via_a2a(self, env_with_data):
        """ACCOUNT_NOT_FOUND surfaces through A2A transport (not stripped by harness).

        Covers: salesagent-l9wn regression test
        """
        result = env_with_data.call_via(Transport.A2A, req=self._nonexistent_account_req())
        assert result.is_error, f"Expected ACCOUNT_NOT_FOUND error, got success: {result.payload}"
        wire = result.wire_error_envelope
        assert wire is not None, "No wire error envelope captured"
        errors = wire.get("errors", [])
        assert errors, "Error envelope has no errors"
        assert errors[0].get("code") == "ACCOUNT_NOT_FOUND", f"Expected ACCOUNT_NOT_FOUND, got: {errors[0].get('code')}"

    def test_account_not_found_via_mcp(self, env_with_data):
        """ACCOUNT_NOT_FOUND surfaces through MCP transport (not stripped by harness).

        call_mcp calls the tool function directly (not through FastMCP server),
        so the error surfaces as a raw AdCPError on result.error.

        Covers: salesagent-l9wn regression test
        """
        result = env_with_data.call_via(Transport.MCP, req=self._nonexistent_account_req())
        assert result.is_error, f"Expected ACCOUNT_NOT_FOUND error, got success: {result.payload}"
        assert hasattr(result.error, "error_code"), f"Expected AdCPError, got: {type(result.error)}"
        assert result.error.error_code == "ACCOUNT_NOT_FOUND", (
            f"Expected ACCOUNT_NOT_FOUND, got: {result.error.error_code}"
        )
