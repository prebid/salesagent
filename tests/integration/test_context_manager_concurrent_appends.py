"""Integration repro: concurrent JSON appends must not lose an entry.

``ContextManager.add_message()`` and ``update_workflow_step(add_comment=...)``
append to a JSONType collection by loading the whole list, appending in
Python, and writing the whole list back. Two concurrent appenders on the same
row can each read the same snapshot; the later commit erases the earlier
append — silently lost conversation history / comments (salesagent-pgqs; same
disease as the v8dt authorized-list races).

The rival append runs on its own thread-local scoped session (a genuinely
separate transaction) and is fired deterministically inside the main call's
write window via ``rival_fires_in_session_write_window`` — same technique as
the v8dt race repros, adapted to ``DatabaseManager``'s long-lived session.
The assertion is interleaving-independent: BOTH entries must survive.
"""

from __future__ import annotations

import threading

import pytest

from tests.helpers.race_window import rival_fires_in_session_write_window

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "ctx_append_race_t1"
_PRINCIPAL_ID = "ctx_append_race_p1"


def _run_in_thread(fn) -> None:
    """Run ``fn`` on a fresh thread (own scoped session) and re-raise its error."""
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - repro must surface everything
            errors.append(exc)

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "rival append deadlocked"
    if errors:
        raise errors[0]


@pytest.fixture
def seeded(integration_db):
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness._base import BareIntegrationEnv

    with BareIntegrationEnv(tenant_id=_TENANT_ID) as env:
        tenant = TenantFactory(tenant_id=_TENANT_ID)
        PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)
        env.get_session()  # commit factory data
        yield env


class TestConversationHistoryConcurrentAppend:
    def test_rival_message_survives_concurrent_append(self, seeded):
        from src.core.context_manager import ContextManager

        cm = ContextManager()
        ctx = cm.create_context(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID)

        def rival_append() -> None:
            _run_in_thread(lambda: ContextManager().add_message(ctx.context_id, "user", "rival message"))

        # trigger="execute": the fix made the append a single atomic UPDATE, so
        # the write instant moved from commit to the statement's execute — the
        # rival lands just before it and must still survive (v8dt precedent;
        # the assertion is unchanged from the red run, which used "commit").
        with rival_fires_in_session_write_window(cm.session, rival_append, trigger="execute"):
            cm.add_message(ctx.context_id, "assistant", "main message")

        history = ContextManager().get_context(ctx.context_id).conversation_history
        contents = [m["content"] for m in history]
        assert sorted(contents) == ["main message", "rival message"], (
            f"lost update: conversation_history holds {contents!r} — the rival's "
            f"append was erased by the whole-list write-back"
        )


class TestWorkflowStepCommentsConcurrentAppend:
    def test_rival_comment_survives_concurrent_append(self, seeded):
        from src.core.context_manager import ContextManager

        cm = ContextManager()
        ctx = cm.create_context(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID)
        step = cm.create_workflow_step(
            context_id=ctx.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
        )

        def rival_append() -> None:
            _run_in_thread(
                lambda: ContextManager().update_workflow_step(
                    step.step_id, add_comment={"user": "rival", "comment": "rival comment"}
                )
            )

        # trigger="execute" — see the conversation_history test for why the
        # write instant moved with the fix; assertion unchanged from red.
        with rival_fires_in_session_write_window(cm.session, rival_append, trigger="execute"):
            cm.update_workflow_step(step.step_id, add_comment={"user": "main", "comment": "main comment"})

        session = seeded.get_session()
        session.expire_all()
        from src.core.database.models import WorkflowStep

        comments = seeded.get_one(WorkflowStep, step_id=step.step_id).comments
        texts = sorted(c["text"] for c in comments)
        assert texts == ["main comment", "rival comment"], (
            f"lost update: comments hold {texts!r} — the rival's append was erased by the whole-list write-back"
        )
