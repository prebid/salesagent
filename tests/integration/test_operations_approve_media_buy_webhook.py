"""Regression: approve_media_buy() webhook dispatch per branch.

Context
-------
Before this refactor, `approve_media_buy`'s Phase 1/2/3 split (session-safety
refactor for D2 bare `sessionmaker`) flattened the per-branch webhook dispatch
decision. `adapter_task_status` defaulted to `completed` and Phase 3 fired
unconditionally when a `push_notification_url` was configured. Two regressions
resulted:

- **Step-level ack** (media_buy status != "pending_approval"): no webhook in main;
  pre-fix PR emitted `completed`.
- **Creatives NOT approved**: main called the adapter anyway and sent `completed`
  after success; pre-fix PR skipped the adapter but still sent `completed` — so
  the buyer was told the media buy was operational with no adapter execution.

The fix replaces the variable with `webhook_status: AdcpTaskStatus | None`,
set explicitly per branch in Phase 1 (or flipped to `failed` in Phase 2 per
P3 follow-up):

    ==================================================== ===================
    Branch                                               webhook_status
    ==================================================== ===================
    approve + pending + all_creatives_approved + OK      completed
    approve + pending + all_creatives_approved + FAIL    failed (P3)
    approve + pending + creatives NOT approved           input_required
    approve + step-level ack (status != pending)         None
    reject                                               rejected
    ==================================================== ===================

`input_required` is the AdCP A2A `TaskStatus` for "workflow step complete but
waiting on buyer input (approved creatives)". The result payload for that
branch uses `CreateMediaBuyInputRequired(reason=APPROVAL_REQUIRED)` — the
canonical discriminated-union variant for `input_required` — rather than
`CreateMediaBuySuccessResponse` which implies an operational media buy.

`failed` (P3) closes the buyer's create_media_buy task when the adapter
refuses to execute. Result payload wraps the adapter error in
`CreateMediaBuyErrorResponse(errors=[Error(code="adapter_execution_failed", ...)])`.
Pre-P3 the handler early-returned with no webhook — the buyer's task hung.

Covers the 5 dispatch branches + result-schema assertions. Phase 3's inline
`select(PushNotificationConfig)` stays inline to avoid adding an entry to the
`test_architecture_no_raw_select` shrinking allowlist.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy
from tests.factories import (
    CreativeAssignmentFactory,
    CreativeFactory,
    PushNotificationConfigFactory,
)
from tests.integration._approval_helpers import (
    authenticated,  # noqa: F401  pytest fixture re-exported; consumed via fixture-injection below
    build_approval_scenario,
    make_csrf_disabled_client,
)
from tests.integration._approval_helpers import (
    simulate_adapter_sets_active as _simulate_adapter_sets_active,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_OPS_APP = create_app()


@pytest.fixture
def client():
    """Flask test client bound to the operations-test app instance."""
    with make_csrf_disabled_client(_OPS_APP) as c:
        yield c


@pytest.fixture
def webhook_scenario(integration_db, sample_tenant, sample_principal):
    """Create a MediaBuy pending approval, WorkflowStep with push_notification_config, and PushNotificationConfig."""
    tenant_id = sample_tenant["tenant_id"]
    principal_id = sample_principal["principal_id"]
    scenario = build_approval_scenario(
        tenant_id=tenant_id,
        principal_id=principal_id,
        id_prefix="ops_webhook_regression",
    )

    # Attach push_notification_config to the workflow step's request_data AND
    # seed a PushNotificationConfig row that the Phase 3 lookup will find.
    push_url = "https://buyer.example.com/webhook"
    from sqlalchemy import select

    from src.core.database.models import WorkflowStep

    with get_db_session() as session:
        step = session.scalars(select(WorkflowStep).filter_by(step_id=scenario["step_id"])).first()
        assert step is not None
        step.request_data = {"push_notification_config": {"url": push_url}}
        session.commit()

    PushNotificationConfigFactory(
        tenant_id=tenant_id,
        principal_id=principal_id,
        url=push_url,
        is_active=True,
    )

    scenario["push_url"] = push_url
    return scenario


def _capture_webhook_call() -> tuple[MagicMock, AsyncMock]:
    """Build a (service_mock, send_mock) pair suitable for patching the webhook service.

    `service_mock` is returned by `get_protocol_webhook_service()`; its
    `send_notification` AsyncMock is returned separately so tests can inspect
    the call args conveniently.
    """
    send_mock = AsyncMock()
    service = MagicMock()
    service.send_notification = send_mock
    return service, send_mock


def _assign_creative_to_media_buy(
    tenant_id: str,
    principal_id: str,
    creative_id: str,
    media_buy_id: str,
) -> None:
    """Attach an existing creative to the scenario's pre-seeded media buy.

    The factory would normally create a new MediaBuy via SubFactory; that
    would collide with ``webhook_scenario``'s hand-seeded row. Instead,
    construct the row via the factory with explicit IDs bound to the
    scenario. Uses ``create()`` for factory's persistence — avoids the
    "inline session.add in tests" structural-guard violation.
    """
    CreativeAssignmentFactory.create(
        tenant_id=tenant_id,
        principal_id=principal_id,
        creative_id=creative_id,
        media_buy_id=media_buy_id,
        package_id="pkg_webhook_test",
    )


class TestApproveMediaBuyWebhookDispatch:
    """End-to-end Flask-route tests pinning per-branch webhook_status + result-schema."""

    def test_approve_pending_creatives_approved_adapter_ok_sends_completed(
        self,
        client,
        authenticated,  # noqa: F811
        webhook_scenario,
    ):
        """Happy path: all creatives approved, adapter succeeds → completed webhook."""
        tenant_id = webhook_scenario["tenant_id"]
        principal_id = webhook_scenario["principal_id"]
        media_buy_id = webhook_scenario["media_buy_id"]

        # Seed one approved creative + assignment so the "all approved" branch is taken.
        creative = CreativeFactory(tenant_id=tenant_id, principal_id=principal_id, status="approved")
        _assign_creative_to_media_buy(tenant_id, principal_id, creative.creative_id, media_buy_id)

        service, send_mock = _capture_webhook_call()

        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                side_effect=_simulate_adapter_sets_active,
            ),
            patch(
                "src.services.protocol_webhook_service.get_protocol_webhook_service",
                return_value=service,
            ),
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve"},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303)
        send_mock.assert_called_once()
        payload = send_mock.call_args.kwargs["payload"]
        assert _payload_status(payload) in ("completed", "Completed")

    def test_approve_pending_creatives_approved_adapter_fails_sends_failed_webhook(
        self,
        client,
        authenticated,  # noqa: F811
        webhook_scenario,
    ):
        """Adapter failure (P3): emit a ``failed`` webhook with CreateMediaBuyErrorResponse.

        Pre-P3 the handler early-returned without any webhook emission, so the
        buyer's ``create_media_buy`` task hung forever. Post-P3, Phase 3 covers
        both the DB status write (``media_buy.status = "failed"``) AND the
        ``failed`` webhook with an ``Error(code="adapter_execution_failed", ...)``
        carrying the adapter's error message.
        """
        tenant_id = webhook_scenario["tenant_id"]
        principal_id = webhook_scenario["principal_id"]
        media_buy_id = webhook_scenario["media_buy_id"]

        creative = CreativeFactory(tenant_id=tenant_id, principal_id=principal_id, status="approved")
        _assign_creative_to_media_buy(tenant_id, principal_id, creative.creative_id, media_buy_id)

        service, send_mock = _capture_webhook_call()

        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                return_value=(False, "adapter boom"),
            ),
            patch(
                "src.services.protocol_webhook_service.get_protocol_webhook_service",
                return_value=service,
            ),
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve"},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303)
        send_mock.assert_called_once()
        payload = send_mock.call_args.kwargs["payload"]
        # A2A hyphenates; MCP uses underscore. Default protocol is MCP here.
        assert _payload_status(payload) in ("failed", "Failed")

        # Result payload must be the CreateMediaBuyErrorResponse variant with
        # the adapter's error message surfaced in Error(code="adapter_execution_failed").
        result_dict = _payload_result(payload)
        assert result_dict is not None, f"no result found in payload: {payload!r}"
        errors = result_dict.get("errors")
        assert isinstance(errors, list) and errors, f"expected non-empty errors list, got {result_dict!r}"
        assert errors[0].get("code") == "adapter_execution_failed"
        assert "adapter boom" in errors[0].get("message", "")

    def test_approve_pending_adapter_fails_marks_media_buy_failed_in_db(
        self,
        client,
        authenticated,  # noqa: F811
        webhook_scenario,
    ):
        """P3 regression guard: adapter-failure path commits media_buy.status="failed".

        Pre-P3 this happened in a mid-flow session before the early return.
        Post-P3 it happens inside Phase 3 before webhook emission so the buyer
        cannot observe ``failed`` status on a post-webhook GET before the DB
        write has committed.
        """
        tenant_id = webhook_scenario["tenant_id"]
        principal_id = webhook_scenario["principal_id"]
        media_buy_id = webhook_scenario["media_buy_id"]

        creative = CreativeFactory(tenant_id=tenant_id, principal_id=principal_id, status="approved")
        _assign_creative_to_media_buy(tenant_id, principal_id, creative.creative_id, media_buy_id)

        service, _send_mock = _capture_webhook_call()

        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                return_value=(False, "boom"),
            ),
            patch(
                "src.services.protocol_webhook_service.get_protocol_webhook_service",
                return_value=service,
            ),
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve"},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303)

        from sqlalchemy import select

        with get_db_session() as session:
            mb = session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
            assert mb is not None
            assert mb.status == "failed", f"expected media_buy.status == 'failed', got {mb.status!r}"

    def test_approve_pending_adapter_fails_webhook_raise_does_not_mask_db_write(
        self,
        client,
        authenticated,  # noqa: F811
        webhook_scenario,
    ):
        """If the buyer's webhook endpoint is unreachable, Phase 3 still marks
        the media buy ``failed`` in the DB.

        The webhook emission is wrapped in ``try/except Exception → logger.warning``
        (mirrors the pre-P3 pattern for other statuses). This test asserts that
        the DB write (which happens BEFORE emission within the same session)
        survives even if the send raises.
        """
        tenant_id = webhook_scenario["tenant_id"]
        principal_id = webhook_scenario["principal_id"]
        media_buy_id = webhook_scenario["media_buy_id"]

        creative = CreativeFactory(tenant_id=tenant_id, principal_id=principal_id, status="approved")
        _assign_creative_to_media_buy(tenant_id, principal_id, creative.creative_id, media_buy_id)

        service = MagicMock()
        service.send_notification = AsyncMock(side_effect=RuntimeError("buyer endpoint down"))

        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                return_value=(False, "boom"),
            ),
            patch(
                "src.services.protocol_webhook_service.get_protocol_webhook_service",
                return_value=service,
            ),
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve"},
                follow_redirects=False,
            )

        # Handler does NOT 5xx — the webhook raise is swallowed.
        assert response.status_code in (302, 303)

        # DB write committed before the webhook attempt.
        from sqlalchemy import select

        with get_db_session() as session:
            mb = session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
            assert mb is not None
            assert mb.status == "failed"

    def test_approve_pending_creatives_not_approved_sends_input_required(
        self,
        client,
        authenticated,  # noqa: F811
        webhook_scenario,
    ):
        """Creatives-not-ready branch: skip adapter, send input_required with
        CreateMediaBuyInputRequired(APPROVAL_REQUIRED) result.

        This replaces main's misleading `completed` (sent after running the
        adapter anyway on incomplete creatives) with the AdCP A2A TaskStatus
        that means exactly "workflow step done, waiting on buyer input".
        """
        tenant_id = webhook_scenario["tenant_id"]
        principal_id = webhook_scenario["principal_id"]
        media_buy_id = webhook_scenario["media_buy_id"]

        # Seed a PENDING creative — assignment present but status != approved.
        creative = CreativeFactory(tenant_id=tenant_id, principal_id=principal_id, status="pending")
        _assign_creative_to_media_buy(tenant_id, principal_id, creative.creative_id, media_buy_id)

        service, send_mock = _capture_webhook_call()

        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
            ) as adapter_mock,
            patch(
                "src.services.protocol_webhook_service.get_protocol_webhook_service",
                return_value=service,
            ),
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve"},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303)
        adapter_mock.assert_not_called()  # adapter skipped in creatives-not-approved branch
        send_mock.assert_called_once()
        payload = send_mock.call_args.kwargs["payload"]
        # A2A wire format hyphenates; MCP uses underscore. Accept either — the
        # test fixture's step `request_data` does not declare a protocol, so
        # the handler defaults to MCP (underscore form).
        assert _payload_status(payload) in ("input_required", "input-required", "InputRequired")

        # Verify the result payload is CreateMediaBuyInputRequired shape, not
        # CreateMediaBuySuccessResponse. Walk the payload to find the result.
        result_dict = _payload_result(payload)
        assert result_dict is not None, f"no result found in payload: {payload!r}"
        assert result_dict.get("reason") in (
            "APPROVAL_REQUIRED",
            "approval_required",
        ), f"expected reason == APPROVAL_REQUIRED, got {result_dict!r}"

        # Media buy status flipped to draft (no adapter call to set active).
        with get_db_session() as session:
            from sqlalchemy import select

            mb = session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
            assert mb is not None
            assert mb.status == "draft"

    def test_approve_step_level_ack_no_webhook(
        self,
        client,
        authenticated,  # noqa: F811
        webhook_scenario,
    ):
        """Step-level ack (media_buy status != pending_approval): NO webhook.

        Matches main's behavior. The step gets approved for workflow-audit
        bookkeeping, but the media buy state is not transitioning so there's
        no buyer-visible event to report.
        """
        tenant_id = webhook_scenario["tenant_id"]
        media_buy_id = webhook_scenario["media_buy_id"]

        # Flip media_buy status away from pending_approval so Phase 1 takes the
        # step-level-ack branch (the `else` under `if media_buy.status == "pending_approval"`).
        with get_db_session() as session:
            from sqlalchemy import select

            mb = session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
            assert mb is not None
            mb.status = "active"
            session.commit()

        service, send_mock = _capture_webhook_call()

        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
            ) as adapter_mock,
            patch(
                "src.services.protocol_webhook_service.get_protocol_webhook_service",
                return_value=service,
            ),
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve"},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303)
        adapter_mock.assert_not_called()
        send_mock.assert_not_called()

    def test_reject_sends_rejected(
        self,
        client,
        authenticated,  # noqa: F811
        webhook_scenario,
    ):
        """Reject branch: rejected webhook, no adapter."""
        tenant_id = webhook_scenario["tenant_id"]
        media_buy_id = webhook_scenario["media_buy_id"]

        service, send_mock = _capture_webhook_call()

        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
            ) as adapter_mock,
            patch(
                "src.services.protocol_webhook_service.get_protocol_webhook_service",
                return_value=service,
            ),
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "reject", "reason": "not a fit"},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303)
        adapter_mock.assert_not_called()
        send_mock.assert_called_once()
        payload = send_mock.call_args.kwargs["payload"]
        assert _payload_status(payload) in ("rejected", "Rejected")


def _payload_status(payload: object) -> str | None:
    """Extract the task status from either an MCP or A2A webhook payload.

    MCP payloads are dicts with a top-level `status` key. A2A payloads are
    Pydantic models (TaskStatusUpdateEvent) whose `status.state` carries the
    enum. This helper normalizes access so the per-branch tests don't care
    which protocol variant was emitted.
    """
    # MCP dict form.
    if isinstance(payload, dict):
        status = payload.get("status")
        if isinstance(status, str):
            return status
        if isinstance(status, dict):
            return str(status.get("state") or status.get("value") or "")
    # A2A Pydantic / dataclass form.
    status_attr = getattr(payload, "status", None)
    if status_attr is not None:
        # TaskStatusUpdateEvent.status is itself a TaskStatus with .state enum.
        state = getattr(status_attr, "state", None)
        if state is not None:
            # state may be enum-like; coerce to str via .value or str().
            return getattr(state, "value", str(state))
        # Or it may be the raw enum.
        return getattr(status_attr, "value", str(status_attr))
    return None


def _payload_result(payload: object) -> dict | None:
    """Extract the `result` object from an MCP or A2A webhook payload as a dict."""
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        if result is not None and hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return None
    # A2A payload — hunt for a result on the event object or its status message.
    result = getattr(payload, "result", None)
    if result is not None:
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return result
    # A2A TaskStatusUpdateEvent carries the result in status.message.parts[].data.
    status_attr = getattr(payload, "status", None)
    message = getattr(status_attr, "message", None) if status_attr is not None else None
    parts = getattr(message, "parts", None) if message is not None else None
    if parts:
        for part in parts:
            data = getattr(part, "data", None)
            if isinstance(data, dict):
                return data
    return None
