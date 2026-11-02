"""The transaction that owns an effect decides whether that effect fires.

Grades the seam #1970 introduces: an effect registered
on a unit of work runs after that unit COMMITS, and never runs when it rolls
back — including the ``dry_run`` rollback that makes a preview a preview.

Level. This is integration, not unit: it runs a real ``CreativeUoW`` against a
real PostgreSQL session, because the whole obligation is about transaction
disposal and a mocked session cannot have a disposal. It sits beside, not
instead of, the behavioural graders — the live branch is graded end to end by
tests/bdd/features/local-uc006-post-commit-effect-ordering.feature and the
preview branch by local-uc006-dry-run-out-of-transaction-effects.feature. What
neither can reach is the exception branch and the queue's own hygiene: production
has no path that raises out of the sync's UoW block after an effect has been
registered, and a scenario cannot inject one without a fault-injection seam that
would itself be untested code.

The surface under test is the repository mixin (``creative_repo.after_commit``,
``creative_repo.is_preview``) rather than the free functions behind it, because
the mixin is what the six production call sites hold — every one of them has a
repository in hand and none of them has the UoW.
"""

from __future__ import annotations

import pytest

from src.core.database.repositories.uow import CreativeUoW

TENANT_ID = "tenant-effect-boundary"
PRINCIPAL_ID = "principal-effect-boundary"


@pytest.mark.requires_db
class TestAfterCommitEffectBoundary:
    """``after_commit`` fires on commit, stays silent on every rollback."""

    def test_effect_does_not_run_at_registration_and_runs_once_after_commit(self, integration_db):
        fired: list[str] = []

        with CreativeUoW(TENANT_ID) as uow:
            assert uow.creatives is not None
            uow.creatives.after_commit(lambda: fired.append("ai-review"))
            assert fired == [], "an effect must not run while the transaction that owns it is still open"

        assert fired == ["ai-review"]

    def test_effect_does_not_run_when_the_unit_is_a_preview(self, integration_db):
        """``dry_run`` rolls back on clean exit, so the effect never earned its run."""
        fired: list[str] = []

        with CreativeUoW(TENANT_ID, dry_run=True) as uow:
            assert uow.creatives is not None
            uow.creatives.after_commit(lambda: fired.append("ai-review"))

        assert fired == []

    def test_effect_does_not_run_when_the_block_raises(self, integration_db):
        """The failure branch no scenario can reach: rollback by exception, not by preview."""
        fired: list[str] = []

        with pytest.raises(RuntimeError, match="creative sync blew up"):
            with CreativeUoW(TENANT_ID) as uow:
                assert uow.creatives is not None
                uow.creatives.after_commit(lambda: fired.append("ai-review"))
                raise RuntimeError("creative sync blew up")

        assert fired == []

    def test_a_later_transaction_does_not_re_run_a_drained_effect(self, integration_db):
        """The queue belongs to the transaction, not to whatever session object it reused.

        The session is scoped, so a later unit of work can be handed the same
        Session instance. An effect must fire once, for the transaction it was
        registered on.
        """
        fired: list[str] = []

        with CreativeUoW(TENANT_ID) as uow:
            assert uow.creatives is not None
            uow.creatives.after_commit(lambda: fired.append("ai-review"))

        with CreativeUoW(TENANT_ID) as later:
            assert later.creatives is not None

        assert fired == ["ai-review"]

    def test_an_effect_may_open_its_own_unit_of_work(self, integration_db):
        """Every real effect here opens its own transaction — so the drain must allow it.

        The AI-review job opens an ``AdminCreativeUoW``. Draining while the
        outer session is still open would let this inner unit close and remove
        the scoped session out from under the outer exit, so the drain point is
        part of the contract, not an implementation detail.
        """
        read_back: list[list] = []

        def effect() -> None:
            with CreativeUoW(TENANT_ID) as inner:
                assert inner.creatives is not None
                read_back.append(inner.creatives.list_by_principal(PRINCIPAL_ID))

        with CreativeUoW(TENANT_ID) as uow:
            assert uow.creatives is not None
            uow.creatives.after_commit(effect)

        assert read_back == [[]]

    def test_a_rolled_back_savepoint_discards_the_effects_queued_inside_it(self, integration_db):
        """The granularity sync_creatives actually isolates at.

        Each creative is processed inside ``creative_repo.savepoint()``, and the
        AI-review submit is the FIRST thing the ai-powered branch registers. A
        savepoint rollback does not touch ``session.info``, so before this the
        effect survived its own creative's failure and the OUTER commit drained
        it — the background job then ran for a creative the buyer was told had
        ``action="failed"``, writing status back onto the untouched row.
        """
        fired: list[str] = []

        with CreativeUoW(TENANT_ID) as uow:
            assert uow.creatives is not None
            uow.creatives.after_commit(lambda: fired.append("creative-1"), label="ai_review:1")

            with pytest.raises(RuntimeError, match="this creative failed"):
                with uow.creatives.savepoint():
                    uow.creatives.after_commit(lambda: fired.append("creative-2"), label="ai_review:2")
                    raise RuntimeError("this creative failed")

            uow.creatives.after_commit(lambda: fired.append("creative-3"), label="ai_review:3")

        assert fired == ["creative-1", "creative-3"], (
            "the effect queued inside the rolled-back savepoint must not fire, and the "
            "effects on either side of it must be untouched"
        )

    def test_a_committed_savepoint_keeps_its_effects(self, integration_db):
        """The negative of the above: truncation must key on the ROLLBACK, not on nesting."""
        fired: list[str] = []

        with CreativeUoW(TENANT_ID) as uow:
            assert uow.creatives is not None
            with uow.creatives.savepoint():
                uow.creatives.after_commit(lambda: fired.append("creative-1"), label="ai_review:1")

        assert fired == ["creative-1"]

    def test_an_effect_may_register_another_effect(self, integration_db):
        """The drain loops until the queue is empty rather than over one snapshot.

        A snapshot loop dropped the follow-up silently and ``end_effects`` then
        popped the scope — nothing ran it and nothing logged it, which is the
        quiet failure ``after_commit`` raises a RuntimeError to prevent when the
        registration has no owner at all.
        """
        fired: list[str] = []

        with CreativeUoW(TENANT_ID) as uow:
            assert uow.creatives is not None
            repo = uow.creatives

            def first() -> None:
                fired.append("first")
                repo.after_commit(lambda: fired.append("second"))

            repo.after_commit(first)

        assert fired == ["first", "second"]

    def test_is_preview_reports_the_disposal_of_the_owning_transaction(self, integration_db):
        """The one question the four inline-result call sites are allowed to ask.

        They must not ask "did we get a result" — that conflates "we did not
        call" with "the agent had nothing", which is the confusion the honest
        create-report branch exists to prevent.
        """
        with CreativeUoW(TENANT_ID) as live:
            assert live.creatives is not None
            assert live.creatives.is_preview is False

        with CreativeUoW(TENANT_ID, dry_run=True) as preview:
            assert preview.creatives is not None
            assert preview.creatives.is_preview is True
