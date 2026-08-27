"""Two properties of ``resolve_or_write`` that no call site can grade.

**Losing the race must discard the failed write and NOTHING else.** The recovery
runs inside ``session.begin_nested()``, and that choice is invisible at most call
sites: a handler staging only the contested row answers identically whether the
recovery rolls back to a SAVEPOINT or rolls back the whole session. The
difference only shows where the same transaction already carries other work —
the self-service signup shape, where the tenant, its adapter config and its
currency limit are staged before the admin user is inserted, and a session-wide
rollback destroys all three (measured) before the handler adds a default
principal carrying a foreign key to the tenant it just lost.

That shape cannot be raced through the signup route itself: the route mints a
fresh tenant inside its own uncommitted transaction, so no independent writer
can reference it — the winning ``User`` insert would fail the foreign key before
it could ever collide. The other site with earlier staged work, the settings
virtual-host update, returns its answer before committing, so survival is
unobservable there. So it is graded here, in the shape production has: earlier
work staged in the transaction, then a contested write that loses.

**An unrelated constraint violation must keep its own story.** The narrowing is
only observable when the re-resolve WOULD find a winner — otherwise the
"claim no cause we cannot attribute" arm re-raises and an unnarrowed handler
looks correct. That combination (a resolvable conflict plus a failure caused by
a DIFFERENT constraint) cannot be staged through a route, where the two are the
same statement. Here the write violates the ``role`` CHECK while a resolvable
row exists, so an unnarrowed recovery reports a bad role as "already exists" —
which is exactly how a real data defect disappears behind a plausible story
about concurrency.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SASession

from src.core.database.integrity import resolve_or_write
from src.core.database.models import Tenant, User
from tests.factories import TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

CONTESTED_EMAIL = "contested-admin@example.com"
RENAMED = "renamed-before-the-contested-write"


def _commit_winner_from_independent_session(tenant_id: str, user_id: str) -> None:
    """Insert and COMMIT the winning user from a genuinely separate transaction."""
    from src.core.database.database_session import get_engine

    with SASession(bind=get_engine()) as winner:
        winner.execute(
            insert(User).values(
                tenant_id=tenant_id,
                user_id=user_id,
                email=CONTESTED_EMAIL,
                name="Winner",
                role="admin",
                is_active=True,
            )
        )
        winner.commit()


@pytest.mark.requires_db
class TestSavepointRecoveryKeepsEarlierWork:
    """resolve_or_write rolls back to a SAVEPOINT, not the whole transaction."""

    def test_lost_race_keeps_earlier_work_staged_in_the_same_transaction(self, integration_db, factory_session):
        tenant = TenantFactory()
        factory_session.commit()

        # Earlier work, staged in the same transaction as the contested write and
        # not yet committed — the stand-in for signup's tenant/adapter/currency rows.
        tenant.name = RENAMED

        loser = User(
            tenant_id=tenant.tenant_id,
            user_id="user_loser",
            email=CONTESTED_EMAIL,
            name="Loser",
            role="admin",
            is_active=True,
        )

        def adopt_existing_user():
            return factory_session.scalars(
                select(User).filter_by(tenant_id=tenant.tenant_id, email=CONTESTED_EMAIL)
            ).first()

        def write():
            # The winner commits between our pre-check and our flush. That is the
            # interleaving a real loser experiences, made deterministic.
            _commit_winner_from_independent_session(tenant.tenant_id, "user_winner")
            factory_session.add(loser)

        adopted = resolve_or_write(
            factory_session,
            conflict=adopt_existing_user,
            write=write,
            constraint="uq_users_tenant_email",
        )

        # The race arm answers with the winner — the same answer the pre-check
        # would have produced had it seen the row.
        assert adopted is not None
        assert adopted.user_id == "user_winner"

        # Recoverable: the transaction is usable and the earlier work commits.
        factory_session.commit()

        with SASession(bind=factory_session.get_bind()) as verifier:
            persisted = verifier.scalars(select(Tenant).filter_by(tenant_id=tenant.tenant_id)).first()
            assert persisted.name == RENAMED
            user_ids = sorted(
                verifier.scalars(
                    select(User.user_id).filter_by(tenant_id=tenant.tenant_id, email=CONTESTED_EMAIL)
                ).all()
            )
        assert user_ids == ["user_winner"]


@pytest.mark.requires_db
class TestNarrowingKeepsUnrelatedViolations:
    """Only the named constraint is recovered from; everything else propagates."""

    def test_check_violation_is_not_reported_as_the_conflict(self, integration_db, factory_session):
        tenant = TenantFactory()
        factory_session.commit()

        # ``role`` violates the table's CHECK — a different constraint entirely,
        # and on an email that collides with nothing.
        bad_role = User(
            tenant_id=tenant.tenant_id,
            user_id="user_bad_role",
            email="other@example.com",
            name="Bad Role",
            role="superuser",
            is_active=True,
        )

        def adopt_existing_user():
            return factory_session.scalars(
                select(User).filter_by(tenant_id=tenant.tenant_id, email=CONTESTED_EMAIL)
            ).first()

        def write():
            # A row the re-resolve CAN answer with lands in the write window, so
            # the "claim no cause we cannot attribute" arm is not what saves this
            # — only the narrowing is. Without it, a bad ``role`` is reported as
            # this row.
            _commit_winner_from_independent_session(tenant.tenant_id, "user_resolvable")
            factory_session.add(bad_role)

        with pytest.raises(IntegrityError) as raised:
            resolve_or_write(
                factory_session,
                conflict=adopt_existing_user,
                write=write,
                constraint="uq_users_tenant_email",
            )

        assert "uq_users_tenant_email" not in str(raised.value.orig)
