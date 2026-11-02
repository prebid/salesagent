"""Integration tests for the harness's account seeding.

AdCP 3.1.1 makes ``account`` REQUIRED on sync-creatives-request and
update-media-buy-request (both list it in ``/required``). A scenario that cannot seed
an Account cannot build a valid request at all -- it fails on a missing field before it
reaches the behaviour it means to grade. These exercise the seeding against real
PostgreSQL, because the failure it exists to prevent is a foreign-key violation and a
mocked session cannot have one.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.database.models import Account, AgentAccountAccess
from tests.harness._base import IntegrationEnv


@pytest.mark.requires_db
class TestSetupDefaultAccount:
    def test_seeds_an_account_owned_by_this_tenant(self, integration_db):
        """The FK must resolve: the Account belongs to the env's tenant, not a new one."""
        with IntegrationEnv(tenant_id="acct_t1", principal_id="acct_p1") as env:
            account = env.setup_default_account()

            assert account.tenant_id == "acct_t1"
            rows = env.get_session().scalars(select(Account).filter_by(tenant_id="acct_t1")).all()
            assert [r.account_id for r in rows] == [account.account_id]

    def test_grants_this_principal_access_to_it(self, integration_db):
        """Seeding the Account alone is not enough -- the principal must be able to use it."""
        with IntegrationEnv(tenant_id="acct_t2", principal_id="acct_p2") as env:
            account = env.setup_default_account()

            access = (
                env.get_session()
                .scalars(select(AgentAccountAccess).filter_by(tenant_id="acct_t2", principal_id="acct_p2"))
                .all()
            )
            assert [a.account_id for a in access] == [account.account_id]

    def test_is_idempotent(self, integration_db):
        """Repeated Given steps must reuse the row, not collide on the PK."""
        with IntegrationEnv(tenant_id="acct_t3", principal_id="acct_p3") as env:
            first = env.setup_default_account()
            second = env.setup_default_account()

            assert second.account_id == first.account_id
            rows = env.get_session().scalars(select(Account).filter_by(tenant_id="acct_t3")).all()
            assert len(rows) == 1

    def test_reference_is_the_account_id_form_of_the_oneOf(self, integration_db):
        """core/account-ref.json is oneOf[{account_id}, {brand, operator}].

        The seeded row can satisfy the account_id branch exactly, so that is the branch the
        harness hands out -- a brand/operator pair would be reconstructed data the DB
        never agreed to.
        """
        with IntegrationEnv(tenant_id="acct_t4", principal_id="acct_p4") as env:
            account = env.setup_default_account()

            assert env.default_account_reference().model_dump() == {"account_id": account.account_id}
