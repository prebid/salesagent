"""Integration tests for account resolution error codes in create_media_buy context.

Verifies that account resolution errors return spec-compliant error codes
(ACCOUNT_NOT_FOUND, ACCOUNT_AMBIGUOUS) rather than generic codes (NOT_FOUND).

beads: salesagent-2rq
"""

import pytest
from adcp.types.generated_poc.core.account_ref import (
    AccountReference,
    AccountReference1,
    AccountReference2,
)
from adcp.types.generated_poc.core.brand_ref import BrandReference

from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import AdCPAccountNotFoundError, AdCPNotFoundError
from src.core.helpers.account_helpers import resolve_account
from src.core.resolved_identity import ResolvedIdentity
from tests.harness._base import IntegrationEnv

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
            ref = AccountReference(root=AccountReference1(account_id="nonexistent_acc"))

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
                root=AccountReference2(
                    brand=BrandReference(domain="nonexistent.com"),
                    operator="nobody.com",
                )
            )

            with AccountUoW(tenant.tenant_id) as uow:
                with pytest.raises(AdCPAccountNotFoundError) as exc_info:
                    resolve_account(ref, identity, uow.accounts)

            assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"
