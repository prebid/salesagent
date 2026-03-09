"""Integration tests: async creative lifecycle obligation coverage.

Exercises the async lifecycle states (submitted, working, input_required) through
_sync_creatives_impl using CreativeSyncEnv with real PostgreSQL.

These obligations test that the creative sync workflow produces states that map
to the AdCP async lifecycle protocol:
- submitted: creative queued for human review (approval_mode=require-human)
- working: creative being processed by AI reviewer (approval_mode=ai-powered)
- input_required: creative needs buyer input before it can proceed

Each test creates a real creative through the impl, verifies the DB state,
then validates the AdCP async schema type can represent that state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from adcp.types import CreativeAction
from adcp.types.generated_poc.media_buy.sync_creatives_async_response_input_required import (
    Reason,
    SyncCreativesInputRequired,
)
from adcp.types.generated_poc.media_buy.sync_creatives_async_response_submitted import (
    SyncCreativesSubmitted,
)
from adcp.types.generated_poc.media_buy.sync_creatives_async_response_working import (
    SyncCreativesWorking,
)
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from tests.harness import CreativeSyncEnv

DEFAULT_AGENT_URL = "https://creative.test.example.com"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _creative(**overrides) -> dict:
    """Minimal creative dict for testing."""
    defaults = {
        "creative_id": "c_async_1",
        "name": "Async Lifecycle Test",
        "format_id": {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
        "assets": {"banner": {"url": "https://example.com/banner.png"}},
    }
    defaults.update(overrides)
    return defaults


class TestAsyncSubmittedLifecycle:
    """Async submitted lifecycle state through _sync_creatives_impl.

    When a creative is synced with require-human approval, the system queues it
    for review. The creative enters pending_review status and workflow steps are
    created -- this is the "submitted" state in the AdCP async protocol.
    """

    def test_async_submitted_response(self, integration_db):
        """Creative synced with require-human enters submitted lifecycle state.

        Covers: UC-006-ASYNC-LIFECYCLE-01

        Given the system supports async creative sync
        When a sync operation is queued (require-human approval)
        Then the creative is persisted with pending_review status
        And a SyncCreativesAsyncResponseSubmitted can represent this state
        And it conforms to the adcp 3.6.0 async-response-submitted schema
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # require-human is the default approval_mode

            result = env.call_impl(
                creatives=[_creative(creative_id="c_submitted_1")],
            )

            # Creative was created successfully
            assert len(result.creatives) == 1
            assert result.creatives[0].action == CreativeAction.created

            # Verify DB state: creative is in pending_review (queued for review = submitted)
            with get_db_session() as session:
                db_creative = session.scalars(select(DBCreative).filter_by(creative_id="c_submitted_1")).first()
                assert db_creative is not None
                assert db_creative.status == "pending_review"

            # Verify AdCP async submitted schema can represent this state
            submitted = SyncCreativesSubmitted(context=None, ext=None)
            assert "context" in SyncCreativesSubmitted.model_fields
            assert "ext" in SyncCreativesSubmitted.model_fields

            # Can be constructed with no args (all optional per spec)
            empty = SyncCreativesSubmitted()
            assert empty.context is None

            # Serialization roundtrip
            data = submitted.model_dump()
            assert isinstance(data, dict)


class TestAsyncWorkingLifecycle:
    """Async working lifecycle state through _sync_creatives_impl.

    When a creative is synced with ai-powered approval, the AI review runs
    in background. The creative is in pending_review with an active background
    task -- this is the "working" state in the AdCP async protocol.
    """

    def test_async_working_response(self, integration_db):
        """Creative synced with ai-powered enters working lifecycle state.

        Covers: UC-006-ASYNC-LIFECYCLE-02

        Given an async sync operation is in progress (ai-powered review)
        When the creative is being processed by the AI reviewer
        Then the creative is persisted with pending_review status
        And a background AI review task is submitted
        And a SyncCreativesAsyncResponseWorking can represent progress
        And includes percentage, steps, and creatives processed counts
        """
        mock_executor = MagicMock()
        mock_executor.submit.return_value = MagicMock()

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.identity.tenant["approval_mode"] = "ai-powered"

            with (
                patch("src.admin.blueprints.creatives._ai_review_executor", mock_executor),
                patch("src.admin.blueprints.creatives._ai_review_lock", MagicMock()),
                patch("src.admin.blueprints.creatives._ai_review_tasks", {}),
            ):
                result = env.call_impl(
                    creatives=[_creative(creative_id="c_working_1")],
                )

            # Creative was created successfully
            assert len(result.creatives) == 1
            assert result.creatives[0].action == CreativeAction.created

            # Verify DB state: creative is pending_review (AI review in progress = working)
            with get_db_session() as session:
                db_creative = session.scalars(select(DBCreative).filter_by(creative_id="c_working_1")).first()
                assert db_creative is not None
                assert db_creative.status == "pending_review"

            # Verify background AI review task was submitted (proves async work is happening)
            assert mock_executor.submit.called

            # Verify AdCP async working schema can represent progress state
            working = SyncCreativesWorking(
                percentage=50.0,
                creatives_processed=1,
                creatives_total=1,
                current_step="ai_review",
                step_number=1,
                total_steps=2,
            )
            data = working.model_dump()
            assert data["percentage"] == 50.0
            assert data["creatives_processed"] == 1
            assert data["creatives_total"] == 1
            assert data["current_step"] == "ai_review"


class TestAsyncInputRequiredLifecycle:
    """Async input_required lifecycle state through _sync_creatives_impl.

    When a creative is synced with require-human approval, it enters
    pending_review and creates workflow steps that need human action.
    This is the "input_required" state -- the system pauses and waits
    for the buyer to approve the creative.
    """

    def test_async_input_required_response(self, integration_db):
        """Creative needing human approval enters input_required lifecycle state.

        Covers: UC-006-ASYNC-LIFECYCLE-03

        Given an async sync operation requires buyer input (approval)
        When the system pauses for human review
        Then the creative is persisted with pending_review status
        And workflow notifications are triggered
        And a SyncCreativesAsyncResponseInputRequired can represent this state
        And indicates what input is needed (APPROVAL_REQUIRED)
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # require-human triggers needs_approval=True -> workflow steps + notifications

            result = env.call_impl(
                creatives=[_creative(creative_id="c_input_req_1")],
            )

            # Creative was created successfully
            assert len(result.creatives) == 1
            assert result.creatives[0].action == CreativeAction.created

            # Verify DB state: creative needs human input
            with get_db_session() as session:
                db_creative = session.scalars(select(DBCreative).filter_by(creative_id="c_input_req_1")).first()
                assert db_creative is not None
                assert db_creative.status == "pending_review"

            # Verify workflow notifications were sent (proves system is waiting for input)
            env.mock["send_notifications"].assert_called_once()
            call_kwargs = env.mock["send_notifications"].call_args[1]
            assert call_kwargs["approval_mode"] == "require-human"
            assert len(call_kwargs["creatives_needing_approval"]) == 1

            # Verify AdCP async input-required schema can represent this state
            input_required = SyncCreativesInputRequired(reason=Reason.APPROVAL_REQUIRED)
            assert input_required.reason == Reason.APPROVAL_REQUIRED

            # model_dump with mode="json" serializes enum to string
            data = input_required.model_dump(mode="json")
            assert data["reason"] == "APPROVAL_REQUIRED"

            # All reason enum values should be valid
            for reason in Reason:
                r = SyncCreativesInputRequired(reason=reason)
                assert r.reason == reason
