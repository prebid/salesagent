"""Integration tests for sprint 1.6 piece B: agent-aware natural-key match
+ ``pending_provision`` status on new GAM-tenant accounts.

Covers:
- ``billing=operator``: same (operator, brand, sandbox) → one shared Account
  regardless of which buyer agent calls (today's behavior, regression
  guard).
- ``billing=agent``: same (operator, brand, sandbox) but different calling
  principals → two distinct Accounts.
- New account on a GAM tenant lands in ``pending_provision`` status
  (advertiser map happens at first-buy or via Tenant Mgmt API).
- New account on a Mock tenant lands in ``active`` (no provisioning).
- Sandbox account on a GAM tenant lands in ``active`` (sandbox advertiser
  is wired at first-buy, not via the pending_provision path).

``SyncAccountsRequest`` is built with an explicit ``idempotency_key`` to
dodge a pre-existing SDK-drift requirement (#49). ``_build_sync_result``
is patched out via ``MagicMock`` for the same reason — the library
``Account`` response type now requires ``account_id`` which the salesagent
impl doesn't yet pass through. The DB writes happen before the response
construction, so we assert against DB state directly, not the response.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Principal, Tenant
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import SyncAccountsRequest
from src.core.testing_hooks import AdCPTestContext
from src.core.tools import accounts as accounts_module

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture(autouse=True)
def _ensure_integration_db(integration_db):
    """Ensure every test in this file goes through the per-test DB setup.

    Without this autouse hookup, the ``requires_db`` marker is a label only —
    pytest never invokes the ``integration_db`` fixture, so when ``DATABASE_URL``
    is absent the tests crash on ``get_db_session()`` instead of skipping cleanly
    like sibling files (e.g., ``test_sync_accounts.py``).
    """
    yield


@pytest.fixture(autouse=True)
def _stub_response_builder(monkeypatch):
    """Stub ``_build_sync_result`` to dodge the pre-existing library Account
    schema-drift (#49). DB state is what we assert against."""
    monkeypatch.setattr(accounts_module, "_build_sync_result", MagicMock(return_value=MagicMock(action="created")))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant(ad_server: str = "google_ad_manager") -> str:
    """Create a tenant with the given ad_server. Returns tenant_id."""
    tid = f"t_premap_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.add(
            Tenant(
                tenant_id=tid,
                name=f"Pre-map Test {tid}",
                subdomain=tid,
                ad_server=ad_server,
                is_active=True,
                billing_plan="standard",
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_format_ids=[],
                policy_settings={},
                supported_billing=["operator", "agent"],
            )
        )
        session.commit()
    return tid


def _identity(
    tenant_id: str,
    principal_id: str,
    *,
    ad_server: str = "google_ad_manager",
    account_approval_mode: str = "auto",
    supported_billing: list[str] | None = None,
) -> ResolvedIdentity:
    """Build a ResolvedIdentity that mirrors what the auth chain produces."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={
            "tenant_id": tenant_id,
            "ad_server": ad_server,
            "account_approval_mode": account_approval_mode,
            "supported_billing": supported_billing or ["operator", "agent"],
        },
        protocol="mcp",
        testing_context=AdCPTestContext(),
    )


def _ensure_principal(tenant_id: str, principal_id: str) -> None:
    """Create a Principal row for ``principal_id`` so AgentAccountAccess
    grants don't fail the FK to ``principals``."""
    with get_db_session() as session:
        existing = session.get(Principal, (tenant_id, principal_id))
        if existing is None:
            session.add(
                Principal(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    name=f"Test Principal {principal_id}",
                    platform_mappings={"mock": {"advertiser_id": "test"}},
                    access_token=f"tok_{uuid.uuid4().hex[:12]}",
                )
            )
            session.commit()


def _sync(
    *,
    tenant_id: str,
    principal_id: str,
    accounts: list[dict[str, Any]],
    ad_server: str = "google_ad_manager",
) -> None:
    """Run _sync_accounts_impl with the given identity+accounts.

    Swallows the SyncAccountsResponse construction error (#49 SDK drift)
    after the DB writes commit — these tests assert against DB state.
    """
    _ensure_principal(tenant_id, principal_id)
    req = SyncAccountsRequest(
        accounts=accounts,
        idempotency_key=f"idem_{uuid.uuid4().hex[:12]}",
    )
    identity = _identity(tenant_id, principal_id, ad_server=ad_server)
    try:
        asyncio.run(accounts_module._sync_accounts_impl(req, identity))
    except Exception as exc:  # pragma: no cover - intentional drift bypass
        if "SyncAccountsResponse" not in repr(exc) and "Account" not in repr(exc):
            raise


def _account_count(tenant_id: str, **filters: Any) -> int:
    with get_db_session() as session:
        stmt = select(Account).where(Account.tenant_id == tenant_id)
        for k, v in filters.items():
            stmt = stmt.where(getattr(Account, k) == v)
        return len(list(session.scalars(stmt).all()))


def _accounts(tenant_id: str) -> list[Account]:
    with get_db_session() as session:
        return list(session.scalars(select(Account).where(Account.tenant_id == tenant_id)).all())


# ---------------------------------------------------------------------------
# Agent-aware natural-key match
# ---------------------------------------------------------------------------


class TestNaturalKeyAgentScope:
    def test_operator_billed_shared_across_agents(self):
        """billing=operator: same key from two agents → one row (today's behavior)."""
        tid = _make_tenant()
        entry = {
            "brand": {"domain": "cocacola.com"},
            "operator": "accuweather.com",
            "billing": "operator",
        }

        _sync(tenant_id=tid, principal_id="agent-A", accounts=[entry])
        _sync(tenant_id=tid, principal_id="agent-B", accounts=[entry])

        assert _account_count(tid) == 1

    def test_agent_billed_separated_per_agent(self):
        """billing=agent: same (operator, brand) from two agents → two rows."""
        tid = _make_tenant()
        entry = {
            "brand": {"domain": "cocacola.com"},
            "operator": "accuweather.com",
            "billing": "agent",
        }

        _sync(tenant_id=tid, principal_id="agent-A", accounts=[entry])
        _sync(tenant_id=tid, principal_id="agent-B", accounts=[entry])

        accounts = _accounts(tid)
        assert len(accounts) == 2
        principals = {a.principal_id for a in accounts}
        assert principals == {"agent-A", "agent-B"}

    def test_agent_billed_idempotent_for_same_agent(self):
        """billing=agent: same agent calling twice with same key → one row.

        DB state assertion only — the SyncAccountsResponse build error
        from #49 is swallowed by ``_sync``, so we can't inspect ``action``
        on the response object. The single row is what matters.
        """
        tid = _make_tenant()
        entry = {
            "brand": {"domain": "cocacola.com"},
            "operator": "accuweather.com",
            "billing": "agent",
        }

        _sync(tenant_id=tid, principal_id="agent-A", accounts=[entry])
        _sync(tenant_id=tid, principal_id="agent-A", accounts=[entry])

        assert _account_count(tid) == 1


# ---------------------------------------------------------------------------
# pending_provision status precedence
# ---------------------------------------------------------------------------


class TestPendingProvisionStatus:
    def test_gam_tenant_new_account_lands_pending_provision(self):
        """ad_server=google_ad_manager + auto-approve + no pre-mapping → pending_provision."""
        tid = _make_tenant(ad_server="google_ad_manager")
        _sync(
            tenant_id=tid,
            principal_id="agent-A",
            accounts=[
                {
                    "brand": {"domain": "cocacola.com"},
                    "operator": "accuweather.com",
                    "billing": "operator",
                }
            ],
        )

        accounts = _accounts(tid)
        assert len(accounts) == 1
        assert accounts[0].status == "pending_provision"

    def test_mock_tenant_new_account_lands_active(self):
        """ad_server=mock → no provisioning concept → status=active."""
        tid = _make_tenant(ad_server="mock")
        _sync(
            tenant_id=tid,
            principal_id="agent-A",
            accounts=[
                {
                    "brand": {"domain": "cocacola.com"},
                    "operator": "accuweather.com",
                    "billing": "operator",
                }
            ],
            ad_server="mock",
        )

        accounts = _accounts(tid)
        assert accounts[0].status == "active"

    def test_sandbox_account_lands_active_on_gam_tenant(self):
        """Sandbox carve-out: GAM tenant + sandbox=True → status=active.

        The per-tenant sandbox advertiser is wired at first-buy (sprint 1.6
        piece C); we don't gate sandbox on pending_provision. Uses a real
        TLD (``.org``) — ``.example`` is rejected by domain validation.
        """
        tid = _make_tenant(ad_server="google_ad_manager")
        _sync(
            tenant_id=tid,
            principal_id="agent-A",
            accounts=[
                {
                    "brand": {"domain": "sandbox-test-brand.org"},
                    "operator": "accuweather.com",
                    "billing": "operator",
                    "sandbox": True,
                }
            ],
        )

        accounts = _accounts(tid)
        assert accounts[0].sandbox is True
        assert accounts[0].status == "active"

    def test_existing_pre_mapped_account_stays_active_on_sync(self):
        """Pre-mapped Account (status=active, advertiser attached) survives
        a subsequent sync_accounts call for the same natural key — the
        upsert leaves status alone when fields haven't changed."""
        tid = _make_tenant(ad_server="google_ad_manager")

        # Simulate a Tenant Management API pre-mapping.
        with get_db_session() as session:
            session.add(
                Account(
                    tenant_id=tid,
                    account_id=f"acct_{uuid.uuid4().hex[:12]}",
                    name="Pre-mapped Coca-Cola",
                    status="active",
                    operator="accuweather.com",
                    brand={"domain": "cocacola.com"},
                    billing="operator",
                    sandbox=False,
                    platform_mappings={"google_ad_manager": {"advertiser_id": "12345"}},
                )
            )
            session.commit()

        _sync(
            tenant_id=tid,
            principal_id="agent-A",
            accounts=[
                {
                    "brand": {"domain": "cocacola.com"},
                    "operator": "accuweather.com",
                    "billing": "operator",
                }
            ],
        )

        accounts = _accounts(tid)
        assert len(accounts) == 1
        assert accounts[0].status == "active"
        # Advertiser mapping survived the sync.
        assert accounts[0].platform_mappings.get("google_ad_manager", {}).get("advertiser_id") == "12345"
