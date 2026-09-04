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
from src.core.database.models import PersistedMediaBuyStatus
from src.core.tools.media_buy_create import ApprovalOutcome, ApprovalResult
from src.core.webhooks.delivery import WebhookTaskContext
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.factories.creative_asset import build_assets, image_spec
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
    test body) a tenant + principal, a pending_approval media buy, optionally with
    a CreativeAssignment (approved by default — required for the approve
    finalize/webhook path after #1696), an active PushNotificationConfig at
    WEBHOOK_URL, and a tenant-scoped approval workflow step whose
    ObjectWorkflowMapping ties it to the media buy with action "reject". All rows
    are committed (factories persist on commit; ContextManager commits its own
    writes) so the Flask route's separate get_db_session() sees them.

    ``request_data_context``: optional dict stored as ``request_data["context"]`` on
    the workflow step — drives the approve webhook's context-echo branch.
    ``protocol``: the workflow step's originating protocol ("mcp" default; "a2a"
    drives the create_a2a_webhook_payload branch).
    ``include_assignment``: when False, omit CreativeAssignment (zero-assignment hold).
    ``creative_approved``: when True (default) the assigned creative is approved;
    when False the creative stays pending (unapproved_creatives hold).
    ``tenant_id`` / ``media_buy_id``: override defaults for multi-scenario tests.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.factories import (
        ALL_FACTORIES,
        CreativeAssignmentFactory,
        CreativeFactory,
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

    def _make(
        request_data_context: dict | None = None,
        protocol: str = "mcp",
        *,
        include_assignment: bool = True,
        creative_approved: bool = True,
        starts_in_days: int = 7,
        tenant_id: str = "reject_wh_tenant",
        media_buy_id: str = "mb_reject_wh",
        principal_id: str = "reject_wh_principal",
    ):
        tenant = TenantFactory(tenant_id=tenant_id)
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id=principal_id,
            platform_mappings={"mock": {"id": f"{principal_id}_advertiser"}},
        )
        # Real product + pricing so the APPROVE path's execute_approved_media_buy
        # can reconstruct and re-execute the stored raw_request (the approve
        # webhook test drives the full adapter-execution branch).
        product = ProductFactory(tenant=tenant, product_id=f"prod_{media_buy_id}")
        PricingOptionFactory(product=product)
        now = datetime.now(UTC)
        start_time = now + timedelta(days=starts_in_days)
        end_time = start_time + timedelta(days=30)
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=media_buy_id,
            status="pending_approval",
            start_time=start_time,
            end_time=end_time,
            raw_request={
                "brand": {"domain": "reject-wh.example.com"},
                "po_number": "REJECT-WH-1",
                "start_time": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_time": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "packages": [
                    {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                ],
            },
        )
        # Persisted package row — the approve path's adapter execution reads the
        # buy's MediaPackage records ("No packages found" aborts before the webhook).
        pkg_id = f"pkg_{media_buy_id}_1"
        MediaPackageFactory(
            media_buy=media_buy,
            package_id=pkg_id,
            package_config={
                "package_id": pkg_id,
                "product_id": product.product_id,
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            },
        )
        # Creative assignment required for finalize (#1696 Hold): zero assignments
        # parks at pending_creatives and skips adapter + approve webhook.
        if include_assignment:
            creative_kwargs = {
                "tenant": tenant,
                "principal": principal,
                "creative_id": f"cre_{media_buy_id}_1",
                "data": {"assets": build_assets(image_spec("banner_image"))},
            }
            if creative_approved:
                creative_kwargs["approved"] = True
            else:
                creative_kwargs["status"] = "pending"
            creative = CreativeFactory(**creative_kwargs)
            CreativeAssignmentFactory(
                creative=creative,
                media_buy=media_buy,
                package_id=pkg_id,
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
            "principal_id": principal.principal_id,
            "context_id": context.context_id,
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
    # notify() now (#1567), and notify() on a MagicMock returns a
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


def _a2a_artifact_datas(payload) -> list[dict]:
    """Unwrap ``create_a2a_webhook_payload`` framing into artifact part data dicts.

    Mirrors the double-nested ``part.data.data`` fallback used by both A2A
    approve and reject webhook assertions (KM Aug-05 DRY).
    """
    from google.protobuf.json_format import MessageToDict

    body = MessageToDict(payload, preserving_proto_field_name=True)
    artifacts = body.get("artifacts") or []
    if not artifacts:
        return []
    return [part.get("data", {}).get("data", part.get("data", {})) for part in artifacts[0].get("parts", [])]


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
        persisted = read_media_buy_state(tenant_id, media_buy_id)

        assert body["status"] == "completed", f"outer status should be completed, got {body.get('status')!r}"
        embedded = body.get("result") or {}
        assert embedded.get("media_buy_id") == media_buy_id
        assert embedded.get("status") == "completed", (
            f"approved webhook must embed a completed Success, got status={embedded.get('status')!r}"
        )
        # Domain field (media-buy-status.json@3.1.1): resolve_canonical_status
        # date-refines a future-start buy to pending_start (ORM also persists
        # pending_start on the ready arm). Protocol TaskStatus on the Success
        # envelope stays "completed".
        assert embedded.get("media_buy_status") == "pending_start", (
            f"approved webhook must embed media_buy_status matching get_media_buys "
            f"(canonical), got media_buy_status={embedded.get('media_buy_status')!r}"
        )
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
        # The typed task context travels whole to send_notification (not a loose metadata dict).
        assert webhook_capture["task"] == WebhookTaskContext(
            task_id=pending_reject_media_buy["step_id"],
            task_type="create_media_buy",
            tenant_id=tenant_id,
            principal_id="reject_wh_principal",
            media_buy_id=media_buy_id,
            sequence_number=1,
            notification_type=None,
        )
        # Ready-arm provenance: session operator email on the MediaBuy row.
        _assert_persisted_status(pending_reject_media_buy, "pending_start", approved_by="test@example.com")

    def test_approve_in_flight_webhook_persists_active_and_wire_matches(
        self, authenticated_admin_session, make_pending_media_buy, webhook_capture
    ):
        """In-flight approve: column and wire both grade ``active`` (Chris Aug-28 B2).

        Pre-flight fixtures cannot discriminate ``pending_start`` from legacy
        ``scheduled`` on the wire; an in-flight window pins both the persisted
        column and ``media_buy_status`` on the webhook.
        """
        ids = make_pending_media_buy(
            starts_in_days=-1,
            media_buy_id="mb_inflight_wh",
            tenant_id="reject_wh_inflight",
            principal_id="reject_wh_inflight_principal",
        )
        tenant_id = ids["tenant_id"]
        media_buy_id = ids["media_buy_id"]

        _post_approval_action(authenticated_admin_session, ids, {"action": "approve"})
        body = _webhook_body(webhook_capture)
        persisted = read_media_buy_state(tenant_id, media_buy_id)

        embedded = body.get("result") or {}
        assert persisted.status == "active", f"in-flight approve must persist active, got {persisted.status!r}"
        assert embedded.get("media_buy_status") == "active", (
            f"in-flight approve webhook must embed media_buy_status=active, got {embedded.get('media_buy_status')!r}"
        )
        _assert_persisted_status(ids, "active", approved_by="test@example.com")

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
        flight-window rule's answer — ``pending_start`` here, because this fixture's buy is
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
            expected_status="pending_start",
            confirms=True,
            subject="admin approval (pending_approval -> pending_start)",
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
        (        protobuf Task with artifacts[].parts[].data) differs from the MCP
        payload, so the passing MCP test does not cover it. Asserts on the actual
        protobuf Task create_a2a_webhook_payload emits.
        """
        ids = make_pending_media_buy(protocol="a2a")

        _post_approval_action(authenticated_admin_session, ids, {"action": "reject", "reason": "Budget too low"})
        assert "payload" in webhook_capture, "A2A reject route did not send a webhook payload"
        task = webhook_capture["payload"]
        # Terminated statuses produce a protobuf a2a Task (create_a2a_webhook_payload contract).
        from google.protobuf.json_format import MessageToDict

        body = MessageToDict(task, preserving_proto_field_name=True)

        assert body.get("status", {}).get("state") == "TASK_STATE_REJECTED", (
            f"A2A reject Task must carry the rejected state, got {body.get('status')!r}"
        )
        datas = _a2a_artifact_datas(task)
        assert datas, f"A2A reject Task must embed the result artifact, got {body!r}"
        result_data = next((d for d in datas if isinstance(d, dict) and "errors" in d), None)
        assert result_data is not None, f"A2A reject artifact must carry the errors payload, got {datas!r}"
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
        assert embedded.get("media_buy_status") == "pending_start", (
            f"approve echo webhook must also embed canonical media_buy_status, got {embedded.get('media_buy_status')!r}"
        )

    def test_a2a_approve_webhook_embeds_media_buy_status(
        self, authenticated_admin_session, make_pending_media_buy, webhook_capture
    ):
        """A2A approve embeds the same canonical media_buy_status as the MCP sibling.

        KM Jul29: both approve assertions previously ran under protocol=\"mcp\" only;
        the a2a create_a2a_webhook_payload branch had zero coverage for the dual-emit
        media_buy_status field. Mirror test_a2a_reject_webhook_carries_policy_violation_task
        for the approve Success path.
        """
        ids = make_pending_media_buy(protocol="a2a", media_buy_id="mb_a2a_approve", tenant_id="a2a_appr_tenant")

        _post_approval_action(authenticated_admin_session, ids, {"action": "approve"})
        assert "payload" in webhook_capture, "A2A approve route did not send a webhook payload"
        task = webhook_capture["payload"]

        datas = _a2a_artifact_datas(task)
        assert datas, f"A2A approve Task must embed the result artifact, got empty datas from {task!r}"
        result_data = next(
            (d for d in datas if isinstance(d, dict) and d.get("media_buy_id") == ids["media_buy_id"]),
            None,
        )
        assert result_data is not None, f"A2A approve artifact must carry the Success payload, got {datas!r}"
        assert result_data.get("media_buy_status") == "pending_start", (
            f"A2A approve must embed canonical media_buy_status matching MCP sibling, "
            f"got media_buy_status={result_data.get('media_buy_status')!r}"
        )


_APPROVE_HOLD_CASES = [
    ({"include_assignment": False}, "no_assignments"),
    ({"include_assignment": True, "creative_approved": False}, "unapproved_creatives"),
]


def _assert_persisted_status(ids: dict, expected_status: str, *, approved_by: str) -> None:
    """Read back MediaBuy via UoW and assert status + exact approval provenance."""
    from src.core.database.repositories.uow import MediaBuyUoW

    with MediaBuyUoW(ids["tenant_id"]) as uow:
        assert uow.media_buys is not None
        buy = uow.media_buys.get_by_id(ids["media_buy_id"])
        assert buy is not None
        assert buy.status == expected_status, f"expected status {expected_status!r}, got {buy.status!r}"
        assert buy.approved_at is not None
        assert buy.approved_by == approved_by


def _assert_persisted_hold(ids: dict, *, approved_by: str) -> None:
    _assert_persisted_status(ids, "pending_creatives", approved_by=approved_by)


class TestAdminMediaBuyApproveHold:
    """Approve with Hold predicate (#1696): pending_creatives, no adapter, no webhook."""

    @pytest.mark.parametrize(
        "make_kwargs,expected_hold",
        _APPROVE_HOLD_CASES,
        ids=["no_assignments", "unapproved_creatives"],
    )
    def test_approve_holds_without_execute_or_webhook(
        self,
        authenticated_admin_session,
        make_pending_media_buy,
        webhook_capture,
        make_kwargs,
        expected_hold,
    ):
        suffix = expected_hold.replace("_", "")[:8]
        ids = make_pending_media_buy(
            tenant_id=f"hold_{suffix}_tenant",
            media_buy_id=f"mb_hold_{suffix}",
            principal_id=f"hold_{suffix}_principal",
            **make_kwargs,
        )

        with patch(
            "src.core.tools.media_buy_create.execute_approved_media_buy",
        ) as mock_execute:
            _post_approval_action(authenticated_admin_session, ids, {"action": "approve"})

        mock_execute.assert_not_called()
        assert "payload" not in webhook_capture, f"hold arm ({expected_hold}) must not fire the approve webhook"
        _assert_persisted_hold(ids, approved_by="test@example.com")


def _workflow_approve_url(ids: dict) -> str:
    return f"/tenant/{ids['tenant_id']}/workflows/{ids['context_id']}/steps/{ids['step_id']}/approve"


def _post_workflow_approve(admin_session, ids: dict, *, expected_status: int = 200):
    """Drive the real workflows approve route (JSON) and assert the expected status."""
    resp = admin_session.post(
        _workflow_approve_url(ids),
        content_type="application/json",
        json={},
    )
    assert resp.status_code == expected_status, f"expected {expected_status}, got {resp.status_code}: {resp.data!r}"
    body = resp.get_json()
    if expected_status == 200:
        assert body.get("success") is True, f"expected success, got {body!r}"
    return body


class TestAdminWorkflowApproveHold:
    """Workflows approve twin of TestAdminMediaBuyApproveHold (#1696 / KM Jul29).

    Grades the real ``approve_workflow_step`` Flask route (decorators intact) against
    persisted MediaBuy state — not decorator-stripped unit doubles.
    """

    @pytest.mark.parametrize(
        "make_kwargs,expected_hold",
        _APPROVE_HOLD_CASES,
        ids=["no_assignments", "unapproved_creatives"],
    )
    def test_workflow_approve_holds_without_execute(
        self,
        authenticated_admin_session,
        make_pending_media_buy,
        make_kwargs,
        expected_hold,
    ):
        suffix = f"wf{expected_hold.replace('_', '')[:6]}"
        ids = make_pending_media_buy(
            tenant_id=f"wfh_{suffix}_t",
            media_buy_id=f"mb_wfh_{suffix}",
            principal_id=f"wfh_{suffix}_p",
            **make_kwargs,
        )

        with patch(
            "src.core.tools.media_buy_create.execute_approved_media_buy",
        ) as mock_execute:
            body = _post_workflow_approve(authenticated_admin_session, ids)

        mock_execute.assert_not_called()
        _assert_persisted_hold(ids, approved_by="test@example.com")
        assert body.get("held") is True, f"hold arm must announce held=True, got {body!r}"
        assert body.get("hold_reason") == expected_hold, (
            f"hold_reason must be {expected_hold!r}, got {body.get('hold_reason')!r}"
        )
        assert body.get("message"), f"hold arm must carry a non-empty message, got {body!r}"

    def test_workflow_approve_ready_persists_flight_status_and_executes(
        self,
        authenticated_admin_session,
        make_pending_media_buy,
    ):
        """Ready arm: real compute (unpatched) → pending_start for future-start buy + execute."""
        ids = make_pending_media_buy(
            tenant_id="wf_ready_t",
            media_buy_id="mb_wf_ready",
            principal_id="wf_ready_p",
        )

        with patch(
            "src.core.tools.media_buy_create.execute_approved_media_buy",
            return_value=ApprovalResult(
                outcome=ApprovalOutcome.EXECUTED,
                status=PersistedMediaBuyStatus.SCHEDULED,
                revision=2,
            ),
        ) as mock_execute:
            body = _post_workflow_approve(authenticated_admin_session, ids)

        mock_execute.assert_called_once_with(
            ids["media_buy_id"],
            ids["tenant_id"],
            approved_by="test@example.com",
            approved_at=ANY,
        )
        # Mocked sole writer does not persist; ready-arm no longer pre-stamps.
        # Grade that execute was invoked and the route returned success.
        assert body.get("success") is True


class TestAdminApproveAdapterFailureRollback:
    """Ready-arm adapter failure → shared mark_media_buy_adapter_failed (#1696 / KM Aug-05).

    Both operations and workflows map a failed ``ApprovalResult`` through
    ``mark_media_buy_adapter_failed``; the buy must end ``failed`` on both surfaces.
    """

    @pytest.mark.parametrize("surface", ["operations", "workflows"])
    def test_adapter_failure_persists_failed(
        self,
        authenticated_admin_session,
        make_pending_media_buy,
        surface,
    ):
        ids = make_pending_media_buy(
            tenant_id=f"afail_{surface[:3]}_t",
            media_buy_id=f"mb_afail_{surface[:3]}",
            principal_id=f"afail_{surface[:3]}_p",
        )

        with patch(
            "src.core.tools.media_buy_create.execute_approved_media_buy",
            return_value=ApprovalResult.failed("adapter boom"),
        ) as mock_execute:
            if surface == "operations":
                _post_approval_action(authenticated_admin_session, ids, {"action": "approve"})
            else:
                body = _post_workflow_approve(authenticated_admin_session, ids, expected_status=500)
                assert body.get("success") is False
                assert body.get("error") == "adapter boom"

        mock_execute.assert_called_once_with(
            ids["media_buy_id"],
            ids["tenant_id"],
            approved_by="test@example.com",
            approved_at=ANY,
        )
        _assert_persisted_status(ids, "failed", approved_by="test@example.com")
