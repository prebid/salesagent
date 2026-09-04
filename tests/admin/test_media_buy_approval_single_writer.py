"""Who writes the media buy row when an admin approves it — and how many times.

``execute_approved_media_buy`` is the sole post-adapter writer, and the three admin
approval routes — ``workflows.approve_workflow_step``, ``operations.approve_media_buy``,
``creatives.approve_creative`` — must not touch the row after calling it. One approval
is therefore exactly ONE committed status write, carrying the flight-window status the
shared rule resolves plus the approval stamps.

Each route used to bolt its own post-adapter orchestration around the callee, and the
three disagreed with it and with each other. Nothing graded the combination, because
every test patched the callee out — which is precisely the mocking that let them
diverge. These tests run the REAL callee and mock only the ad-server boundary, so both
units-of-work, every commit, and every session close run for real against PostgreSQL.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.admin.app import create_app
from src.core.database.models import Creative as CreativeModel
from tests.helpers.media_buy_approval import (
    ADAPTER_BOUNDARY,
    adapter_failure,
    adapter_success,
    attach_approved_creative,
    login_as,
    seed_pending_buy,
    uploadable_creative,
)
from tests.helpers.media_buy_write_seam import read_media_buy_state

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


@pytest.fixture
def client():
    """Flask test client with test configuration."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as test_client:
        yield test_client


def _read_creative_status(session, tenant_id: str, creative_id: str) -> str | None:
    """This tenant's creative status, read past any stale identity map."""
    session.expire_all()
    row = session.scalars(select(CreativeModel).filter_by(tenant_id=tenant_id, creative_id=creative_id)).first()
    return None if row is None else row.status


class TestOnePostAdapterWriter:
    """One approval, one committed status write, and the flight-window rule decides it."""

    def test_operations_approval_does_not_activate_a_buy_that_has_not_started(self, client, factory_session):
        """The same defect on the other route, where the unconditional write is the LAST one.

        ``operations.approve_media_buy`` writes the flight-window status BEFORE the
        adapter call, so on the success path the callee's unconditional
        ``PersistedMediaBuyStatus.ACTIVE`` is what survives. A buy seven days away from
        its flight window is persisted ``active``: the column disagrees with the
        calendar, and it is the column the wire projection reads.
        """
        seeded = seed_pending_buy(starts_in_days=7)
        attach_approved_creative(seeded)
        login_as(client, tenant_id=seeded.tenant_id, email="approver@example.com")

        before = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            response = client.post(
                f"/tenant/{seeded.tenant_id}/media-buy/{seeded.media_buy_id}/approve",
                data={"action": "approve"},
            )
        assert response.status_code in (200, 302), response.data

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert after.status == "pending_start", (
            f"a buy approved seven days before its flight window opens is pending_start, but this "
            f"route persisted {after.status!r}. The route resolves the flight-window status and "
            f"commits it BEFORE calling the adapter, so execute_approved_media_buy's "
            f"unconditional ACTIVE is written last and wins"
        )
        assert after.revision == (before.revision or 0) + 1, (
            f"one approval committed {after.revision - (before.revision or 0)} status writes "
            f"(revision {before.revision} -> {after.revision}); the route and the callee each "
            f"write the row"
        )

    def test_adapter_failure_leaves_the_buy_unconfirmed(self, client, factory_session):
        """The failure arm inherits a commitment the seller never made.

        ``operations.approve_media_buy`` writes the flight-window status and commits
        BEFORE calling the adapter (``operations.py:433``). ``pending_start`` is a
        seller-committed status, so that write stamps ``confirmed_at`` — and
        ``confirmed_at`` is write-once. When the adapter then fails and the route
        writes ``failed`` over it, the stamp survives: the row says the seller
        committed to a buy that never reached the ad server.
        """
        seeded = seed_pending_buy(starts_in_days=7)
        attach_approved_creative(seeded)
        login_as(client, tenant_id=seeded.tenant_id, email="approver@example.com")

        with patch(ADAPTER_BOUNDARY, side_effect=adapter_failure):
            response = client.post(
                f"/tenant/{seeded.tenant_id}/media-buy/{seeded.media_buy_id}/approve",
                data={"action": "approve"},
            )
        assert response.status_code in (200, 302), response.data

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert after.status == "failed", (
            f"an approval whose adapter call failed leaves the buy 'failed', not {after.status!r}"
        )
        assert after.confirmed_at is None, (
            f"the adapter never created the order, yet confirmed_at is stamped {after.confirmed_at!r}. "
            f"The route committed the flight-window status before calling the adapter, and "
            f"confirmed_at is write-once — so the failure arm inherits a seller commitment that "
            f"was never made"
        )


class TestCreativeGateIsTenantScoped:
    """The creative gate must read this tenant's creatives, and only this tenant's."""

    def test_a_foreign_tenants_creative_does_not_block_this_approval(self, client, factory_session):
        """``creative_id`` is buyer-supplied, so the gate must scope by tenant.

        The route used to select ``Creative`` by ``creative_id`` alone. The ``Creative``
        primary key is ``(creative_id, tenant_id, principal_id)``, so the same
        ``creative_id`` legitimately exists under another tenant — and that row was
        returned and read for its status. One buyer parking an unapproved creative under
        a colliding id stopped another tenant's approval from ever reaching the ad
        server. The gate now lives behind a tenant-scoped repository, so no caller
        assembles the query and none can omit the predicate.
        """
        from tests.factories import CreativeAssignmentFactory, CreativeFactory, PrincipalFactory, TenantFactory

        seeded = seed_pending_buy(starts_in_days=7)
        shared_creative_id = f"cr_shared_{uuid.uuid4().hex[:8]}"

        mine = uploadable_creative(
            CreativeFactory,
            tenant=seeded.tenant,
            principal=seeded.principal,
            creative_id=shared_creative_id,
            status="approved",
        )
        CreativeAssignmentFactory(creative=mine, media_buy=seeded.media_buy, package_id="pkg_gate_1")

        # Another tenant's creative, same buyer-supplied id, NOT approved.
        foreign_tenant = TenantFactory(tenant_id=f"t_other_{uuid.uuid4().hex[:8]}")
        foreign_principal = PrincipalFactory(tenant=foreign_tenant)
        CreativeFactory(
            tenant=foreign_tenant,
            principal=foreign_principal,
            creative_id=shared_creative_id,
            status="pending",
        )

        login_as(client, tenant_id=seeded.tenant_id, email="approver@example.com")
        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            response = client.post(
                f"/tenant/{seeded.tenant_id}/workflows/{seeded.context_id}/steps/{seeded.step_id}/approve",
                content_type="application/json",
                json={},
            )
        assert response.status_code == 200, response.data

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert after.status == "pending_start", (
            f"this tenant's only assigned creative {shared_creative_id!r} is approved, so the buy "
            f"should have gone to the ad server and persisted 'pending_start' — it persisted "
            f"{after.status!r} instead. The gate query selects Creative by creative_id with no "
            f"tenant predicate, so ANOTHER tenant's row with the same buyer-supplied id was read "
            f"for its status and held this approval"
        )


class TestApprovalRoutesRefuseForeignTenants:
    """``@require_tenant_access()`` on the two approval routes that lack a grader.

    Graded the same way as ``test_approval_from_another_tenants_session_is_refused`` in
    ``test_workflows_blueprint.py``: on the PERSISTED STATE, never the status code. A
    route that redirects to a login page still returns 200 for the redirect target, so
    "did the write happen" is the only question that cannot be answered two ways.
    """

    def test_foreign_session_cannot_approve_a_media_buy(self, client, factory_session):
        """``operations.approve_media_buy`` — the buy must not move."""
        seeded = seed_pending_buy(starts_in_days=7)
        before = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)

        login_as(client, tenant_id="some_other_tenant", email="outsider@example.com", super_admin=False)
        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            client.post(
                f"/tenant/{seeded.tenant_id}/media-buy/{seeded.media_buy_id}/approve",
                data={"action": "approve"},
            )

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert after.status == before.status, (
            f"a session scoped to another tenant moved this buy from {before.status!r} to "
            f"{after.status!r}; require_tenant_access() is not holding on approve_media_buy"
        )
        assert after.confirmed_at is None, "an outsider's request stamped the seller-commitment instant"

        # Positive control: the same request from a tenant-scoped admin of THIS tenant
        # does move the buy. Without it, a route that 404s for an unrelated reason
        # would satisfy the refusal assertion above.
        login_as(client, tenant_id=seeded.tenant_id, email="insider@example.com", super_admin=False)
        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            client.post(
                f"/tenant/{seeded.tenant_id}/media-buy/{seeded.media_buy_id}/approve",
                data={"action": "approve"},
            )
        allowed = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert allowed.status != before.status, (
            f"control failed: this tenant's own admin could not move the buy either — it is still "
            f"{allowed.status!r}, so the refusal above proves nothing about require_tenant_access()"
        )

    def test_foreign_session_cannot_approve_a_creative(self, client, factory_session):
        """``creatives.approve_creative`` — the creative must not move."""
        from tests.factories import CreativeFactory

        seeded = seed_pending_buy(starts_in_days=7)
        creative = CreativeFactory(tenant=seeded.tenant, principal=seeded.principal, status="pending")
        creative_id = creative.creative_id

        login_as(client, tenant_id="some_other_tenant", email="outsider@example.com", super_admin=False)
        client.post(
            f"/tenant/{seeded.tenant_id}/creatives/review/{creative_id}/approve",
            content_type="application/json",
            json={"approved_by": "outsider@example.com"},
        )

        assert _read_creative_status(factory_session, seeded.tenant_id, creative_id) == "pending", (
            "a session scoped to another tenant approved this tenant's creative; "
            "require_tenant_access() is not holding on approve_creative"
        )

        # Positive control, as above: this tenant's own admin CAN approve it.
        login_as(client, tenant_id=seeded.tenant_id, email="insider@example.com", super_admin=False)
        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            client.post(
                f"/tenant/{seeded.tenant_id}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={"approved_by": "insider@example.com"},
            )
        assert _read_creative_status(factory_session, seeded.tenant_id, creative_id) == "approved", (
            "control failed: this tenant's own admin could not approve the creative either, so the "
            "refusal above proves nothing about require_tenant_access()"
        )
