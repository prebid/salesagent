"""Verify webhook emission helpers in core/platforms/_delegate.py only fire
on actual creation, not on submitted-pending or failed responses.

The helpers sit at the framework/_impl boundary so they can import the
admin-layer publisher without violating the transport-agnostic _impl
guard. The contract:

- ``media_buy.created`` fires iff the inner response is
  ``CreateMediaBuySuccess`` AND has a non-empty ``media_buy_id``.
  ``CreateMediaBuySubmitted`` (pending approval — no buy yet) and
  ``CreateMediaBuyError`` (failure) MUST NOT fire it.
- ``creative.created`` fires once per ``action="created"`` row in
  ``sync_creatives`` response. ``updated`` / ``unchanged`` / ``failed``
  rows MUST NOT fire it. dry_run MUST NOT fire any.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from core.platforms._delegate import (
    _emit_creative_created_for_new_creatives,
    _emit_media_buy_created_if_success,
)


class TestMediaBuyCreatedEmission:
    def test_fires_on_create_media_buy_success(self):
        from src.core.schemas import CreateMediaBuySuccess

        success = CreateMediaBuySuccess.model_construct(
            media_buy_id="mb_123",
            status="pending_start",
            packages=[],
            creative_deadline=None,
        )
        result = SimpleNamespace(status="completed", response=success)
        # adcp b9 dropped top-level buyer_ref; the emitted payload sources it from
        # the request's context.buyer_ref (the AdCP v3 correlation successor).
        req_model = SimpleNamespace(context=SimpleNamespace(buyer_ref="ref_abc"))

        with patch("src.admin.services.webhook_publisher.emit_event") as mock_emit:
            _emit_media_buy_created_if_success("t1", result, req_model)

        mock_emit.assert_called_once_with(
            "t1",
            "media_buy.created",
            {"media_buy_id": "mb_123", "buyer_ref": "ref_abc", "status": "pending_start"},
        )

    def test_does_not_fire_on_submitted_pending(self):
        """Submitted = manual approval pending. No media buy was actually
        created yet — the event would be misleading."""
        from src.core.schemas import CreateMediaBuySubmitted

        submitted = CreateMediaBuySubmitted.model_construct(
            status="submitted",
            task_id="task_abc",
        )
        result = SimpleNamespace(status="submitted", response=submitted)

        with patch("src.admin.services.webhook_publisher.emit_event") as mock_emit:
            _emit_media_buy_created_if_success("t1", result)

        mock_emit.assert_not_called()

    def test_does_not_fire_on_error(self):
        from src.core.schemas import CreateMediaBuyError, Error

        error = CreateMediaBuyError.model_construct(
            errors=[Error.model_construct(code="VALIDATION", message="bad input")],
        )
        result = SimpleNamespace(status="failed", response=error)

        with patch("src.admin.services.webhook_publisher.emit_event") as mock_emit:
            _emit_media_buy_created_if_success("t1", result)

        mock_emit.assert_not_called()


class TestCreativeCreatedEmission:
    def _make_result(self, creatives):
        return SimpleNamespace(creatives=creatives)

    def test_fires_for_each_created_creative(self):
        creatives = [
            SimpleNamespace(creative_id="cr_1", action="created", platform_id="p1", status="active"),
            SimpleNamespace(creative_id="cr_2", action="updated", platform_id="p2", status="active"),
            SimpleNamespace(creative_id="cr_3", action="created", platform_id="p3", status="pending_review"),
            SimpleNamespace(creative_id="cr_4", action="failed", platform_id=None, status=None),
        ]
        result = self._make_result(creatives)

        with patch("src.admin.services.webhook_publisher.emit_event") as mock_emit:
            _emit_creative_created_for_new_creatives("t1", result, dry_run=False)

        # Two created creatives → two emit calls. Updated and failed skipped.
        assert mock_emit.call_count == 2
        creative_ids_fired = [call.args[2]["creative_id"] for call in mock_emit.call_args_list]
        assert creative_ids_fired == ["cr_1", "cr_3"]
        for call in mock_emit.call_args_list:
            assert call.args[0] == "t1"
            assert call.args[1] == "creative.created"

    def test_dry_run_never_fires(self):
        """dry_run sync_creatives doesn't actually persist anything, so it
        MUST NOT fire creative.created — otherwise the host product sees
        ghost creatives that don't exist in our DB."""
        creatives = [
            SimpleNamespace(creative_id="cr_1", action="created", platform_id="p1", status="active"),
        ]
        result = self._make_result(creatives)

        with patch("src.admin.services.webhook_publisher.emit_event") as mock_emit:
            _emit_creative_created_for_new_creatives("t1", result, dry_run=True)

        mock_emit.assert_not_called()

    def test_handles_enum_action_values(self):
        """SyncCreativeResult.action can be a CreativeAction enum (live SDK
        path) or a plain string (constructed in tests / serialization
        edge cases). Both shapes must trigger emission for ``created``."""
        from adcp.types import CreativeAction

        creatives = [
            SimpleNamespace(creative_id="cr_enum", action=CreativeAction.created, platform_id="p1", status="active"),
            SimpleNamespace(creative_id="cr_str", action="created", platform_id="p2", status="active"),
        ]
        result = self._make_result(creatives)

        with patch("src.admin.services.webhook_publisher.emit_event") as mock_emit:
            _emit_creative_created_for_new_creatives("t1", result, dry_run=False)

        assert mock_emit.call_count == 2

    def test_empty_creatives_list_is_safe(self):
        result = self._make_result([])
        with patch("src.admin.services.webhook_publisher.emit_event") as mock_emit:
            _emit_creative_created_for_new_creatives("t1", result, dry_run=False)
        mock_emit.assert_not_called()
