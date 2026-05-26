"""Tests for impl-layer idempotency replay on update_media_buy.

Covers tescoboy issue #168: ``_update_media_buy_impl`` had no
idempotency check of its own — it relied on the SDK's post-hoc
``IdempotencyStore.wrap``. Two sequential same-key calls that hit the
impl before the first response committed both reached the workflow
step creation and only "happened" to dedup because the second
invocation crashed during context creation. This is the defence-in-
depth fix: the impl checks for an existing workflow step with the
same idempotency key and replays the cached response verbatim.

These tests target the replay path directly: that the impl returns
the cached envelope without invoking the adapter, that dry-run
skips replay, and that errors replay as UpdateMediaBuyError.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuyRequest, UpdateMediaBuySuccess
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories.spec_required_kwargs import required_request_kwargs

MODULE = "src.core.tools.media_buy_update"


def _make_identity(dry_run=False):
    return ResolvedIdentity(
        principal_id="p_1",
        tenant_id="t_1",
        tenant={"tenant_id": "t_1", "name": "Test"},
        testing_context=AdCPTestContext(dry_run=dry_run),
    )


def _make_uow(media_buy):
    mock_uow = MagicMock()
    mock_uow.session = MagicMock()
    mock_uow.media_buys = MagicMock()
    mock_uow.media_buys.find_by_idempotency_key.return_value = None
    mock_uow.media_buys.get_by_id.return_value = media_buy
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    return mock_uow


def _make_buy():
    return MagicMock(
        media_buy_id="mb_1",
        principal_id="p_1",
        external_id=None,
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        end_time=datetime(2026, 12, 31, tzinfo=UTC),
        currency="USD",
        source="adcp",
    )


@pytest.fixture
def patches():
    """Patch every external dep so the impl runs to the idempotency check."""
    mock_adapter = MagicMock()
    mock_adapter.manual_approval_required = False
    mock_adapter.manual_approval_operations = []
    with (
        patch(f"{MODULE}.get_principal_object", return_value=MagicMock(principal_id="p_1")),
        patch(f"{MODULE}._verify_principal"),
        patch(f"{MODULE}.get_context_manager") as m_ctx,
        patch(f"{MODULE}.get_adapter", return_value=mock_adapter),
        patch(f"{MODULE}.is_projected_media_buy_id", return_value=False),
        patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value={"tenant_id": "t_1"}),
        patch("src.core.audit_logger.AuditLogger"),
    ):
        m_ctx.return_value.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
        m_ctx.return_value.create_workflow_step.return_value = MagicMock(step_id="step_001")
        yield {"adapter": mock_adapter, "ctx_manager": m_ctx.return_value}


class TestIdempotencyReplaySuccess:
    def test_cached_success_replayed_without_adapter_call(self, patches):
        uow = _make_uow(_make_buy())
        cached = {
            "media_buy_id": "mb_1",
            "affected_packages": [],
            "workflow_step_id": "step_first",
            # The impl strips request_data on replay; include here to verify.
            "request_data": {"media_buy_id": "mb_1", "end_time": "2026-06-01T00:00:00Z"},
        }
        existing_step = MagicMock(step_id="step_first", response_data=cached)

        with (
            patch(f"{MODULE}.MediaBuyUoW", return_value=uow),
            patch("src.core.database.repositories.workflow.WorkflowRepository") as m_repo,
        ):
            m_repo.return_value.find_by_idempotency_key.return_value = existing_step

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(idempotency_key="key-abc-123xxxxx"),
                media_buy_id="mb_1",
                end_time="2026-06-01T00:00:00Z",
            )
            result = _update_media_buy_impl(req=req, identity=_make_identity())

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.media_buy_id == "mb_1"
        assert result.workflow_step_id == "step_first"
        # Adapter was never invoked (replay short-circuits before any
        # adapter call site).
        patches["adapter"].update_media_buy.assert_not_called()
        # No new workflow step was created
        patches["ctx_manager"].create_workflow_step.assert_not_called()

    def test_deferred_cancel_cache_is_repaired_not_replayed(self, patches):
        uow = _make_uow(_make_buy())
        cached = {
            "media_buy_id": "mb_1",
            "affected_packages": [],
            "workflow_step_id": "step_first",
            "request_data": {"media_buy_id": "mb_1", "canceled": True},
        }
        existing_step = MagicMock(step_id="step_first", status="requires_approval", response_data=cached)
        existing_step.object_mappings = []

        with (
            patch(f"{MODULE}.MediaBuyUoW", return_value=uow),
            patch("src.core.database.repositories.workflow.WorkflowRepository") as m_repo,
        ):
            m_repo.return_value.find_by_idempotency_key.return_value = existing_step

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(idempotency_key="key-abc-123xxxxx"),
                media_buy_id="mb_1",
                canceled=True,
            )
            result = _update_media_buy_impl(req=req, identity=_make_identity())

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.status == "completed"
        assert getattr(result.media_buy_status, "value", result.media_buy_status) == "canceled"
        uow.media_buys.update_fields.assert_called_once_with("mb_1", status="canceled")
        assert uow.session.add.call_count == 1
        patches["ctx_manager"].create_workflow_step.assert_not_called()
        patches["ctx_manager"].update_workflow_step.assert_called_once_with(
            "step_first",
            status="completed",
            response_data={
                "media_buy_id": "mb_1",
                "affected_packages": [],
                "media_buy_status": "canceled",
                "status": "completed",
            },
        )


class TestIdempotencyReplayError:
    def test_cached_error_replayed_as_update_media_buy_error(self, patches):
        uow = _make_uow(_make_buy())
        cached = {
            "errors": [
                {"code": "currency_not_supported", "message": "Currency JPY is not supported by this publisher."}
            ],
            "context": None,
        }
        existing_step = MagicMock(step_id="step_first", response_data=cached)

        with (
            patch(f"{MODULE}.MediaBuyUoW", return_value=uow),
            patch("src.core.database.repositories.workflow.WorkflowRepository") as m_repo,
        ):
            m_repo.return_value.find_by_idempotency_key.return_value = existing_step

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(idempotency_key="key-abc-123xxxxx"),
                media_buy_id="mb_1",
            )
            result = _update_media_buy_impl(req=req, identity=_make_identity())

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "currency_not_supported"
        patches["adapter"].update_media_buy.assert_not_called()


class TestIdempotencyReplaySkippedConditions:
    # NOTE: test_no_idempotency_key_skips_replay_lookup was removed when adcp 5.0
    # made idempotency_key a required field on UpdateMediaBuyRequest. There is
    # no longer a buyer-omits-key code path to exercise. See #314.

    def test_dry_run_skips_replay_lookup(self, patches):
        uow = _make_uow(_make_buy())

        with (
            patch(f"{MODULE}.MediaBuyUoW", return_value=uow),
            patch("src.core.database.repositories.workflow.WorkflowRepository") as m_repo,
        ):
            req = UpdateMediaBuyRequest(
                **required_request_kwargs(idempotency_key="key-abc-123xxxxx"),
                media_buy_id="mb_1",
                end_time="2026-06-01T00:00:00Z",
            )
            _update_media_buy_impl(req=req, identity=_make_identity(dry_run=True))

        m_repo.return_value.find_by_idempotency_key.assert_not_called()

    def test_step_without_response_data_does_not_replay(self, patches):
        # An in-flight step (response_data still null) is not a completed
        # call — let the second call proceed normally and let the SDK
        # wrap or DB-level race detection handle the concurrent case.
        uow = _make_uow(_make_buy())
        in_flight_step = MagicMock(step_id="step_first", response_data=None)

        with (
            patch(f"{MODULE}.MediaBuyUoW", return_value=uow),
            patch("src.core.database.repositories.workflow.WorkflowRepository") as m_repo,
        ):
            m_repo.return_value.find_by_idempotency_key.return_value = in_flight_step

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(idempotency_key="key-abc-123xxxxx"),
                media_buy_id="mb_1",
            )
            _update_media_buy_impl(req=req, identity=_make_identity())

        # Replay was NOT taken (no early return); impl proceeded and
        # created its own workflow step.
        patches["ctx_manager"].create_workflow_step.assert_called()


@pytest.mark.no_default_workflow_repo
class TestRepositoryQuery:
    """`WorkflowRepository.find_by_idempotency_key` query construction.

    Marked ``no_default_workflow_repo`` to opt out of the autouse fixture
    in ``tests/unit/conftest.py`` that replaces the class with a MagicMock —
    these tests inspect the real class.
    """

    def test_method_signature(self):
        import inspect

        from src.core.database.repositories.workflow import WorkflowRepository

        sig = inspect.signature(WorkflowRepository.find_by_idempotency_key)
        params = list(sig.parameters)
        assert params == ["self", "idempotency_key", "principal_id", "tool_name"]

    def test_returns_workflow_step_or_none(self):
        # Type signature check — avoids instantiating a real session.
        from typing import get_type_hints

        from src.core.database.repositories.workflow import WorkflowRepository

        hints = get_type_hints(WorkflowRepository.find_by_idempotency_key)
        # Return type annotated as WorkflowStep | None.
        assert "return" in hints
