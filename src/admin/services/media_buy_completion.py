"""Shared media-buy completion / rejection webhook emission for the admin approval routes.

The operations approve and reject routes duplicated a near-identical
notification-emission block. Extracting it here (a) removes the duplication and
(b) lets the workflow and creative-unblock approval routes emit the same
completion artifact — async buyers otherwise never receive the final
``revision``/``confirmed_at`` for an approved buy. See #1544.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from sqlalchemy.orm import Session

from src.adapters.base import AdapterIdempotencyUncertain
from src.core.database.models import MEDIA_BUY_FINALIZING_STATUS
from src.core.database.repositories import MediaBuyRepository
from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
from src.core.database.repositories.workflow import WorkflowRepository
from src.core.exceptions import AdCPAdapterError, build_two_layer_error_envelope
from src.core.media_buy_flight import lifecycle_status_for_window, resolve_flight_window_utc
from src.core.schemas import CreateMediaBuySuccess, Package
from src.core.webhook_validator import validate_webhook_task_type
from src.services.protocol_webhook_service import get_protocol_webhook_service

if TYPE_CHECKING:
    from src.core.database.models import MediaBuy, MediaPackage

logger = logging.getLogger(__name__)


class FinalizeOutcome(StrEnum):
    """Result of an approve/reject finalization, for single-winner orchestration. #1544.

    ``APPLIED`` — this request won the claim and applied the decision (order created /
    rejected). ``ADAPTER_FAILED`` — won the claim but the adapter reported a handled
    failure (buy marked failed). ``NOT_CLAIMED`` — a competing request/owner already
    decided or took over the buy (or it vanished): this request did NOTHING further
    (no adapter, no terminalization, no emit). ``RETRYING`` — the operation could not
    complete NOW but the buy remains in the ``finalizing`` claim for the scheduler's
    reconciler: either the adapter raised ``AdapterIdempotencyUncertain`` (nothing
    remote happened; automatic retry) or a crash left a possibly-partial remote graph
    on a non-replayable adapter (``manual_required`` — operator action). #1637.
    """

    APPLIED = "applied"
    ADAPTER_FAILED = "adapter_failed"
    NOT_CLAIMED = "not_claimed"
    RETRYING = "retrying"


# Phase-2 lease TTL (#1637): must cover the WORST-CASE full finalization — GAM order
# create (60s timeout) + line items (up to 300s) + order approval (up to 620s) +
# creative upload — ≈1300s worst case, so default 3600s (~2.5× margin). A worker that
# somehow outlives even this cannot double-publish (every mutation is lease-CAS'd);
# at worst the reconciler flags manual_required and the worker's eventual successful
# publish self-heals it.
FINALIZE_LEASE_TTL_SECONDS = int(os.getenv("MEDIA_BUY_FINALIZE_LEASE_TTL") or "3600")


def build_media_buy_result(
    media_buy: MediaBuy,
    packages: list[MediaPackage],
    *,
    rejection_reason: str | None = None,
) -> CreateMediaBuySuccess:
    """Build the internal ``CreateMediaBuySuccess`` the completion/rejection webhook carries.

    Echoes the persisted ``confirmed_at``/``revision``. ``rejection_reason`` is set
    only on the reject path (a spec MUST on the seller rejection notification, pinned
    beta.3); ``None`` otherwise, so it is omitted from the wire.
    """
    return CreateMediaBuySuccess(
        media_buy_id=media_buy.media_buy_id,
        packages=[Package(package_id=p.package_id) for p in packages],
        context={},
        confirmed_at=media_buy.confirmed_at,
        revision=media_buy.revision,
        rejection_reason=rejection_reason,
    )


def emit_media_buy_webhook(
    step_data: dict[str, Any],
    webhook_config: Any,
    result: CreateMediaBuySuccess,
    status: AdcpTaskStatus,
) -> None:
    """Send the media-buy completion/rejection notification for the buyer's protocol.

    Protocol (``mcp``/``a2a``) is read from the workflow step's ``request_data``.
    Best-effort: a webhook failure is logged, never raised — the DB transition has
    already committed and must not be rolled back by a delivery error (mirrors the
    approval routes). #1544.
    """
    metadata = {"task_type": step_data["tool_name"]}
    # Default to MCP for backward compatibility with steps recorded before the
    # protocol was persisted on request_data.
    protocol = step_data["request_data"].get("protocol", "mcp")
    # Correlate to the id the BUYER holds. A2A returned an outer transport task id
    # (persisted on the step's request_data as ``external_task_id`` at create time);
    # the buyer polls / receives the webhook against THAT id, not the internal
    # step_id. MCP/REST have no outer id, so they fall back to step_id. #1544 B6.
    correlation_task_id = step_data["request_data"].get("external_task_id") or step_data["step_id"]
    payload: Any
    if protocol == "a2a":
        payload = create_a2a_webhook_payload(
            task_id=correlation_task_id,
            status=status,
            result=result,
            context_id=step_data["context_id"],
        )
    else:
        # tool_name is untrusted (workflow_steps DB column) — validate a COPY for the
        # SDK payload; metadata keeps the original label.
        payload = create_mcp_webhook_payload(
            task_id=correlation_task_id,
            task_type=validate_webhook_task_type(step_data.get("tool_name", "create_media_buy")),
            result=result,
            status=status,
        )
    try:
        service = get_protocol_webhook_service()
        asyncio.run(
            service.send_notification(
                push_notification_config=webhook_config,
                payload=payload,
                metadata=metadata,
            )
        )
        logger.info(f"Sent {status} webhook notification for media buy {result.media_buy_id}")
    except Exception as webhook_err:
        logger.warning(f"Failed to send webhook notification: {webhook_err}")


def emit_media_buy_completion(
    session: Session,
    tenant_id: str,
    media_buy: MediaBuy | None,
    packages: list[MediaPackage],
    step_data: dict[str, Any],
    status: AdcpTaskStatus,
    *,
    rejection_reason: str | None = None,
) -> None:
    """Look up the buyer's active push config from the workflow step and emit the
    completion/rejection artifact if one is configured. No-op when the buy has no
    push_notification_config — so every approval route (operations, workflow,
    creative-unblock) delivers the final revision/confirmed_at the same way. #1544.
    """
    if media_buy is None:
        return
    push_config = (step_data.get("request_data") or {}).get("push_notification_config") or {}
    url = push_config.get("url")
    if not url:
        return
    # Repository lookup (no raw select): the buyer's active configs for this
    # principal, matched to the step's push URL — newest wins if more than one.
    configs = PushNotificationConfigRepository(session, tenant_id).list_active_by_principal(media_buy.principal_id)
    matches = sorted((c for c in configs if c.url == url), key=lambda c: c.created_at, reverse=True)
    if not matches:
        return
    webhook_config = matches[0]
    emit_media_buy_webhook(
        step_data,
        webhook_config,
        build_media_buy_result(media_buy, packages, rejection_reason=rejection_reason),
        status,
    )


def _terminalize_step_and_emit(
    session: Session,
    tenant_id: str,
    *,
    media_buy: MediaBuy,
    packages: list[MediaPackage],
    step_id: str,
    step_data: dict[str, Any],
    step_status: str,
    task_status: AdcpTaskStatus,
    rejection_reason: str | None = None,
) -> None:
    """Persist the terminal decision artifact on the workflow step, commit, then emit.

    The single place a media-buy approve/reject decision becomes durable AND
    observable: stores the built ``CreateMediaBuySuccess`` on
    ``WorkflowStep.response_data`` under ``step_status``
    (``completed``/``rejected``/``failed``) so ``tasks/get`` can return the final
    artifact, commits, then emits the buyer's completion/rejection webhook AFTER
    commit (best-effort — a delivery failure never rolls back the committed
    decision). The workflow + creative-unblock routes previously left the step
    non-terminal with no artifact; centralising it here fixes both. #1544.
    """
    result = build_media_buy_result(media_buy, packages, rejection_reason=rejection_reason)
    WorkflowRepository(session, tenant_id).update_status(
        step_id,
        status=step_status,
        response_data=result.model_dump(mode="json"),
        error_message=rejection_reason,
    )
    session.commit()
    emit_media_buy_completion(
        session, tenant_id, media_buy, packages, step_data, task_status, rejection_reason=rejection_reason
    )


def finalize_media_buy_approval(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    step_id: str,
    step_data: dict[str, Any],
    compute_target: Callable[[MediaBuy], str | None],
    run_adapter: Callable[[], tuple[bool, str | None]],
    expected_status: str | tuple[str, ...],
    approved_by: str | None = None,
    approved_at: datetime.datetime | None = None,
) -> tuple[FinalizeOutcome, str | None]:
    """Atomic, single-winner approve finalizer (operations / workflow / creative-unblock).

    Sequence (see #1544 review B4/B5 and the P1 single-winner fix):

      1. CLAIM the decision UNDER THE ROW LOCK: transition ONLY if the committed status
         is still in ``expected_status`` (``update_status_computed`` with
         ``expected_status``), to the flight-derived target computed post-lock. If the
         claim is lost (a concurrent approve/reject/hold already moved the buy) →
         rollback and return ``NOT_CLAIMED`` WITHOUT touching the adapter, so the remote
         order is created exactly once. The claim stamps ``approved_at``/``approved_by``
         (when supplied) — the write-once approval instant ``confirmed_at`` records,
         BEFORE any external work. commit.
      2. Run the adapter. On failure: mark the buy + step ``failed`` (with a buyer-facing
         error envelope on ``response_data``), commit, return ``ADAPTER_FAILED``.
      3. On success: terminalize the step (``completed``) with the response artifact,
         commit, emit the completion webhook after commit, return ``APPLIED``.

    ``approved_at``/``approved_by`` are omitted when finalizing a buy already stamped at
    an earlier ``pending_creatives`` hold (creative-unblock): ``confirmed_at`` is
    write-once and ``approved_at`` keeps the original admin-approval instant.

    **Crash-recoverable (#1637).** The claim moves the buy to the transient
    ``finalizing`` status (NOT the final serving status) and commits BEFORE the
    adapter, so a crash between the commit and terminalization never leaves the buy
    seller-confirmed/serving with no remote order. Phase 2
    (:func:`_run_adapter_and_finalize`) runs the adapter idempotently, then publishes
    the flight-derived serving status AND terminalizes the step in ONE transaction;
    the scheduler's reconciliation pass re-drives any buy stranded in ``finalizing``.
    """
    repo = MediaBuyRepository(session, tenant_id)
    # PHASE 1 — single-winner CLAIM to the transient ``finalizing`` status + a fresh
    # phase-2 LEASE, committed BEFORE any external work. Bumps revision (the
    # approval's single token advance) + stamps approved_at/approved_by. The buy is
    # NOT yet seller-confirmed (finalizing is unconfirmed), so confirmed_at is
    # deferred to the serving transition in phase 2 (stamped from this approved_at). #1637.
    claim = repo.claim_finalizing(
        media_buy_id,
        expected_status=expected_status,
        lease_ttl_seconds=FINALIZE_LEASE_TTL_SECONDS,
        approved_at=approved_at,
        approved_by=approved_by,
    )
    if claim is None:
        # Lost the claim (another request decided this buy) or the buy vanished — do
        # NOTHING further, so no duplicate adapter order / notification. #1544.
        session.rollback()
        return FinalizeOutcome.NOT_CLAIMED, None
    session.commit()
    _, lease_id = claim

    return _run_adapter_and_finalize(
        session,
        tenant_id,
        media_buy_id=media_buy_id,
        step_id=step_id,
        step_data=step_data,
        compute_target=compute_target,
        run_adapter=run_adapter,
        lease_id=lease_id,
    )


def _remote_order_exists(repo: MediaBuyRepository, media_buy_id: str) -> bool:
    """True once the adapter has created the remote order for this buy.

    ``platform_order_id`` is persisted to every package's ``package_config`` AFTER
    ``adapter.create_media_buy`` returns (media_buy_create.py). Its presence is the
    durable idempotency anchor: a resume that sees it must NOT call the adapter
    again. #1637.
    """
    return any((pkg.package_config or {}).get("platform_order_id") for pkg in repo.get_packages(media_buy_id))


def _run_adapter_and_finalize(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    step_id: str | None,
    step_data: dict[str, Any] | None,
    compute_target: Callable[[MediaBuy], str | None],
    run_adapter: Callable[[], tuple[bool, str | None]],
    lease_id: str,
) -> tuple[FinalizeOutcome, str | None]:
    """Phase 2 of the crash-recoverable approval: OWNED adapter run + atomic publish.

    Precondition: the caller holds the buy's phase-2 lease (``lease_id``) while the
    buy is claimed in ``finalizing``. Shared by the initial finalizer and the
    scheduler's reconciler, so a mid-finalize crash is resumed WITHOUT a duplicate
    remote order and WITHOUT a stale worker clobbering a newer owner — every
    mutation below is a lease-CAS whose result is CHECKED (``None`` → rollback →
    ``NOT_CLAIMED``: never run the adapter further, mark failed, terminalize, or
    emit on lost ownership):

      1. ``platform_order_id`` guard — a prior attempt already got past the adapter;
         skip straight to publish.
      2. Otherwise CAS-commit the adapter-invoked marker (durable "remote mutations
         may exist" signal), then run the adapter:
         - ``AdapterIdempotencyUncertain`` (contract: NOTHING remote happened) →
           CAS-clear the marker + release the lease, return ``RETRYING`` — the buy
           stays ``finalizing`` on the AUTOMATIC recovery path.
         - handled failure ``(False, msg)`` → ``failed`` transition via lease-CAS
           (+ step failed with a buyer-facing envelope), commit, ``ADAPTER_FAILED``.
      3. Success publish: ``finalizing`` → flight-derived serving status via
         lease-CAS with ``bump=False`` (the claim already advanced the revision) and
         ``clear_finalize_state=True`` (lease/marker/recovery_mode cleared —
         including the self-heal of a ``manual_required`` flag set while this slow
         owner was still running). The step terminalization commits IN THE SAME
         transaction, so the serving status and the completion artifact become
         durable together; the webhook emits after commit (best-effort).

    ``step_id``/``step_data`` are ``None`` for the step-less creative-unblock path
    (no async buyer task): the serving transition is committed on its own. #1637.
    """
    repo = MediaBuyRepository(session, tenant_id)
    if not _remote_order_exists(repo, media_buy_id):
        # Durable marker BEFORE the adapter: a crash after this commit means remote
        # mutations may exist, so only full-replay adapters may auto-resume past it.
        if not repo.set_finalize_adapter_invoked(media_buy_id, lease_id):
            session.rollback()
            return FinalizeOutcome.NOT_CLAIMED, None
        session.commit()

        try:
            success, error_msg = run_adapter()
        except AdapterIdempotencyUncertain as exc:
            # Contract: no remote mutation happened. Return the buy to the clean
            # automatic-retry state (marker cleared, lease released) — the
            # reconciler re-attempts on its next pass.
            logger.warning(f"Adapter idempotency uncertain for media buy {media_buy_id}; will retry: {exc}")
            repo.clear_finalize_adapter_invoked(media_buy_id, lease_id)
            repo.release_finalize_lease(media_buy_id, lease_id)
            session.commit()
            return FinalizeOutcome.RETRYING, str(exc)
        if not success:
            failed = repo.update_status_computed(
                media_buy_id,
                lambda _mb: "failed",
                expected_status=MEDIA_BUY_FINALIZING_STATUS,
                expected_lease_id=lease_id,
                clear_finalize_state=True,
            )
            if failed is None:
                # Lost ownership while the adapter ran — a newer owner decides.
                session.rollback()
                return FinalizeOutcome.NOT_CLAIMED, error_msg
            if step_id is not None:
                # Store a buyer-facing two-layer error envelope as the step's response_data
                # (NOT just error_message): durable tasks/get rebuilds the failed Task's
                # artifact from response_data. #1544 (P1).
                error_envelope = build_two_layer_error_envelope(
                    AdCPAdapterError(error_msg or "Adapter execution failed while creating the media buy")
                )
                WorkflowRepository(session, tenant_id).update_status(
                    step_id, status="failed", error_message=error_msg, response_data=error_envelope
                )
            session.commit()
            return FinalizeOutcome.ADAPTER_FAILED, error_msg

    # Publish the serving status — OWNERSHIP-CHECKED (#1637): a stale worker whose
    # lease was taken over (or whose buy was already published/failed by the new
    # owner) gets None and must do NOTHING — no terminalize, no second webhook.
    published = repo.update_status_computed(
        media_buy_id,
        compute_target,
        expected_status=MEDIA_BUY_FINALIZING_STATUS,
        expected_lease_id=lease_id,
        clear_finalize_state=True,
        bump=False,
    )
    if published is None:
        session.rollback()
        return FinalizeOutcome.NOT_CLAIMED, None
    if step_id is not None and step_data is not None:
        # Commits the serving-status transition AND the terminal step artifact together.
        _terminalize_step_and_emit(
            session,
            tenant_id,
            media_buy=published,
            packages=repo.get_packages(media_buy_id),
            step_id=step_id,
            step_data=step_data,
            step_status="completed",
            task_status=AdcpTaskStatus.completed,
        )
    else:
        session.commit()
    return FinalizeOutcome.APPLIED, None


def resume_finalizing_media_buy(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    step_id: str | None,
    step_data: dict[str, Any] | None,
    run_adapter: Callable[[], tuple[bool, str | None]],
    adapter_supports_replay: Callable[[], bool],
) -> tuple[FinalizeOutcome, str | None]:
    """Re-drive a buy stranded in ``finalizing`` — the reconciler's single entry (#1637).

    Sequence:

      1. Disposition (fail-closed): if the adapter-invoked marker is set and this
         buy's adapter does NOT support full create replay, a crash may have left a
         partial remote graph (order without line items / creatives / approval —
         even a persisted ``platform_order_id`` doesn't prove completeness). Flag
         ``manual_required`` (locked CAS that respects a live lease and never steals
         it, so a slow-but-alive worker's eventual publish still self-heals), log
         ONCE, return ``RETRYING``. The reconciler scan excludes flagged buys — no
         hot loop.
      2. Otherwise acquire the phase-2 lease via CAS (absent/expired only) — the
         authoritative single-winner gate against concurrent reconcilers AND live
         workers. Failure → ``NOT_CLAIMED``, touch nothing.
      3. Run phase 2 with the new lease (marker absent ⇒ nothing remote happened ⇒
         safe for EVERY adapter; marker set ⇒ only replay-capable adapters reach
         here).
    """
    repo = MediaBuyRepository(session, tenant_id)
    stranded = repo.get_by_id(media_buy_id)
    if stranded is None or stranded.status != MEDIA_BUY_FINALIZING_STATUS or stranded.finalize_recovery_mode:
        return FinalizeOutcome.NOT_CLAIMED, None

    if stranded.finalize_adapter_invoked_at is not None and not adapter_supports_replay():
        if repo.set_finalize_recovery_manual(media_buy_id):
            session.commit()
            logger.error(
                f"Media buy {media_buy_id} (tenant {tenant_id}, step {step_id}) crashed mid-finalization "
                f"AFTER its adapter was invoked; the adapter does not support full create replay, so the "
                f"remote state may be partial. Marked finalize_recovery_mode=manual_required — reconcile "
                f"the remote order manually, then clear the flag (or re-approve) to resume."
            )
        else:
            session.rollback()
        return FinalizeOutcome.RETRYING, "manual reconciliation required"

    lease_id = repo.acquire_finalize_lease(media_buy_id, lease_ttl_seconds=FINALIZE_LEASE_TTL_SECONDS)
    if lease_id is None:
        # A live worker (unexpired lease) or a competing reconciler owns it.
        session.rollback()
        return FinalizeOutcome.NOT_CLAIMED, None
    session.commit()

    return _run_adapter_and_finalize(
        session,
        tenant_id,
        media_buy_id=media_buy_id,
        step_id=step_id,
        step_data=step_data,
        compute_target=_flight_derived_status,
        run_adapter=run_adapter,
        lease_id=lease_id,
    )


def _flight_derived_status(media_buy: MediaBuy) -> str:
    """Lifecycle status from the buy's flight window — the shared window→status decision.

    Evaluated by the finalizer AFTER the row lock, so it reads the committed window.
    """
    return lifecycle_status_for_window(datetime.datetime.now(datetime.UTC), *resolve_flight_window_utc(media_buy))


def claim_pending_creatives_hold(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    approved_by: str | None,
) -> bool:
    """Single-winner CLAIM of ``pending_approval`` → ``pending_creatives`` (the approval
    HOLD taken when creatives are not yet approved).

    Stamps the approval instant + bumps revision UNDER THE ROW LOCK, so a concurrent
    approve/reject that already decided the buy is not overwritten. Returns ``True`` if
    this request won the claim (committed), ``False`` if it lost (rolled back). Shared by
    the operations + workflow approve routes. #1544.
    """
    held = MediaBuyRepository(session, tenant_id).update_status_computed(
        media_buy_id,
        lambda _mb: "pending_creatives",
        approved_at=datetime.datetime.now(datetime.UTC),
        approved_by=approved_by,
        expected_status="pending_approval",
    )
    if held is None:
        session.rollback()
        return False
    session.commit()
    return True


def finalize_pending_media_buy_approval(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    step_id: str,
    step_data: dict[str, Any],
    approved_by: str | None,
) -> tuple[FinalizeOutcome, str | None]:
    """finalize_media_buy_approval with the standard manual-approval callbacks.

    The operations and workflow approve routes finalize a ``pending_approval`` buy
    identically — the single-winner claim on ``pending_approval``, the flight-derived
    status computed UNDER THE LOCK, the shared adapter execution, and the approval
    instant stamped now. Centralising those callbacks here keeps the two routes from
    duplicating them (they differ only in how they render the outcome). #1544.
    """
    from src.core.tools.media_buy_create import execute_approved_media_buy

    return finalize_media_buy_approval(
        session,
        tenant_id,
        media_buy_id=media_buy_id,
        step_id=step_id,
        step_data=step_data,
        compute_target=_flight_derived_status,
        run_adapter=lambda: execute_approved_media_buy(media_buy_id, tenant_id),
        expected_status="pending_approval",
        approved_by=approved_by,
        approved_at=datetime.datetime.now(datetime.UTC),
    )


def finalize_unblocked_media_buy(tenant_id: str, media_buy_id: str) -> tuple[FinalizeOutcome, str | None]:
    """Finalize ONE media buy whose last blocking creative was just approved.

    Called from the creative-approval route once all of a buy's creatives are
    approved. Owns its OWN DB session (the caller's creative UoW has already
    committed, and — unlike a plain route blueprint — ``creatives.py`` is a scanned
    business-logic module that must not open ``get_db_session`` itself), then routes
    through the shared approve finalizer: a single-winner claim on ``pending_creatives``
    (so concurrent creative-unblocks of the same buy run the adapter exactly once),
    then adapter → flight-derived status → terminalize step + emit. ``approved_at`` /
    ``approved_by`` are NOT re-stamped — ``confirmed_at`` was recorded at the earlier
    ``pending_creatives`` hold (write-once). Falls back to a claim + adapter +
    status-only transition when the buy has no workflow step (no async buyer task).
    Returns ``(outcome, error_message)``. #1544.
    """
    from src.core.database.database_session import get_db_session
    from src.core.tools.media_buy_create import execute_approved_media_buy

    with get_db_session() as session:
        wf_repo = WorkflowRepository(session, tenant_id)
        mapping = wf_repo.get_latest_mapping_for_object("media_buy", media_buy_id)
        step = wf_repo.get_by_step_id(mapping.step_id) if mapping else None
        if step is None:
            # No workflow step (no async buyer task to notify) — still single-winner
            # AND crash-recoverable: claim pending_creatives → finalizing + phase-2
            # lease BEFORE the adapter, commit, then run owned phase 2 (step-less).
            # #1544 / #1637.
            repo = MediaBuyRepository(session, tenant_id)
            claim = repo.claim_finalizing(
                media_buy_id,
                expected_status="pending_creatives",
                lease_ttl_seconds=FINALIZE_LEASE_TTL_SECONDS,
            )
            if claim is None:
                session.rollback()
                return FinalizeOutcome.NOT_CLAIMED, None
            session.commit()
            _, lease_id = claim
            return _run_adapter_and_finalize(
                session,
                tenant_id,
                media_buy_id=media_buy_id,
                step_id=None,
                step_data=None,
                compute_target=_flight_derived_status,
                run_adapter=lambda: execute_approved_media_buy(media_buy_id, tenant_id),
                lease_id=lease_id,
            )

        step_data = {
            "step_id": step.step_id,
            "context_id": step.context_id,
            "tool_name": step.tool_name,
            "request_data": step.request_data or {},
        }
        return finalize_media_buy_approval(
            session,
            tenant_id,
            media_buy_id=media_buy_id,
            step_id=step.step_id,
            step_data=step_data,
            compute_target=_flight_derived_status,
            run_adapter=lambda: execute_approved_media_buy(media_buy_id, tenant_id),
            expected_status="pending_creatives",
        )


def finalize_media_buy_rejection(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    step_id: str,
    step_data: dict[str, Any],
    reason: str,
    expected_status: str | tuple[str, ...] = ("pending_approval", "pending_creatives"),
) -> FinalizeOutcome:
    """Atomic, single-winner reject finalizer (operations + workflow reject routes).

    CLAIMS the decision under the row lock: transitions the buy to ``rejected`` ONLY if
    the committed status is in ``expected_status`` (revision bump via the repo seam), so
    a reject that lost to a concurrent approve (or vice-versa) is a ``NOT_CLAIMED`` no-op
    instead of overwriting the winner. Callers pass the OBSERVED buy status so a reject
    that raced an approve-HOLD (``pending_approval`` → ``pending_creatives``) and observed
    ``pending_approval`` loses the claim rather than also succeeding. On a won claim,
    stores the rejection artifact on the workflow step, commits, and emits the rejection
    webhook carrying ``rejection_reason`` (a pinned-beta.3 MUST). #1544.
    """
    repo = MediaBuyRepository(session, tenant_id)
    claimed = repo.update_status_computed(media_buy_id, lambda _mb: "rejected", expected_status=expected_status)
    if claimed is None:
        session.rollback()
        return FinalizeOutcome.NOT_CLAIMED
    _terminalize_step_and_emit(
        session,
        tenant_id,
        media_buy=claimed,
        packages=repo.get_packages(media_buy_id),
        step_id=step_id,
        step_data=step_data,
        step_status="rejected",
        task_status=AdcpTaskStatus.rejected,
        rejection_reason=reason,
    )
    return FinalizeOutcome.APPLIED
