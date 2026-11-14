"""Scheduler-stream storage contracts; HTTP delivery belongs to #1638."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from src.core.database.models import MediaBuy, WebhookDeliveryLog
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.webhooks.delivery import WebhookDeliveryOutcome
from tests.factories import MediaBuyFactory, PrincipalFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def stream(integration_db, bound_factory_session):
    buy = MediaBuyFactory(raw_request={})
    identity = {
        "principal_id": buy.principal_id,
        "media_buy_id": buy.media_buy_id,
        "webhook_url": "https://buyer.example/report",
    }
    tenant = buy.tenant_id
    bound_factory_session.commit()
    return bound_factory_session, tenant, identity


def _read(session, log_id):
    session.expire_all()
    return session.get(WebhookDeliveryLog, log_id)


def _history(session, tenant, identity, *, sequence=1, status="success", task_type="media_buy_delivery"):
    return (
        DeliveryRepository(session, tenant)
        ._create_log(
            log_id=str(uuid4()),
            **identity,
            task_type=task_type,
            sequence_number=sequence,
            status=status,
        )
        .id
    )


def test_reservation_starts_at_one_and_is_owned_by_the_callers_transaction(stream):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    row = repo.reserve_next_sequence(**identity, notification_type="scheduled")
    log_id = row.id
    assert str(UUID(log_id)) == log_id
    assert inspect(row).persistent
    assert (row.sequence_number, row.status, row.attempt_count) == (1, "reserved", 0)
    assert (row.task_type, row.notification_type, row.completed_at) == ("media_buy_delivery", "scheduled", None)
    with Session(session.bind) as observer:
        assert observer.get(WebhookDeliveryLog, log_id) is None
    session.rollback()
    assert session.get(WebhookDeliveryLog, log_id) is None
    assert repo.reserve_next_sequence(**identity).sequence_number == 1


@pytest.mark.parametrize("status", ["success", "failed", "retrying", "refused", "reserved"])
def test_reservation_counts_every_status_but_only_the_scheduler_stream(stream, status):
    session, tenant, identity = stream
    _history(session, tenant, identity, sequence=4, status=status)
    _history(session, tenant, identity, sequence=99, task_type="delivery_report")
    session.commit()
    row = DeliveryRepository(session, tenant).reserve_next_sequence(**identity)
    assert row.sequence_number == 5


@pytest.mark.parametrize("same_tenant", [False, True])
def test_reservations_are_independent_for_other_buys_and_tenants(stream, same_tenant):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    first = repo.reserve_next_sequence(**identity)
    first_id = first.id
    session.commit()
    original = session.get(MediaBuy, identity["media_buy_id"])
    other = MediaBuyFactory(
        raw_request={}, **({"tenant": original.tenant, "principal": original.principal} if same_tenant else {})
    )
    second = DeliveryRepository(session, other.tenant_id).reserve_next_sequence(
        principal_id=other.principal_id,
        media_buy_id=other.media_buy_id,
        webhook_url=identity["webhook_url"],
    )
    assert second.sequence_number == 1
    assert second.id != first_id


def test_another_buy_does_not_wait_for_the_reserved_stream(stream):
    session, tenant, identity = stream
    original = session.get(MediaBuy, identity["media_buy_id"])
    other = MediaBuyFactory(raw_request={}, tenant=original.tenant, principal=original.principal)
    other_identity = {**identity, "media_buy_id": other.media_buy_id}
    session.commit()
    DeliveryRepository(session, tenant).reserve_next_sequence(**identity)

    def reserve_other():
        with Session(session.bind) as worker:
            worker.execute(text("SET LOCAL lock_timeout = '1s'"))
            row = DeliveryRepository(worker, tenant).reserve_next_sequence(**other_identity)
            sequence = row.sequence_number
            worker.commit()
            return sequence

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(reserve_other).result(timeout=5) == 1
    session.rollback()


@pytest.mark.parametrize("scope", ["tenant", "principal"])
def test_reservation_refuses_a_buy_outside_the_declared_owner_scope(stream, scope):
    session, tenant, identity = stream
    original = session.get(MediaBuy, identity["media_buy_id"])
    if scope == "tenant":
        other = MediaBuyFactory(raw_request={})
        wrong_identity = {**identity, "media_buy_id": other.media_buy_id}
    else:
        other = PrincipalFactory(tenant=original.tenant)
        wrong_identity = {**identity, "principal_id": other.principal_id}
    session.commit()
    with pytest.raises(ValueError):
        DeliveryRepository(session, tenant).reserve_next_sequence(**wrong_identity)
    session.rollback()
    assert session.scalars(select(WebhookDeliveryLog)).all() == []


def _contend_then_commit(session, operation):
    """Require a real lock wait before releasing the first transaction."""
    pids = Queue()

    def compete():
        with Session(session.bind) as worker:
            pids.put(worker.scalar(text("SELECT pg_backend_pid()")))
            result = operation(worker)
            worker.commit()
            return result

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(compete)
        try:
            pid = pids.get(timeout=5)
            with session.bind.connect() as observer:
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    waiting = observer.scalar(
                        text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid}
                    )
                    observer.commit()
                    if waiting == "Lock":
                        break
                    assert not pending.done(), "second operation finished before the first transaction committed"
                    sleep(0.01)
                assert waiting == "Lock", "second transaction did not overlap the first operation"
            session.commit()
            return pending.result(timeout=5)
        finally:
            session.rollback()


def test_overlapping_reservations_serialize_until_commit(stream):
    session, tenant, identity = stream
    first = DeliveryRepository(session, tenant).reserve_next_sequence(**identity)
    first_id = first.id

    def reserve(worker):
        row = DeliveryRepository(worker, tenant).reserve_next_sequence(**identity)
        return row.id, row.sequence_number

    second_id, sequence = _contend_then_commit(session, reserve)
    assert second_id != first_id
    assert sequence == 2


def test_acknowledgement_updates_one_reservation_with_cumulative_attempts(stream):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    row = repo.reserve_next_sequence(**identity)
    log_id = row.id
    session.commit()
    retry = datetime.now(UTC) + timedelta(minutes=5)
    repo.mark_delivery_result(
        log_id, outcome=WebhookDeliveryOutcome("exhausted", 2, 503, "unavailable"), next_retry_at=retry
    )
    session.commit()
    row = _read(session, log_id)
    assert (row.status, row.attempt_count, row.next_retry_at, row.completed_at) == ("retrying", 2, retry, None)
    repo.mark_delivery_result(
        log_id, outcome=WebhookDeliveryOutcome("delivered", 3, 200, payload_size_bytes=42), response_time_ms=7
    )
    session.commit()
    row = _read(session, log_id)
    assert (row.status, row.attempt_count, row.sequence_number) == ("success", 3, 1)
    assert (row.http_status_code, row.payload_size_bytes, row.response_time_ms) == (200, 42, 7)
    assert row.completed_at is not None
    assert row.next_retry_at is None and row.error_message is None
    assert len(session.scalars(select(WebhookDeliveryLog)).all()) == 1


def test_success_cannot_be_overwritten_through_a_stale_identity_map(stream):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    row = repo.reserve_next_sequence(**identity)
    log_id = row.id
    session.commit()
    assert row.status == "reserved"  # keep the stale row loaded in this session
    with Session(session.bind) as other:
        DeliveryRepository(other, tenant).mark_delivery_result(
            log_id, outcome=WebhookDeliveryOutcome("delivered", 2, 200)
        )
        other.commit()
    repo.mark_delivery_result(log_id, outcome=WebhookDeliveryOutcome("exhausted", 3, 503, "late failure"))
    session.commit()
    row = _read(session, log_id)
    assert (row.status, row.attempt_count, row.http_status_code, row.error_message) == ("success", 2, 200, None)


def test_lower_attempt_total_leaves_reservation_evidence_unchanged(stream):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    log_id = repo.reserve_next_sequence(**identity).id
    retry = datetime.now(UTC) + timedelta(minutes=5)
    repo.mark_delivery_result(log_id, outcome=WebhookDeliveryOutcome("exhausted", 3, 503), next_retry_at=retry)
    session.commit()
    repo.mark_delivery_result(log_id, outcome=WebhookDeliveryOutcome("exhausted", 2, 500))
    session.commit()
    row = _read(session, log_id)
    assert (row.status, row.attempt_count, row.http_status_code, row.next_retry_at) == ("retrying", 3, 503, retry)


def test_auth_refusal_preserves_a_fresh_reservation_without_inventing_a_status(stream):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    log_id = repo.reserve_next_sequence(**identity).id
    session.commit()
    repo.mark_delivery_result(log_id, outcome=WebhookDeliveryOutcome("refused_auth", 0))
    session.commit()
    row = _read(session, log_id)
    assert (row.id, row.sequence_number, row.status, row.attempt_count) == (log_id, 1, "reserved", 0)
    assert row.completed_at is None and row.next_retry_at is None and row.error_message is None


def test_concurrent_failure_acknowledgement_cannot_overwrite_success(stream):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    log_id = repo.reserve_next_sequence(**identity).id
    session.commit()
    repo.mark_delivery_result(log_id, outcome=WebhookDeliveryOutcome("delivered", 1, 200))

    def fail(worker):
        assert worker.get(WebhookDeliveryLog, log_id).status == "reserved"
        row = DeliveryRepository(worker, tenant).mark_delivery_result(
            log_id, outcome=WebhookDeliveryOutcome("exhausted", 2, 503, "late failure")
        )
        return row.status, row.attempt_count, row.http_status_code

    assert _contend_then_commit(session, fail) == ("success", 1, 200)
    row = _read(session, log_id)
    assert (row.status, row.attempt_count, row.http_status_code, row.error_message) == ("success", 1, 200, None)


@pytest.mark.parametrize("scope", ["missing", "tenant", "stream"])
def test_acknowledgement_cannot_create_or_modify_a_different_reservation(stream, scope):
    session, tenant, identity = stream
    log_id = _history(
        session, tenant, identity, task_type="delivery_report" if scope == "stream" else "media_buy_delivery"
    )
    session.commit()
    repo = DeliveryRepository(session, "another-tenant" if scope == "tenant" else tenant)
    with pytest.raises(ValueError):
        repo.mark_delivery_result(
            str(uuid4()) if scope == "missing" else log_id, outcome=WebhookDeliveryOutcome("delivered", 2, 200)
        )
    session.rollback()
    assert len(session.scalars(select(WebhookDeliveryLog)).all()) == 1
    assert _read(session, log_id).attempt_count == 1


def test_acknowledgement_is_rolled_back_by_the_caller(stream):
    session, tenant, identity = stream
    repo = DeliveryRepository(session, tenant)
    log_id = repo.reserve_next_sequence(**identity).id
    session.commit()
    repo.mark_delivery_result(log_id, outcome=WebhookDeliveryOutcome("delivered", 1, 200))
    session.rollback()
    assert _read(session, log_id).status == "reserved"
