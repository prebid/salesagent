"""MediaBuyAccountEnv — integration test environment for account resolution in create_media_buy.

Tests resolve_account() with real PostgreSQL. Account resolution runs at the
transport boundary (before _impl), so this harness tests the resolution function
directly with proper DB state.

Requires: integration_db fixture.

beads: salesagent-2rq
"""

from __future__ import annotations

import uuid
from typing import Any

from tests.harness._base import IntegrationEnv


class MediaBuyAccountEnv(IntegrationEnv):
    """Integration test environment for account resolution in create_media_buy context.

    Only patches audit logger. Everything else is real:
    - Real DB with tenant, principal, accounts
    - Real AccountRepository
    - Real resolve_account() logic

    Uses a unique tenant_id per instance to avoid cross-test collisions.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}

    def __init__(self, **kwargs: Any) -> None:
        # Generate unique tenant/principal IDs to avoid collisions
        suffix = uuid.uuid4().hex[:8]
        kwargs.setdefault("tenant_id", f"mb_acct_{suffix}")
        kwargs.setdefault("principal_id", f"agent_{suffix}")
        super().__init__(**kwargs)

    def call_impl(self, **kwargs: Any) -> str:
        """Call resolve_account() with real DB.

        Returns the resolved account_id string.

        Kwargs:
            account_ref: AccountReference from the request
            identity: ResolvedIdentity (defaults to self.identity)
        """
        from src.core.database.repositories.uow import AccountUoW
        from src.core.helpers.account_helpers import resolve_account

        self._commit_factory_data()

        account_ref = kwargs["account_ref"]
        identity = kwargs.get("identity", self.identity)

        with AccountUoW(identity.tenant_id) as uow:
            return resolve_account(account_ref, identity, uow.accounts)
