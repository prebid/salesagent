"""Integration tests for _sync_governance (UC-030, #1329) with real PostgreSQL.

Verifies the seller-side governance-binding contract end-to-end against a real
DB: authority check (the normative MUST) -> url-only persistence (replace
semantics) on the accounts.governance_agents column -> synced/failed results,
plus a REST wire-path roundtrip.

Idempotency replay / IDEMPOTENCY_CONFLICT and the full UC-030 boundary matrix
are the richer BDD ledger (deferred follow-up); these tests pin the working
tool the capabilities honesty pass depends on.
"""

from __future__ import annotations

import pytest

from src.core.schemas.account import SyncGovernanceRequest
from tests.factories import AccountFactory, TenantFactory
from tests.harness.governance_sync import GovernanceSyncEnv
from tests.harness.transport import Transport, _pinned_error_metadata
from tests.helpers.accounts import seed_account_with_access
from tests.helpers.governance import (
    BEARER_CREDS,
    GOV_URL,
    LEAK_SECRET,
    account_entry,
    governance_agent_dict,
    governance_request,
    leaky_governance_agent,
    persisted_governance_agents_raw,
    persisted_governance_urls,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

GOV_URL_2 = "https://governance.new-buyer.com"

# Expected recovery on the uniform ACCOUNT_NOT_FOUND per-account error, derived from
# the pinned spec enum (the authority), not a copied literal (#1329).
_ACCOUNT_NOT_FOUND_RECOVERY = _pinned_error_metadata()["ACCOUNT_NOT_FOUND"]["recovery"]


def _request(
    account_ref: dict, url: str = GOV_URL, key: str = "uuid-v4-int-000000000000000001"
) -> SyncGovernanceRequest:
    # Delegates to the shared single-account request builder (#1329) so this suite and the
    # unit/BDD suites construct the pinned request MODEL one way.
    return governance_request(account_ref=account_ref, url=url, idempotency_key=key)


class TestSyncGovernancePersistence:
    """Authority-gated persistence: synced accounts store the binding url-only."""

    @pytest.mark.asyncio
    async def test_owned_account_synced_and_persisted_url_only(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t1", principal_id="gov_agent1") as env:
            tenant, principal = env.setup_default_data()
            seed_account_with_access(tenant, principal, account_id="acc_gov_1")

            resp = await env.call_impl_async(req=_request({"account_id": "acc_gov_1"}))

        assert resp.accounts[0].status == "synced"
        # The echoed agent is the SDK CoreGovernanceAgent whose url is an AnyUrl (Pattern #1),
        # so stringify for the exact normalized comparison (#1329).
        assert str(resp.accounts[0].governance_agents[0].url) == GOV_URL + "/"
        # Persisted url-only (credentials are never stored — column model is url-only).
        persisted = persisted_governance_urls("gov_t1", "acc_gov_1")
        assert len(persisted) == 1
        assert persisted[0] == GOV_URL + "/"
        # Assert the RAW STORED JSON has exactly one key, `url`. Reading through the ORM
        # (above) re-coerces to the url-only column model, so a leaked credential would
        # RAISE on read rather than fail this assertion — the raw JSONB read makes the
        # strip assertion actually execute against what is on disk (#1329).
        raw = persisted_governance_agents_raw("gov_t1", "acc_gov_1")
        assert raw == [{"url": GOV_URL + "/"}], f"raw stored governance agent must be url-only, got {raw}"

    @pytest.mark.asyncio
    async def test_replace_semantics_overwrites_prior_binding(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t2", principal_id="gov_agent2") as env:
            tenant, principal = env.setup_default_data()
            seed_account_with_access(tenant, principal, account_id="acc_gov_2")

            await env.call_impl_async(req=_request({"account_id": "acc_gov_2"}, url=GOV_URL))
            # Second sync with a different agent replaces the first.
            resp = await env.call_impl_async(
                req=_request({"account_id": "acc_gov_2"}, url=GOV_URL_2, key="uuid-v4-int-000000000000000002")
            )

        assert resp.accounts[0].status == "synced"
        persisted = persisted_governance_urls("gov_t2", "acc_gov_2")
        assert len(persisted) == 1
        assert persisted[0] == GOV_URL_2 + "/"


class TestSyncGovernanceAuthority:
    """The normative MUST: unknown/unowned accounts fail per-account, no persistence."""

    @pytest.mark.asyncio
    async def test_unknown_account_fails_account_not_found(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t3", principal_id="gov_agent3") as env:
            env.setup_default_data()

            resp = await env.call_impl_async(req=_request({"account_id": "acc_does_not_exist"}))

        assert resp.accounts[0].status == "failed"
        assert resp.accounts[0].errors[0].code == "ACCOUNT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_existing_but_unowned_account_fails_account_not_found(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t4", principal_id="gov_agent4") as env:
            tenant, _principal = env.setup_default_data()
            # Account exists in the tenant but the agent has NO AgentAccountAccess grant.
            AccountFactory(tenant=tenant, account_id="acc_unowned")

            resp = await env.call_impl_async(req=_request({"account_id": "acc_unowned"}))

        assert resp.accounts[0].status == "failed"
        # Existing-but-unowned is collapsed to the SAME ACCOUNT_NOT_FOUND result as a
        # nonexistent account (uniform response → no cross-principal enumeration
        # oracle). NOT SCOPE_INSUFFICIENT (a task-scope code this seller does not
        # model). #1329.
        err = resp.accounts[0].errors[0]
        assert err.code == "ACCOUNT_NOT_FOUND"
        assert err.recovery == _ACCOUNT_NOT_FOUND_RECOVERY
        # Uniform generic message — must NOT reveal the account exists via the
        # authorization-specific "does not have access to account 'X'" phrasing.
        assert "does not have access" not in err.message
        # No binding persisted on a failed account.
        assert persisted_governance_urls("gov_t4", "acc_unowned") == []

    @pytest.mark.asyncio
    async def test_cross_tenant_account_fails_account_not_found(self, integration_db):
        # Account lives in tenant B; the agent authenticates in tenant A. The sync
        # is scoped to the agent's tenant (AccountUoW(tenant_id) → tenant-filtered
        # repo), so the account is unresolvable there → ACCOUNT_NOT_FOUND, with no
        # persistence and no cross-tenant existence leak (#1329).
        with GovernanceSyncEnv(tenant_id="gov_ta", principal_id="gov_agent_a") as env:
            env.setup_default_data()
            tenant_b = TenantFactory(tenant_id="gov_tb")
            AccountFactory(tenant=tenant_b, account_id="acc_in_b")

            resp = await env.call_impl_async(req=_request({"account_id": "acc_in_b"}))

        assert resp.accounts[0].status == "failed"
        assert resp.accounts[0].errors[0].code == "ACCOUNT_NOT_FOUND"
        # The account was NOT touched in tenant B.
        assert persisted_governance_urls("gov_tb", "acc_in_b") == []

    @pytest.mark.asyncio
    async def test_natural_key_ambiguous_fails_account_ambiguous(self, integration_db):
        """Covers the ACCOUNT_AMBIGUOUS branch: a natural key matching several of the
        caller's OWN accounts (scoped to accessible — no oracle) fails per-account
        correctable, not synced. Last round's ambiguous fix was 0%-covered (#1329).
        """
        with GovernanceSyncEnv(tenant_id="gov_amb", principal_id="gov_agent_amb") as env:
            tenant, principal = env.setup_default_data()
            # Two OWNED accounts sharing one natural key (operator + brand.domain + sandbox).
            for aid in ("acc_amb_1", "acc_amb_2"):
                seed_account_with_access(
                    tenant, principal, account_id=aid, operator="pinnacle.com", brand_domain="spark", sandbox=False
                )
            ref = {"brand": {"domain": "spark"}, "operator": "pinnacle.com", "sandbox": False}
            resp = await env.call_impl_async(
                req=SyncGovernanceRequest(
                    idempotency_key="uuid-v4-int-000000000000000amb",
                    accounts=[account_entry(ref, agents=[governance_agent_dict(GOV_URL)])],
                )
            )

        assert resp.accounts[0].status == "failed"
        err = resp.accounts[0].errors[0]
        assert err.code == "ACCOUNT_AMBIGUOUS"
        assert err.recovery == _pinned_error_metadata()["ACCOUNT_AMBIGUOUS"]["recovery"]
        # No binding persisted on a failed account.
        assert persisted_governance_urls("gov_amb", "acc_amb_1") == []

    @pytest.mark.asyncio
    async def test_status_blocked_account_fails_with_status_code(self, integration_db):
        """Covers the ``except AdCPError`` fallthrough: an OWNED but status-blocked account
        surfaces the resolver's own (canonical) code + recovery — an honest per-account
        failure, not a silent success. Last round's fallthrough was 0%-covered (#1329).
        """
        with GovernanceSyncEnv(tenant_id="gov_susp", principal_id="gov_agent_susp") as env:
            tenant, principal = env.setup_default_data()
            seed_account_with_access(tenant, principal, account_id="acc_susp", status="suspended")

            resp = await env.call_impl_async(req=_request({"account_id": "acc_susp"}))

        assert resp.accounts[0].status == "failed"
        err = resp.accounts[0].errors[0]
        # ACCOUNT_SUSPENDED is a canonical pinned code; recovery agrees by construction.
        assert err.code == "ACCOUNT_SUSPENDED"
        assert err.recovery == _pinned_error_metadata()["ACCOUNT_SUSPENDED"]["recovery"]
        assert persisted_governance_urls("gov_susp", "acc_susp") == []


class TestSyncGovernanceCrossTransportWire:
    """Happy-path synced/url-echo/no-credentials shape on the real MCP + A2A + REST wire.

    Grades the synced shape (status, echoed url, NO credential echo) on the actual
    serialized body across all three wire transports — matching the [MCP, A2A, REST]
    parametrize of the capabilities cross-transport sibling it mirrors
    (test_get_adcp_capabilities_wire). REST joins here (rather than a standalone test that
    asserted on a re-parsed model and skipped the whole-envelope credential scan) so the
    same assertions hold identically on every transport (#1329).
    """

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_happy_path_synced_wire(self, transport, integration_db):
        tid = f"gov_wire_{transport.value}"
        with GovernanceSyncEnv(tenant_id=tid, principal_id=f"{tid}_agent") as env:
            tenant, principal = env.setup_default_data()
            seed_account_with_access(tenant, principal, account_id="acc_wire")

            result = env.call_via(
                transport,
                idempotency_key="uuid-v4-wire-0000000000000001",
                accounts=[account_entry({"account_id": "acc_wire"}, agents=[governance_agent_dict(GOV_URL)])],
            )

        assert result.is_success, f"{transport}: expected success, got {result.error!r}"
        assert result.wire_response is not None, f"{transport}: env did not stash success-path wire"
        accounts = result.wire_response.get("accounts") or []
        assert len(accounts) == 1, f"{transport}: expected 1 account on the wire, got {accounts}"
        acct = accounts[0]
        assert acct["status"] == "synced"
        agents = acct.get("governance_agents") or []
        assert agents and agents[0].get("url") == GOV_URL + "/", f"{transport}: url not echoed: {agents}"
        # Credentials are write-only — the wire echo MUST NOT carry authentication.
        assert "authentication" not in agents[0], f"{transport}: credentials echoed on wire: {agents[0]}"
        assert BEARER_CREDS not in str(result.wire_response), f"{transport}: credentials leaked on wire"

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_context_echoed_on_wire(self, transport, integration_db):
        """The application ``context`` is echoed unchanged on the wire (what the specialism
        storyboards grade). Previously exercised by zero tests — no test sent a context
        (#1329). ContextObject allows extra fields, so a conversation id round-trips.
        """
        tid = f"gov_ctx_{transport.value}"
        with GovernanceSyncEnv(tenant_id=tid, principal_id=f"{tid}_agent") as env:
            tenant, principal = env.setup_default_data()
            seed_account_with_access(tenant, principal, account_id="acc_ctx")

            result = env.call_via(
                transport,
                idempotency_key="uuid-v4-ctx-00000000000000001",
                context={"conversation_id": "conv-gov-xyz"},
                accounts=[account_entry({"account_id": "acc_ctx"}, agents=[governance_agent_dict(GOV_URL)])],
            )

        assert result.is_success, f"{transport}: expected success, got {result.error!r}"
        echoed = (result.wire_response or {}).get("context") or {}
        assert echoed.get("conversation_id") == "conv-gov-xyz", f"{transport}: context not echoed: {echoed}"


class TestSyncGovernanceCredentialRedactionWire:
    """A credential-bearing ``extra_forbidden`` rejection emits CREDENTIAL_IN_ARGS + hides the secret.

    A credential smuggled into a request arg (here a mistyped ``authentication.credential`` extra
    field carrying a 32+ char secret) is rejected with the pinned ``CREDENTIAL_IN_ARGS`` code
    (recovery=terminal — auto-retry re-logs the credential; @source authentication.mdx L2), and the
    envelope MUST NOT echo the secret. This is the error-path mirror of the success-echo credential
    grade at ``test_happy_path_synced_wire`` (#1329).

    Parametrized over A2A + REST + MCP: the MCP compat middleware routes a TypeAdapter rejection
    through the SAME ``adcp_validation_error_from`` path as A2A/REST, so the credential-in-args
    detection + redaction hold on the MCP wire for the right reason too — reverting the
    CREDENTIAL_IN_ARGS routing reddens all three (verified by mutation).
    """

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_credential_bearing_extra_field_rejected_credential_in_args(self, transport, integration_db):
        tid = f"gov_redact_{transport.value}"
        with GovernanceSyncEnv(tenant_id=tid, principal_id=f"{tid}_agent") as env:
            tenant, principal = env.setup_default_data()
            seed_account_with_access(tenant, principal, account_id="acc_redact")

            # `credential` (singular) is not in the Authentication schema -> extra_forbidden; it
            # carries a 32+ char secret. A credential-bearing extra field is a credential-in-args:
            # the boundary rejects it with CREDENTIAL_IN_ARGS (terminal) AND redacts the echoed
            # value. The shared builder + LEAK_SECRET are the same the BDD leak scenario uses, so a
            # length-sensitive redaction regression cannot redden one suite and not the other (#1329).
            leaky_agent = leaky_governance_agent("extra-authentication-key")
            result = env.call_via(
                transport,
                idempotency_key="uuid-v4-redact-0000000000001",
                accounts=[account_entry({"account_id": "acc_redact"}, agents=[leaky_agent])],
            )

        assert result.is_error, f"{transport}: expected a credential-in-args rejection, got {result.payload!r}"
        # recovery=terminal is pin-defaulted from the enum; field is the detection path only.
        result.assert_wire_error("CREDENTIAL_IN_ARGS", field_substr="governance_agents[0]")
        result.assert_secret_absent(LEAK_SECRET)


# TestSyncGovernanceRestWire (REST happy-path roundtrip) was folded into
# TestSyncGovernanceCrossTransportWire.test_happy_path_synced_wire, which now parametrizes
# [MCP, A2A, REST] and asserts on the real serialized body + the whole-envelope credential
# scan on every transport — a strict superset of the deleted test (#1329).
