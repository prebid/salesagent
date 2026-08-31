"""Integration test: the background order-approval job, end to end against the DB.

Replaces a mocked-session unit test (the 'test_start_approval_creates_sync_job' one) that
asserted the SyncJob's fields off ``session.add.call_args`` — a MagicMock, never a
persisted row — and, because it never let the worker body run under control, leaked a
live daemon thread past the end of the test. That thread later reached the real
``get_db_session()``, found no adapter config, and fired ``_send_approval_webhook`` ->
``httpx.Client`` from inside whatever test happened to be running by then, breaking
``test_approval_webhook_rejects_metadata_url_without_post``'s ``assert_not_called()``
under an unlucky pytest-randomly seed (found and fixed in GH #1941).

Here the whole path runs for real: the row is INSERTed by production code, the worker
thread reads the tenant's adapter config through AdapterConfigRepository, and the
failure is marked and committed — then the test joins the thread before returning, so
nothing escapes into a sibling test.
"""

import threading
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from src.core.database.models import SyncJob
from src.services.order_approval_service import (
    get_approval_status,
    start_order_approval_background,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The worker's first real step is the adapter-config lookup; with no GAM config it
# marks the job failed immediately, so it terminates well inside this budget. Generous
# enough not to flake on a loaded CI box, short enough to fail fast if the thread
# never terminates (the leak this test exists to prevent).
_JOIN_TIMEOUT_SECONDS = 30.0
# Separate from the join budget on purpose: this bounds how long a GATED worker sits
# blocked if a test forgets to release it, which is a different question from how long
# we are willing to wait for a real worker to finish. One constant for both would mean
# tuning either changes the other.
_GATE_TIMEOUT_SECONDS = 30.0

# Real-shaped, deliberately unroutable: ``.invalid`` is reserved by RFC 2606 and
# resolves nowhere. Nothing in the gated test can POST it — the only outbound path,
# ``_send_approval_webhook``, is reachable only from inside ``_run_approval_thread``,
# which the gate stops from ever running — and the reserved host keeps that true even
# if a later edit removes the gate.
_GATED_WEBHOOK_URL = "https://approval-callbacks.invalid/order-approval"


@dataclass
class _ApprovalEnv:
    tenant_id: str
    session: SASession


@pytest.fixture
def approval_env(bound_factory_session):
    """A committed tenant carrying no AdapterConfig row, plus the session that wrote it.

    Committed, not just flushed: the approval worker runs on its own connection and
    would not see an open transaction's writes. The session is handed to the test so
    it can read the persisted row back without opening a raw ``get_db_session()``
    (CLAUDE.md Pattern #8 / the repository-pattern guard) — SyncJob has no repository
    of its own to go through.

    The session and the factory binding both come from ``bound_factory_session``,
    which SAVES and restores the previous binding. The bind-then-null loop this
    replaces was only correct while this fixture happened to be outermost — and its
    own replacement''s docstring said so. Depending on ``bound_factory_session``
    rather than ``integration_db`` also keeps the ordering guarantee: that fixture
    takes ``integration_db`` itself, so factories still bind after the engine swap.
    """
    from tests.factories import TenantFactory

    tenant = TenantFactory(tenant_id="approval_tenant")
    yield _ApprovalEnv(tenant_id=tenant.tenant_id, session=bound_factory_session)


def _join_worker(approval_id: str, *, require_found: bool) -> None:
    """Join the approval worker thread, and assert it is dead afterwards.

    The worker is found by the name production gives it at
    ``src/services/order_approval_service.py:108`` — ``approval-<approval_id>`` — scanning
    ``threading.enumerate()``.

    Why ``threading.enumerate()`` and NOT ``ThreadRegistry.get``, whose own docstring
    (``src/core/thread_registry.py:72``) offers itself "for callers that need to ``join()`` the
    underlying thread": ``enumerate`` observes the interpreter's thread set, which the
    worker cannot deregister itself from. The registry is strictly worse here — it
    reaps dead entries on every read (``src/core/thread_registry.py``: ``contains``
    :65-70 and ``get`` :72-80 both call ``_reap_locked`` :102-108 first) AND the worker
    removes its own entry in a ``finally`` at
    ``src/services/order_approval_service.py:242`` that runs while the thread is
    still alive. So ``registry.get`` returns ``None`` in exactly the alive-but-
    unwinding window this oracle exists for. Do not "simplify" this to ``registry.get``.

    ``require_found`` deliberately has no default: a defaulted ``False`` is the
    not-found-passes shape this helper exists to remove, so every call site has to
    state its answer out loud.
    """
    thread_name = f"approval-{approval_id}"
    worker = next((t for t in threading.enumerate() if t.name == thread_name), None)

    if worker is None:
        assert not require_found, (
            f"no live thread named {thread_name!r}: production's thread name "
            "(order_approval_service.py:108) no longer matches this oracle, which would "
            "silently turn every _join_worker call in this file into a no-op"
        )
        return

    worker.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not worker.is_alive(), (
        f"approval worker {thread_name!r} was still alive {_JOIN_TIMEOUT_SECONDS}s after the join — "
        "it leaked past the end of the test"
    )


def test_start_approval_persists_job_then_worker_marks_it_failed_without_gam_config(approval_env):
    """The job is persisted with its tracking payload, and the worker terminates it.

    One test covers both halves deliberately: the row's creation is only meaningful if
    the worker that owns it can be observed reaching a terminal state, and the join is
    what keeps the thread from outliving the test. The join comes FIRST, before every
    assertion below — that ordering is what makes the status read that follows final by
    construction rather than a poll.
    """
    approval_id = start_order_approval_background(
        order_id="12345",
        media_buy_id="mb_123",
        tenant_id=approval_env.tenant_id,
        principal_id="principal_1",
        # No webhook_url: this test drives the failure path, and _mark_approval_failed
        # POSTs to ANY truthy URL (order_approval_service.py:320 ->
        # _post_approval_webhook_with_retries:359-408, up to 3 httpx posts). Only the
        # gated sibling below, whose worker body never runs, may pass a URL.
        webhook_url=None,
    )

    assert approval_id.startswith("approval_12345_")

    # Join FIRST, then read the status exactly once. After the join the worker is
    # provably finished, so its terminal write is already committed and there is
    # nothing left to poll for.
    #
    # require_found=False is correct HERE and only here: a worker that settles before
    # this line is reached is already out of threading.enumerate(), so demanding it be
    # found would be a flake rather than an oracle. That is safe ONLY because the gated
    # sibling below pins production's thread name with require_found=True while its
    # worker is provably alive. Delete that test and every _join_worker call in this
    # file silently degrades into a no-op.
    _join_worker(approval_id, require_found=False)

    status = get_approval_status(approval_id)
    assert status is not None, f"production never persisted a SyncJob for {approval_id}"

    # Terminal state, reached through the real AdapterConfigRepository lookup.
    assert status["status"] == "failed"
    assert status["error_message"] == "GAM not configured for tenant"
    assert status["completed_at"] is not None

    # Tracking payload production wrote at INSERT time; the worker never rewrites it.
    assert status["progress"]["order_id"] == "12345"
    assert status["progress"]["media_buy_id"] == "mb_123"
    assert status["progress"]["webhook_url"] is None

    # Columns the status API does not expose, read off the persisted row. rollback()
    # first: this session opened its snapshot before the worker committed.
    approval_env.session.rollback()
    job = approval_env.session.scalars(select(SyncJob).where(SyncJob.sync_id == approval_id)).one()
    assert job.tenant_id == approval_env.tenant_id
    assert job.sync_type == "order_approval"
    assert job.adapter_type == "google_ad_manager"
    assert job.triggered_by == "order_creation"
    assert job.triggered_by_id == "mb_123"

    # An `assert not is_approval_running(approval_id)` used to close this test. It was
    # deleted because it could not fail: is_approval_running -> ThreadRegistry.contains
    # (thread_registry.py:65-70) calls _reap_locked (:102-108) FIRST, dropping every
    # entry whose thread is not alive, and only then tests membership. After the join
    # above the thread is provably dead, so that call reaps the entry itself and returns
    # False on every path — including the path where nothing was ever registered.
    # Registry membership is graded where it CAN fail, by
    # tests/unit/test_order_approval_service.py::test_approval_thread_tracks_in_registry,
    # which asserts it TRUE while the worker is blocked on an Event. Do not re-add it.


def test_start_approval_persists_running_row_with_webhook_url_before_worker_runs(approval_env):
    """At INSERT time the row is 'running' and carries the caller's webhook_url.

    The terminal test above cannot observe either fact: by the time it looks, the worker
    has already rewritten ``status`` and ``completed_at``, and it must pass
    ``webhook_url=None`` to keep the failure path from POSTing. So here the worker body
    is gated on an Event and never executes — the same block
    ``tests/unit/test_order_approval_service.py:111-115`` uses to beat the dead-thread
    reaper.

    The patch must be entered BEFORE the call: ``threading.Thread`` binds the module
    global as its ``target`` at construction (``order_approval_service.py:95-109``),
    which is also why patching the name works at all.
    """
    gate = threading.Event()

    with patch(
        "src.services.order_approval_service._run_approval_thread",
        side_effect=lambda *args, **kwargs: gate.wait(timeout=_GATE_TIMEOUT_SECONDS),
    ):
        approval_id = start_order_approval_background(
            order_id="12345",
            media_buy_id="mb_123",
            tenant_id=approval_env.tenant_id,
            principal_id="principal_1",
            webhook_url=_GATED_WEBHOOK_URL,
        )
        try:
            status = get_approval_status(approval_id)
            assert status is not None, f"production never persisted a SyncJob for {approval_id}"
            assert status["status"] == "running"
            assert status["progress"]["webhook_url"] == _GATED_WEBHOOK_URL
        finally:
            gate.set()
            # require_found=True: the worker was blocked on the gate through every
            # assertion above, so a thread carrying production's name MUST be there.
            # This call is what pins the thread-name contract for the whole file — it is
            # the reason the terminal test's require_found=False is an oracle and not a
            # no-op.
            _join_worker(approval_id, require_found=True)
