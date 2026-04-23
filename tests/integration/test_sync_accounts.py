"""Integration tests for _sync_accounts_impl.

Verifies sync_accounts upsert semantics with real PostgreSQL.

beads: salesagent-619
Business rules: BR-RULE-055 (auth required), BR-RULE-056 (upsert by natural key),
BR-RULE-057 (atomic XOR response), BR-RULE-060 (approval workflow),
BR-RULE-061 (delete_missing), BR-RULE-062 (dry_run)
"""

import pytest

from src.core.schemas.account import SyncAccountsRequest
from tests.harness import Transport
from tests.harness.account_sync import AccountSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]


def _action_value(action):
    """Extract string value from Action enum or return as-is."""
    return action.value if hasattr(action, "value") else str(action)


def _status_value(status):
    """Extract string value from Status enum or return as-is."""
    return status.value if hasattr(status, "value") else str(status)


class TestSyncAccountsCreate:
    """BR-RULE-056: sync_accounts creates new accounts by natural key."""

    @pytest.mark.asyncio
    async def test_creates_new_account(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_t1", principal_id="agent_sync") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 1
        result = response.accounts[0]
        assert _action_value(result.action) == "created"
        assert _status_value(result.status) == "active"
        assert result.brand.domain == "acme.com"
        assert result.operator == "example.com"

    @pytest.mark.asyncio
    async def test_creates_multiple_accounts(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_t2", principal_id="agent_sync2") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    },
                    {
                        "brand": {"domain": "beta.com"},
                        "operator": "example.com",
                        "billing": "agent",
                    },
                ],
            )
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 2
        actions = [_action_value(a.action) for a in response.accounts]
        assert actions == ["created", "created"]


class TestSyncAccountsUpdate:
    """BR-RULE-056: sync_accounts updates existing accounts."""

    @pytest.mark.asyncio
    async def test_updates_existing_account(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_t3", principal_id="agent_sync3") as env:
            env.setup_default_data()

            # Create account first
            req1 = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            await env.call_impl_async(req=req1)

            # Sync again with updated billing
            req2 = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "agent",
                    }
                ],
            )
            response = await env.call_impl_async(req=req2)

        assert len(response.accounts) == 1
        result = response.accounts[0]
        assert _action_value(result.action) == "updated"

    @pytest.mark.asyncio
    async def test_unchanged_account(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_t4", principal_id="agent_sync4") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            # Create
            await env.call_impl_async(req=req)
            # Sync identical
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 1
        assert _action_value(response.accounts[0].action) == "unchanged"


class TestSyncAccountsAuth:
    """BR-RULE-055: sync_accounts requires valid authentication."""

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_error(self, integration_db):
        from src.core.exceptions import AdCPAuthenticationError

        with AccountSyncEnv(tenant_id="sync_t5", principal_id="agent_sync5") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            with pytest.raises(AdCPAuthenticationError):
                await env.call_impl_async(req=req, identity=None)


class TestSyncAccountsDeleteMissing:
    """BR-RULE-061: delete_missing deactivates absent accounts scoped to agent."""

    @pytest.mark.asyncio
    async def test_delete_missing_closes_absent_accounts(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_t6", principal_id="agent_sync6") as env:
            env.setup_default_data()

            # Create two accounts
            req1 = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    },
                    {
                        "brand": {"domain": "beta.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    },
                ],
            )
            await env.call_impl_async(req=req1)

            # Sync with only one account + delete_missing=True
            req2 = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    },
                ],
                delete_missing=True,
            )
            response = await env.call_impl_async(req=req2)

        # The synced account is unchanged
        actions = {a.brand.domain: _action_value(a.action) for a in response.accounts}
        assert actions["acme.com"] == "unchanged"
        # beta.com should appear as updated (deactivated) with status=closed
        # AdCP Action enum has no "deleted" value — deactivation is action=updated, status=closed
        assert "beta.com" in actions
        assert actions["beta.com"] == "updated"
        statuses = {a.brand.domain: _status_value(a.status) for a in response.accounts}
        assert statuses["beta.com"] == "closed"


class TestSyncAccountsDryRun:
    """BR-RULE-062: dry_run returns preview without applying changes."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_persist(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_t7", principal_id="agent_sync7") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
                dry_run=True,
            )
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 1
        assert _action_value(response.accounts[0].action) == "created"
        assert response.dry_run is True

    @pytest.mark.asyncio
    async def test_dry_run_account_not_in_db(self, integration_db):
        """After dry_run, the account should not actually exist."""
        from src.core.database.repositories.uow import AccountUoW

        with AccountSyncEnv(tenant_id="sync_t8", principal_id="agent_sync8") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "dryrun.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
                dry_run=True,
            )
            await env.call_impl_async(req=req)

        # Verify no account was actually created
        with AccountUoW("sync_t8") as uow:
            assert uow.accounts is not None
            all_accounts = uow.accounts.list_all()
            assert len(all_accounts) == 0

    @pytest.mark.asyncio
    async def test_dry_run_credit_review_previews_pending_approval(self, integration_db):
        """BR-RULE-062 + BR-RULE-060: dry_run must preview the status that would
        result from a real create. With account_approval_mode='credit_review', a
        real create returns status=pending_approval with setup — so the dry-run
        preview must show the same, not 'active'.

        Regression for salesagent-jcvn: _sync_accounts_impl hardcoded
        status='active' in the dry_run branch, bypassing the approval-mode check
        and silently lying to buyers about what would happen.
        """
        with AccountSyncEnv(tenant_id="dryrun_cr_t", principal_id="dryrun_cr_p") as env:
            env.setup_default_data()
            env.set_approval_mode("credit_review")

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                ],
                dry_run=True,
            )
            response = await env.call_impl_async(req=req)

        assert response.dry_run is True
        assert len(response.accounts) == 1
        result = response.accounts[0]
        assert _action_value(result.action) == "created"
        assert _status_value(result.status) == "pending_approval", (
            "dry_run must preview the approval-mode-derived status, not hardcoded 'active'"
        )
        assert result.setup is not None, "dry_run must preview the setup object"
        assert result.setup.message is not None
        assert result.setup.url is not None
        assert result.setup.expires_at is not None


class TestSyncAccountsBillingPolicy:
    """BR-RULE-059: billing policy enforcement per-account."""

    @pytest.mark.asyncio
    async def test_unsupported_billing_returns_failed(self, integration_db):
        """Unsupported billing → action=failed, status=rejected, BILLING_NOT_SUPPORTED."""
        with AccountSyncEnv(
            tenant_id="sync_t9",
            principal_id="agent_sync9",
            supported_billing=["agent"],
        ) as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 1
        result = response.accounts[0]
        assert _action_value(result.action) == "failed"
        assert _status_value(result.status) == "rejected"
        assert result.errors is not None
        assert len(result.errors) >= 1
        assert result.errors[0].code == "BILLING_NOT_SUPPORTED"

    @pytest.mark.asyncio
    async def test_mixed_billing_partial_success(self, integration_db):
        """Mixed billing: supported succeeds, unsupported fails per-account."""
        with AccountSyncEnv(
            tenant_id="sync_t10",
            principal_id="agent_sync10",
            supported_billing=["agent"],
        ) as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "good.com"},
                        "operator": "example.com",
                        "billing": "agent",
                    },
                    {
                        "brand": {"domain": "bad.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    },
                ],
            )
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 2
        actions = {a.brand.domain: _action_value(a.action) for a in response.accounts}
        assert actions["good.com"] == "created"
        assert actions["bad.com"] == "failed"


class TestSyncAccountsApproval:
    """BR-RULE-060: approval workflow determines initial account status."""

    @pytest.mark.asyncio
    async def test_credit_review_returns_pending_with_setup(self, integration_db):
        """Credit review → pending_approval with setup (url + message + expires_at)."""
        with AccountSyncEnv(
            tenant_id="sync_t11",
            principal_id="agent_sync11",
            account_approval_mode="credit_review",
        ) as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 1
        result = response.accounts[0]
        assert _action_value(result.action) == "created"
        assert _status_value(result.status) == "pending_approval"
        assert result.setup is not None
        assert result.setup.message is not None
        assert result.setup.url is not None
        assert result.setup.expires_at is not None

    @pytest.mark.asyncio
    async def test_set_approval_mode_writes_to_account_approval_mode_column(self, integration_db):
        """Regression for salesagent-69xd: AccountSyncEnv.set_approval_mode() must write to
        the account_approval_mode DB column (BR-RULE-060), NOT the creative approval_mode
        column (BR-RULE-037). The MCP real-auth chain reads account_approval_mode from the
        DB tenant row — if the harness writes to the wrong column, MCP tests silently fall
        through to the default (None → 'auto') even though the harness claims credit_review.
        """
        from sqlalchemy import select

        from src.core.config_loader import get_tenant_by_id
        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant

        with AccountSyncEnv(tenant_id="harness_audit_t", principal_id="harness_audit_p") as env:
            env.setup_default_data()
            env.set_approval_mode("credit_review")

            # Fresh session (simulates MCP auth chain opening its own session)
            with get_db_session() as fresh_session:
                tenant = fresh_session.scalars(select(Tenant).filter_by(tenant_id="harness_audit_t")).first()
                assert tenant is not None
                # MUST be written to account_approval_mode (BR-RULE-060)
                assert tenant.account_approval_mode == "credit_review", (
                    "set_approval_mode writes to wrong DB column; MCP auth chain won't see it"
                )

            # And the serialized tenant dict used by resolve_identity must include it
            tenant_dict = get_tenant_by_id("harness_audit_t")
            assert tenant_dict is not None
            assert tenant_dict["account_approval_mode"] == "credit_review"


class TestSyncAccountsBillingPolicyTransport:
    """BR-RULE-059: billing policy behavior must be identical across all transports.

    Part of salesagent-wp9u — transport-matrix coverage for #1184 billing policy.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unsupported_billing_returns_failed(self, integration_db, transport):
        """Seller that does not support 'operator' billing rejects operator accounts
        with per-account action=failed, status=rejected, code=BILLING_NOT_SUPPORTED."""
        with AccountSyncEnv(
            tenant_id=f"bp_unsup_{transport.value}",
            principal_id=f"agent_bp_{transport.value}",
        ) as env:
            env.setup_default_data()
            env.set_billing_policy(["agent"])

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                ],
            )
            result = env.call_via(transport, req=req)

        assert result.is_success, f"Expected success for {transport}: {result.error}"
        accounts = result.payload.accounts
        assert len(accounts) == 1
        acct = accounts[0]
        assert _action_value(acct.action) == "failed"
        assert _status_value(acct.status) == "rejected"
        assert acct.errors is not None
        assert acct.errors[0].code == "BILLING_NOT_SUPPORTED"

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_billing_rejection_error_includes_suggestion(self, integration_db, transport):
        """BR-RULE-059 requires the error payload to include a suggestion field
        pointing buyers to supported billing models."""
        with AccountSyncEnv(
            tenant_id=f"bp_sugg_{transport.value}",
            principal_id=f"agent_bps_{transport.value}",
        ) as env:
            env.setup_default_data()
            env.set_billing_policy(["agent"])

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                ],
            )
            result = env.call_via(transport, req=req)

        assert result.is_success
        err = result.payload.accounts[0].errors[0]
        assert err.suggestion is not None
        assert "agent" in err.suggestion

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unconfigured_billing_policy_accepts_all(self, integration_db, transport):
        """When supported_billing is not configured, all billing values are accepted."""
        with AccountSyncEnv(
            tenant_id=f"bp_any_{transport.value}",
            principal_id=f"agent_bpa_{transport.value}",
        ) as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                    {"brand": {"domain": "beta.com"}, "operator": "example.com", "billing": "agent"},
                ],
            )
            result = env.call_via(transport, req=req)

        assert result.is_success
        actions = {a.brand.domain: _action_value(a.action) for a in result.payload.accounts}
        assert actions == {"acme.com": "created", "beta.com": "created"}


class TestSyncAccountsApprovalTransport:
    """BR-RULE-060: account approval mode behavior must be identical across all transports.

    Part of salesagent-wp9u — transport-matrix coverage for #1184 approval workflow.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_credit_review_returns_pending_with_setup(self, integration_db, transport):
        """credit_review → status=pending_approval with setup(url + message + expires_at)."""
        with AccountSyncEnv(
            tenant_id=f"ap_cr_{transport.value}",
            principal_id=f"agent_apcr_{transport.value}",
        ) as env:
            env.setup_default_data()
            env.set_approval_mode("credit_review")

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                ],
            )
            result = env.call_via(transport, req=req)

        assert result.is_success
        acct = result.payload.accounts[0]
        assert _status_value(acct.status) == "pending_approval"
        assert acct.setup is not None
        assert acct.setup.message is not None
        assert acct.setup.url is not None
        assert acct.setup.expires_at is not None

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_legal_review_returns_pending_message_only(self, integration_db, transport):
        """legal_review → status=pending_approval with setup(message only, no url, no expires_at)."""
        with AccountSyncEnv(
            tenant_id=f"ap_lr_{transport.value}",
            principal_id=f"agent_aplr_{transport.value}",
        ) as env:
            env.setup_default_data()
            env.set_approval_mode("legal_review")

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                ],
            )
            result = env.call_via(transport, req=req)

        assert result.is_success
        acct = result.payload.accounts[0]
        assert _status_value(acct.status) == "pending_approval"
        assert acct.setup is not None
        assert acct.setup.message is not None
        assert acct.setup.url is None
        assert acct.setup.expires_at is None

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_auto_approve_returns_active_no_setup(self, integration_db, transport):
        """account_approval_mode=None (default) → status=active with no setup."""
        with AccountSyncEnv(
            tenant_id=f"ap_au_{transport.value}",
            principal_id=f"agent_apau_{transport.value}",
        ) as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                ],
            )
            result = env.call_via(transport, req=req)

        assert result.is_success
        acct = result.payload.accounts[0]
        assert _status_value(acct.status) == "active"
        assert acct.setup is None
