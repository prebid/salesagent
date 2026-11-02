"""Grade the CI account seeding that the E2E suite depends on.

`account` is in /required on sync-creatives, create-media-buy and update-media-buy.
Without a resolvable account every E2E call to those tools is refused with
INVALID_REQUEST before reaching the behavior under test -- which is how this gap
stayed invisible: the E2E suite failed for a reason that looked like a production
bug in the tools themselves.

Two failure modes are graded here because both actually happened while wiring it:
the account row alone is not enough (resolution is gated on the GRANT), and the
grant cannot be written before its parents (composite FKs, no ORM relationship,
so nothing orders the INSERTs).
"""

from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.models import Account, AgentAccountAccess


@pytest.mark.requires_db
def test_ci_seeding_creates_a_resolvable_account_for_its_principal(bound_factory_session):
    from scripts.setup.init_database_ci import CI_TEST_ACCOUNT_ID, init_db_ci

    # The fixture already migrated; the script migrates unconditionally and would hit
    # DuplicateTable. The migration step is not what this grades -- the seeding is.
    with patch("scripts.ops.migrate.run_migrations"):
        init_db_ci()

    account = bound_factory_session.scalars(select(Account).filter_by(account_id=CI_TEST_ACCOUNT_ID)).first()
    assert account is not None, "the E2E builders send this account id; seeding must create it"
    assert account.status == "active", f"a non-active status blocks resolution, got {account.status!r}"

    # The grant must exist FOR THE SAME TENANT as the account, and name a real principal
    # of that tenant. Checked as a join rather than by hardcoding ids because the CI
    # script mints a fresh tenant id per run and may move the token's principal into it.
    grant = bound_factory_session.scalars(
        select(AgentAccountAccess).filter_by(tenant_id=account.tenant_id, account_id=CI_TEST_ACCOUNT_ID)
    ).first()
    assert grant is not None, (
        "resolution is gated on AgentAccountAccess: without the grant the account "
        "resolves to AUTHORIZATION_ERROR rather than succeeding"
    )
