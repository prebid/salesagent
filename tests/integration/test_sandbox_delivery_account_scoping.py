"""SA-006: an account-scoped delivery request must not reach buys of another account.

`get_media_buy_delivery` accepts an account reference, but target selection fetched buys
by principal only. A request scoped to a SANDBOX account could therefore pull in a LIVE
buy of the same principal — explicitly by id or by browsing — and read it through the
tenant's real ad server, defeating the sandbox guarantee through an account-scoping hole.

Driven against a real database because `_get_media_buy_delivery_impl` cannot be driven far
enough with unit mocks: it raises before the decision seam, and asserting on an empty call
list passes vacuously.

AdCP 3.1.1 ``dist/docs/3.1.1/media-buy/advanced-topics/sandbox.mdx`` §Seller
implementation: sandbox requests MUST NOT make real ad platform API calls.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.harness.transport import Transport, TransportResult
from tests.helpers.sandbox_assertions import assert_all_live, assert_all_sandbox, sandbox_modes

pytestmark = pytest.mark.requires_db


def _returned_ids(result: TransportResult) -> set[str]:
    """media_buy_ids in the response, read from the real wire body.

    Reads ``wire_response`` — the serialized bytes the buyer receives — rather than
    the typed ``payload``. Asserting on the model would grade re-serialization of an
    in-process object, so a transport that never applied the scope could still look
    correct. Fails loudly when no wire was captured instead of silently degrading.
    """
    assert not result.is_error, f"dispatch failed: {result.error!r}"
    wire = result.wire_response
    assert wire is not None, "no wire response captured — this assertion would grade nothing"
    return {d["media_buy_id"] for d in (wire.get("media_buy_deliveries") or [])}


def _seed_two_accounts(tenant, principal):
    """One sandbox account and one live account, each owning a media buy.

    Factories only — no session.add()/get_db_session() in the test body
    (CLAUDE.md §Test Fixtures, enforced by test_architecture_repository_pattern).
    """
    from tests.factories import AccountFactory, AgentAccountAccessFactory, MediaBuyFactory

    buys = {}
    for account_id, sandbox in (("acc_sbx", True), ("acc_live", False)):
        AccountFactory(tenant=tenant, account_id=account_id, sandbox=sandbox)
        # Resolution enforces agent access; without the grant the request fails
        # authorization before the scoping filter is ever reached.
        AgentAccountAccessFactory(
            tenant_id=tenant.tenant_id,
            principal_id=principal.principal_id,
            account_id=account_id,
        )
        buys[account_id] = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            account_id=account_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
    return buys["acc_sbx"], buys["acc_live"]


# Account resolution (enrich_identity_with_account) is a transport-boundary
# responsibility, so identity.account_id — which the scope keys on — is only populated
# on a real wrapper path; call_impl would grade nothing. Every wire transport is
# parametrized rather than MCP alone: the isolation guarantee is a property of the
# buyer contract, so a transport that resolved the account differently (or not at all)
# would be a live hole this file exists to catch.
_WIRE_TRANSPORTS = (Transport.MCP, Transport.A2A, Transport.REST)

# The account reference in its WIRE shape. A real buyer sends JSON, and A2A/REST carry
# kwargs through protobuf/JSON serialization, so a constructed AccountReference model
# does not survive the trip — passing one fails validation there while working
# in-process on MCP. The dict is what every transport actually receives.
_ACCOUNT_REF = {"account_id": "acc_sbx"}


class TestDeliveryAccountScoping:
    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_sandbox_scoped_request_excludes_the_live_buy(self, integration_db, transport):
        """Both buys belong to the principal; only the in-scope one may be returned."""
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        suffix = transport.value
        with DeliveryPollEnv(tenant_id=f"t_scope_{suffix}", principal_id=f"p_scope_{suffix}") as env:
            tenant = TenantFactory(tenant_id=f"t_scope_{suffix}")
            principal = PrincipalFactory(tenant=tenant, principal_id=f"p_scope_{suffix}")
            sandbox_buy, live_buy = _seed_two_accounts(tenant, principal)

            env.set_adapter_response(sandbox_buy.media_buy_id, impressions=1000)
            env.set_adapter_response(live_buy.media_buy_id, impressions=2000)

            result = env.call_via(
                transport,
                media_buy_ids=[sandbox_buy.media_buy_id, live_buy.media_buy_id],
                account=_ACCOUNT_REF,
            )

            returned = _returned_ids(result)

            # Exact-set, not just absence: `live not in returned` is also satisfied by an
            # EMPTY response, which is how an over-broad filter would slip through green.
            assert returned == {sandbox_buy.media_buy_id}, (
                f"[{suffix}] expected only the in-scope sandbox buy, got {returned}; "
                f"{live_buy.media_buy_id} present means a buy from another account was read "
                "through the tenant's real adapter, and an empty set means the filter is over-broad"
            )

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_unscoped_request_still_returns_both(self, integration_db, transport):
        """Negative control: without an account reference, both buys remain in scope.

        Without this, 'return nothing' would satisfy the test above.
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        suffix = transport.value
        with DeliveryPollEnv(tenant_id=f"t_scope2_{suffix}", principal_id=f"p_scope2_{suffix}") as env:
            tenant = TenantFactory(tenant_id=f"t_scope2_{suffix}")
            principal = PrincipalFactory(tenant=tenant, principal_id=f"p_scope2_{suffix}")
            sandbox_buy, live_buy = _seed_two_accounts(tenant, principal)

            env.set_adapter_response(sandbox_buy.media_buy_id, impressions=1000)
            env.set_adapter_response(live_buy.media_buy_id, impressions=2000)

            result = env.call_via(transport, media_buy_ids=[sandbox_buy.media_buy_id, live_buy.media_buy_id])

            returned = _returned_ids(result)

            assert returned == {sandbox_buy.media_buy_id, live_buy.media_buy_id}, (
                f"[{suffix}] unscoped request lost buys (returned={returned}); account filtering "
                "must apply only when the request carries an account reference"
            )


class TestUnresolvableAccountRefusesOnTheWire:
    """The fail-closed refusal must reach the buyer as a typed envelope, not a 500.

    ``_account_is_sandbox`` raises when a buy's non-null account cannot be resolved,
    rather than defaulting to live — refusing to dispatch beats guessing "live" for a
    buy that might be sandbox. Asserting that only as ``pytest.raises`` on the
    in-process exception would grade the raise but not the contract: what the buyer
    receives is the two-layer envelope, and the boundary translation sits between.

    An orphaned reference is unreachable through any supported write, though not by the
    mechanism first written here: the account DELETE does not null the referencing buys,
    it FAILS. The FK is composite with ON DELETE SET NULL and no column list, so Postgres
    nulls ALL referencing columns — including MediaBuy.tenant_id, which is NOT NULL — and
    rejects the delete outright (probed against real Postgres: NotNullViolation on
    tenant_id). Separately, sync_accounts --delete_missing only sets status="closed", and
    get_by_id applies no status filter. So this raise is defense-in-depth against
    corruption or a future write path that skips resolution. Reaching it therefore
    means simulating the unresolvable lookup at the repository seam. Everything after
    that point is real: production's raise, the boundary translation, the wire bytes.
    """

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_unresolvable_account_yields_account_not_found(self, integration_db, transport):
        from unittest.mock import patch

        from src.core.database.repositories.account import AccountRepository
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        suffix = transport.value
        with DeliveryPollEnv(tenant_id=f"t_unres_{suffix}", principal_id=f"p_unres_{suffix}") as env:
            tenant = TenantFactory(tenant_id=f"t_unres_{suffix}")
            principal = PrincipalFactory(tenant=tenant, principal_id=f"p_unres_{suffix}")
            sandbox_buy, _live_buy = _seed_two_accounts(tenant, principal)
            env.set_adapter_response(sandbox_buy.media_buy_id, impressions=1000)

            # No account reference on the request: the only account lookup left is the
            # sandbox derivation for the targeted buys, so this isolates that raise
            # from request-side account resolution (already graded elsewhere).
            with patch.object(AccountRepository, "get_by_id", return_value=None):
                result = env.call_via(transport, media_buy_ids=[sandbox_buy.media_buy_id])

            assert result.is_error, (
                f"[{suffix}] an unresolvable account must refuse dispatch, not return a "
                f"success payload: {result.payload!r}"
            )
            result.assert_wire_error("ACCOUNT_NOT_FOUND", recovery="terminal")


class TestDeliveryAdapterModes:
    """SA-010: the account filter is not enough — assert which adapter each buy is read
    through. The scoping tests above pass on returned IDs alone, so replacing
    sandbox_by_buy[...] with False would leave them green (the mocked adapter is
    mode-agnostic).

    Parametrized over every wire transport, not MCP alone: a constructed
    ``AccountReference`` model — the form this class used to send — is not JSON
    serializable on REST and is rejected by A2A's kwarg-through-JSON path, so an
    MCP-only oracle graded nothing on two of the three transports it claimed to cover.
    The wire-shape dict (``_ACCOUNT_REF``) is what every transport actually receives,
    same as the scoping tests above.
    """

    def _modes_for(self, *, transport: Transport, scoped_account: str | None):
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        suffix = f"{scoped_account or 'none'}_{transport.value}"
        with DeliveryPollEnv(tenant_id=f"t_mode_{suffix}", principal_id=f"p_mode_{suffix}") as env:
            tenant = TenantFactory(tenant_id=f"t_mode_{suffix}")
            principal = PrincipalFactory(tenant=tenant, principal_id=f"p_mode_{suffix}")
            sandbox_buy, live_buy = _seed_two_accounts(tenant, principal)

            env.set_adapter_response(sandbox_buy.media_buy_id, impressions=1000)
            env.set_adapter_response(live_buy.media_buy_id, impressions=2000)

            kwargs: dict = {"media_buy_ids": [sandbox_buy.media_buy_id, live_buy.media_buy_id]}
            if scoped_account is not None:
                kwargs["account"] = {"account_id": scoped_account}

            result = env.call_via(transport, **kwargs)
            assert not result.is_error, f"[{transport.value}] dispatch failed: {result.error!r}"

            # The adapter mock is patched in-process by the harness regardless of which
            # transport dispatched the request, so it reflects the real call made by
            # _get_media_buy_delivery_impl on every transport.
            return env.mock["adapter"]

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_sandbox_scoped_request_uses_only_a_sandbox_adapter(self, integration_db, transport):
        assert_all_sandbox(
            self._modes_for(transport=transport, scoped_account="acc_sbx"), context="sandbox-scoped delivery"
        )

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_live_scoped_request_uses_only_a_live_adapter(self, integration_db, transport):
        """Negative control — 'always sandbox' would pass the test above."""
        assert_all_live(self._modes_for(transport=transport, scoped_account="acc_live"), context="live-scoped delivery")

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_unscoped_mixed_request_uses_both_modes(self, integration_db, transport):
        """Both buys are in play, each read through the adapter its own account dictates."""
        modes = sandbox_modes(self._modes_for(transport=transport, scoped_account=None))

        assert set(modes) == {True, False}, (
            f"[{transport.value}] a mixed unscoped request must read each buy through its own mode, got {modes}"
        )
