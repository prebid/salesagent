"""Guard tests for the A2A update_media_buy submitted contract (PR #1567 follow-up).

These pin the behavior that must SURVIVE the removal of the unreachable
``UpdateMediaBuySubmitted`` reconstruction branch in
``src/a2a_server/adcp_a2a_server.py::_reconstruct_response_object``:

1. A manual-approval update_media_buy driven through the REAL A2A
   ``on_message_send`` pipeline yields a Task with state=TASK_STATE_SUBMITTED
   and NO artifacts — the submitted early-return fires BEFORE artifact
   reconstruction, so no serialized submitted body ever crosses the A2A wire.
   (The create-side twin of this pin lives at
   tests/integration/test_a2a_skill_invocation.py::test_explicit_skill_create_media_buy_manual_approval;
   no test pinned the update side at the Task level before this one — the
   BR-UC-003 a2a BDD scenarios grade the submitted ENVELOPE, but the harness
   synthesizes that envelope from Task state alone and never asserts artifact
   absence.)

2. The REACHABLE part of ``_reconstruct_response_object``'s update contract:
   a completed artifact body (carrying media_buy_id) reconstructs as
   ``UpdateMediaBuySuccess``; an errors-only body as ``UpdateMediaBuyError``.
   (tests/integration/test_a2a_response_compliance.py exercises reconstruction
   only for list_authorized_properties / get_products / list_creative_formats
   — the update_media_buy union discrimination was previously unpinned.)

Both tests pass BEFORE the dead-branch removal and must keep passing AFTER it.
They are guards, not TDD-red artifacts: the removal is a no-behavior-change
refactor, so there is no failing behavior to write first.

Honesty notes:
- Test 1 asserts on the raw Task captured by the harness
  (``env.last_a2a_task``), because the harness's parsed submitted response is
  SYNTHESIZED from Task state + id (tests/harness/_base.py) and by itself
  cannot prove that the server attached no artifacts.
- Test 1 mocks only the ad-server adapter (the external boundary — flipped to
  manual-approval mode) plus the audit/context-manager seams MediaBuyDualEnv
  always patches; message parsing, skill routing, auth, the shared
  ``_update_media_buy_impl``, and Task/artifact framing are all real, and the
  media buy row lives in real PostgreSQL.
- Test 2 calls the production reconstruction helper directly; it involves no
  wire and no DB by design — it pins the pure union-discrimination contract
  that survives the branch removal. It deliberately does NOT assert anything
  about a submitted-shaped body: after the removal, that shape is
  structurally impossible on this path.
"""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from src.core.schemas import (
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
    UpdateMediaBuySubmitted,
    UpdateMediaBuySuccess,
)
from tests.factories import MediaBuyFactory
from tests.harness.media_buy_dual import MediaBuyDualEnv

pytestmark = pytest.mark.integration

_MEDIA_BUY_ID = "mb_a2a_submitted_guard"


@pytest.mark.requires_db
def test_manual_approval_update_via_real_a2a_pipeline_is_submitted_task_without_artifacts(integration_db):
    """A2A submitted contract: Task state=SUBMITTED, NO artifacts, no response body on the wire.

    Drives update_media_buy through the real AdCPRequestHandler.on_message_send
    (message parsing -> skill routing -> _update_media_buy_impl -> Task framing)
    with the adapter requiring manual approval. The submitted early-return in
    on_message_send must convey the pending state exclusively via the Task
    object — this is the control-flow fact that makes the UpdateMediaBuySubmitted
    reconstruction branch dead, and it must hold after that branch is removed.
    """
    with MediaBuyDualEnv() as env:
        tenant, principal, _product, _pricing_option = env.setup_media_buy_data()
        MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=_MEDIA_BUY_ID,
            status="active",
        )
        env._commit_factory_data()

        # External boundary: the ad-server adapter requires human approval for updates.
        adapter = env.mock["update_adapter"].return_value
        adapter.manual_approval_required = True
        adapter.manual_approval_operations = ["update_media_buy"]

        result = env.call_a2a(req=UpdateMediaBuyRequest(media_buy_id=_MEDIA_BUY_ID, budget=15000.0))

        task = env.last_a2a_task
        assert task is not None, "harness did not capture the A2A Task — did the dispatch bypass _run_a2a_handler?"
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED, (
            f"manual-approval update must yield a SUBMITTED Task, got {task.status.state!r}"
        )
        # The early-return deletes artifacts: no serialized response body may
        # cross the A2A wire for a submitted update. (protobuf uses an empty
        # repeated field rather than None, hence the falsiness check.)
        assert not task.artifacts, (
            f"submitted Task must carry NO artifacts (state is conveyed by the Task itself), "
            f"got {task.artifacts!r} — a serialized submitted body crossed the A2A wire"
        )

    # The harness-synthesized envelope (built from Task state + id) parses as the
    # submitted variant and carries the task_id the buyer polls. Secondary pin:
    # this proves the Task id doubles as the AdCP task_id, not artifact content.
    # Production's _update_media_buy_impl returns the bare UpdateMediaBuySubmitted
    # protocol variant (spec 3.1.1 serializes it flat, status="submitted"+task_id at
    # top level), so the harness reconstructs it bare — not wrapped in UpdateMediaBuyResult.
    assert isinstance(result, UpdateMediaBuySubmitted), (
        f"expected bare UpdateMediaBuySubmitted, got {type(result).__name__}"
    )
    assert result.status == "submitted"
    assert result.task_id, "submitted update must carry a task_id for the buyer to poll"


def test_reconstruct_update_media_buy_reachable_contract():
    """_reconstruct_response_object update union: completed -> Success, errors-only -> Error.

    These are the only two shapes that can reach reconstruction (submitted
    results early-return in on_message_send before the artifact loop). Pins
    the media_buy_id-based discrimination so the dead-branch removal cannot
    disturb the reachable cases. Note: reconstruction failures return None
    (logged, not raised), so the isinstance assertions also prove the data
    shapes were accepted by the concrete models.
    """
    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

    handler = AdCPRequestHandler()

    completed_body = {
        "media_buy_id": _MEDIA_BUY_ID,
        "status": "completed",
        "revision": 2,
    }
    completed = handler._reconstruct_response_object("update_media_buy", completed_body)
    assert isinstance(completed, UpdateMediaBuySuccess), (
        f"completed body (has media_buy_id) must reconstruct as UpdateMediaBuySuccess, got {completed!r}"
    )
    assert completed.media_buy_id == _MEDIA_BUY_ID

    errors_only_body = {
        "errors": [{"code": "invalid_budget", "message": "budget must be positive"}],
    }
    errored = handler._reconstruct_response_object("update_media_buy", errors_only_body)
    assert isinstance(errored, UpdateMediaBuyError), (
        f"errors-only body (no media_buy_id) must reconstruct as UpdateMediaBuyError, got {errored!r}"
    )
    assert errored.errors[0].code == "invalid_budget"
