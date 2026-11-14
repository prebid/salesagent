"""The scheduler-stream migration preserves historical rows or refuses atomically.

Raw SQL seeds the previous Alembic schema, including duplicates that the new
metadata deliberately cannot represent. Repository behavior is tested separately.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.migration_helpers import reset_to_revision, run_alembic_downgrade, run_alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "b74fd8c260ae"
_PREVIOUS = "e2af0c242a7f"
_INDEX = "uq_webhook_log_scheduler_sequence"


@pytest.fixture
def legacy(migration_db):
    engine, url = reset_to_revision(
        migration_db, revision=_PREVIOUS, tenant_id="sequence_tenant", tenant_name="Sequences", subdomain="sequences"
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (tenant_id, principal_id, name, platform_mappings, token_hash, token_prefix) VALUES ('sequence_tenant', 'buyer', 'Buyer', '{}', repeat('a', 64), 'seq_token')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, order_name, advertiser_name, start_date, end_date, status, raw_request) VALUES ('buy', 'sequence_tenant', 'buyer', 'Order', 'Brand', '2026-01-01', '2026-12-01', 'active', '{}')"
            )
        )
    yield engine, url
    # A refused downgrade deliberately leaves unresolved rows. Remove test-owned
    # rows before the shared helper resets the module's isolated migration DB.
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE webhook_delivery_log"))


def _log(engine, log_id, *, task_type="media_buy_delivery", sequence=1, status="success"):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO webhook_delivery_log (id, tenant_id, principal_id, media_buy_id, webhook_url, task_type, sequence_number, status) VALUES (:id, 'sequence_tenant', 'buyer', 'buy', 'https://buyer.example/report', :task, :sequence, :status)"
            ),
            {"id": log_id, "task": task_type, "sequence": sequence, "status": status},
        )


def _snapshot(engine):
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT to_jsonb(webhook_delivery_log) FROM webhook_delivery_log ORDER BY id"))
            .scalars()
            .all()
        )


def _revision(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_upgrade_rejects_duplicate_scheduler_sequences_without_deleting_history(legacy):
    engine, url = legacy
    _log(engine, "duplicate_a", status="failed")
    _log(engine, "duplicate_b")
    before = _snapshot(engine)
    with pytest.raises(RuntimeError) as failure:
        run_alembic_upgrade(url, _REVISION)
    assert all(value in str(failure.value) for value in ("sequence_tenant", "buy", "duplicate_a", "duplicate_b"))
    assert _snapshot(engine) == before
    assert _revision(engine) == _PREVIOUS
    assert _INDEX not in {index["name"] for index in inspect(engine).get_indexes("webhook_delivery_log")}


def test_unique_index_covers_scheduler_only_and_preserves_fanout(legacy):
    engine, url = legacy
    _log(engine, "fanout_a", task_type="delivery_report")
    _log(engine, "fanout_b", task_type="delivery_report")
    _log(engine, "scheduler")
    before = _snapshot(engine)
    run_alembic_upgrade(url, _REVISION)
    assert _snapshot(engine) == before
    with pytest.raises(IntegrityError):
        _log(engine, "scheduler_duplicate", status="failed")
    _log(engine, "fanout_c", task_type="delivery_report")
    assert len(_snapshot(engine)) == 4


@pytest.mark.parametrize("status", ["reserved", "retrying"])
def test_downgrade_refuses_unresolved_scheduler_reservations(legacy, status):
    engine, url = legacy
    run_alembic_upgrade(url, _REVISION)
    _log(engine, "unresolved", status=status)
    before = _snapshot(engine)
    with pytest.raises(RuntimeError) as failure:
        run_alembic_downgrade(url, _PREVIOUS)
    assert all(value in str(failure.value) for value in ("sequence_tenant", "buy", "unresolved", status))
    assert _snapshot(engine) == before
    assert _revision(engine) == _REVISION
    assert _INDEX in {index["name"] for index in inspect(engine).get_indexes("webhook_delivery_log")}


def test_downgrade_preserves_settled_rows_and_uncovered_sender_rows(legacy):
    engine, url = legacy
    run_alembic_upgrade(url, _REVISION)
    _log(engine, "settled")
    _log(engine, "legacy_retry", task_type="delivery_report", status="retrying")
    before = _snapshot(engine)
    run_alembic_downgrade(url, _PREVIOUS)
    assert _snapshot(engine) == before
    assert _INDEX not in {index["name"] for index in inspect(engine).get_indexes("webhook_delivery_log")}
    _log(engine, "duplicate_after_downgrade")
