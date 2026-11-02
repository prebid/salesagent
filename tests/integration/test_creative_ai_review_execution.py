"""The AI review an ai-powered sync defers must actually RUN (GH #1972).

Every existing scenario for the ai-powered branch grades the SUBMIT: the harness
replaces ``_ai_review_executor`` with a mock and the assertion is "submit() was
called". That is true whether or not the submitted job ever executes a line of
its body — which is precisely how a job that never executes stayed invisible
while creatives piled up at ``pending_review`` waiting on a reviewer that had
never started.

These scenarios put the real executor back (``env.run_ai_review_for_real()``)
and grade the EFFECT the review is supposed to have: the verdict committed onto
the creative, and the Slack notification the verdict is supposed to trigger.
Both are read after joining on the job's own Future, and the verdict is read
over a connection this test's session does not own — the only read that can
tell a committed verdict from one that was never written.

Only the LLM boundary is mocked. The reviewer's unit of work, its threshold
policy, the status write, and the notification are all real.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from tests.factories import CreativeAssetFactory, FormatIdFactory
from tests.harness import CreativeSyncEnv

TENANT_ID = "test_tenant"
PRINCIPAL_ID = "test_principal"

#: The reviewer refuses to run without criteria configured, so this is setup,
#: not decoration: an empty value short-circuits to "AI review unavailable".
REVIEW_CRITERIA = "Reject adult content. Approve everything else."

#: What the (mocked) model says. "high" maps to a 0.9 confidence score, which
#: meets the default 0.90 auto-approve threshold — so the verdict the reviewer
#: must commit is exactly "approved", not merely "some verdict".
AI_REASON = "Meets the tenant criteria"
EXPECTED_DECISION = "approved"

SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/test"
CREATIVE_FORMAT_ID = "display_300x250"
FORMAT_ID = FormatIdFactory(id=CREATIVE_FORMAT_ID)


@contextmanager
def mocked_ai_backend() -> Iterator[MagicMock]:
    """Patch ONLY the model call and the Slack transport, yielding the notifier.

    The seam is the same one ``tests/unit/test_ai_review.py`` uses: the review
    agent's async entry point plus the service factory that decides AI is
    available. Everything between that seam and the database — the confidence
    thresholds, the status write, the ``ai_review`` payload — is the production
    code under test here.
    """
    from src.services.ai.agents.review_agent import CreativeReviewResult

    verdict = CreativeReviewResult(decision="APPROVE", reason=AI_REASON, confidence="high")
    with (
        patch("src.services.ai.AIServiceFactory"),
        patch("src.services.ai.agents.review_agent.create_review_agent"),
        patch("src.services.ai.agents.review_agent.review_creative_async", return_value=verdict),
        patch("src.services.slack_notifier.get_slack_notifier") as get_notifier,
    ):
        yield get_notifier.return_value


def ai_powered_env(env: CreativeSyncEnv) -> None:
    """Configure *env* as an ai-powered tenant with a Slack webhook.

    ``configure_tenant_field`` writes both the DB column the reviewer reads and
    the identity dict the sync reads, which have to agree for the deferred job
    to be handed the same tenant policy the sync acted on.
    """
    env.setup_default_data()
    env.configure_tenant_field("approval_mode", "ai-powered")
    env.configure_tenant_field("creative_review_criteria", REVIEW_CRITERIA)
    env.configure_tenant_field("slack_webhook_url", SLACK_WEBHOOK_URL)


@pytest.mark.requires_db
class TestAIReviewVerdictIsCommitted:
    """An ai-powered sync ends with a review verdict committed on the creative."""

    def test_ai_powered_sync_commits_a_verdict(self, integration_db):
        """The deferred review writes its decision to the creative, for real.

        The assertion is the whole point of the test: not that the job was
        handed off, but that a separate connection can afterwards read the
        decision the reviewer reached.
        """
        with CreativeSyncEnv(tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID) as env:
            ai_powered_env(env)
            env.run_ai_review_for_real()

            creative = CreativeAssetFactory(
                creative_id="c_ai_verdict",
                name="AI Reviewed Creative",
                format_id=FORMAT_ID,
            )

            with mocked_ai_backend():
                response = env.call_impl(creatives=[creative])
                assert response.creatives[0].action == "created"

                env.await_ai_review("c_ai_verdict")
                committed = env.committed_ai_review("c_ai_verdict")

            assert committed.verdict is not None, (
                "the AI review committed no verdict — the creative is parked at "
                f"status={committed.status!r} waiting on a reviewer that never ran"
            )
            assert committed.verdict["decision"] == EXPECTED_DECISION
            assert committed.verdict["reason"] == AI_REASON
            assert committed.verdict["confidence"] == "high"
            assert committed.status == EXPECTED_DECISION


@pytest.mark.requires_db
class TestAIReviewSurvivesTheWebhookBranch:
    """The webhook branch does not destroy the verdict it just committed.

    This scenario exists because the other two CANNOT reach the failure it
    grades. ``should_call_webhook = bool(webhook_url)`` and ``webhook_url``
    comes ONLY from ``push_notification_config`` -- never from the tenant --
    so a tenant-only fixture never executes the webhook call at all.

    That matters because the webhook branch is where the reviewer's own
    ``except Exception`` handler can fire and REVERT a committed verdict:
    the handler opens a fresh unit of work, sets ``status='pending_review'``
    and writes ``data['ai_review_error']``, WITHOUT clearing
    ``data['ai_review']``. The result is a creative carrying
    ``verdict['decision'] == 'approved'`` AND ``status == 'pending_review'``
    at the same time -- internally contradictory, and invisible to any test
    that only reads ``verdict`` (#1972, HIGH-1).
    """

    def test_ai_powered_sync_with_a_push_config_keeps_its_verdict(self, integration_db):
        with CreativeSyncEnv(tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID) as env:
            ai_powered_env(env)
            env.run_ai_review_for_real()

            creative = CreativeAssetFactory(
                creative_id="c_ai_webhook",
                name="AI Reviewed Creative",
                format_id=FORMAT_ID,
            )

            with mocked_ai_backend():
                response = env.call_impl(
                    creatives=[creative],
                    push_notification_config={"url": "https://buyer.example/hook"},
                )
                assert response.creatives[0].action == "created"

                env.await_ai_review("c_ai_webhook")
                committed = env.committed_ai_review("c_ai_webhook")

            assert committed.verdict is not None, (
                "the AI review committed no verdict — the creative is parked at "
                f"status={committed.status!r} waiting on a reviewer that never ran"
            )
            assert committed.error is None, (
                "the reviewer's error handler fired and rewrote the row: "
                f"{committed.error!r}. The webhook branch must not blow up the review "
                "that already succeeded."
            )
            assert committed.status == EXPECTED_DECISION, (
                f"verdict says {committed.verdict.get('decision')!r} but the committed status is "
                f"{committed.status!r} — the error handler reverted a decision it should never "
                "have seen, leaving the row self-contradictory"
            )


@pytest.mark.requires_db
class TestAIReviewNotifiesSlack:
    """The verdict's escaping effect fires — Slack is told what was decided."""

    def test_ai_powered_sync_sends_the_review_notification(self, integration_db):
        """The reviewer notifies Slack with the decision it committed.

        Graded as the CALL the real notifier receives, with the values the
        verdict determined, rather than as "a notification happened".
        """
        with CreativeSyncEnv(tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID) as env:
            ai_powered_env(env)
            env.run_ai_review_for_real()

            creative = CreativeAssetFactory(
                creative_id="c_ai_slack",
                name="AI Reviewed Creative",
                format_id=FORMAT_ID,
            )

            with mocked_ai_backend() as notifier:
                env.call_impl(creatives=[creative])
                env.await_ai_review("c_ai_slack")

                notifier.notify_creative_pending.assert_called_once_with(
                    creative_id="c_ai_slack",
                    principal_name=PRINCIPAL_ID,
                    format_type=CREATIVE_FORMAT_ID,
                    media_buy_id=None,
                    tenant_id=TENANT_ID,
                    ai_review_reason=AI_REASON,
                )
