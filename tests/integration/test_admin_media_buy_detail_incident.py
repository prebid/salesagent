"""Render test: the possible-duplicate reconcile incident banner on media_buy_detail.

The #1637 finalization lease records ``finalize_reconcile_incident_at`` /
``finalize_reconcile_incident_reason`` on the MediaBuy when a worker's ad-server
call ran but could not confirm single ownership. templates/media_buy_detail.html
renders an ownership-independent warning banner from those fields, plus a
re-approve affordance when the buy is (re)approvable — the operator's only path
back into finalization after reconciling the remote graph.

This drives the REAL admin GET route (no full Docker stack; Flask test client on
the integration Postgres) and asserts:

* the banner block renders when the incident marker is set;
* the operator-facing incident reason is HTML-ESCAPED (the reason is free text
  recorded from an exception message — a ``<script>`` payload must not execute
  in the admin UI);
* the re-approve affordance renders for an approvable buy with an actionable
  create step (pins the media_buy_approvable + _select_actionable_create_step
  wiring of the detail route).
"""

from datetime import UTC, datetime

import pytest

from src.core.context_manager import ContextManager

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Free-text reason with an active-content payload: proves Jinja autoescaping, not
# just that the text made it into the page.
INCIDENT_REASON = "duplicate suspected after lease loss <script>alert('xss')</script>"


@pytest.fixture
def incident_media_buy(integration_db):
    """A pending-approval media buy flagged with a reconcile incident.

    Factories + ContextManager production APIs only (no session.add in the test
    body). The workflow create-approval step + ObjectWorkflowMapping make the
    detail route's ``_select_actionable_create_step`` return a step, so the
    re-approve affordance is expected to render.
    """
    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.factories import ALL_FACTORIES, MediaBuyFactory, PrincipalFactory, TenantFactory

    engine = get_engine()
    session = SASession(bind=engine)
    try:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = session

        tenant = TenantFactory(tenant_id="incident_banner_tenant")
        principal = PrincipalFactory(tenant=tenant, principal_id="incident_banner_principal")
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_incident_banner",
            status="pending_approval",
            finalize_reconcile_incident_at=datetime.now(UTC),
            finalize_reconcile_incident_reason=INCIDENT_REASON,
        )

        cm = ContextManager()
        context = cm.create_context(
            tenant_id=tenant.tenant_id,
            principal_id=principal.principal_id,
        )
        cm.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="create_media_buy",
            request_data={},
            object_mappings=[
                {
                    "object_type": "media_buy",
                    "object_id": media_buy.media_buy_id,
                    "action": "approve",
                }
            ],
        )

        yield {
            "tenant_id": tenant.tenant_id,
            "media_buy_id": media_buy.media_buy_id,
        }
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


def test_incident_banner_renders_escaped_reason_and_reapprove_affordance(
    authenticated_admin_session, incident_media_buy
):
    """GET media_buy_detail renders the incident banner, escaped reason, re-approve link."""
    tenant_id = incident_media_buy["tenant_id"]
    media_buy_id = incident_media_buy["media_buy_id"]

    resp = authenticated_admin_session.get(f"/tenant/{tenant_id}/media-buy/{media_buy_id}")
    assert resp.status_code == 200, f"detail page failed to render: {resp.status_code}"
    html = resp.get_data(as_text=True)

    # Banner block present (keyed on finalize_reconcile_incident_at).
    assert "Possible duplicate remote order" in html, "incident banner block missing from media_buy_detail"

    # The free-text reason renders ESCAPED — the payload text is visible but inert.
    assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in html, (
        "incident reason must render HTML-escaped in the banner"
    )
    assert "<script>alert('xss')</script>" not in html, "incident reason rendered UNESCAPED — XSS in the admin UI"

    # Approvable buy + actionable create step -> the reconcile-then-re-approve
    # affordance renders (pins the media_buy_approvable / pending_approval_step wiring).
    assert "re-approve this media buy" in html, (
        "re-approve affordance missing for an approvable buy with an actionable create step"
    )
