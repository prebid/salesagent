"""Integration regression: admin reject of a media buy must still fire the buyer webhook.

Bug found in PR #1567 review (adcp 6.6 / spec 3.1.1).
src/admin/blueprints/operations.py approve_media_buy() used to construct the RAW
library adcp.types.CreateMediaBuySuccessResponse in the reject webhook branch. Under
adcp 6.6 that raw type requires ``confirmed_at`` AND ``revision``; constructing it with
only media_buy_id/packages/context raises a pydantic ValidationError. The handler's
outer try/except swallows that error (flash "Error processing approval", 302), so the
buyer webhook SILENTLY never fired. The fix routes construction through our defaulted
subclass src.core.schemas.CreateMediaBuySuccess (status/confirmed_at/revision defaulted).

Behavioral guard: drive the admin reject route and assert the webhook service's
send_notification was awaited exactly once. Pre-fix the raw construction raises BEFORE
send, so send_notification is never called — that is what this test detects.
"""

from unittest.mock import ANY, AsyncMock, patch

import pytest

from src.core.context_manager import ContextManager
from src.core.webhooks.delivery import WebhookTaskContext
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.helpers.media_buy_write_seam import (
    MediaBuyState,
    assert_status_move_carried_bookkeeping,
    read_media_buy_state,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

WEBHOOK_URL = "https://buyer.example.com/adcp-webhook"


@pytest.fixture
def make_pending_media_buy(integration_db):
    """Factory for a pending-approval media buy wired for the admin approve/reject webhook path.

    Builds (via factories + ContextManager production APIs — no session.add in the
    test body) a tenant + principal, a pending_approval media buy, an active
    PushNotificationConfig at WEBHOOK_URL, and a tenant-scoped approval workflow step
    whose ObjectWorkflowMapping ties it to the media buy with action "reject". All rows
    are committed (factories persist on commit; ContextManager commits its own writes)
    so the Flask route's separate get_db_session() sees them.

    ``request_data_context``: optional dict stored as ``request_data["context"]`` on
    the workflow step — drives the approve webhook's context-echo branch.
    ``protocol``: the workflow step's originating protocol ("mcp" default; "a2a"
    drives the create_a2a_webhook_payload branch).
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.factories import (
        ALL_FACTORIES,
        MediaBuyFactory,
        MediaPackageFactory,
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        PropertyTagFactory,
        PushNotificationConfigFactory,
        TenantFactory,
    )

    engine = get_engine()
    session = SASession(bind=engine)

    def _make(request_data_context: dict | None = None, protocol: str = "mcp"):
        tenant = TenantFactory(tenant_id="reject_wh_tenant")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="reject_wh_principal",
            platform_mappings={"mock": {"id": "reject_wh_advertiser"}},
        )
        # Real product + pricing so the APPROVE path's execute_approved_media_buy
        # can reconstruct and re-execute the stored raw_request (the approve
        # webhook test drives the full adapter-execution branch).
        product = ProductFactory(tenant=tenant, product_id="prod_reject_wh")
        PricingOptionFactory(product=product)
        now = datetime.now(UTC)
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_reject_wh",
            status="pending_approval",
            start_time=now + timedelta(days=7),
            end_time=now + timedelta(days=37),
            raw_request={
                "brand": {"domain": "reject-wh.example.com"},
                "po_number": "REJECT-WH-1",
                "start_time": (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_time": (now + timedelta(days=37)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "packages": [
                    {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                ],
            },
        )
        # Persisted package row — the approve path's adapter execution reads the
        # buy's MediaPackage records ("No packages found" aborts before the webhook).
        MediaPackageFactory(
            media_buy=media_buy,
            package_id="pkg_reject_wh_1",
            package_config={
                "package_id": "pkg_reject_wh_1",
                "product_id": product.product_id,
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            },
        )
        PushNotificationConfigFactory(
            tenant=tenant,
            principal=principal,
            url=WEBHOOK_URL,
            is_active=True,
        )

        # Tenant-scoped approval workflow step + object mapping (production API).
        request_data = {
            "push_notification_config": {"url": WEBHOOK_URL},
            "protocol": protocol,
        }
        if request_data_context is not None:
            request_data["context"] = request_data_context
        cm = ContextManager()
        context = cm.create_context(
            tenant_id=tenant.tenant_id,
            principal_id=principal.principal_id,
        )
        step = cm.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="create_media_buy",
            request_data=request_data,
            object_mappings=[
                {
                    "object_type": "media_buy",
                    "object_id": media_buy.media_buy_id,
                    "action": "reject",
                }
            ],
        )

        return {
            "tenant_id": tenant.tenant_id,
            "media_buy_id": media_buy.media_buy_id,
            # The webhook's task_id. Exposed so the expected WebhookTaskContext can
            # NAME it rather than matching it with ANY -- an ANY there would let the
            # route send a webhook keyed on some other step and still pass.
            "step_id": step.step_id,
        }

    try:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = session
        yield _make
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


@pytest.fixture
def pending_reject_media_buy(make_pending_media_buy):
    """Pending media buy with NO request_data context (the absent-echo branch)."""
    return make_pending_media_buy()


@pytest.fixture
def webhook_capture():
    """Patch the protocol webhook service; yield a dict capturing the outbound call.

    Single shared capture (hoisted from per-test copies — PR #1567 round-3 nit):
    ``captured["payload"]``/``["task"]`` are set atomically by the side_effect
    (no split assert_called_once() + call_args inspection); ``captured["service"]``
    exposes the mock for call-signature assertions.

    ``task`` is a typed ``WebhookTaskContext``, not the loose ``metadata`` dict this
    used to capture. That dict was flattened from the context and rebuilt downstream
    from the PAYLOAD, which reset ``sequence_number`` and ``notification_type`` on
    the way to ``webhook_delivery_log``; the context now travels whole.
    """
    captured: dict = {}

    async def _capture(*, push_notification_config=None, payload=None, task=None):
        captured["push_notification_config"] = push_notification_config
        captured["payload"] = payload
        captured["task"] = task

    # A REAL service with only the wire call stubbed. The route dispatches through
    # notify() now (GH #1802), and notify() on a MagicMock returns a
    # MagicMock that asyncio.run refuses -- the route would swallow that as a failed
    # send and this guard would assert against an empty capture. With the real
    # object, notify() builds the payload for real and _capture still sees exactly
    # what reaches the wire.
    mock_service = ProtocolWebhookService()
    mock_service.send_notification = AsyncMock(side_effect=_capture)  # type: ignore[method-assign]
    captured["service"] = mock_service
    with patch(
        "src.admin.blueprints.operations.get_protocol_webhook_service",
        return_value=mock_service,
    ):
        yield captured


def _post_approval_action(admin_session, ids: dict, data: dict):
    """Drive the real admin approve/reject route and assert the 302 redirect."""
    resp = admin_session.post(
        f"/tenant/{ids['tenant_id']}/media-buy/{ids['media_buy_id']}/approve",
        data=data,
    )
    assert resp.status_code == 302, f"expected redirect, got {resp.status_code}"


def _parse_instant(value: str):
    """Parse a wire ISO-8601 timestamp into an aware datetime.

    Both sides of the confirmed_at comparison go through a parse: the wire carries a
    string (with a trailing "Z" that fromisoformat wants spelled "+00:00"), the column
    carries a datetime, and comparing the two textually would grade formatting rather
    than the instant.
    """
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _webhook_body(captured: dict) -> dict:
    """The outbound webhook body as a plain dict (model_dump when a model)."""
    assert "payload" in captured, "route did not send a webhook payload"
    payload = captured["payload"]
    return payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload


class TestAdminMediaBuyRejectWebhook:
    """Rejecting a pending media buy from the admin UI must fire the buyer webhook."""

    def test_reject_fires_buyer_webhook(self, authenticated_admin_session, pending_reject_media_buy, webhook_capture):
        """POST reject -> 302 and the webhook service's send_notification is awaited once.

        Regression (PR #1567): before the fix, the raw CreateMediaBuySuccessResponse
        construction ValidationErrors before send_notification is reached, and the swallowing
        try/except hides it — so send_notification is never called.
        """
        tenant_id = pending_reject_media_buy["tenant_id"]
        media_buy_id = pending_reject_media_buy["media_buy_id"]

        _post_approval_action(
            authenticated_admin_session, pending_reject_media_buy, {"action": "reject", "reason": "test"}
        )
        # The real guard: the webhook actually fired with the rejected media buy's envelope.
        # Pre-fix, the raw-type construction raises before this call, so it is never made.
        # metadata.task_type echoes the workflow step's tool_name ("create_media_buy").
        webhook_capture["service"].send_notification.assert_called_once_with(
            push_notification_config=ANY,
            payload=ANY,
            # The typed context carries the audit identifiers the webhook service
            # logs. Asserted as a whole value rather than field-by-field: it is a
            # frozen dataclass, so equality IS the field-by-field check, and a field
            # added to it without a value here fails rather than passing silently.
            task=WebhookTaskContext(
                task_id=pending_reject_media_buy["step_id"],
                task_type="create_media_buy",
                tenant_id=tenant_id,
                principal_id="reject_wh_principal",
                media_buy_id=media_buy_id,
                sequence_number=1,
                notification_type=None,
            ),
        )

    def test_reject_webhook_does_not_embed_completed_success(
        self, authenticated_admin_session, pending_reject_media_buy, webhook_capture
    ):
        """The rejected media buy webhook body must not embed a completed success result.

        Regression (PR #1567, adcp 6.6 / spec 3.1.1): the reject branch built the
        embedded ``result`` as CreateMediaBuySuccess, which now defaults status="completed",
        confirmed_at=now, revision=1. So the outbound body had a correct OUTER status="rejected"
        but an embedded result asserting the buy COMPLETED — a Success envelope cannot represent
        a rejection. Assert the embedded result does not claim completion.
        """
        _post_approval_action(
            authenticated_admin_session, pending_reject_media_buy, {"action": "reject", "reason": "test"}
        )
        body = _webhook_body(webhook_capture)

        # Outer envelope correctly reports the rejection.
        assert body["status"] == "rejected", f"outer status should be rejected, got {body.get('status')!r}"

        # The embedded result must NOT assert completion inside a rejection payload.
        embedded = body.get("result") or {}
        assert embedded.get("status") != "completed", (
            f"rejected webhook embeds a result claiming status={embedded.get('status')!r}; "
            "a rejection must not carry a completed-success envelope"
        )
        assert not embedded.get("confirmed_at"), (
            "rejected webhook embeds confirmed_at — the buy was rejected, not confirmed/completed"
        )

    def test_reject_webhook_embeds_wire_code_not_internal_code(
        self, authenticated_admin_session, pending_reject_media_buy, webhook_capture
    ):
        """The rejected webhook body carries the WIRE error code POLICY_VIOLATION.

        Regression for PR #1567 round-2 blocker 1: the reject
        branch hand-picked the INTERNAL code MEDIA_BUY_REJECTED for the embedded
        Error. src/core/exceptions.py maps MEDIA_BUY_REJECTED -> POLICY_VIOLATION
        and lists it in INTERNAL_CODES ("Seller declined the buy; wire emits
        POLICY_VIOLATION"); the tool path emits POLICY_VIOLATION for this same
        event (AdCPMediaBuyRejectedError). The webhook must not leak the internal
        token to the buyer agent — both paths carry the identical wire code.
        """
        _post_approval_action(
            authenticated_admin_session, pending_reject_media_buy, {"action": "reject", "reason": "Budget too low"}
        )
        body = _webhook_body(webhook_capture)

        embedded = body.get("result") or {}
        errors = embedded.get("errors") or []
        assert errors, f"rejected webhook must embed an errors array, got result={embedded!r}"
        assert errors[0]["code"] == "POLICY_VIOLATION", (
            f"rejected webhook leaked code {errors[0]['code']!r} to the buyer — the wire code for a "
            "seller rejection is POLICY_VIOLATION (ERROR_CODE_MAPPING; MEDIA_BUY_REJECTED is internal)"
        )
        assert "Budget too low" in errors[0].get("message", ""), (
            "rejection reason must reach the buyer in the error message"
        )

    def test_approve_webhook_embeds_confirmed_success_via_factory(
        self, authenticated_admin_session, pending_reject_media_buy, webhook_capture
    ):
        """The APPROVED media buy webhook embeds a confirmed completed Success.

        Pin for PR #1567 round-2 cleanup (approve site routed through the sync_success()
        factory): the buy IS committed at approval time, so the embedded result
        must keep asserting completion — status="completed", the media_buy_id, NO
        leaked internal fields, and confirmed_at/revision AGREEING WITH THE PERSISTED
        ROW (they no longer come from subclass defaults; the repository owns both). Guards the factory switch against any wire drift and
        pins that approve stays a Success (never the Submitted variant the
        pending-approval CREATE path now emits — PR #1567 round-2 item 2).
        """
        tenant_id = pending_reject_media_buy["tenant_id"]
        media_buy_id = pending_reject_media_buy["media_buy_id"]

        _post_approval_action(authenticated_admin_session, pending_reject_media_buy, {"action": "approve"})
        body = _webhook_body(webhook_capture)

        assert body["status"] == "completed", f"outer status should be completed, got {body.get('status')!r}"
        embedded = body.get("result") or {}
        assert embedded.get("media_buy_id") == media_buy_id
        assert embedded.get("status") == "completed", (
            f"approved webhook must embed a completed Success, got status={embedded.get('status')!r}"
        )
        # Both are read off the PERSISTED row, not asserted as constants. They used to
        # be pinned at the schema defaults (a truthy confirmed_at and revision == 1),
        # which is precisely what made the response a second producer: the row here is
        # several bumps past 1 by the time approval completes, and the old assertion
        # passed only because the envelope was reporting a fabricated value.
        persisted = read_media_buy_state(tenant_id, media_buy_id)
        assert embedded.get("confirmed_at"), "approved (committed) buy must carry confirmed_at"
        assert _parse_instant(embedded["confirmed_at"]) == persisted.confirmed_at, (
            f"embedded confirmed_at {embedded['confirmed_at']!r} disagrees with the persisted "
            f"column {persisted.confirmed_at!r} — one producer per field"
        )
        assert embedded.get("revision") == persisted.revision, (
            f"embedded revision {embedded.get('revision')!r} disagrees with the persisted column "
            f"{persisted.revision!r}; get_media_buys publishes the column, so a buyer taking the "
            f"token from this webhook would hand back a stale one"
        )
        assert "workflow_step_id" not in embedded, "internal workflow_step_id must not leak onto the wire"
        # Absent-context branch pin (PR #1567 round-3): with no "context" key in
        # the workflow step's request_data, the echo path stays dormant and the
        # embedded result must not invent one (exclude_none omits the None field).
        assert embedded.get("context") is None, (
            f"approve webhook with no stored request context must not embed one, got {embedded.get('context')!r}"
        )
        # The typed context carries the audit identifiers the webhook service logs.
        # Compared as a whole frozen value: equality IS the field-by-field check,
        # and a field added to the type without a value here fails rather than
        # passing silently.
        assert webhook_capture["task"] == WebhookTaskContext(
            task_id=pending_reject_media_buy["step_id"],
            task_type="create_media_buy",
            tenant_id=tenant_id,
            principal_id="reject_wh_principal",
            media_buy_id=media_buy_id,
            sequence_number=1,
            notification_type=None,
        )

    def test_reject_bumps_revision_without_confirming(
        self, authenticated_admin_session, pending_reject_media_buy, webhook_capture
    ):
        """Rejecting the buy is a mutation of the buy: revision moves, confirmation does not.

        approve_media_buy's reject arm assigns media_buy.status = "rejected" directly, so the
        buy changes state while ``revision`` — the buyer's optimistic-concurrency token, which
        must strictly increase on every mutation — stays where it was. Routing the write through
        MediaBuyRepository.update_status is what moves it. "rejected" is NOT in
        models._SELLER_COMMITTED_STATUSES, so this transition must NOT stamp confirmed_at:
        a rejection is the seller declining to commit.
        """
        tenant_id = pending_reject_media_buy["tenant_id"]
        media_buy_id = pending_reject_media_buy["media_buy_id"]

        before = read_media_buy_state(tenant_id, media_buy_id)
        before_revision = before.revision
        assert before.status == "pending_approval"
        assert before.confirmed_at is None, "fixture must start with an unstamped confirmation instant"

        _post_approval_action(
            authenticated_admin_session, pending_reject_media_buy, {"action": "reject", "reason": "test"}
        )

        after = read_media_buy_state(tenant_id, media_buy_id)
        # confirms=False: "rejected" is NOT a committed status, so this move must not
        # mint a commitment instant the seller never made.
        assert_status_move_carried_bookkeeping(
            MediaBuyState(status="pending_approval", revision=before_revision, confirmed_at=None),
            after,
            expected_status="rejected",
            confirms=False,
            subject="rejecting the media buy",
        )

    def test_approve_bumps_revision_for_every_status_move(
        self, authenticated_admin_session, pending_reject_media_buy, webhook_capture
    ):
        """An admin approval is ONE status move, so revision must move exactly once.

        THE CONTRACT CHANGED, and this test previously graded the old one: ``bumps=2``
        and ``expected_status="active"``. That was two committed writes for a single
        approval — ``approve_media_buy`` resolved the flight-window status and committed
        it BEFORE calling the adapter, and ``execute_approved_media_buy`` then committed
        an unconditional ``ACTIVE`` over it. Both defects are real and both are named in
        the finding list: the buy was published as ``active`` before its flight window
        opened, and a buyer polling on ``revision`` was handed a token that
        skipped a value for one logical event.

        ``execute_approved_media_buy`` is now the sole post-adapter writer, and the route
        touches nothing after calling it. So the delta is 1, and the status is the
        flight-window rule's answer — ``scheduled`` here, because this fixture's buy is
        approved before its window opens.

        ``bumps`` is an EXACT delta, which is what makes this the grader for the
        single-writer property: if any caller reintroduces a second write, this fails
        rather than looking like a smaller-but-positive increase.
        """
        tenant_id = pending_reject_media_buy["tenant_id"]
        media_buy_id = pending_reject_media_buy["media_buy_id"]

        before = read_media_buy_state(tenant_id, media_buy_id)
        before_revision = before.revision
        assert before.confirmed_at is None, "fixture must start with an unstamped confirmation instant"

        _post_approval_action(authenticated_admin_session, pending_reject_media_buy, {"action": "approve"})

        after = read_media_buy_state(tenant_id, media_buy_id)
        assert after.approved_by == "test@example.com"
        assert_status_move_carried_bookkeeping(
            MediaBuyState(status="pending_approval", revision=before_revision, confirmed_at=None),
            after,
            expected_status="scheduled",
            confirms=True,
            subject="admin approval (pending_approval -> scheduled)",
        )

    def test_a2a_reject_webhook_carries_policy_violation_task(
        self, authenticated_admin_session, make_pending_media_buy, webhook_capture
    ):
        """An A2A-originated reject fires a protobuf Task carrying POLICY_VIOLATION, not a Success.

        Regression for PR #1567 round-3 (ChrisHuie review): the protocol=="a2a"
        branch of the reject webhook (create_a2a_webhook_payload) had ZERO test
        references — the reject fixture hardcoded protocol "mcp", so what this PR
        changed inside that branch (the typed CreateMediaBuyError carrying the
        wire code POLICY_VIOLATION) was unpinned on A2A. The A2A envelope framing
        (protobuf Task with artifacts[].parts[].data) differs from the MCP
        payload, so the passing MCP test does not cover it. Asserts on the actual
        protobuf Task create_a2a_webhook_payload emits.
        """
        from google.protobuf.json_format import MessageToDict

        ids = make_pending_media_buy(protocol="a2a")

        _post_approval_action(authenticated_admin_session, ids, {"action": "reject", "reason": "Budget too low"})
        assert "payload" in webhook_capture, "A2A reject route did not send a webhook payload"
        task = webhook_capture["payload"]
        # Terminated statuses produce a protobuf a2a Task (create_a2a_webhook_payload contract).
        body = MessageToDict(task, preserving_proto_field_name=True)

        assert body.get("status", {}).get("state") == "TASK_STATE_REJECTED", (
            f"A2A reject Task must carry the rejected state, got {body.get('status')!r}"
        )
        artifacts = body.get("artifacts") or []
        assert artifacts, f"A2A reject Task must embed the result artifact, got {body!r}"
        datas = [part.get("data", {}).get("data", part.get("data", {})) for part in artifacts[0].get("parts", [])]
        result_data = next((d for d in datas if isinstance(d, dict) and "errors" in d), None)
        assert result_data is not None, f"A2A reject artifact must carry the errors payload, got {artifacts!r}"
        errors = result_data["errors"]
        assert errors and errors[0].get("code") == "POLICY_VIOLATION", (
            f"A2A reject artifact leaked code {errors and errors[0].get('code')!r} — the wire code for a "
            "seller rejection is POLICY_VIOLATION (same contract the MCP sibling pins)"
        )
        assert "Budget too low" in errors[0].get("message", ""), (
            "rejection reason must reach the buyer in the A2A error message"
        )
        # A rejection must not embed a completed-Success shape in the artifact.
        assert result_data.get("status") != "completed", (
            f"A2A reject artifact claims status={result_data.get('status')!r} — a rejection "
            "must not carry a completed-success envelope"
        )
        assert not result_data.get("confirmed_at"), (
            "A2A reject artifact embeds confirmed_at — the buy was rejected, not confirmed"
        )

    def test_approve_webhook_echoes_buyer_request_context(
        self, authenticated_admin_session, make_pending_media_buy, webhook_capture
    ):
        """The approve webhook echoes the buyer's create_media_buy request context.

        Oracle for PR #1567 round-3 (ChrisHuie review): 4f60cbf4c resolved the
        context TODO by echoing request_data["context"], but no fixture carried a
        context, so the non-None echo path never executed — reverting the echo to
        context={} (or dropping it) kept every test green. This drives the real
        admin approve route with a stored buyer context and asserts the outbound
        webhook body's embedded result echoes it verbatim (ContextObject is an
        extra=allow passthrough — arbitrary buyer keys survive).
        """
        buyer_context = {"correlation_id": "corr-approve-echo-1", "buyer_ref": "buyer-ref-42"}
        ids = make_pending_media_buy(request_data_context=buyer_context)

        _post_approval_action(authenticated_admin_session, ids, {"action": "approve"})
        body = _webhook_body(webhook_capture)

        embedded = body.get("result") or {}
        assert embedded.get("context") == buyer_context, (
            f"approve webhook must echo the buyer's request context verbatim, "
            f"got {embedded.get('context')!r} (expected {buyer_context!r})"
        )
