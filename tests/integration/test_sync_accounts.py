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
from tests.helpers import assert_envelope_shape

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
    async def test_creates_advertiser_billing_account(self, integration_db):
        """billing='advertiser' is a spec-valid billing-party value and must persist (#1521).

        Mirrors the operator/agent create cases. An unconfigured tenant accepts
        the full billing-party enum — including 'advertiser' — and the DB CHECK
        (ck_accounts_billing) must permit the INSERT; before the #1521 fix a
        spec-valid advertiser entry IntegrityError'd against the 2-value CHECK.
        @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/billing-party.json pointer=/enum
        """
        from src.core.database.repositories.uow import AccountUoW

        with AccountSyncEnv(tenant_id="sync_adv1", principal_id="agent_sync_adv") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com"},
                        "operator": "example.com",
                        "billing": "advertiser",
                    }
                ],
            )
            response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 1
        result = response.accounts[0]
        assert _action_value(result.action) == "created"
        assert _status_value(result.status) == "active"

        # Round-trip: the row must actually be persisted with billing='advertiser'
        with AccountUoW("sync_adv1") as uow:
            assert uow.accounts is not None
            rows = uow.accounts.list_all()
            assert len(rows) == 1
            assert rows[0].billing == "advertiser"

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

    @pytest.mark.asyncio
    async def test_delete_missing_spares_settings_update_target(self, integration_db):
        """An account named by a settings-update entry IS included in the request.

        delete_missing may deactivate only accounts "not included in this
        request" (sync-accounts-request.json#/properties/delete_missing).
        seen_account_ids used to be populated on the provisioning path only, so
        the very request that successfully updated an account also closed it and
        the response carried both results for the same account.
        """
        with AccountSyncEnv(tenant_id="sync_dm_su", principal_id="agent_dmsu") as env:
            env.setup_default_data()

            created = await env.call_impl_async(
                req=SyncAccountsRequest(
                    accounts=[
                        {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"},
                        {"brand": {"domain": "beta.com"}, "operator": "example.com", "billing": "operator"},
                    ],
                )
            )
            target_id = next(a.account_id for a in created.accounts if a.brand.domain == "acme.com")

            response = await env.call_impl_async(
                req=SyncAccountsRequest(
                    accounts=[{"account": {"account_id": target_id}, "payment_terms": "net_45"}],
                    delete_missing=True,
                )
            )

        results_for_target = [a for a in response.accounts if a.account_id == target_id]
        assert len(results_for_target) == 1, (
            f"expected exactly one result for the settings-update target, got "
            f"{[(_action_value(a.action), _status_value(a.status)) for a in results_for_target]}"
        )
        assert _action_value(results_for_target[0].action) == "updated"
        assert _status_value(results_for_target[0].status) != "closed"
        # beta.com was genuinely absent from the request and is deactivated.
        beta = [a for a in response.accounts if a.brand and a.brand.domain == "beta.com"]
        assert len(beta) == 1 and _status_value(beta[0].status) == "closed"

    @pytest.mark.asyncio
    async def test_delete_missing_closes_target_of_failed_settings_update(self, integration_db):
        """Boundary: a FAILED settings-update entry does not shield its account.

        Same boundary as failed provisioning entries, which never reach
        seen_account_ids either — and structurally forced here because a failed
        result carries no account_id. The entry fails via ``sandbox``, which the
        field policy marks rejected on the settings-update arm.
        """
        with AccountSyncEnv(tenant_id="sync_dm_suf", principal_id="agent_dmsuf") as env:
            env.setup_default_data()

            created = await env.call_impl_async(
                req=SyncAccountsRequest(
                    accounts=[{"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"}],
                )
            )
            target_id = created.accounts[0].account_id

            response = await env.call_impl_async(
                req=SyncAccountsRequest(
                    accounts=[{"account": {"account_id": target_id}, "sandbox": True}],
                    delete_missing=True,
                )
            )

        actions = [(_action_value(a.action), _status_value(a.status)) for a in response.accounts]
        assert ("failed", "rejected") in actions, f"expected the settings-update entry to fail, got {actions}"
        closed = [a for a in response.accounts if a.account_id == target_id and _status_value(a.status) == "closed"]
        assert len(closed) == 1, (
            f"a failed settings-update entry must not shield its account from delete_missing; got {actions}"
        )


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
    async def test_settings_update_dry_run_matches_live_and_persists_nothing(self, integration_db):
        """Live-run-as-oracle for the settings-update arm under dry_run.

        The settings-update dispatch used to route before any dry_run branch and
        call repo.update_fields unconditionally, so a preview PERSISTED the
        update. The preview must describe exactly what the live run does
        (action=updated, the new payment_terms echoed) while writing nothing
        (sync-accounts-request.json#/properties/dry_run).
        """
        from src.core.database.repositories.uow import AccountUoW

        provision = {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"}

        async def run(tenant_id: str, principal_id: str, *, dry_run: bool):
            with AccountSyncEnv(tenant_id=tenant_id, principal_id=principal_id) as env:
                env.setup_default_data()
                created = await env.call_impl_async(req=SyncAccountsRequest(accounts=[dict(provision)]))
                account_id = created.accounts[0].account_id
                update_entry = {"account": {"account_id": account_id}, "payment_terms": "net_45"}
                update_req = SyncAccountsRequest(accounts=[update_entry], **({"dry_run": True} if dry_run else {}))
                resp = await env.call_impl_async(req=update_req)
            return account_id, resp

        live_id, live = await run("sync_su_live", "agent_sul", dry_run=False)
        preview_id, preview = await run("sync_su_dry", "agent_sud", dry_run=True)

        # Oracle: the preview describes exactly the outcome the live run produces.
        assert _action_value(live.accounts[0].action) == "updated"
        assert _action_value(preview.accounts[0].action) == "updated"
        assert preview.accounts[0].payment_terms == live.accounts[0].payment_terms
        assert preview.accounts[0].account_id == preview_id
        assert preview.dry_run is True

        # Persistence: the live run wrote, the preview wrote nothing.
        with AccountUoW("sync_su_live") as uow:
            assert uow.accounts is not None
            live_row = uow.accounts.get_by_id(live_id)
            assert live_row is not None and live_row.payment_terms == "net_45"
        with AccountUoW("sync_su_dry") as uow:
            assert uow.accounts is not None
            preview_row = uow.accounts.get_by_id(preview_id)
            assert preview_row is not None and preview_row.payment_terms is None, (
                f"dry_run persisted payment_terms={preview_row.payment_terms!r} — "
                "the preview wrote a value it only promised to preview"
            )

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

    @pytest.mark.asyncio
    async def test_two_entries_on_one_key_preview_the_outcome_a_real_run_produces(self, integration_db):
        """BR-RULE-062: a preview must reflect what a real create would return.

        For a payload carrying TWO entries on the SAME natural key it did not. The
        live path creates on entry 1 and FLUSHES, so entry 2's lookup finds that
        row and reports it — the buyer sees created, then unchanged, both naming
        one account. The dry_run arm appends its result and continues before any
        write, so entry 2's lookup still missed and the preview claimed created
        TWICE, under two different account_ids: an outcome a real run cannot
        produce, and precisely the one the buyer would use the preview to rule out.
        """
        entry = {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"}

        with AccountSyncEnv(tenant_id="sync_dup_dry", principal_id="agent_dup_dry") as env:
            env.setup_default_data()
            preview = await env.call_impl_async(
                req=SyncAccountsRequest(accounts=[dict(entry), dict(entry)], dry_run=True)
            )

        # The live run is the oracle, not a hand-written expectation.
        with AccountSyncEnv(tenant_id="sync_dup_live", principal_id="agent_dup_live") as env:
            env.setup_default_data()
            live = await env.call_impl_async(req=SyncAccountsRequest(accounts=[dict(entry), dict(entry)]))

        assert [_action_value(a.action) for a in live.accounts] == ["created", "unchanged"], (
            "precondition: the live path must resolve the second entry against the first"
        )
        assert live.accounts[0].account_id == live.accounts[1].account_id

        assert [_action_value(a.action) for a in preview.accounts] == [
            _action_value(a.action) for a in live.accounts
        ], (
            f"dry_run previewed {[_action_value(a.action) for a in preview.accounts]} but a real run returns "
            f"{[_action_value(a.action) for a in live.accounts]}"
        )
        assert preview.accounts[0].account_id == preview.accounts[1].account_id, (
            "the two previewed entries name different accounts "
            f"({preview.accounts[0].account_id} vs {preview.accounts[1].account_id}); one natural key is one account"
        )

    @pytest.mark.asyncio
    async def test_two_entries_on_one_key_that_differ_preview_the_update(self, integration_db):
        """A later entry that CHANGES a field must preview 'updated', with the new value.

        The obvious fix — remember which keys were previewed — reports 'unchanged'
        here and drops the difference from the preview entirely. Only carrying the
        previewed STATE forward, and running the same field comparison the live arm
        runs against it, gets this right.
        """
        first = {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"}
        second = {**first, "payment_terms": "net_30"}

        with AccountSyncEnv(tenant_id="sync_dup_diff_dry", principal_id="agent_ddd") as env:
            env.setup_default_data()
            preview = await env.call_impl_async(
                req=SyncAccountsRequest(accounts=[dict(first), dict(second)], dry_run=True)
            )

        with AccountSyncEnv(tenant_id="sync_dup_diff_live", principal_id="agent_ddl") as env:
            env.setup_default_data()
            live = await env.call_impl_async(req=SyncAccountsRequest(accounts=[dict(first), dict(second)]))

        assert [_action_value(a.action) for a in live.accounts] == ["created", "updated"], (
            "precondition: the live path must apply the second entry's change"
        )
        assert [_action_value(a.action) for a in preview.accounts] == [
            _action_value(a.action) for a in live.accounts
        ], (
            f"dry_run previewed {[_action_value(a.action) for a in preview.accounts]} but a real run returns "
            f"{[_action_value(a.action) for a in live.accounts]}"
        )
        assert preview.accounts[1].payment_terms == live.accounts[1].payment_terms, (
            f"the preview must show the value the change WOULD apply: {preview.accounts[1].payment_terms!r} "
            f"vs live {live.accounts[1].payment_terms!r}"
        )

    @pytest.mark.asyncio
    async def test_three_entries_on_one_key_resolve_against_the_running_state(self, integration_db):
        """The third entry is resolved against what the SECOND left, not the first.

        The live arm updates the row, so entry 3 compares against the updated row.
        A preview that remembered only entry 1's state would report entry 3 against
        stale values — a case no two-entry payload can expose.
        """
        base = {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"}
        entries = [dict(base), {**base, "payment_terms": "net_30"}, {**base, "payment_terms": "net_30"}]

        with AccountSyncEnv(tenant_id="sync_trip_dry", principal_id="agent_td") as env:
            env.setup_default_data()
            preview = await env.call_impl_async(req=SyncAccountsRequest(accounts=entries, dry_run=True))

        with AccountSyncEnv(tenant_id="sync_trip_live", principal_id="agent_tl") as env:
            env.setup_default_data()
            live = await env.call_impl_async(req=SyncAccountsRequest(accounts=entries))

        assert [_action_value(a.action) for a in live.accounts] == ["created", "updated", "unchanged"], (
            "precondition: the third entry matches what the second applied, so it is unchanged"
        )
        assert [_action_value(a.action) for a in preview.accounts] == [
            _action_value(a.action) for a in live.accounts
        ], (
            f"dry_run previewed {[_action_value(a.action) for a in preview.accounts]} but a real run returns "
            f"{[_action_value(a.action) for a in live.accounts]}"
        )

    @pytest.mark.asyncio
    async def test_entries_differing_only_by_brand_id_stay_distinct_accounts(self, integration_db):
        """brand_id is part of the key, so these are two accounts, not one.

        Guards the fix against being written against a PARTIAL natural key: keying
        on domain+operator alone would collapse these and preview one account where
        a real run creates two — a worse preview bug than the one being fixed.
        """
        entries = [
            {
                "brand": {"domain": "acme.com", "brand_id": "brand_one"},
                "operator": "example.com",
                "billing": "operator",
            },
            {
                "brand": {"domain": "acme.com", "brand_id": "brand_two"},
                "operator": "example.com",
                "billing": "operator",
            },
        ]

        with AccountSyncEnv(tenant_id="sync_bid_dry", principal_id="agent_bd") as env:
            env.setup_default_data()
            preview = await env.call_impl_async(req=SyncAccountsRequest(accounts=entries, dry_run=True))

        with AccountSyncEnv(tenant_id="sync_bid_live", principal_id="agent_bl") as env:
            env.setup_default_data()
            live = await env.call_impl_async(req=SyncAccountsRequest(accounts=entries))

        assert [_action_value(a.action) for a in live.accounts] == ["created", "created"]
        assert live.accounts[0].account_id != live.accounts[1].account_id

        assert [_action_value(a.action) for a in preview.accounts] == ["created", "created"], (
            f"a different brand_id is a different natural key: {[_action_value(a.action) for a in preview.accounts]}"
        )
        assert preview.accounts[0].account_id != preview.accounts[1].account_id, (
            "the preview collapsed two distinct brand_ids onto one account"
        )

    @pytest.mark.asyncio
    async def test_duplicate_key_dry_run_persists_nothing(self, integration_db):
        """The preview builds ORM rows now, so pin that none of them reach the DB."""
        from src.core.database.repositories.uow import AccountUoW

        entry = {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"}

        with AccountSyncEnv(tenant_id="sync_dup_nodb", principal_id="agent_dnd") as env:
            env.setup_default_data()
            await env.call_impl_async(req=SyncAccountsRequest(accounts=[dict(entry), dict(entry)], dry_run=True))

            with AccountUoW("sync_dup_nodb") as uow:
                assert uow.accounts is not None
                rows = uow.accounts.list_all()

        assert rows == [], f"dry_run persisted {[(r.account_id, r.brand) for r in rows]}"


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


class TestSyncAccountsBrandlessEntryRejected:
    """Regression (PR1399 R3-F1): a brandless account entry
    must be rejected with a clean VALIDATION_ERROR (400, correctable), NOT a 500.

    SDK 5.7's ``SyncAccountsRequest.accounts`` is ``list[Accounts | Accounts3]``;
    the ``Accounts3`` arm makes ``brand`` optional, so a payload like
    ``{"account": {...}, "operator": "..."}`` validates with ``brand=None``.
    Before the fix, ``_extract_natural_key`` did ``brand.domain`` unguarded →
    ``AttributeError`` → INTERNAL_ERROR/500. The pinned 3.1 spec (tag
    v3.1-04f59d2d5, sync-accounts-request.json) marks each accounts[] item
    ``required: ["brand","operator","billing"]`` — a brandless entry MUST be a
    clean buyer-correctable 400.
    """

    # A2A and REST parse the request into SyncAccountsRequest (Accounts3 arm,
    # brand=None) and reach _impl — the transports that exercise the unguarded
    # _extract_natural_key path. MCP rejects earlier at the FastMCP TypeAdapter
    # (brand required on the tool signature), a distinct boundary not touched by
    # this _impl guard; the all-transports obligation is encoded in the
    # hand-authored BR-UC-011-account-validation.feature companion.
    IMPL_REACHING_TRANSPORTS = [Transport.A2A, Transport.REST]

    @pytest.mark.parametrize("transport", IMPL_REACHING_TRANSPORTS, ids=lambda t: t.value)
    def test_brandless_entry_yields_validation_error_not_500(self, integration_db, transport):
        with AccountSyncEnv(
            tenant_id=f"brandless_{transport.value}",
            principal_id=f"agent_brandless_{transport.value}",
        ) as env:
            env.setup_default_data()

            # Accounts3 arm: omits brand entirely → parses with brand=None.
            req = SyncAccountsRequest(
                accounts=[{"account": {"account_id": "x"}, "operator": "example.com"}],
            )
            result = env.call_via(transport, req=req)

        assert result.is_error, f"brandless entry must error on {transport.value}, got {result.payload!r}"
        assert_envelope_shape(
            result.wire_error_envelope,
            "VALIDATION_ERROR",
            recovery="correctable",
        )


class TestSyncAccountsBrandIdRoundTrip:
    """Regression (salesagent-myhs): a brand_id entry must persist the brand_id VALUE.

    ``BrandReference.brand_id`` is ``BrandId``, a ``RootModel[str]`` whose
    ``__str__`` is ``BaseModel.__str__`` — so ``str(brand.brand_id)`` yields the
    repr ``"root='brand_one'"``, not ``'brand_one'``. sync_accounts stringified it
    that way at four sites, persisting the mangled text into
    ``accounts.brand->>'brand_id'`` and looking the natural key up under the same
    mangled value. ``_resolve_by_natural_key`` (account_helpers) always used
    ``.root``, so a media buy referencing such an account by natural key could
    never resolve it.

    The natural key is brand.domain + brand.brand_id + operator + sandbox
    (BR-RULE-056), so this is a corruption of the key itself, not a cosmetic echo.
    """

    @pytest.mark.asyncio
    async def test_brand_id_persists_unmangled_and_resolves(self, integration_db):
        from adcp.types import (
            AccountReference,
            AccountReferenceByNaturalKey,
        )

        from src.core.database.repositories.uow import AccountUoW
        from src.core.helpers.account_helpers import resolve_account

        with AccountSyncEnv(tenant_id="sync_bid1", principal_id="agent_sync_bid") as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com", "brand_id": "brand_one"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            response = await env.call_impl_async(req=req)

            assert len(response.accounts) == 1
            result = response.accounts[0]
            assert _action_value(result.action) == "created"
            created_account_id = result.account_id

            # The persisted natural-key component must be the submitted value.
            # Loading the row AT ALL is half the assertion: brand is a
            # JSONType(model=BrandReference) column that validates on read, and
            # "root='brand_one'" violates BrandId's ^[a-z0-9_]+$ pattern — so
            # before the fix this raised ValidationError instead of comparing.
            with AccountUoW("sync_bid1") as uow:
                assert uow.accounts is not None
                rows = uow.accounts.list_all()
                assert len(rows) == 1
                assert rows[0].brand.brand_id.root == "brand_one"

            # ...and the resolver (which reads .root) must find that same row.
            ref = AccountReference(
                AccountReferenceByNaturalKey(
                    brand={"domain": "acme.com", "brand_id": "brand_one"},
                    operator="example.com",
                )
            )
            with AccountUoW("sync_bid1") as uow:
                assert uow.accounts is not None
                resolved = resolve_account(ref, env.identity, uow.accounts)

            assert resolved == created_account_id

    @pytest.mark.asyncio
    async def test_settings_update_by_natural_key_with_brand_id_applies(self, integration_db):
        """A settings-update entry keyed by a natural key WITH brand_id must match.

        Covers the second defective site (_process_settings_update_entry): the
        reference lookup mangled brand_id the same way, so the entry matched
        nothing and came back UNSUPPORTED_PROVISIONING — the buyer's update was
        silently not applied to an account that plainly exists.
        """
        with AccountSyncEnv(tenant_id="sync_bid3", principal_id="agent_sync_bid3") as env:
            env.setup_default_data()

            brand = {"domain": "acme.com", "brand_id": "brand_one"}
            created = await env.call_impl_async(
                req=SyncAccountsRequest(
                    accounts=[{"brand": brand, "operator": "example.com", "billing": "operator"}],
                )
            )
            assert _action_value(created.accounts[0].action) == "created"

            updated = await env.call_impl_async(
                req=SyncAccountsRequest(
                    accounts=[
                        {
                            "account": {"brand": brand, "operator": "example.com"},
                            "payment_terms": "net_30",
                        }
                    ],
                )
            )

        assert len(updated.accounts) == 1
        result = updated.accounts[0]
        assert _action_value(result.action) == "updated", f"settings-update did not match the account: {result!r}"
        assert result.account_id == created.accounts[0].account_id

    @pytest.mark.asyncio
    async def test_already_proven_subscriber_is_not_reproven_for_a_brand_id_account(self, integration_db):
        """A re-sent, already-proven subscriber must not be re-proven (proof reuse).

        Covers the remaining two defective sites (_lookup_existing_for_entry),
        which feed _already_proven_tuples. With the brand_id mangled, the second
        sync failed to find the account it had just created, so it treated an
        already-proven subscriber as new and fired a fresh proof-of-control
        request at the buyer's endpoint — an externally visible side effect, not
        just an internal lookup miss.

        The assertion drives that consequence rather than the lookup: proof is
        forced to FAIL before the second sync, so a re-proof would surface as a
        failed entry. Staying 'unchanged' is only possible if no proof was fired.
        """
        with AccountSyncEnv(tenant_id="sync_bid4", principal_id="agent_sync_bid4") as env:
            env.setup_default_data()

            entry = {
                "brand": {"domain": "acme.com", "brand_id": "brand_one"},
                "operator": "example.com",
                "billing": "operator",
                "notification_configs": [
                    {
                        "subscriber_id": "sub_1",
                        "url": "https://buyer.example.com/hook",
                        # Account-scoped: media-buy-anchored types are refused on
                        # this surface, which is a different rule than the one
                        # under test here.
                        "event_types": ["creative.status_changed"],
                        "active": True,
                    }
                ],
            }

            first = await env.call_impl_async(req=SyncAccountsRequest(accounts=[entry]))
            assert _action_value(first.accounts[0].action) == "created", (
                f"first sync must provision and prove the subscriber: {first.accounts[0]!r}"
            )

            # From here on, any NEW proof-of-control attempt fails.
            env.set_notification_proof_result(succeeds=False)

            second = await env.call_impl_async(req=SyncAccountsRequest(accounts=[entry]))

        assert len(second.accounts) == 1
        result = second.accounts[0]
        assert _action_value(result.action) == "unchanged", (
            f"re-sending an already-proven subscriber must reuse the proof, not re-fire it: {result!r}"
        )

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_echoed_account_name_carries_the_plain_brand_id(self, integration_db, transport):
        """The buyer-visible name must not carry the RootModel repr.

        The mangled brand_id also reached _generate_account_name, and unlike
        ``brand`` — which is echoed from the parsed REQUEST object and was
        therefore never corrupted — ``name`` is built from the extracted value
        and IS returned to the buyer. This is the one buyer-facing surface the
        defect reached, so it is graded on the real wire across every transport.
        """
        with AccountSyncEnv(
            tenant_id=f"sync_bid_name_{transport.value}",
            principal_id=f"agent_bid_name_{transport.value}",
        ) as env:
            env.setup_default_data()

            req = SyncAccountsRequest(
                accounts=[
                    {
                        "brand": {"domain": "acme.com", "brand_id": "brand_one"},
                        "operator": "example.com",
                        "billing": "operator",
                    }
                ],
            )
            result = env.call_via(transport, req=req)

        assert result.is_success, f"sync must succeed on {transport.value}: {result.payload!r}"
        account = result.payload.accounts[0]
        assert account.name == "acme.com:brand_one c/o example.com", (
            f"the echoed name must carry the plain brand_id on {transport.value}, got {account.name!r}"
        )


# ---------------------------------------------------------------------------
# dry_run preview vs live run — the LIVE RUN IS THE ORACLE
# ---------------------------------------------------------------------------


def _entry(domain: str, *, operator: str = "example.com", billing: str = "operator") -> dict:
    """One provisioning-mode sync entry keyed by the natural key."""
    return {"brand": {"domain": domain}, "operator": operator, "billing": billing}


def _canonical(results: list) -> list[dict]:
    """Wire dumps with seller-generated account_ids canonicalised by first use.

    ``_generate_account_id`` is a uuid4, so two runs of the same payload in two
    tenants can never produce equal ids — but WHICH results share an id is
    exactly what a preview must get right (two entries on one natural key must
    not preview two different accounts). Mapping each distinct id to the ordinal
    of its first appearance keeps that relationship gradable while dropping the
    randomness.
    """
    seen: dict[str, str] = {}
    out: list[dict] = []
    for r in results:
        d = r.model_dump(mode="json")
        aid = d.get("account_id")
        if aid is not None:
            d["account_id"] = seen.setdefault(aid, f"acc#{len(seen)}")
        out.append(d)
    return out


async def _sync_wire(slug: str, *, dry_run: bool, seed: list | tuple = (), **req_kwargs) -> list[dict]:
    """Run ONE sync in its own tenant and return the canonicalised wire results.

    ``seed`` is synced LIVE first (that is how an account becomes persisted),
    then the measured call runs with ``dry_run``.
    """
    tenant_id, principal_id = f"dro_{slug}", f"ag_dro_{slug}"
    with AccountSyncEnv(tenant_id=tenant_id, principal_id=principal_id) as env:
        env.setup_default_data()
        if seed:
            await env.call_impl_async(req=SyncAccountsRequest(accounts=list(seed)))
        response = await env.call_impl_async(req=SyncAccountsRequest(dry_run=dry_run, **req_kwargs))
    return _canonical(response.accounts)


async def _preview_and_live(slug: str, **kwargs) -> tuple[list[dict], list[dict]]:
    """Run the SAME payload as a preview and as a live sync, in separate tenants."""
    preview = await _sync_wire(f"{slug}d", dry_run=True, **kwargs)
    live = await _sync_wire(f"{slug}l", dry_run=False, **kwargs)
    return preview, live


def _assert_preview_matches_live(preview: list[dict], live: list[dict]) -> None:
    """The live run is the oracle: the preview must be what a real run produces."""
    assert preview == live, (
        "dry_run previewed a result a real run does not produce\n"
        f"  DRY  ({len(preview)} entries): {preview}\n"
        f"  LIVE ({len(live)} entries): {live}"
    )


def _summary(wire: list[dict]) -> list[tuple]:
    """(brand domain, action, status) per result — the readable divergence."""
    return [((e.get("brand") or {}).get("domain"), e.get("action"), e.get("status")) for e in wire]


def _persisted_applied_state(tenant_id: str) -> list[dict]:
    """Every persisted account's status plus each field a provisioning re-sync can apply.

    The field set is DERIVED from ``_FIELD_POLICY`` rather than named: naming it
    would make this the third hand-maintained field list, which is the shape of
    the bug these tests grade.
    """
    from src.core.database.repositories.uow import AccountUoW
    from src.core.tools.accounts import _FIELD_POLICY, _disposition

    applied = [f for f in _FIELD_POLICY if _disposition(f, "provisioning").kind == "applied"]
    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        return [
            {"account_id": row.account_id, "status": row.status, **{f: getattr(row, f) for f in applied}}
            for row in uow.accounts.list_all()
        ]


class TestDryRunPreviewMatchesLiveRun:
    """BR-RULE-062: a dry_run preview must describe an outcome a real run produces.

    Each case runs the SAME payload twice — once with ``dry_run=True``, once live,
    in separate tenants — and grades the preview with the LIVE RUN AS THE ORACLE.
    Nothing here hand-writes what the preview "should" say; a hand-written
    expectation would merely re-encode today's behaviour as a contract.

    Spec (adcp 6.6 / AdCP 3.1.1): ``dist/schemas/3.1.1/account/
    sync-accounts-request.json#/properties/dry_run`` — "When true, preview what
    would change without applying. Returns what would be created/updated/
    **deactivated**"; ``#/properties/delete_missing`` — "accounts previously
    synced by this agent but not included in this request will be deactivated".
    Deactivation is the ONLY thing ``delete_missing`` does, so a preview that
    omits it does not return "what would be deactivated".
    Conformance storyboard: UNGRADED — neither ``dry_run`` nor ``delete_missing``
    appears anywhere in ``dist/compliance/3.1.1`` (198 scenario files scanned).
    """

    @pytest.mark.asyncio
    async def test_delete_missing_previews_the_closures(self, integration_db):
        """(a) The reproduction: the whole delete_missing block is skipped under dry_run.

        ``accounts.py`` guards it with ``if delete_missing and not dry_run:``, so a
        buyer who runs delete_missing=true with dry_run=true — precisely to see
        WHICH of their accounts would close before doing it for real — gets a
        response that mentions none of them.
        """
        seed = [_entry("acme.com"), _entry("beta.com")]
        payload = [_entry("acme.com")]

        preview, live = await _preview_and_live("dm", seed=seed, accounts=payload, delete_missing=True)

        # Precondition: a real run closes beta.com and says so.
        assert ("beta.com", "updated", "closed") in _summary(live), (
            f"precondition: the live run must report the closure, got {_summary(live)}"
        )
        _assert_preview_matches_live(preview, live)

    @pytest.mark.asyncio
    async def test_delete_missing_preview_closes_nothing(self, integration_db):
        """A preview that gains the closure entries must still write nothing."""
        from src.core.database.repositories.uow import AccountUoW

        with AccountSyncEnv(tenant_id="dro_dmp", principal_id="ag_dro_dmp") as env:
            env.setup_default_data()
            await env.call_impl_async(req=SyncAccountsRequest(accounts=[_entry("acme.com"), _entry("beta.com")]))
            await env.call_impl_async(
                req=SyncAccountsRequest(accounts=[_entry("acme.com")], delete_missing=True, dry_run=True)
            )

        with AccountUoW("dro_dmp") as uow:
            assert uow.accounts is not None
            statuses = {a.brand.domain: a.status for a in uow.accounts.list_all()}
        assert statuses == {"acme.com": "active", "beta.com": "active"}, (
            f"dry_run must not close anything, got {statuses}"
        )

    @pytest.mark.asyncio
    async def test_repeated_update_of_existing_account_previews_the_live_outcome(self, integration_db):
        """(b) A second entry on a PRE-EXISTING key resolves against stale state.

        Live, entry 1's write lands on the row entry 2 then reads, so entry 2 has
        nothing left to change. The preview compares BOTH entries against the same
        persisted row — the in-request memory added for previewed CREATES is never
        populated for a key that already has a row — so it reports the change twice.
        """
        seed = [_entry("acme.com", billing="operator")]
        payload = [_entry("acme.com", billing="advertiser"), _entry("acme.com", billing="advertiser")]

        preview, live = await _preview_and_live("ru", seed=seed, accounts=payload)

        assert [a for _, a, _ in _summary(live)] == ["updated", "unchanged"], (
            f"precondition: live must resolve entry 2 against entry 1's write, got {_summary(live)}"
        )
        _assert_preview_matches_live(preview, live)

    @pytest.mark.asyncio
    async def test_third_entry_resolves_against_what_the_second_left(self, integration_db):
        """Entry 3 must be graded against entry 2's state, not the seeded row."""
        seed = [_entry("acme.com", billing="operator")]
        payload = [
            _entry("acme.com", billing="advertiser"),
            _entry("acme.com", billing="operator"),
            _entry("acme.com", billing="operator"),
        ]

        preview, live = await _preview_and_live("th", seed=seed, accounts=payload)

        assert [a for _, a, _ in _summary(live)] == ["updated", "updated", "unchanged"], (
            f"precondition: live must chain all three entries onto one row, got {_summary(live)}"
        )
        _assert_preview_matches_live(preview, live)

    @pytest.mark.asyncio
    async def test_single_entry_update_previews_the_state_it_would_leave(self, integration_db):
        """The narrowest case: ONE entry updating ONE persisted account.

        No duplicates, no delete_missing — this isolates whether the preview echoes
        the state the write WOULD leave or the state that is persisted now.
        """
        seed = [_entry("acme.com", billing="operator")]
        payload = [_entry("acme.com", billing="advertiser")]

        preview, live = await _preview_and_live("se", seed=seed, accounts=payload)

        assert [a for _, a, _ in _summary(live)] == ["updated"], (
            f"precondition: live must update the seeded account, got {_summary(live)}"
        )
        _assert_preview_matches_live(preview, live)

    @pytest.mark.asyncio
    async def test_update_preview_persists_nothing(self, integration_db):
        """The update preview builds a SECOND kind of ORM row — pin that none of it lands.

        Two ways this path could write, and both surface as a changed persisted
        account: the never-added seed row reaching a flush, or the in-memory
        "apply the changes" loop landing on the LOADED row, which the UoW commits
        on clean exit. Companion to ``test_duplicate_key_dry_run_persists_nothing``,
        which pins the same thing for the CREATE preview.
        """
        with AccountSyncEnv(tenant_id="dro_upn", principal_id="ag_dro_upn") as env:
            env.setup_default_data()
            await env.call_impl_async(req=SyncAccountsRequest(accounts=[_entry("acme.com", billing="operator")]))
            before = _persisted_applied_state("dro_upn")

            await env.call_impl_async(
                req=SyncAccountsRequest(accounts=[_entry("acme.com", billing="advertiser")], dry_run=True)
            )
            after = _persisted_applied_state("dro_upn")

        assert before, "precondition: the seed sync must have persisted an account to preview against"
        assert after == before, f"dry_run changed persisted state:\n  BEFORE {before}\n  AFTER  {after}"

    @pytest.mark.asyncio
    async def test_distinct_natural_keys_are_not_collapsed(self, integration_db):
        """Control: the fix must not over-collapse two genuinely distinct accounts."""
        payload = [_entry("one.com"), _entry("two.com")]

        preview, live = await _preview_and_live("dk", accounts=payload)

        assert [a for _, a, _ in _summary(live)] == ["created", "created"], (
            f"precondition: distinct natural keys are distinct accounts, got {_summary(live)}"
        )
        _assert_preview_matches_live(preview, live)
