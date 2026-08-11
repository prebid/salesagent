"""Regression: AccountSyncEnv must genuinely dispatch list_accounts per transport.

Core invariant (salesagent-oyiv.15): a scenario's wire body may only ever hold
bytes that a REAL transport produced for the tool the scenario names. A step
that cannot dispatch the right tool must fail loudly — never substitute the
production serializer's output under a real transport label.

AccountSyncEnv is the harness env three UC-011 sites run under while sending
``list_accounts`` requests (the sandbox filter, the unfiltered list including
its DB-error-injection branch, and the ``@T-UC-011-ext-g-echo`` outline's
list row). Until this ticket, the env had no list capability at all: every
``list_accounts`` request it received was either answered by ``sync_accounts``
(the outline row and the error-injection branch) or short-circuited by a
TRANSPORT-BYPASS that called ``_list_accounts_impl`` directly and stashed
``resp.model_dump(mode="json")`` as a fake wire. Both make the three
transport parametrizations grade the same bytes, so no transport-specific
regression could ever fail them.

These tests pin the opposite: the list tool answers on every transport, the
captured wire is the transport's own list body, routing does not leak between
the two tools, and breaking ONE transport's list path fails exactly that
transport.

beads: salesagent-oyiv.15
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.core.schemas.account import (
    ListAccountsRequest,
    ListAccountsResponse,
    SyncAccountsRequest,
    SyncAccountsResponse,
)
from tests.harness import Transport
from tests.harness.account_sync import AccountSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]
# Transports that serialize a real body. IMPL has no wire by definition, so it
# is excluded from every wire_response assertion below.
WIRE_TRANSPORTS = [Transport.A2A, Transport.REST, Transport.MCP]


def _seed_sandbox_and_production_accounts(env: AccountSyncEnv) -> dict[str, str]:
    """Grant the env's agent one sandbox and one production account.

    Mirrors the Given step the three affected scenarios share
    (``given_sandbox_and_production_accounts``, uc011_accounts.py) so these
    tests grade the same data those scenarios do.

    Returns {"sandbox": account_id, "production": account_id}.
    """
    from tests.factories import AccountFactory, AgentAccountAccessFactory

    tenant, principal = env.setup_default_data()
    created = {}
    for label, sandbox in (("sandbox", True), ("production", False)):
        account = AccountFactory(tenant=tenant, status="active", sandbox=sandbox)
        AgentAccountAccessFactory(
            tenant_id=tenant.tenant_id,
            principal=principal,
            account=account,
        )
        created[label] = account.account_id
    return created


def _wire_account_ids(wire: dict[str, Any]) -> set[str]:
    return {account["account_id"] for account in wire["accounts"]}


class TestAccountSyncEnvDispatchesListRequests:
    """A ListAccountsRequest under AccountSyncEnv must be answered by list_accounts."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unfiltered_list_reaches_the_list_tool(self, integration_db, transport):
        with AccountSyncEnv(
            tenant_id=f"asl_unfilt_{transport.value}",
            principal_id=f"agent_asl_unfilt_{transport.value}",
        ) as env:
            created = _seed_sandbox_and_production_accounts(env)
            result = env.call_via(transport, req=ListAccountsRequest())

        assert result.is_success, f"list_accounts must succeed on {transport.value}: {result.error!r}"
        assert isinstance(result.payload, ListAccountsResponse), (
            f"{transport.value} answered a ListAccountsRequest with {type(result.payload).__name__} — "
            "the env dispatched the wrong tool"
        )
        assert {account.account_id for account in result.payload.accounts} == set(created.values())

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_sandbox_filter_crosses_the_wire_and_is_applied(self, integration_db, transport):
        """The request's own fields must reach the list tool, not just the tool name."""
        with AccountSyncEnv(
            tenant_id=f"asl_sbox_{transport.value}",
            principal_id=f"agent_asl_sbox_{transport.value}",
        ) as env:
            created = _seed_sandbox_and_production_accounts(env)
            result = env.call_via(transport, req=ListAccountsRequest(sandbox=True))

        assert result.is_success, f"sandbox-filtered list must succeed on {transport.value}: {result.error!r}"
        assert isinstance(result.payload, ListAccountsResponse), (
            f"{transport.value} answered a sandbox-filtered ListAccountsRequest with "
            f"{type(result.payload).__name__} — the env dispatched the wrong tool"
        )
        assert {account.account_id for account in result.payload.accounts} == {created["sandbox"]}

    @pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_captured_wire_is_the_transports_own_list_body(self, integration_db, transport):
        """wire_response must be a list-shaped body the transport actually produced.

        The deleted bypass stashed ``ListAccountsResponse.model_dump()`` under a
        real transport label, which satisfies ``wire_dict``'s presence guard while
        proving nothing about the transport. Requiring the accounts the DB holds —
        and the absence of sync's per-account ``action`` key — pins that the body
        came from a list dispatch on this transport.
        """
        with AccountSyncEnv(
            tenant_id=f"asl_wire_{transport.value}",
            principal_id=f"agent_asl_wire_{transport.value}",
        ) as env:
            created = _seed_sandbox_and_production_accounts(env)
            result = env.call_via(transport, req=ListAccountsRequest())

        assert result.is_success, f"list_accounts must succeed on {transport.value}: {result.error!r}"
        wire = result.wire_response
        assert wire is not None, f"{transport.value} captured no wire body for a list_accounts dispatch"
        assert _wire_account_ids(wire) == set(created.values())
        for account in wire["accounts"]:
            assert "action" not in account, (
                f"{transport.value} wire carries sync_accounts' per-account 'action' — "
                "the sync tool answered a list request"
            )


class TestAccountSyncEnvKeepsTheTwoToolsApart:
    """Routing a list request must not disturb the env's sync dispatch, and vice versa."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_sync_after_list_still_reaches_the_sync_tool(self, integration_db, transport):
        """Pins 'every entry point sets the routing flag; nothing resets it'.

        A flag set on the list request and left stale (or reset at the wrong
        moment) misroutes the next sync request on the same env — the REST
        endpoint property is the concrete carrier of that risk.
        """
        sync_req = SyncAccountsRequest(
            accounts=[{"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "agent"}],
        )
        with AccountSyncEnv(
            tenant_id=f"asl_mix_{transport.value}",
            principal_id=f"agent_asl_mix_{transport.value}",
        ) as env:
            _seed_sandbox_and_production_accounts(env)
            list_result = env.call_via(transport, req=ListAccountsRequest())
            sync_result = env.call_via(transport, req=sync_req)
            relist_result = env.call_via(transport, req=ListAccountsRequest())

        assert list_result.is_success, f"list must succeed on {transport.value}: {list_result.error!r}"
        assert isinstance(list_result.payload, ListAccountsResponse)

        assert sync_result.is_success, f"sync after list must succeed on {transport.value}: {sync_result.error!r}"
        assert isinstance(sync_result.payload, SyncAccountsResponse), (
            f"{transport.value} answered a SyncAccountsRequest with "
            f"{type(sync_result.payload).__name__} after a list request"
        )

        assert relist_result.is_success, f"list after sync must succeed on {transport.value}: {relist_result.error!r}"
        assert isinstance(relist_result.payload, ListAccountsResponse), (
            f"{transport.value} answered a ListAccountsRequest with "
            f"{type(relist_result.payload).__name__} after a sync request"
        )

    @pytest.mark.asyncio
    async def test_call_impl_async_rejects_a_list_request_loudly(self, integration_db):
        """The async entry point serves sync only — a list request must name call_impl.

        ``call_impl_async`` awaits ``_sync_accounts_impl``; handing it a
        ListAccountsRequest is a misdispatch, and a silent one is exactly the
        failure mode this ticket exists to remove.
        """
        with AccountSyncEnv(tenant_id="asl_async_guard", principal_id="agent_asl_async") as env:
            _seed_sandbox_and_production_accounts(env)

            with pytest.raises(RuntimeError, match="call_impl"):
                await env.call_impl_async(req=ListAccountsRequest())


class TestAccountSyncEnvListDispatchIsTransportSpecific:
    """Falsification check: breaking ONE transport's list path fails only that transport.

    This is the property the fake wire destroyed — with every parametrization
    grading the same serializer output, no per-transport regression could be
    detected. Breaking the A2A list skill handler must fail A2A and leave
    REST and MCP green.
    """

    def test_breaking_the_a2a_list_skill_fails_only_a2a(self, integration_db):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        async def _broken_list_skill(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("regression probe: A2A list skill deliberately broken")

        with AccountSyncEnv(tenant_id="asl_isolation", principal_id="agent_asl_iso") as env:
            created = _seed_sandbox_and_production_accounts(env)

            baseline = env.call_via(Transport.A2A, req=ListAccountsRequest())

            with patch.object(AdCPRequestHandler, "_handle_list_accounts_skill", _broken_list_skill):
                broken_a2a = env.call_via(Transport.A2A, req=ListAccountsRequest())
                rest = env.call_via(Transport.REST, req=ListAccountsRequest())
                mcp = env.call_via(Transport.MCP, req=ListAccountsRequest())

        assert baseline.is_success, f"A2A list must be live before the probe: {baseline.error!r}"

        assert broken_a2a.is_error, (
            "breaking the A2A list skill did not fail the A2A parametrization — "
            "A2A is not dispatching list_accounts through its own pipeline"
        )
        assert rest.is_success, f"REST must be unaffected by an A2A-only break: {rest.error!r}"
        assert isinstance(rest.payload, ListAccountsResponse)
        assert {account.account_id for account in rest.payload.accounts} == set(created.values())
        assert mcp.is_success, f"MCP must be unaffected by an A2A-only break: {mcp.error!r}"
        assert isinstance(mcp.payload, ListAccountsResponse)
        assert {account.account_id for account in mcp.payload.accounts} == set(created.values())
