"""Integration tests for account resolution error codes in create_media_buy context.

Verifies that account resolution errors return spec-compliant error codes
(ACCOUNT_NOT_FOUND, ACCOUNT_AMBIGUOUS) rather than generic codes (NOT_FOUND).

"""

import pytest
from adcp.types import (
    AccountReference,
    AccountReferenceById,
    AccountReferenceByNaturalKey,
    BrandReference,
)

from src.core.database.repositories.account_lookup import find_account
from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import (
    AdCPAccountNotFoundError,
    AdCPNotFoundError,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import TenantContext
from tests.factories.principal import PrincipalFactory
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
    return PrincipalFactory.make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=TenantContext.load(tenant_id),
    )


class TestAccountResolutionErrorCodes:
    """Account resolution errors must use spec-compliant error codes."""

    def test_not_found_by_id_returns_account_not_found(self, integration_db):
        """find_account with nonexistent account_id → ACCOUNT_NOT_FOUND."""
        from tests.factories import TenantFactory

        with _AccountResolutionEnv() as env:
            tenant = TenantFactory(tenant_id="acct_err_t1")
            env.get_session()  # commit factory data

            identity = _make_identity(tenant.tenant_id)
            ref = AccountReference(root=AccountReferenceById(account_id="nonexistent_acc"))

            with AccountUoW(tenant.tenant_id) as uow:
                with pytest.raises(AdCPAccountNotFoundError) as exc_info:
                    find_account(uow.accounts, ref, identity.principal)

            # AdCPAccountNotFoundError is a subclass of AdCPNotFoundError (still 404)
            assert isinstance(exc_info.value, AdCPNotFoundError)
            assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"

    def test_not_found_by_natural_key_returns_account_not_found(self, integration_db):
        """find_account with nonexistent natural key → AdCPAccountNotFoundError."""
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
                    find_account(uow.accounts, ref, identity.principal)

            assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"


# (Retired) TestRequireAccountAccessFalsyPrincipal and
# TestResolveAccountFalsyPrincipalEntryGuard drove ``account_helpers`` with an identity
# carrying a falsy principal_id, and asserted the helper minted AUTH_MISSING itself
# (hl35). Neither the helper nor that identity exists now. Account resolution moved
# behind the boundary: ``find_account`` takes a ``Principal``, whose ``principal_id`` is a
# required string, and the resolver calls it only for an authenticated caller -- a request
# naming an account must present a valid credential, so an anonymous caller is refused
# before resolution runs. The identity these tests built cannot even be constructed
# (``make_identity(principal_id=None)`` fails Principal validation).
#
# The obligation is unchanged and is graded on the wire instead, across a2a/mcp/rest:
# tests/bdd/features/BR-UC-002-account-access.feature,
# @T-UC-002-fb2l-unauth-no-disclosure -- an unauthenticated caller naming a natural key
# gets AUTH_MISSING and learns no match count. That grades the refusal a buyer actually
# receives, which a helper-level test never did.


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

        Covers: #1417 regression test
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
        so the rejection surfaces as an ACCOUNT_NOT_FOUND envelope on the wire.

        Covers: #1417 regression test
        """
        result = env_with_data.call_via(Transport.MCP, req=self._nonexistent_account_req())
        assert result.is_error, f"Expected ACCOUNT_NOT_FOUND error, got success: {result.payload}"
        # Graded on the WIRE. This used to assert result.error was an AdCPSalesAgentError with an
        # .error_code, which held only because the harness rebuilt one from wire bytes;
        # that reconstruction is gone (salesagent-3dawm.15) and result.error is now the
        # carrier holding the envelope.
        result.assert_wire_error("ACCOUNT_NOT_FOUND")
