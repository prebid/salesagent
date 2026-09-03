"""Regression: XandrAdapter's manual-approval path must actually notify Slack (GH #1802).

``XandrAdapter._create_human_task`` (src/adapters/xandr.py) does two bare,
non-absolute imports inside its body:

* ``from database_session import get_db_session`` (no ``database_session``
  module exists at the repo root — the real module is
  ``src.core.database.database_session``)
* ``from slack_notifier import get_slack_notifier`` (no ``slack_notifier``
  module exists at the repo root — the real module is
  ``src.services.slack_notifier``)

Both violate the project's absolute-import convention (CLAUDE.md), and both
sit inside the manual-approval path that ``create_media_buy``/
``update_package`` dial when ``manual_approval_required`` is configured — an
ImportError waiting on first fire, exactly the same dead-notification class
PR #1802 already fixed in ``base_workflow``. Nothing exercised this path, so
it survived.

Fixing the imports surfaced a second latent bug in the same call: once
``slack_notifier`` actually resolves, the ``notify_new_task(...)`` call was
passing ``title=``/``description=`` — neither is a real parameter, and the
REQUIRED ``principal_name`` was missing entirely (mypy only caught this once
the import resolved to a real, checkable type). ``mock_notifier`` below is
built with ``spec=SlackNotifier`` specifically so a reintroduced
signature mismatch fails the test too, not only mypy.

This test actually calls ``_create_human_task`` (not an AST scan) and fails
today with ``ModuleNotFoundError`` on the first bare import it hits.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.xandr import XandrAdapter
from src.core.schemas import Principal
from src.services.slack_notifier import SlackNotifier

pytestmark = pytest.mark.unit


def _make_adapter_stand_in() -> SimpleNamespace:
    """A minimal stand-in carrying only what _create_human_task reads.

    XandrAdapter itself cannot be instantiated today (src/adapters/xandr.py's
    own header: "needs full refactor" — it's missing 4 abstract methods and is
    commented out of the adapter registry entirely). That is out of scope for
    this bug (a bare import inside one method); this stand-in calls the real,
    unbound ``_create_human_task`` — the actual production method body,
    imports included — against the two attributes it reads, without pulling
    the unrelated full-adapter-construction gap into this fix's scope.
    """
    principal = Principal(principal_id="p1", name="Test Advertiser", platform_mappings={})
    return SimpleNamespace(tenant_id="test_tenant", principal=principal)


class TestXandrManualApprovalNotifiesSlack:
    def test_create_human_task_notifies_slack_when_webhook_configured(self):
        """A tenant with slack_webhook_url set gets a real Slack notification."""
        adapter = _make_adapter_stand_in()

        mock_tenant = MagicMock()
        mock_tenant.slack_webhook_url = "https://hooks.slack.example/services/T00/B00/XXX"

        mock_session = MagicMock()
        mock_session.scalars.return_value.first.return_value = mock_tenant

        mock_session_cm = MagicMock()
        mock_session_cm.__enter__.return_value = mock_session
        mock_session_cm.__exit__.return_value = False

        mock_notifier = MagicMock(spec=SlackNotifier)

        with (
            patch("src.core.database.database_session.get_db_session", return_value=mock_session_cm),
            patch("src.services.slack_notifier.get_slack_notifier", return_value=mock_notifier) as get_notifier,
        ):
            task_id = XandrAdapter._create_human_task(
                adapter,
                "create_media_buy",
                {"media_buy_id": "mb_123"},
            )

        assert task_id.startswith("task_")
        get_notifier.assert_called_once_with({"features": {"slack_webhook_url": mock_tenant.slack_webhook_url}})
        mock_notifier.notify_new_task.assert_called_once_with(
            task_id=task_id,
            task_type="create_media_buy",
            principal_name=adapter.principal.name,
            media_buy_id="mb_123",
        )
