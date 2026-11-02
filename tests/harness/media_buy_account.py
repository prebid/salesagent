"""MediaBuyAccountEnv — integration test environment for account resolution in create_media_buy.

Tests ``account_lookup.find_account`` with real PostgreSQL. Account resolution runs in
the resolver (before _impl), so this harness tests the lookup directly with proper DB
state.

Requires: integration_db fixture.

"""

from __future__ import annotations

import uuid
from typing import Any

from tests.factories.mint import mint
from tests.harness._base import IntegrationEnv


class MediaBuyAccountEnv(IntegrationEnv):
    """Integration test environment for account resolution in create_media_buy context.

    Only patches audit logger. Everything else is real:
    - Real DB with tenant, principal, accounts
    - Real AccountRepository and the resolver's ``find_account`` lookup over it

    Uses a unique tenant_id per instance to avoid cross-test collisions.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}

    def __init__(self, **kwargs: Any) -> None:
        # Generate unique tenant/principal IDs to avoid collisions
        suffix = uuid.uuid4().hex[:8]
        kwargs.setdefault("tenant_id", mint(f"mb_acct_{suffix}"))
        kwargs.setdefault("principal_id", mint(f"agent_{suffix}"))
        super().__init__(**kwargs)

    def call_impl(self, **kwargs: Any) -> str:
        """Call ``find_account`` with real DB, as the resolver does.

        Returns the resolved account_id string.

        Kwargs:
            account_ref: AccountReference from the request
            identity: ResolvedIdentity (defaults to self.identity); its principal is the
                caller the access check runs for
        """
        from src.core.database.repositories.account_lookup import find_account
        from src.core.database.repositories.uow import AccountUoW

        self._commit_factory_data()

        account_ref = kwargs["account_ref"]
        identity = kwargs.get("identity", self.identity)

        with AccountUoW(identity.tenant_id) as uow:
            assert uow.accounts is not None
            return find_account(uow.accounts, account_ref, identity.principal).account_id
