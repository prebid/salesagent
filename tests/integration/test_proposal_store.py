"""Integration tests for :class:`SalesAgentProposalStore`.

Exercises the Postgres-backed :class:`adcp.decisioning.ProposalStore`
implementation against a real database. The store implements the
v1.5 ``ProposalStore`` Protocol — the framework's
``proposal_dispatch`` calls into it to persist ``get_products``
proposals (as DRAFT) and resolve them on
``create_media_buy(proposal_id=X)``.

Lifecycle promotion (DRAFT → COMMITTED) is owned by the framework:
managers declaring
:attr:`ProposalCapabilities.auto_commit_on_put_draft=True` get a
synthetic :meth:`commit` call from the framework right after
:meth:`put_draft`. The store doesn't bake any lifecycle shortcuts in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.database.repositories import SalesAgentProposalStore
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.requires_db, pytest.mark.integration, pytest.mark.asyncio]


class _BareEnv(IntegrationEnv):
    """Minimal integration env — just session + factory binding."""

    EXTERNAL_PATCHES: dict = {}


def _make_payload(proposal_id: str = "prop_test") -> dict:
    return {
        "proposal_id": proposal_id,
        "name": "Recommended bundle",
        "allocations": [
            {"product_id": "prod_a", "allocation_percentage": 60.0},
            {"product_id": "prod_b", "allocation_percentage": 40.0},
        ],
    }


def _seven_days_from_now() -> datetime:
    """Default ``expires_at`` matching the manager's
    ``auto_commit_ttl_seconds=604800``. The framework computes
    ``expires_at`` from the capability when it calls
    ``store.commit`` after ``put_draft``; tests synthesize the same
    value directly when exercising commit out-of-band."""
    return datetime.now(UTC) + timedelta(days=7)


class TestPutDraft:
    """``put_draft`` persists in DRAFT state per spec; the framework
    owns the DRAFT → COMMITTED promotion via ``auto_commit_on_put_draft``."""

    async def test_writes_row_in_draft_state(self, integration_db):
        """The store writes spec-canonical ``draft`` — no hidden
        promotion. Managers that want the brief→create_media_buy flow
        to work without an explicit finalize step declare
        ``auto_commit_on_put_draft=True`` on their capabilities; the
        framework's dispatch calls :meth:`commit` immediately after."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_proposal_a")

            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_1",
                account_id="tenant_proposal_a",
                recipes={},
                proposal_payload=_make_payload("prop_1"),
            )

            record = await store.get("prop_1", expected_account_id="tenant_proposal_a")
            assert record is not None
            assert record.state == ProposalState.DRAFT, (
                "put_draft must write DRAFT per Protocol; DRAFT → COMMITTED is the framework's job"
            )
            assert record.expires_at is None, "DRAFT records have no hold window; commit sets expires_at"

    async def test_payload_round_trips(self, integration_db):
        """The wire ``Proposal`` payload survives persist + reload —
        :meth:`maybe_hydrate_recipes_for_create_media_buy` reads
        ``proposal_payload`` to derive packages."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_proposal_b")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_2")
            await store.put_draft(
                proposal_id="prop_2",
                account_id="tenant_proposal_b",
                recipes={},
                proposal_payload=payload,
            )
            record = await store.get("prop_2", expected_account_id="tenant_proposal_b")
            assert record is not None
            assert dict(record.proposal_payload) == payload

    async def test_refine_iteration_overwrites_existing_draft(self, integration_db):
        """``put_draft`` on an existing DRAFT record overwrites the
        payload — refine iterations re-issue the same ``proposal_id``
        and the buyer expects the latest content to win."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_proposal_c")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_3",
                account_id="tenant_proposal_c",
                recipes={},
                proposal_payload=_make_payload("prop_3"),
            )
            updated = _make_payload("prop_3")
            updated["name"] = "Updated bundle"
            await store.put_draft(
                proposal_id="prop_3",
                account_id="tenant_proposal_c",
                recipes={},
                proposal_payload=updated,
            )
            record = await store.get("prop_3", expected_account_id="tenant_proposal_c")
            assert record is not None
            assert record.proposal_payload["name"] == "Updated bundle"

    async def test_put_draft_handles_compound_account_id(self, integration_db):
        """The framework passes ``ctx.account.id`` straight into the
        store. :class:`SalesagentAccountStore` mints
        ``f"{tenant_id}:{ref}"`` (``ref`` defaults to ``"default"``;
        storyboard runs use ``"acct_demo"``), so the store has to split
        the compound string back into the ``tenant_id`` for the
        ``proposals.tenant_id`` FK. Regression: pre-fix, every prod
        ``put_draft`` would FK-violate because the column would receive
        the full compound string."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="my_tenant")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_compound",
                # SalesagentAccountStore.resolve() shape — what the
                # framework actually passes at runtime.
                account_id="my_tenant:default",
                recipes={},
                proposal_payload=_make_payload("prop_compound"),
            )
            # No FK violation. account_id preserved verbatim for the
            # cross-tenant defense; tenant_id derived from the prefix.
            record = await store.get("prop_compound", expected_account_id="my_tenant:default")
            assert record is not None
            assert record.account_id == "my_tenant:default", (
                "account_id must be stored verbatim — every cross-tenant "
                "defense compares against the full compound string"
            )
            assert record.state == ProposalState.DRAFT

    async def test_put_draft_on_committed_raises(self, integration_db):
        """Per Protocol, ``put_draft`` is only legal on DRAFT records.
        A COMMITTED proposal_id is immutable — overwrite would mean
        the buyer's prior commit/expires_at silently rolls back."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_putd_committed")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_c",
                account_id="tenant_putd_committed",
                recipes={},
                proposal_payload=_make_payload("prop_c"),
            )
            await store.commit(
                "prop_c",
                expires_at=_seven_days_from_now(),
                proposal_payload=_make_payload("prop_c"),
            )
            with pytest.raises(AdcpError) as exc:
                await store.put_draft(
                    proposal_id="prop_c",
                    account_id="tenant_putd_committed",
                    recipes={},
                    proposal_payload=_make_payload("prop_c"),
                )
            assert exc.value.code == "INTERNAL_ERROR"


class TestCommit:
    """``commit`` promotes DRAFT → COMMITTED and sets ``expires_at``."""

    async def test_commit_advances_state_and_sets_expires_at(self, integration_db):
        """The framework calls this right after :meth:`put_draft` when
        the manager declares ``auto_commit_on_put_draft=True``. The
        TTL applied here comes from
        ``ProposalCapabilities.auto_commit_ttl_seconds``."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_commit")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_commit")
            await store.put_draft(
                proposal_id="prop_commit",
                account_id="tenant_commit",
                recipes={},
                proposal_payload=payload,
            )
            expires_at = _seven_days_from_now()
            await store.commit("prop_commit", expires_at=expires_at, proposal_payload=payload)

            record = await store.get("prop_commit", expected_account_id="tenant_commit")
            assert record is not None
            assert record.state == ProposalState.COMMITTED
            assert record.expires_at == expires_at

    async def test_commit_is_idempotent_on_equal_values(self, integration_db):
        """Per Protocol: re-commit with the same ``expires_at`` +
        payload is a no-op; mismatch is an ``INTERNAL_ERROR``. The
        idempotency case lets the framework's auto-commit dispatch
        re-run safely on transient retries."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_commit_idem")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_idem")
            await store.put_draft(
                proposal_id="prop_idem",
                account_id="tenant_commit_idem",
                recipes={},
                proposal_payload=payload,
            )
            expires_at = _seven_days_from_now()
            await store.commit("prop_idem", expires_at=expires_at, proposal_payload=payload)
            # Same values — no raise.
            await store.commit("prop_idem", expires_at=expires_at, proposal_payload=payload)

    async def test_commit_rejects_changed_payload(self, integration_db):
        """Re-commit with a different payload raises ``INTERNAL_ERROR``
        — adopter / framework bug, not buyer-fixable."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_commit_drift")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_drift",
                account_id="tenant_commit_drift",
                recipes={},
                proposal_payload=_make_payload("prop_drift"),
            )
            expires_at = _seven_days_from_now()
            await store.commit(
                "prop_drift",
                expires_at=expires_at,
                proposal_payload=_make_payload("prop_drift"),
            )
            drifted = _make_payload("prop_drift")
            drifted["name"] = "Different bundle"
            with pytest.raises(AdcpError) as exc:
                await store.commit("prop_drift", expires_at=expires_at, proposal_payload=drifted)
            assert exc.value.code == "INTERNAL_ERROR"


class TestGet:
    """``get`` enforces cross-tenant probe defense."""

    async def test_cross_tenant_probe_returns_none(self, integration_db):
        """A proposal_id known to tenant A must not be visible to tenant
        B — the Protocol requires collapsing the cross-tenant probe to
        ``None`` so adversarial buyers can't enumerate proposals via
        id-guessing."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_owner")
            TenantFactory(tenant_id="tenant_probe")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_secret",
                account_id="tenant_owner",
                recipes={},
                proposal_payload=_make_payload("prop_secret"),
            )

            # Probe from the wrong tenant collapses to None.
            assert await store.get("prop_secret", expected_account_id="tenant_probe") is None
            # No expected_account_id allows admin / ops lookup.
            assert await store.get("prop_secret") is not None

    async def test_unknown_proposal_returns_none(self, integration_db):
        """Unknown ``proposal_id`` returns ``None`` — the Protocol
        contract; the framework projects this to
        ``PROPOSAL_NOT_FOUND`` at the dispatch layer."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_empty")
            store = SalesAgentProposalStore()
            assert await store.get("prop_nope", expected_account_id="tenant_empty") is None


class TestReservationLifecycle:
    """Two-phase consumption: ``committed`` → ``consuming`` → ``consumed``."""

    async def _put_and_commit(self, store: SalesAgentProposalStore, *, proposal_id: str, account_id: str) -> None:
        """Helper: put_draft + commit, the two-step the framework runs
        when ``auto_commit_on_put_draft=True``. Tests exercising
        consumption assume a COMMITTED starting state — this seeds it
        the same way the framework would."""
        payload = _make_payload(proposal_id)
        await store.put_draft(
            proposal_id=proposal_id,
            account_id=account_id,
            recipes={},
            proposal_payload=payload,
        )
        await store.commit(proposal_id, expires_at=_seven_days_from_now(), proposal_payload=payload)

    async def test_try_reserve_consumption_advances_state(self, integration_db):
        """The reservation flips the record from ``committed`` to
        ``consuming``; framework runs the adapter against this
        reservation and either finalizes (success) or releases
        (rollback)."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_reserve_a")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_reserve", account_id="tenant_reserve_a")
            reserved = await store.try_reserve_consumption("prop_reserve", expected_account_id="tenant_reserve_a")
            assert reserved.state == ProposalState.CONSUMING

    async def test_reserve_on_draft_raises_not_committed(self, integration_db):
        """A DRAFT proposal (no commit yet) must raise
        ``PROPOSAL_NOT_COMMITTED`` on reserve — sanity check that the
        store's lifecycle enforcement matches the Protocol contract
        (the prior v1 workaround skipped DRAFT entirely)."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_reserve_draft")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_unc",
                account_id="tenant_reserve_draft",
                recipes={},
                proposal_payload=_make_payload("prop_unc"),
            )
            with pytest.raises(AdcpError) as exc:
                await store.try_reserve_consumption("prop_unc", expected_account_id="tenant_reserve_draft")
            assert exc.value.code == "PROPOSAL_NOT_COMMITTED"

    async def test_second_reservation_raises(self, integration_db):
        """A second :meth:`try_reserve_consumption` on a reserved
        proposal raises ``PROPOSAL_NOT_COMMITTED`` — solves the
        inventory double-spend race the Protocol calls out. Two
        parallel callers cannot both reserve the same proposal."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_reserve_b")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_double", account_id="tenant_reserve_b")
            await store.try_reserve_consumption("prop_double", expected_account_id="tenant_reserve_b")
            with pytest.raises(AdcpError) as exc:
                await store.try_reserve_consumption("prop_double", expected_account_id="tenant_reserve_b")
            assert exc.value.code == "PROPOSAL_NOT_COMMITTED"

    async def test_reserve_cross_tenant_returns_not_found(self, integration_db):
        """Cross-tenant probe on :meth:`try_reserve_consumption`
        collapses to ``PROPOSAL_NOT_FOUND`` — same defense as
        :meth:`get`, since this method is reachable via the framework's
        ``create_media_buy(proposal_id=X)`` dispatch and proposal_ids
        are buyer-controllable."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_owner_r")
            TenantFactory(tenant_id="tenant_probe_r")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_cross", account_id="tenant_owner_r")
            with pytest.raises(AdcpError) as exc:
                await store.try_reserve_consumption("prop_cross", expected_account_id="tenant_probe_r")
            assert exc.value.code == "PROPOSAL_NOT_FOUND"

    async def test_finalize_records_media_buy_id(self, integration_db):
        """Successful adapter dispatch finalizes the reservation —
        ``state`` becomes ``consumed`` and ``media_buy_id`` is recorded
        for reverse-index lookup via :meth:`get_by_media_buy_id`."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_finalize")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_final", account_id="tenant_finalize")
            await store.try_reserve_consumption("prop_final", expected_account_id="tenant_finalize")
            await store.finalize_consumption("prop_final", media_buy_id="mb_123", expected_account_id="tenant_finalize")
            record = await store.get("prop_final", expected_account_id="tenant_finalize")
            assert record is not None
            assert record.state == ProposalState.CONSUMED
            assert record.media_buy_id == "mb_123"

    async def test_release_rolls_back_to_committed(self, integration_db):
        """Adapter failure releases the reservation back to
        ``committed`` so the buyer can retry without
        ``PROPOSAL_NOT_COMMITTED``."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_release")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_release", account_id="tenant_release")
            await store.try_reserve_consumption("prop_release", expected_account_id="tenant_release")
            await store.release_consumption("prop_release", expected_account_id="tenant_release")
            record = await store.get("prop_release", expected_account_id="tenant_release")
            assert record is not None
            assert record.state == ProposalState.COMMITTED, (
                "release must roll back to COMMITTED so the buyer's retry succeeds"
            )

    async def test_finalize_cross_tenant_collapses_to_internal_error(self, integration_db):
        """:meth:`finalize_consumption` filters ``account_id`` in the
        WHERE clause so cross-tenant probes never take the ``FOR UPDATE``
        row lock. A foreign tenant attempting to finalize another
        tenant's reserved proposal gets ``INTERNAL_ERROR`` without
        acquiring the lock (the same wire code a missing record
        produces — no existence disclosure)."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_owner_f")
            TenantFactory(tenant_id="tenant_probe_f")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_cross_f", account_id="tenant_owner_f")
            await store.try_reserve_consumption("prop_cross_f", expected_account_id="tenant_owner_f")
            with pytest.raises(AdcpError) as exc:
                await store.finalize_consumption(
                    "prop_cross_f", media_buy_id="mb_foreign", expected_account_id="tenant_probe_f"
                )
            assert exc.value.code == "INTERNAL_ERROR"

    async def test_release_cross_tenant_is_noop(self, integration_db):
        """:meth:`release_consumption` is idempotent on miss — including
        cross-tenant misses. WHERE-clause filtering ensures the
        foreign tenant never takes the lock; the silent no-op
        preserves the unconditional-rollback contract."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_owner_rel")
            TenantFactory(tenant_id="tenant_probe_rel")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_cross_rel", account_id="tenant_owner_rel")
            await store.try_reserve_consumption("prop_cross_rel", expected_account_id="tenant_owner_rel")
            # Foreign tenant calls release — must no-op, must NOT roll
            # back the legitimate tenant's CONSUMING reservation.
            await store.release_consumption("prop_cross_rel", expected_account_id="tenant_probe_rel")
            record = await store.get("prop_cross_rel", expected_account_id="tenant_owner_rel")
            assert record is not None
            from adcp.decisioning.proposal_store import ProposalState

            assert record.state == ProposalState.CONSUMING, (
                "Foreign tenant's release must not roll back the owner's reservation"
            )

    async def test_release_on_committed_is_idempotent(self, integration_db):
        """Releasing a record already in ``committed`` is a no-op so
        the adapter-failure rollback path can fire unconditionally."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_idem")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_idem", account_id="tenant_idem")
            # Never reserved — release should no-op without raising.
            await store.release_consumption("prop_idem", expected_account_id="tenant_idem")

    async def test_release_silent_no_op_on_missing(self, integration_db):
        """The framework's adapter-failure rollback path is
        unconditional — it runs ``release_consumption`` in a ``finally``
        block whether or not the reserve succeeded. Raising on missing
        would blow up that rollback path on transient lookups; silent
        no-op matches the upstream :class:`InMemoryProposalStore`
        shape."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_release_missing")
            store = SalesAgentProposalStore()
            # No raise — the rollback path stays unconditional.
            await store.release_consumption("prop_does_not_exist", expected_account_id="tenant_release_missing")

    async def test_release_silent_no_op_on_cross_account(self, integration_db):
        """Same rollback-path invariant for cross-account: a foreign
        tenant's ``release`` call is a no-op, and the legitimate
        owner's CONSUMING reservation stays intact."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_release_x_owner")
            TenantFactory(tenant_id="tenant_release_x_probe")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_release_x", account_id="tenant_release_x_owner")
            await store.try_reserve_consumption("prop_release_x", expected_account_id="tenant_release_x_owner")
            # Foreign tenant — silent no-op; the real reserve stays CONSUMING.
            await store.release_consumption("prop_release_x", expected_account_id="tenant_release_x_probe")
            record = await store.get("prop_release_x", expected_account_id="tenant_release_x_owner")
            assert record is not None
            assert record.state == ProposalState.CONSUMING

    async def test_finalize_idempotent_on_consumed_matching_media_buy(self, integration_db):
        """A retried finalize after a successful one is a no-op when
        the same ``media_buy_id`` is supplied — protects against
        webhook re-delivery / framework dispatch retries that would
        otherwise raise on the second finalize. Matches upstream."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_fin_idem")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_fin_idem", account_id="tenant_fin_idem")
            await store.try_reserve_consumption("prop_fin_idem", expected_account_id="tenant_fin_idem")
            await store.finalize_consumption(
                "prop_fin_idem", media_buy_id="mb_idem", expected_account_id="tenant_fin_idem"
            )
            # Second finalize with the same media_buy_id — must NOT raise.
            await store.finalize_consumption(
                "prop_fin_idem", media_buy_id="mb_idem", expected_account_id="tenant_fin_idem"
            )

    async def test_finalize_mismatched_media_buy_raises(self, integration_db):
        """A re-finalize with a DIFFERENT ``media_buy_id`` violates the
        one-buy-per-proposal invariant — framework bug,
        ``INTERNAL_ERROR``."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_fin_mis")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_fin_mis", account_id="tenant_fin_mis")
            await store.try_reserve_consumption("prop_fin_mis", expected_account_id="tenant_fin_mis")
            await store.finalize_consumption(
                "prop_fin_mis", media_buy_id="mb_first", expected_account_id="tenant_fin_mis"
            )
            with pytest.raises(AdcpError) as exc:
                await store.finalize_consumption(
                    "prop_fin_mis", media_buy_id="mb_second", expected_account_id="tenant_fin_mis"
                )
            assert exc.value.code == "INTERNAL_ERROR"

    async def test_reserve_past_expires_at_raises_expired(self, integration_db):
        """Defense-in-depth TTL check inside the row lock — a proposal
        held past its ``expires_at`` can't be reserved, even if the
        framework's get-side filter was bypassed. Mirrors upstream
        :meth:`InMemoryProposalStore._evict_expired_locked` but reports
        rather than silently deleting so audit trails survive."""
        from datetime import timedelta as _td

        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_expired")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_expired")
            await store.put_draft(
                proposal_id="prop_expired",
                account_id="tenant_expired",
                recipes={},
                proposal_payload=payload,
            )
            # Commit with an already-past expiry — simulates clock skew /
            # very long-lived COMMITTED proposals.
            await store.commit(
                "prop_expired",
                expires_at=datetime.now(UTC) - _td(seconds=1),
                proposal_payload=payload,
            )

            with pytest.raises(AdcpError) as exc:
                await store.try_reserve_consumption("prop_expired", expected_account_id="tenant_expired")
            assert exc.value.code == "PROPOSAL_EXPIRED"


class TestReverseIndex:
    """``get_by_media_buy_id`` requires ``expected_account_id`` per Protocol."""

    async def test_resolves_consumed_proposal_for_media_buy(self, integration_db):
        """After finalize, the proposal is reachable via the consumed
        ``media_buy_id`` — used by audit / debug flows that have a
        media buy and want to recover the proposal context."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_reverse")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_rev")
            await store.put_draft(
                proposal_id="prop_rev",
                account_id="tenant_reverse",
                recipes={},
                proposal_payload=payload,
            )
            await store.commit("prop_rev", expires_at=_seven_days_from_now(), proposal_payload=payload)
            await store.try_reserve_consumption("prop_rev", expected_account_id="tenant_reverse")
            await store.finalize_consumption("prop_rev", media_buy_id="mb_rev", expected_account_id="tenant_reverse")
            record = await store.get_by_media_buy_id("mb_rev", expected_account_id="tenant_reverse")
            assert record is not None
            assert record.proposal_id == "prop_rev"

    async def test_cross_tenant_reverse_lookup_returns_none(self, integration_db):
        """Reverse lookup with the wrong ``expected_account_id`` returns
        ``None`` — guards against collisions across tenants when
        ``media_buy_id`` sequences overlap (e.g., deterministic test
        fixtures, sequential numeric IDs)."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_rev_owner")
            TenantFactory(tenant_id="tenant_rev_probe")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_rev_secret")
            await store.put_draft(
                proposal_id="prop_rev_secret",
                account_id="tenant_rev_owner",
                recipes={},
                proposal_payload=payload,
            )
            await store.commit("prop_rev_secret", expires_at=_seven_days_from_now(), proposal_payload=payload)
            await store.try_reserve_consumption("prop_rev_secret", expected_account_id="tenant_rev_owner")
            await store.finalize_consumption(
                "prop_rev_secret",
                media_buy_id="mb_shared",
                expected_account_id="tenant_rev_owner",
            )
            assert (await store.get_by_media_buy_id("mb_shared", expected_account_id="tenant_rev_probe")) is None


class TestDiscardFailsClosed:
    """``discard`` Protocol signature has no ``expected_account_id``.
    Implementing it would let any caller with a guessed ``proposal_id``
    destroy another tenant's reservable proposal. We fail closed; the
    framework's dispatch (adcp 5.4) doesn't call it today, so this
    surfaces loudly if a future framework version adds it as a
    callable surface."""

    async def test_discard_raises_not_implemented(self, integration_db):
        """``discard`` is fail-closed pending an upstream Protocol fix
        that adds ``expected_account_id``. Calling it must raise
        ``NotImplementedError`` (not silently succeed) so any future
        framework regression is loud."""
        with _BareEnv():
            store = SalesAgentProposalStore()
            with pytest.raises(NotImplementedError):
                await store.discard("prop_any")


class TestMarkConsumed:
    """Legacy direct ``committed`` → ``consumed`` transition (v1.5 alpha
    back-compat). The Protocol signature lacks ``expected_account_id``
    — the framework's adcp 5.4 dispatch doesn't call this path, but
    we implement it for upstream Protocol compatibility with a
    WARNING audit log on every call (see store docstring).

    Behavior locked here matches the upstream
    :class:`InMemoryProposalStore.mark_consumed` shape verbatim.
    """

    async def _committed(self, store: SalesAgentProposalStore, *, proposal_id: str, account_id: str) -> None:
        payload = _make_payload(proposal_id)
        await store.put_draft(
            proposal_id=proposal_id,
            account_id=account_id,
            recipes={},
            proposal_payload=payload,
        )
        await store.commit(proposal_id, expires_at=_seven_days_from_now(), proposal_payload=payload)

    async def test_mark_consumed_promotes_to_consumed(self, integration_db):
        """Happy path: COMMITTED → CONSUMED in one call. Records the
        ``media_buy_id`` back-reference (same as the two-phase
        finalize path)."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_mc_a")
            store = SalesAgentProposalStore()
            await self._committed(store, proposal_id="prop_mc_a", account_id="tenant_mc_a")
            await store.mark_consumed("prop_mc_a", media_buy_id="mb_mc_a")

            record = await store.get("prop_mc_a", expected_account_id="tenant_mc_a")
            assert record is not None
            assert record.state == ProposalState.CONSUMED
            assert record.media_buy_id == "mb_mc_a"

    async def test_mark_consumed_idempotent_on_matching(self, integration_db):
        """Re-marking with the same ``media_buy_id`` is a no-op — the
        framework's dispatch retry on transient errors must not raise
        on the second call."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_mc_b")
            store = SalesAgentProposalStore()
            await self._committed(store, proposal_id="prop_mc_b", account_id="tenant_mc_b")
            await store.mark_consumed("prop_mc_b", media_buy_id="mb_mc_b")
            # Second call: must NOT raise.
            await store.mark_consumed("prop_mc_b", media_buy_id="mb_mc_b")

    async def test_mark_consumed_mismatched_raises(self, integration_db):
        """A second ``mark_consumed`` with a DIFFERENT ``media_buy_id``
        violates the one-buy-per-proposal invariant — framework /
        adopter bug, raise ``INTERNAL_ERROR``."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_mc_c")
            store = SalesAgentProposalStore()
            await self._committed(store, proposal_id="prop_mc_c", account_id="tenant_mc_c")
            await store.mark_consumed("prop_mc_c", media_buy_id="mb_first")
            with pytest.raises(AdcpError) as exc:
                await store.mark_consumed("prop_mc_c", media_buy_id="mb_second")
            assert exc.value.code == "INTERNAL_ERROR"

    async def test_mark_consumed_unknown_raises_internal_error(self, integration_db):
        """Framework-only path — missing record is a framework / adopter
        bug, not a buyer-visible error. Distinct from
        :meth:`try_reserve_consumption`'s ``PROPOSAL_NOT_FOUND`` which
        IS buyer-reachable."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_mc_d")
            store = SalesAgentProposalStore()
            with pytest.raises(AdcpError) as exc:
                await store.mark_consumed("prop_unknown", media_buy_id="mb_x")
            assert exc.value.code == "INTERNAL_ERROR"
