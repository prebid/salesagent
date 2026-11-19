"""A failed GAM sync stores fixed text on its job, never the exception's text.

The job's ``error_message`` is what the sync status endpoints return later, so
exception text stored there reaches the admin UI as surely as text in the
failing request's own response.
"""

from unittest.mock import MagicMock

import pytest

from src.adapters.gam.managers.sync import GAMSyncManager
from src.core.operator_errors import SYNC_FAILED_OPERATOR_MESSAGE

_SECRET = "GAM fault: networkCode=123 token=ya29.secret"


def test_failed_sync_job_keeps_fixed_text():
    inventory = MagicMock()
    inventory.sync_all_inventory.side_effect = RuntimeError(_SECRET)
    db_session = MagicMock()
    manager = GAMSyncManager(MagicMock(), inventory, MagicMock(), "t1")

    with pytest.raises(RuntimeError):
        manager.sync_inventory(db_session, force=True)

    (sync_job,) = [c.args[0] for c in db_session.add.call_args_list]
    assert sync_job.status == "failed"
    assert sync_job.error_message == SYNC_FAILED_OPERATOR_MESSAGE
