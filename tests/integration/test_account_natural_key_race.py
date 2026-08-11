"""A create that LOSES the natural-key race must not surface as a 500.

Regression for salesagent-ocs1, the concurrency half of salesagent-0njj.

0njj closed the sequential duplicate: ``AccountRepository.create`` refuses a key
that already resolves (the message), and ``uq_accounts_natural_key`` enforces it
(the invariant). Its own docstring notes the check "cannot replace the index (two
concurrent creates can both pass it)" — which is exactly the hole this file
covers. Two creates that both pass the pre-check race to the index; the loser
takes an ``IntegrityError`` out of ``flush()``, and nothing handles it.

Why that is worse than it sounds: the admin blueprint catches only ``ValueError``
(the pre-check's error), so the loser propagates an ``IntegrityError`` out of the
request and the operator gets a 500 for a condition the very same code path
explains politely one microsecond earlier. The occupied key is the SAME condition
either way — only the timing differs, and timing must not decide whether a caller
gets a usable message or a stack trace.

The race is made deterministic by holding both callers at a barrier AFTER the
pre-check and BEFORE the flush. Nothing about the failure is simulated: both
sessions are real, both pre-checks genuinely pass (neither transaction can see
the other's uncommitted row), and the ``IntegrityError`` comes from the real
unique index. Only the scheduling is pinned, because a test that relied on
wall-clock interleaving would be flaky rather than deterministic.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.uow import AccountUoW
from src.core.schemas.account import SyncAccountsRequest
from tests.factories.account import AccountFactory
from tests.harness.account_sync import AccountSyncEnv
from tests.harness.admin_accounts import AdminAccountEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_DOMAIN = "acme.com"
_OPERATOR = "example.com"
_TENANT = "nkr_t1"


@contextmanager
def _competitor_commits_mid_create(tenant_id: str, winner_id: str, *, brand: dict | None = None):
    """Let a competing writer COMMIT the natural key between a caller's pre-check and its insert.

    The single-threaded equivalent of the barrier above, for exercising ONE
    caller's recovery: the caller's own ``_find_natural_key_conflict`` runs for
    real and finds nothing, then this hook commits the winner through the real
    repository in its own transaction, so the caller's insert hits the real
    unique index. Fires exactly once, so the winner's own create is not recursed
    into.
    """
    real_check = AccountRepository._find_natural_key_conflict
    fired = threading.Event()

    def check_then_let_the_competitor_win(self, account):
        occupant = real_check(self, account)
        if fired.is_set() or occupant is not None:
            return occupant
        fired.set()
        with AccountUoW(tenant_id) as uow:
            assert uow.accounts is not None
            uow.accounts.create(
                AccountFactory.build(
                    tenant_id=tenant_id,
                    account_id=winner_id,
                    name=f"Winner {winner_id}",
                    status="active",
                    brand=brand if brand is not None else {"domain": _DOMAIN},
                    operator=_OPERATOR,
                    billing="operator",
                )
            )

    with patch.object(AccountRepository, "_find_natural_key_conflict", check_then_let_the_competitor_win):
        yield fired


def _race_two_creates(tenant_id: str, account_ids: tuple[str, str]) -> dict[str, BaseException | None]:
    """Run two creates on ONE natural key, both released from the pre-check together.

    Returns {account_id: exception-or-None}. Each thread owns its own UoW, hence
    its own session and transaction — the same shape as two concurrent requests.
    """
    outcomes: dict[str, BaseException | None] = {}
    barrier = threading.Barrier(len(account_ids), timeout=30)
    real_check = AccountRepository._find_natural_key_conflict

    def synchronized_check(self, account):
        # The real check runs for real; the barrier only guarantees that BOTH
        # have passed it before either reaches the flush.
        occupant = real_check(self, account)
        barrier.wait()
        return occupant

    def attempt(account_id: str) -> None:
        try:
            with AccountUoW(tenant_id) as uow:
                assert uow.accounts is not None
                uow.accounts.create(
                    AccountFactory.build(
                        tenant_id=tenant_id,
                        account_id=account_id,
                        name=f"Acme via {account_id}",
                        status="active",
                        brand={"domain": _DOMAIN},
                        operator=_OPERATOR,
                        billing="operator",
                    )
                )
            outcomes[account_id] = None
        except BaseException as exc:  # noqa: BLE001 — the whole point is what escapes
            outcomes[account_id] = exc

    with patch.object(AccountRepository, "_find_natural_key_conflict", synchronized_check):
        threads = [threading.Thread(target=attempt, args=(aid,)) for aid in account_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "a racing create deadlocked instead of resolving"

    return outcomes


class TestConcurrentCreateOnOneNaturalKey:
    def test_the_loser_does_not_escape_as_an_integrityerror(self, integration_db):
        """The losing create must present the occupied key, not a raw DB error.

        Asserted as "no IntegrityError escapes" rather than as one specific
        recovery, because the two create callers legitimately want different
        surfaces (the admin form wants the pre-check's message; sync_accounts
        wants the winning row). What neither can want is the database's
        constraint violation reaching the caller unhandled.
        """
        with AccountSyncEnv(tenant_id=_TENANT, principal_id="agent_nkr") as env:
            env.setup_default_data()
            env._commit_factory_data()

            outcomes = _race_two_creates(_TENANT, ("acc_race_a", "acc_race_b"))

        # Pin the race actually happened before judging its outcome. Without this,
        # the test passes vacuously whenever BOTH creates fail for an unrelated
        # reason — which is exactly what it did while a factory defect was
        # relocating both rows to another tenant.
        winners = [aid for aid, exc in outcomes.items() if exc is None]
        assert len(winners) == 1, (
            f"expected exactly one create to win the race, got {len(winners)}: "
            f"{ {aid: type(exc).__name__ if exc else 'SUCCESS' for aid, exc in outcomes.items()} }"
        )

        integrity_errors = {aid: exc for aid, exc in outcomes.items() if isinstance(exc, IntegrityError)}
        assert not integrity_errors, (
            "the create that lost the uq_accounts_natural_key race raised a raw IntegrityError: "
            f"{ {aid: str(exc)[:200] for aid, exc in integrity_errors.items()} }. "
            "The admin blueprint catches only ValueError, so this reaches the operator as a 500 "
            "for the very condition _find_natural_key_conflict explains politely when it wins."
        )

    def test_exactly_one_account_survives_the_race(self, integration_db):
        """Whatever the loser is told, the key must still name exactly one account.

        This is the invariant the index exists for, and it holds today — it is
        asserted so a fix that recovers the loser by, say, retrying the insert
        cannot quietly reintroduce the duplicate salesagent-0njj closed.
        """
        with AccountSyncEnv(tenant_id="nkr_t2", principal_id="agent_nkr2") as env:
            env.setup_default_data()
            env._commit_factory_data()

            _race_two_creates("nkr_t2", ("acc_race_c", "acc_race_d"))

            with AccountUoW("nkr_t2") as uow:
                assert uow.accounts is not None
                on_key = uow.accounts.list_by_natural_key(
                    operator=_OPERATOR,
                    brand_domain=_DOMAIN,
                    limit=5,
                )

        assert len(on_key) == 1, (
            f"the race left {len(on_key)} accounts on one natural key: "
            f"{[(a.account_id, a.brand, a.operator) for a in on_key]}"
        )


class TestAdminCreateLosingTheRace:
    """The admin surface: an occupied key is a flash message, never a 500."""

    def test_losing_admin_create_flashes_instead_of_500(self, integration_db):
        with AdminAccountEnv(mode="integration", tenant_id="nkr_admin") as admin:
            admin.authenticate()

            with _competitor_commits_mid_create("nkr_admin", "acc_admin_winner"):
                response = admin.post_create(
                    {
                        "name": "Acme (operator copy)",
                        "brand_domain": _DOMAIN,
                        "operator": _OPERATOR,
                        "billing": "operator",
                    }
                )

            assert response.status_code < 500, (
                f"the losing admin create returned {response.status_code} — the operator gets a 500 for "
                "the very condition _find_natural_key_conflict explains politely when it wins"
            )

            on_key = admin.accounts_on_natural_key(domain=_DOMAIN, operator=_OPERATOR)
            assert [a.account_id for a in on_key] == ["acc_admin_winner"], (
                f"the key must still name exactly the winner, got {[(a.account_id, a.name) for a in on_key]}"
            )


class TestSyncAccountsLosingTheRace:
    """The buyer surface: the losing entry resolves to the winner, and the batch survives."""

    @pytest.mark.asyncio
    async def test_losing_entry_resolves_to_the_winner_and_the_batch_continues(self, integration_db):
        """The assertion that separates a real fix from a bare try/except.

        sync_accounts runs its whole entry loop in ONE AccountUoW, so an
        unrecovered IntegrityError aborts the transaction and every later entry
        dies with PendingRollbackError. The second entry here has a DIFFERENT
        natural key and nothing to do with the race — if it is not created, the
        recovery did not really recover.
        """
        with AccountSyncEnv(tenant_id="nkr_sync", principal_id="agent_nkr_sync") as env:
            env.setup_default_data()
            env._commit_factory_data()

            req = SyncAccountsRequest(
                accounts=[
                    {"brand": {"domain": _DOMAIN}, "operator": _OPERATOR, "billing": "operator"},
                    {"brand": {"domain": "beta.com"}, "operator": _OPERATOR, "billing": "agent"},
                ],
            )

            with _competitor_commits_mid_create("nkr_sync", "acc_sync_winner"):
                response = await env.call_impl_async(req=req)

        assert len(response.accounts) == 2

        loser, sibling = response.accounts
        loser_action = loser.action.value if hasattr(loser.action, "value") else str(loser.action)
        assert loser_action in {"unchanged", "updated"}, (
            f"the entry that lost the race must resolve to the winner, not fail: {loser!r}"
        )
        assert loser.account_id == "acc_sync_winner", (
            f"the losing entry reported {loser.account_id!r}, not the winning account"
        )

        sibling_action = sibling.action.value if hasattr(sibling.action, "value") else str(sibling.action)
        assert sibling_action == "created", (
            f"the unrelated second entry was not applied ({sibling_action!r}) — the losing create "
            "poisoned the batch transaction instead of rolling back its own savepoint"
        )


class TestUnrelatedIntegrityErrorsStillPropagate:
    """The narrowing: only the natural-key violation is recovered."""

    def test_a_check_violation_during_a_race_is_not_blamed_on_the_natural_key(self, integration_db):
        """An unrelated constraint failure must surface as itself, even mid-race.

        This is the exact situation narrowing exists for, and the only one where
        omitting it is observable: the insert fails for a DIFFERENT reason (here
        the ck_accounts_billing CHECK) while the natural key HAS meanwhile been
        taken. An unnarrowed `except IntegrityError` catches the CHECK violation,
        re-runs the pre-check, finds the concurrent winner, and reports "natural
        key already in use" — burying a genuine data defect under a plausible
        story about concurrency.

        Outside a race the omission is invisible, because the re-check finds no
        occupant and the original error is re-raised anyway. A test built on that
        path would pass with the narrowing deleted.
        """
        from src.core.database.repositories.account import NaturalKeyConflict

        with AccountSyncEnv(tenant_id="nkr_narrow", principal_id="agent_nkr_narrow") as env:
            env.setup_default_data()
            env._commit_factory_data()

            with pytest.raises(IntegrityError) as caught:  # noqa: PT012 — the raise is inside the UoW
                with _competitor_commits_mid_create("nkr_narrow", "acc_narrow_winner"):
                    with AccountUoW("nkr_narrow") as uow:
                        assert uow.accounts is not None
                        uow.accounts.create(
                            AccountFactory.build(
                                tenant_id="nkr_narrow",
                                account_id="acc_narrow_loser",
                                name="Loser with bad billing",
                                status="active",
                                brand={"domain": _DOMAIN},
                                operator=_OPERATOR,
                                billing="not_a_billing_party",
                            )
                        )

            assert not isinstance(caught.value, NaturalKeyConflict), (
                "a ck_accounts_billing violation was reported as a natural-key conflict — "
                "the recovery is not narrowed to uq_accounts_natural_key"
            )
            assert "ck_accounts_billing" in str(caught.value), (
                f"expected the CHECK violation to surface as itself, got: {str(caught.value)[:300]}"
            )
