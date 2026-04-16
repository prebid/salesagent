"""Shared helpers for approve_*-no-lost-update regression tests.

The operations.py and workflows.py approval routes both exhibit the same
pre-migration nested-session hazard: adapter commits `media_buy.status="active"`
on its own session; a stale outer-session mutation can clobber it under bare
`sessionmaker`. Both regression tests share:

  - An ``authenticated`` pytest fixture that sets super-admin session state.
  - A ``build_approval_scenario`` helper that seeds MediaBuy + Context +
    WorkflowStep + ObjectWorkflowMapping rows for the shared scenario.
  - An ``simulate_adapter_sets_active`` stub for ``execute_approved_media_buy``.
  - An ``assert_media_buy_status_active`` post-route assertion.

These are extracted here so DRY is preserved (CLAUDE.md non-negotiable invariant).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    Context,
    CreativeAssignment,
    MediaBuy,
    ObjectWorkflowMapping,
    WorkflowStep,
)


def make_csrf_disabled_client(app):  # type: ignore[no-untyped-def]
    """Return a Flask test client context manager with CSRF + secure cookies disabled.

    Used by approval regression tests that drive POST routes via Flask's
    test_client without going through a browser / real CSRF handshake.
    """
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    app.config["SESSION_COOKIE_HTTPONLY"] = False
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()


@pytest.fixture
def authenticated(client, sample_tenant, monkeypatch):  # type: ignore[no-untyped-def]
    """Authenticate as a super admin with access to ``sample_tenant``.

    Uses ``SUPER_ADMIN_EMAILS`` so ``is_super_admin()`` passes without needing a
    database-seeded admin user. Shared by both approval regression tests.
    """
    monkeypatch.setenv("SUPER_ADMIN_EMAILS", "admin@example.com")
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "admin@example.com"}
        sess["email"] = "admin@example.com"
        sess["is_super_admin"] = True
        sess["admin_email"] = "admin@example.com"
        sess["role"] = "super_admin"
        sess["tenant_id"] = sample_tenant["tenant_id"]
    yield sess


def build_approval_scenario(
    tenant_id: str,
    principal_id: str,
    id_prefix: str,
) -> dict[str, str]:
    """Seed a MediaBuy + Context + WorkflowStep + ObjectWorkflowMapping scenario.

    Idempotent: deletes any existing rows with the same IDs before insert.
    Returns a dict of generated IDs for the caller to reference.
    """
    media_buy_id = f"mb_{id_prefix}"
    context_id = f"ctx_{id_prefix}"
    step_id = f"ws_{id_prefix}"

    with get_db_session() as session:
        session.execute(CreativeAssignment.__table__.delete().where(CreativeAssignment.media_buy_id == media_buy_id))
        session.execute(ObjectWorkflowMapping.__table__.delete().where(ObjectWorkflowMapping.object_id == media_buy_id))
        session.execute(WorkflowStep.__table__.delete().where(WorkflowStep.step_id == step_id))
        session.execute(Context.__table__.delete().where(Context.context_id == context_id))
        session.execute(MediaBuy.__table__.delete().where(MediaBuy.media_buy_id == media_buy_id))
        session.commit()

        now = datetime.now(UTC)
        today = date.today()
        session.add(
            MediaBuy(
                media_buy_id=media_buy_id,
                tenant_id=tenant_id,
                principal_id=principal_id,
                order_name="Regression Order",
                advertiser_name="Regression Advertiser",
                budget=1000,
                currency="USD",
                start_date=today,
                end_date=today,
                status="pending_approval",
                raw_request={},
            )
        )
        session.add(
            Context(
                context_id=context_id,
                tenant_id=tenant_id,
                principal_id=principal_id,
                created_at=now,
                last_activity_at=now,
            )
        )
        session.add(
            WorkflowStep(
                step_id=step_id,
                context_id=context_id,
                step_type="approval",
                owner="publisher",
                status="requires_approval",
                tool_name="create_media_buy",
                request_data={},
                created_at=now,
            )
        )
        session.add(
            ObjectWorkflowMapping(
                step_id=step_id,
                object_type="media_buy",
                object_id=media_buy_id,
                action="approve",
            )
        )
        session.commit()

    return {
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "media_buy_id": media_buy_id,
        "context_id": context_id,
        "step_id": step_id,
    }


def simulate_adapter_sets_active(media_buy_id: str, tenant_id: str) -> tuple[bool, str | None]:
    """Stand-in for ``execute_approved_media_buy()`` that mirrors the real behavior.

    Opens an INDEPENDENT session and commits ``status="active"``. This is what
    the outer handler must NOT overwrite after returning.

    Returns the `(success, error_message)` tuple shape the real helper returns.
    """
    with get_db_session() as inner_session:
        mb = inner_session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
        assert mb is not None, "Adapter stub could not find media buy"
        mb.status = "active"
        mb.updated_at = datetime.now(UTC)
        inner_session.commit()
    return True, None


def assert_media_buy_status_active(tenant_id: str, media_buy_id: str) -> None:
    """Post-route assertion that the adapter's 'active' status survived the handler.

    Raises ``AssertionError`` with a clear message if the outer-session write
    reintroduced the lost-update bug.
    """
    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
        assert mb is not None
        assert mb.status == "active", (
            f"Expected media_buy.status == 'active' (set by adapter's inner UoW), "
            f"got {mb.status!r}. This indicates the outer-session write reintroduced "
            f"the lost-update bug."
        )


def build_media_buy_row(tenant_id: str, principal_id: str, media_buy_id: str) -> MediaBuy:
    """Construct an unpersisted MediaBuy row in ``pending_approval`` state.

    Callers add it to their own session and commit.
    """
    today = date.today()
    return MediaBuy(
        media_buy_id=media_buy_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        buyer_ref="approval-lost-update-regression",
        order_name="regression-order",
        advertiser_name="regression-advertiser",
        status="pending_approval",
        budget=1000.0,
        currency="USD",
        start_date=today,
        end_date=today,
        raw_request={"products": []},
    )
