"""Background order approval polling service for GAM.

GAM requires time (0-120 seconds) to run inventory forecasting before an order
can be approved. This service polls GAM in the background and notifies via webhook
when approval completes or fails.
"""

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob
from src.core.security.webhook_egress import deliver_webhook
from src.core.signing.outbound import delivery_signer_for_tenant
from src.core.thread_registry import ThreadRegistry
from src.core.webhook_validator import webhook_url_for_log
from src.core.webhooks.delivery import WebhookDeliveryOutcome

logger = logging.getLogger(__name__)

# Global registry of running approval threads. ThreadRegistry reaps dead
# threads on every read — same defensive cleanup as the sync registry
# (production memory-leak triage #5).
_active_approvals = ThreadRegistry()

# The parallel stop-signal dict, the same dual-dict shape delivery_simulator.py
# and gam/managers/reporting.py already use and that ThreadRegistry's own
# docstring sanctions. Without it the registry could observe a thread but never
# stop one: the retry path below sleeps 2**attempt behind HTTP POSTs that time
# out after 10 s, so a started approval outlived the request that began it by
# tens of seconds. Measured (#2056): a thread from one unit test called sleep(1)
# and sleep(2) inside two OTHER tests ~1500 tests later, landing in their mocks
# because src/core/webhook_delivery.py imports `time` and patching
# src.core.webhook_delivery.time.sleep replaces it process-wide.
_stop_signals: dict[str, threading.Event] = {}
_stop_signals_lock = threading.Lock()  # Protects _stop_signals iteration


def _on_approval_reaped(approval_id: str) -> None:
    """Drop the parallel _stop_signals entry when a dead approval is reaped.

    Lock-free by design, exactly as delivery_simulator._on_simulation_reaped is:
    ``dict.pop`` is atomic under the GIL, and the reap path runs
    registry-lock -> here while the accessors take _stop_signals_lock ->
    registry. Taking the lock here would invert that order and risk an ABBA
    deadlock.
    """
    _stop_signals.pop(approval_id, None)


_active_approvals.add_reap_callback(_on_approval_reaped)


def get_approval_stop_signal(approval_id: str) -> threading.Event | None:
    """The stop signal for a running approval, or None if it is not running."""
    with _stop_signals_lock:
        return _stop_signals.get(approval_id)


def cancel_order_approval(approval_id: str) -> bool:
    """Ask a running approval thread to stop; True if one was signalled.

    Cooperative: it sets the Event the worker's interruptible sleep waits on, so
    the thread stops at its next sleep boundary rather than being killed. Join
    it with ``_active_approvals.get(approval_id)`` when the caller needs the
    thread actually finished.
    """
    signal = get_approval_stop_signal(approval_id)
    if signal is None:
        return False
    signal.set()
    return True


def start_order_approval_background(
    order_id: str,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str,
    webhook_url: str | None = None,
    max_attempts: int = 12,
    poll_interval_seconds: int = 10,
) -> str:
    """Start background order approval polling.

    Args:
        order_id: GAM order ID to approve
        media_buy_id: Associated media buy ID
        tenant_id: Tenant identifier
        principal_id: Principal identifier
        webhook_url: Optional webhook URL to notify on completion
        max_attempts: Maximum polling attempts (default: 12 = 2 minutes)
        poll_interval_seconds: Seconds between polling attempts (default: 10)

    Returns:
        approval_id: The approval job ID for tracking progress

    Raises:
        ValueError: If an approval is already running for this order
    """
    # Check if approval already running
    with get_db_session() as db:
        stmt = select(SyncJob).where(
            SyncJob.sync_type == "order_approval",
            SyncJob.status == "running",
        )
        existing_approvals = db.scalars(stmt).all()

        # Check if any existing approval is for this order
        for approval in existing_approvals:
            if approval.progress and approval.progress.get("order_id") == order_id:
                raise ValueError(f"Approval already running for order {order_id}: {approval.sync_id}")

        # Create new approval job
        approval_id = f"approval_{order_id}_{int(datetime.now(UTC).timestamp())}"

        approval_job = SyncJob(
            sync_id=approval_id,
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            sync_type="order_approval",
            status="running",
            started_at=datetime.now(UTC),
            triggered_by="order_creation",
            triggered_by_id=media_buy_id,
            progress={
                "order_id": order_id,
                "media_buy_id": media_buy_id,
                "principal_id": principal_id,
                "webhook_url": webhook_url,
                "attempts": 0,
                "max_attempts": max_attempts,
                "phase": "Starting approval polling",
            },
        )
        db.add(approval_job)
        db.commit()

    # Reserve the stop signal BEFORE starting the thread, so a cancel racing the
    # start still lands: the worker reads the signal, and a caller that cancels
    # between add() and the worker's first sleep finds an Event already there.
    # Never call into the registry while holding this lock — the registry has its
    # own, and the reap callback re-enters _stop_signals (ABBA avoidance).
    with _stop_signals_lock:
        _stop_signals[approval_id] = threading.Event()

    # Start background thread
    thread = threading.Thread(
        target=_run_approval_thread,
        args=(
            approval_id,
            order_id,
            media_buy_id,
            tenant_id,
            principal_id,
            webhook_url,
            max_attempts,
            poll_interval_seconds,
        ),
        daemon=True,
        name=f"approval-{approval_id}",
    )

    _active_approvals.add(approval_id, thread)

    thread.start()
    logger.info(f"Started background approval polling thread: {approval_id}")

    return approval_id


def _run_approval_thread(
    approval_id: str,
    order_id: str,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str,
    webhook_url: str | None,
    max_attempts: int,
    poll_interval_seconds: int,
):
    """Run the actual approval polling in a background thread.

    This function runs in a separate thread and polls GAM every 10 seconds
    for up to 2 minutes (12 attempts) to approve the order. Updates the SyncJob
    record as it progresses.
    """
    try:
        logger.info(f"[{approval_id}] Starting order approval polling for order {order_id}")

        # Import here to avoid circular dependencies
        from src.adapters.gam.managers.orders import GAMOrdersManager

        # Get adapter config via repository
        with get_db_session() as db:
            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            adapter_repo = AdapterConfigRepository(db, tenant_id)
            adapter_config = adapter_repo.find_by_tenant()

            if not adapter_config or not adapter_config.gam_network_code:
                _mark_approval_failed(
                    approval_id, "GAM not configured for tenant", webhook_url, tenant_id, principal_id, media_buy_id
                )
                return

            gam_config = adapter_repo.get_gam_config(adapter_config)

        # Create GAM client
        from src.adapters.gam.client import GAMClientManager

        client_manager = GAMClientManager(gam_config, adapter_config.gam_network_code)
        orders_manager = GAMOrdersManager(client_manager)

        # Poll GAM approval endpoint
        for attempt in range(1, max_attempts + 1):
            try:
                _update_approval_progress(
                    approval_id, {"attempts": attempt, "phase": f"Approval attempt {attempt}/{max_attempts}"}
                )

                logger.info(f"[{approval_id}] Approval attempt {attempt}/{max_attempts} for order {order_id}")

                # Attempt approval
                success = orders_manager.approve_order(order_id, max_retries=1)

                if success:
                    # Approval succeeded
                    _mark_approval_complete(
                        approval_id,
                        {
                            "order_id": order_id,
                            "media_buy_id": media_buy_id,
                            "attempts": attempt,
                            "duration_seconds": attempt * poll_interval_seconds,
                        },
                        webhook_url,
                        tenant_id,
                        principal_id,
                        media_buy_id,
                    )
                    logger.info(f"[{approval_id}] Order {order_id} approved after {attempt} attempts")
                    return

                # Check if we should retry
                if attempt < max_attempts:
                    logger.info(
                        f"[{approval_id}] Approval not ready yet, waiting {poll_interval_seconds}s before retry"
                    )
                    time.sleep(poll_interval_seconds)
                else:
                    # Max attempts reached
                    error_msg = f"Order approval failed after {max_attempts} attempts (2 minutes). GAM forecasting may still be in progress."
                    _mark_approval_failed(approval_id, error_msg, webhook_url, tenant_id, principal_id, media_buy_id)
                    return

            except Exception as e:
                error_str = str(e)

                # Check for non-retryable errors
                if "NO_FORECAST_YET" not in error_str and "ForecastingError" not in error_str:
                    # Non-retryable error
                    _mark_approval_failed(
                        approval_id,
                        f"Non-retryable error: {error_str}",
                        webhook_url,
                        tenant_id,
                        principal_id,
                        media_buy_id,
                    )
                    return

                # Retryable error - continue polling
                if attempt < max_attempts:
                    logger.warning(f"[{approval_id}] Retryable error: {error_str}, will retry")
                    time.sleep(poll_interval_seconds)
                else:
                    # Max attempts reached
                    _mark_approval_failed(
                        approval_id,
                        f"Order approval timed out after {max_attempts} attempts: {error_str}",
                        webhook_url,
                        tenant_id,
                        principal_id,
                        media_buy_id,
                    )
                    return

    except Exception as e:
        logger.error(f"[{approval_id}] Approval polling failed: {e}", exc_info=True)
        _mark_approval_failed(approval_id, str(e), webhook_url, tenant_id, principal_id, media_buy_id)

    finally:
        # Remove from active approvals
        _active_approvals.remove(approval_id)


def _update_approval_progress(approval_id: str, progress_data: dict[str, Any]):
    """Update approval job progress in database."""
    try:
        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()
            if approval_job:
                # Merge with existing progress
                if approval_job.progress:
                    approval_job.progress.update(progress_data)
                else:
                    approval_job.progress = progress_data
                db.commit()
    except Exception as e:
        logger.warning(f"Failed to update approval progress: {e}")


def _mark_approval_complete(
    approval_id: str,
    summary: dict[str, Any],
    webhook_url: str | None,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
):
    """Mark approval as completed and send webhook notification."""
    try:
        with get_db_session() as db:
            import json

            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()
            if approval_job:
                approval_job.status = "completed"
                approval_job.completed_at = datetime.now(UTC)
                approval_job.summary = json.dumps(summary) if summary else None
                db.commit()

        # Send webhook notification
        if webhook_url:
            _send_approval_webhook(
                webhook_url=webhook_url,
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                status="approved",
                message="Order approved successfully",
                order_id=summary.get("order_id"),
                attempts=summary.get("attempts"),
                stop_signal=get_approval_stop_signal(approval_id),
            )

    except Exception as e:
        logger.error(f"Failed to mark approval complete: {e}")


def _mark_approval_failed(
    approval_id: str,
    error_message: str,
    webhook_url: str | None,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
):
    """Mark approval as failed and send webhook notification."""
    try:
        # Read the progress fields BEFORE the session closes. ``db.commit()``
        # expires every attribute on ``approval_job``, so touching ``.progress``
        # after the ``with`` block raises DetachedInstanceError — which the
        # ``except`` below then swallowed, and the buyer was never told the order
        # had failed at all (salesagent-98t2, reproduced by
        # tests/integration/test_order_approval_webhook_signing.py).
        order_id: str | None = None
        attempts: int | None = None

        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()
            if approval_job:
                approval_job.status = "failed"
                approval_job.completed_at = datetime.now(UTC)
                approval_job.error_message = error_message
                db.commit()

                progress = approval_job.progress or {}
                order_id = progress.get("order_id")
                attempts = progress.get("attempts")

        # Send webhook notification
        if webhook_url:
            _send_approval_webhook(
                webhook_url=webhook_url,
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                status="failed",
                message=error_message,
                order_id=order_id,
                attempts=attempts,
                stop_signal=get_approval_stop_signal(approval_id),
            )

    except Exception as e:
        logger.error(f"Failed to mark approval failed: {e}")


def _lookup_approval_webhook_auth(
    tenant_id: str, principal_id: str, webhook_url: str
) -> tuple[str | None, str | None, str | None]:
    """Resolve ``(scheme, credentials, validation_token)`` for this webhook URL.

    The lookup goes through :class:`PushNotificationConfigRepository` rather than
    a hand-written ``select`` here, so the (tenant, principal, active) scope that
    every config lookup must carry is enforced in one place instead of being
    retyped at this call site.

    The three columns are read INSIDE the session and returned as plain values:
    the config row itself never escapes the session, so no caller can touch a
    detached instance, and the auth decision is resolved exactly once per
    delivery.
    """
    from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository

    with get_db_session() as db:
        config = PushNotificationConfigRepository(db, tenant_id).find_by_url(
            principal_id, webhook_url, active_only=True
        )
        if config is None:
            return None, None, None
        return config.authentication_type, config.authentication_token, config.validation_token


def _approval_webhook_headers(validation_token: str | None) -> dict[str, str]:
    """Build HTTP headers for an order-approval webhook POST.

    Takes the already-resolved authentication rather than the config row,
    so the auth decision is made exactly once per delivery (in
    :func:`_send_approval_webhook`) and this function cannot reach a different
    answer than the signing branch did.

    ``validation_token`` stays outside the resolver deliberately: this sender
    emits ``X-Webhook-Token`` and ``protocol_webhook_service`` does not, so
    folding it into the shared decision would silently change one sender's
    headers under cover of unification.
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AdCP-Sales-Agent/1.0 (Order Approval Notifications)",
    }
    # No auth ladder here any more: the seam applies whatever the registered scheme
    # requires. X-Webhook-Token STAYS, because it is sender-local — this sender
    # emits it and protocol_webhook_service does not, so folding it into the shared
    # decision would silently change one sender's headers under cover of unification.
    if validation_token:
        headers["X-Webhook-Token"] = validation_token
    return headers


def _post_approval_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    scheme: str | None = None,
    credentials: str | None = None,
    tenant_id: str | None = None,
    principal_id: str | None = None,
    stop_signal: threading.Event | None = None,
) -> WebhookDeliveryOutcome | None:
    """POST the approval payload through the egress seam and say what became of it.

    The seam owns every address and transport decision this function used to make
    for itself, which is why none of them is restated here: https-only, reserved-
    range refusal and resolve-once IP pinning (so the send-time SSRF gate is
    subsumed — there is no separate pre-flight check to run), refusing to follow
    redirects, the response-size cap, what counts as retryable (4xx terminal,
    5xx/429 retried), and BR-RULE-029's 1s/2s/4s-plus-jitter backoff.

    Authentication is likewise one decision, made once, at the seam. The stored
    PRIMITIVES go over as ``scheme=`` / ``credentials=`` (never a type this caller
    constructed and would have to interpret a ``ValidationError`` from), and
    ``signer=`` carries the tenant's RFC 9421 strategy for the arm the pinned
    schema selects by the ABSENCE of an ``authentication`` block (security.mdx @
    v3.1.1 :1424). Handing over a strategy rather than a signature is the point:
    the seam invokes ``build_auth_headers`` once PER ATTEMPT over
    ``request.content``, so every retry carries a fresh ``nonce`` over the exact
    bytes httpx is about to transmit — a signature computed once above a retry
    loop is one a conformant receiver must reject on attempt two.

    What stays local is the logging contract: every message names the SANITIZED
    URL, never the raw one, so a webhook URL carrying credentials or a token in
    its query string cannot reach the logs.

    ``stop_signal`` is the last boundary a cancelled approval can still stop at
    inside this module. It is passed in rather than looked up from the registry
    so this helper stays ignorant of approval bookkeeping — it only needs "has
    the caller been asked to stop". Its reach narrowed when the retry loop moved
    behind the seam: the interruptible ``Event.wait`` that replaced
    ``time.sleep(2 ** attempt)`` has no loop left to sit in, because the seam
    owns attempt count and backoff and exposes no cancellation hook. So the
    check happens HERE, before the hand-off — a cancelled approval never enters
    a three-attempt delivery it could no longer be pulled out of. It does NOT
    abort a delivery already in flight; do not read it as if it did.

    Returns ``None`` — never a fabricated outcome — when the approval was
    cancelled before the dial. ``WebhookDeliveryOutcome.kind`` is a closed Literal
    (delivered / refused_destination / refused_auth / client_error / exhausted)
    and none of them means "not attempted"; reusing ``refused_destination`` would
    claim a policy refused a URL that was never judged. Absence is the honest
    answer.
    """
    safe_url = webhook_url_for_log(webhook_url)
    if stop_signal is not None and stop_signal.is_set():
        # The cancel landed before we dialled, which is the only moment this
        # module still controls. Logged at INFO, not WARNING: a cancelled
        # approval not sending its webhook is the requested outcome, not a fault.
        logger.info("Approval webhook to %s not sent: the approval was cancelled", safe_url)
        return None

    # The tenant's RFC 9421 strategy, resolved on a session the signing layer opens and
    # CLOSES here, before ``deliver_webhook`` below can dial anything (#1757). One shared
    # ``delivery_signer_for_tenant`` rather than a local composition of ``signing_repo`` +
    # ``webhook_delivery_signer``: all three webhook senders needed the identical
    # open-read-close, and three copies is how the key a delivery is signed with starts
    # depending on which transport carried it.
    #
    # No ``try``, and that is the no-silent-downgrade rule. ``None`` is returned only for
    # the DECIDED postures (no tenant/repository, or published capabilities that already
    # say ``webhook_signing.supported=false``), which mean "this receiver was told not to
    # expect a Signature header" and are delivered plain by the seam. Every other outcome
    # RAISES out of the helper — notably ``AdCPConfigurationError`` when the key's ``alg``
    # contradicts the declared ``webhook_signing.algorithms`` — and PROPAGATES past the
    # call below, so nothing is serialized and nothing is sent. This sender lets it reach
    # the two polling-thread callers that already wrap their call; the other two senders
    # book the same raise their own way, which is why the helper resolves and never handles.
    #
    # Resolved UNCONDITIONALLY, without first asking whether the stored registration
    # selects a legacy arm. That is the seam's stated contract (``_headers_for``: "a
    # caller reading a stored row cannot know which arm the row selects, so it passes
    # the tenant's signer unconditionally and this match decides"), and re-deriving the
    # arm here is precisely the duplicated selector #1802 exists to delete. Consequence,
    # stated rather than discovered: a tenant whose 9421 declaration is inconsistent now
    # fails loudly even on a legacy-registered delivery, where the old SDK path returned
    # early and never looked.
    signer = delivery_signer_for_tenant(tenant_id)

    outcome = deliver_webhook(
        webhook_url,
        payload,
        scheme=scheme,
        credentials=credentials,
        headers=headers,
        timeout=10.0,
        max_attempts=3,
        signer=signer,
    )

    if outcome.kind == "refused_auth":
        # FAIL-CLOSED BACKSTOP -- deliberately log-and-return, and deliberately NOT
        # a raise. This is not the primary refusal: a non-conforming registration is
        # rejected at INGEST, where a request still exists to refuse into and the
        # buyer -- the only party who can fix it -- actually sees it.
        #
        # By the time control reaches here we are on a daemon thread, after
        # create_media_buy already returned, with the approval committed; the outcome
        # returned from here is booked by nobody who could act on it, and this
        # function's exceptions are blanket-caught one frame up by
        # _mark_approval_complete / _mark_approval_failed. There is no caller left
        # that could act on a raise, and TestExhaustedDeliveryIsSilent grades that
        # this path stays non-raising. So the backstop's job is narrow: never let an
        # unauthenticated request reach a receiver that asked to be authenticated.
        #
        # This does NOT cite "No Quiet Failures" -- that rule's worked example bans
        # exactly this shape, and the honest reason it is an exception is above. It is
        # also NOT the arm a failed signer resolution takes: that one raises above,
        # before deliver_webhook is reached, because an unsignable delivery must not
        # be downgraded to an unsigned one.
        logger.error(
            "Refusing to send approval webhook to %s: %s (tenant=%s, principal=%s)",
            safe_url,
            outcome.detail or outcome.reason,
            tenant_id,
            principal_id,
        )
    elif outcome.kind == "refused_destination":
        # The URL never left the process. Deliberately opaque: the seam has already
        # logged which policy refused it and why. ``attempts`` is zero, so ``signer``
        # was never invoked and no unsigned body was ever produced to fall back to.
        # Severity carried on the outcome, not chosen here (#1802).
        logger.log(outcome.log_level, "Approval webhook to %s was refused by egress policy", safe_url)
    elif outcome.kind != "delivered":
        logger.error(
            "Failed to send approval webhook to %s after %s attempts (last status: %s)",
            safe_url,
            outcome.attempts,
            outcome.http_status,
        )
    else:
        logger.info(
            "Approval webhook sent to %s (status: %s, attempts: %s)",
            safe_url,
            payload.get("status"),
            outcome.attempts,
        )
    return outcome


def _send_approval_webhook(
    webhook_url: str,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
    status: str,
    message: str,
    order_id: str | None = None,
    attempts: int | None = None,
    stop_signal: threading.Event | None = None,
) -> WebhookDeliveryOutcome | None:
    """Send webhook notification for approval status update.

    Returns the seam's :class:`~src.core.webhooks.delivery.WebhookDeliveryOutcome` rather
    than ``None``. That is what stops a refused destination being indistinguishable from a
    delivery to everything upstream — the defect the deleted
    ``_reject_unsafe_approval_webhook_url`` bool had, and which a bare ``return`` after a
    log line would quietly reinstate.

    There is deliberately no blanket ``except Exception`` around this body any more. It
    used to reduce EVERY failure to one log line, including the two that must not be
    reduced: a tenant whose RFC 9421 signer cannot be built (see
    :func:`~src.core.signing.outbound.delivery_signer_for_tenant`, resolved in
    :func:`_post_approval_webhook` — that must fail the delivery, never downgrade it to
    an unsigned send) and a failure to read the registration at all. Transport failure no
    longer needs the guard, because it is an outcome rather than an exception. Both
    callers, :func:`_mark_approval_complete` and :func:`_mark_approval_failed`, already
    wrap their call, so nothing escapes into the polling thread.

    Args:
        webhook_url: Webhook URL to POST to
        tenant_id: Tenant identifier
        principal_id: Principal identifier
        media_buy_id: Media buy identifier
        status: Approval status (approved, failed)
        message: Status message
        order_id: GAM order ID (if available)
        attempts: Number of polling attempts (if available)
        stop_signal: The approval's cancel Event, when one is running. Checked
            before the delivery is handed to the egress seam; None means
            "nothing to cancel", which is what every non-worker caller passes.
    """
    from adcp.webhooks import generate_webhook_idempotency_key

    payload: dict[str, Any] = {
        "event": "order_approval_update",
        "media_buy_id": media_buy_id,
        "status": status,
        "message": message,
        "timestamp": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        # Carried in the BODY, exactly where the SDK sender this call replaces injected
        # it (``WebhookSender.send_raw``: ``{**payload, "idempotency_key": key}``), so
        # routing through the seam does not quietly cost the receiver its dedup key.
        # ONE key per distinct EVENT: the seam serializes once and retries those same
        # bytes, so every attempt of this event carries this key and no other.
        "idempotency_key": generate_webhook_idempotency_key(),
    }

    if order_id:
        payload["order_id"] = order_id
    if attempts is not None:
        payload["attempts"] = attempts

    scheme, credentials, validation_token = _lookup_approval_webhook_auth(tenant_id, principal_id, webhook_url)

    # The egress seam validates the URL as part of sending it, so there is no
    # separate SSRF pre-flight here: one refusal path, raised as
    # OutboundRequestBlocked before any connection is attempted.
    return _post_approval_webhook(
        webhook_url,
        payload,
        _approval_webhook_headers(validation_token),
        scheme=scheme,
        credentials=credentials,
        tenant_id=tenant_id,
        principal_id=principal_id,
        # Threaded through from the caller, not looked up here: _send_approval_webhook
        # is called from the worker thread AND from tests with no approval row at all,
        # so "no signal" has to stay a legal, meaningful argument.
        stop_signal=stop_signal,
    )


def get_active_approvals() -> list[str]:
    """Get list of approval IDs currently running in background threads.

    Reaps dead threads on read so the returned list reflects live state
    even if the worker's ``finally`` cleanup didn't fire.
    """
    return _active_approvals.list_active()


def is_approval_running(approval_id: str) -> bool:
    """Check if an approval is currently running in a background thread.

    Reaps dead threads on read — an approval_id with a dead thread is no
    longer running, so this returns False (and the entry is pruned).
    """
    return _active_approvals.contains(approval_id)


def get_approval_status(approval_id: str) -> dict[str, Any] | None:
    """Get current status of an approval job.

    Args:
        approval_id: Approval job identifier

    Returns:
        Dictionary with approval status or None if not found
    """
    try:
        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()

            if not approval_job:
                return None

            started_at_iso = None
            if approval_job.started_at is not None:
                # Handle both datetime and SQLAlchemy DateTime objects
                if hasattr(approval_job.started_at, "isoformat"):
                    started_at_iso = approval_job.started_at.isoformat()
                else:
                    started_at_iso = str(approval_job.started_at)

            completed_at_iso = None
            if approval_job.completed_at is not None:
                # Handle both datetime and SQLAlchemy DateTime objects
                if hasattr(approval_job.completed_at, "isoformat"):
                    completed_at_iso = approval_job.completed_at.isoformat()
                else:
                    completed_at_iso = str(approval_job.completed_at)

            return {
                "approval_id": approval_id,
                "status": approval_job.status,
                "started_at": started_at_iso,
                "completed_at": completed_at_iso,
                "progress": approval_job.progress,
                "error_message": approval_job.error_message,
                "summary": approval_job.summary,
            }
    except Exception as e:
        logger.error(f"Error getting approval status: {e}")
        return None
