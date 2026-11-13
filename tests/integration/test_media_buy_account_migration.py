"""Account recovery requires same-buy ownership and historical mode evidence.

These fixtures use SQL because the migration's input includes legacy NULL account
rows, which the resulting ORM contract deliberately cannot persist.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_account,
    seed_principal,
    seed_tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "5b0563cb0ac6"
_PREVIOUS = "e2af0c242a7f"
_TENANT = "account_migration"
_PRINCIPAL = "buyer"


@pytest.fixture
def legacy(migration_db):
    engine, url = reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Account migration",
        subdomain="account-migration",
    )
    seed_principal(engine, tenant_id=_TENANT, principal_id=_PRINCIPAL)
    seed_account(
        engine,
        tenant_id=_TENANT,
        account_id="acc_live",
        name="Live",
        domain="brand.example",
        operator="agent.example",
        sandbox=None,
        created_at="2026-01-01",
    )
    seed_account(
        engine,
        tenant_id=_TENANT,
        account_id="acc_sandbox",
        name="Sandbox",
        domain="brand.example",
        operator="agent.example",
        sandbox=True,
        created_at="2026-01-01",
    )
    return engine, url


def _buy(engine, buy_id="buy", *, account=None, request=None, key=None):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, account_id, "
                "order_name, advertiser_name, start_date, end_date, status, raw_request, "
                "created_at, idempotency_key) VALUES (:buy, :tenant, :principal, :account, "
                "'Order', 'Brand', '2026-02-01', '2026-12-01', 'pending_approval', "
                "CAST(:request AS jsonb), '2026-02-01', :key)"
            ),
            {
                "buy": buy_id,
                "tenant": _TENANT,
                "principal": _PRINCIPAL,
                "account": account,
                "request": json.dumps(request or {}),
                "key": key,
            },
        )


def _workflow(
    engine,
    buy_id="buy",
    *,
    request=None,
    response=None,
    principal=_PRINCIPAL,
    tenant=_TENANT,
    tool="create_media_buy",
    action="create",
    step_type="media_buy_creation",
):
    step = f"step_{buy_id}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO contexts (context_id, tenant_id, principal_id, conversation_history) "
                "VALUES (:step, :tenant, :principal, '[]')"
            ),
            {"step": step, "tenant": tenant, "principal": principal},
        )
        conn.execute(
            text(
                "INSERT INTO workflow_steps (step_id, context_id, step_type, tool_name, request_data, "
                "response_data, status, owner) VALUES (:step, :step, :kind, :tool, "
                "CAST(:request AS jsonb), CAST(:response AS jsonb), 'completed', 'system')"
            ),
            {
                "step": step,
                "kind": step_type,
                "tool": tool,
                "request": json.dumps(request) if request is not None else None,
                "response": json.dumps(response) if response is not None else None,
            },
        )
        conn.execute(
            text(
                "INSERT INTO object_workflow_mapping (object_type, object_id, step_id, action) "
                "VALUES ('media_buy', :buy, :step, :action)"
            ),
            {"buy": buy_id, "step": step, "action": action},
        )
    return step


def _cache(
    engine,
    buy_id="buy",
    *,
    account="acc_sandbox",
    response=None,
    key="key",
    principal=_PRINCIPAL,
    tenant=_TENANT,
    tool="create_media_buy",
):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO idempotency_attempts (attempt_id, tenant_id, principal_id, account_id, "
                "tool_name, idempotency_key, response_envelope, expires_at) "
                "VALUES (:key, :tenant, :principal, :account, :tool, :key, "
                "CAST(:envelope AS jsonb), '2026-02-02')"
            ),
            {
                "tenant": tenant,
                "principal": principal,
                "account": account,
                "tool": tool,
                "key": key,
                "envelope": json.dumps(
                    {
                        "status": "submitted" if response and "task_id" in response else "completed",
                        "response": response if response is not None else {"media_buy_id": buy_id, "sandbox": True},
                    }
                ),
            },
        )


def _accounts(engine):
    with engine.connect() as conn:
        return dict(conn.execute(text("SELECT media_buy_id, account_id FROM media_buys ORDER BY media_buy_id")).all())


def _ref(sandbox):
    # Valid in both the historical AdCP 3.1.1 and current 3.2.7 account-ref schema.
    return {"brand": {"domain": "brand.example"}, "operator": "agent.example", "sandbox": sandbox}


@pytest.mark.parametrize("source", ["raw", "workflow", "cache", "submitted"])
def test_exact_same_buy_evidence_recovers_sandbox(legacy, source):
    engine, url = legacy
    _buy(
        engine,
        request={"account": {"account_id": "acc_sandbox"}}
        if source == "raw"
        else {"account": _ref(True)}
        if source in {"workflow", "submitted"}
        else {},
    )
    if source in {"raw", "workflow"}:
        _workflow(
            engine,
            request={"account": {"account_id": "acc_sandbox"}} if source == "workflow" else {"account": _ref(True)},
        )
    elif source == "submitted":
        step = _workflow(engine, request={"account": _ref(True)})
        _cache(engine, response={"task_id": step})
    else:
        _cache(engine)
    run_alembic_upgrade(url, _REVISION)
    assert _accounts(engine) == {"buy": "acc_sandbox"}


@pytest.mark.parametrize("mode,account", [(False, "acc_live"), (True, "acc_sandbox")])
def test_natural_key_mode_requires_independent_resolved_id(legacy, mode, account):
    engine, url = legacy
    _buy(engine, request={"account": _ref(mode)})
    _cache(engine, account=account, response={"media_buy_id": "buy"})
    run_alembic_upgrade(url, _REVISION)
    assert _accounts(engine) == {"buy": account}


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({}, "account_identity_unproven"),
        ({"account": {"account_id": "acc_sandbox"}}, "historical_mode_unproven"),
        ({"account": _ref(True)}, "account_identity_unproven"),
    ],
)
def test_unproven_ownership_or_mode_refuses_without_guessing(legacy, payload, reason):
    engine, url = legacy
    _buy(engine, request=payload)
    # Even a single current grant cannot establish the original account/mode.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agent_account_access (tenant_id, principal_id, account_id) "
                "VALUES (:tenant, :principal, 'acc_sandbox')"
            ),
            {"tenant": _TENANT, "principal": _PRINCIPAL},
        )
    with pytest.raises(RuntimeError, match=reason) as failure:
        run_alembic_upgrade(url, _REVISION)
    assert all(value in str(failure.value) for value in (_TENANT, _PRINCIPAL, "buy"))
    assert _accounts(engine) == {"buy": None}


@pytest.mark.parametrize("mode", [None, "false", 0])
def test_unknown_or_malformed_natural_mode_is_not_live_evidence(legacy, mode):
    engine, url = legacy
    _buy(engine, request={"account": _ref(mode)})
    _cache(engine, account="acc_live", response={"media_buy_id": "buy"})
    with pytest.raises(RuntimeError, match="historical_mode_unproven"):
        run_alembic_upgrade(url, _REVISION)
    assert _accounts(engine) == {"buy": None}


@pytest.mark.parametrize("change", ["other_buy", "other_tool", "unmapped_task"])
def test_unrelated_cache_cannot_supply_ownership(legacy, change):
    engine, url = legacy
    _buy(engine, key="key", request={"account": _ref(True)})
    _cache(
        engine,
        response={"media_buy_id": "other", "sandbox": True}
        if change == "other_buy"
        else {"task_id": "unmapped", "sandbox": True}
        if change == "unmapped_task"
        else None,
        tool="update_media_buy" if change == "other_tool" else "create_media_buy",
    )
    with pytest.raises(RuntimeError, match="account_identity_unproven"):
        run_alembic_upgrade(url, _REVISION)
    assert _accounts(engine) == {"buy": None}


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("account", "account_evidence_conflict"),
        ("mode", "mode_evidence_conflict"),
        ("missing_account", "account_missing"),
        ("natural_key", "natural_key_mismatch"),
    ],
)
def test_conflicting_evidence_aborts_entire_upgrade(legacy, kind, reason):
    engine, url = legacy
    _buy(engine, "safe")
    _cache(engine, "safe", key="safe_key")
    request = {
        "account": {
            "account_id": "acc_live" if kind == "account" else "missing" if kind == "missing_account" else "acc_sandbox"
        }
    }
    if kind in {"mode", "natural_key"}:
        request = {"account": _ref(False if kind == "mode" else True)}
        if kind == "natural_key":
            request["account"]["operator"] = "wrong.example"
    _buy(engine, "unsafe", request=request)
    _cache(engine, "unsafe", account="missing" if kind == "missing_account" else "acc_sandbox")
    with pytest.raises(RuntimeError, match=reason) as failure:
        run_alembic_upgrade(url, _REVISION)
    assert "unsafe" in str(failure.value)
    assert _accounts(engine) == {"safe": None, "unsafe": None}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _PREVIOUS


def test_projected_idempotency_collision_names_both_buys_and_rolls_back(legacy):
    engine, url = legacy
    _buy(engine, "old", account="acc_sandbox", key="shared")
    _buy(engine, "recover", key="shared")
    _cache(engine, "recover", key="shared")
    with pytest.raises(
        RuntimeError, match="Account recovery would collide with idx_media_buys_idempotency_key"
    ) as failure:
        run_alembic_upgrade(url, _REVISION)
    assert all(value in str(failure.value) for value in ("idx_media_buys_idempotency_key", "old", "recover"))
    assert _accounts(engine) == {"old": "acc_sandbox", "recover": None}


def test_constraint_and_downgrade_preserve_recovered_account(legacy):
    engine, url = legacy
    _buy(engine)
    _cache(engine)
    run_alembic_upgrade(url, _REVISION)
    fk = next(fk for fk in inspect(engine).get_foreign_keys("media_buys") if fk["name"] == "fk_media_buys_account")
    assert fk["options"]["ondelete"] == "RESTRICT"
    with pytest.raises(IntegrityError):
        _buy(engine, "new_null")
    with pytest.raises(IntegrityError) as failure, engine.begin() as conn:
        conn.execute(text("DELETE FROM accounts WHERE account_id='acc_sandbox'"))
    assert failure.value.orig.pgcode == "23503"
    run_alembic_downgrade(url, _PREVIOUS)
    assert _accounts(engine) == {"buy": "acc_sandbox"}
    columns = {column["name"]: column for column in inspect(engine).get_columns("media_buys")}
    assert columns["account_id"]["nullable"] is True
    fk = next(fk for fk in inspect(engine).get_foreign_keys("media_buys") if fk["name"] == "fk_media_buys_account")
    assert fk["options"]["ondelete"] == "SET NULL"
    _buy(engine, "new_null")
    assert _accounts(engine) == {"buy": "acc_sandbox", "new_null": None}


def test_migration_does_not_change_the_existing_tenant_delete_constraint(legacy):
    engine, url = legacy
    _buy(engine, account="acc_sandbox")
    run_alembic_upgrade(url, _REVISION)
    # The shipped migration graph has NO ACTION here, although metadata declares
    # CASCADE. Repairing that pre-existing discrepancy is a separate change.
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("DELETE FROM tenants WHERE tenant_id=:tenant"), {"tenant": _TENANT})
    assert _accounts(engine) == {"buy": "acc_sandbox"}


def test_metadata_tenant_cascade_still_removes_its_accounts_and_buys(integration_db, bound_factory_session):
    from tests.factories import MediaBuyFactory

    # Isolate the account FK: package cleanup has its own non-cascading FK.
    buy = MediaBuyFactory(raw_request={})
    tenant_id = buy.tenant_id
    session = bound_factory_session
    session.execute(text("DELETE FROM tenants WHERE tenant_id=:tenant"), {"tenant": tenant_id})
    session.commit()
    assert (
        session.execute(
            text("SELECT count(*) FROM media_buys WHERE tenant_id=:tenant"), {"tenant": tenant_id}
        ).scalar_one()
        == 0
    )
    assert (
        session.execute(
            text("SELECT count(*) FROM accounts WHERE tenant_id=:tenant"), {"tenant": tenant_id}
        ).scalar_one()
        == 0
    )


@pytest.mark.parametrize("source", ["workflow", "cache"])
@pytest.mark.parametrize("foreign_tenant", [False, True])
def test_evidence_is_scoped_to_the_buy_tenant_and_principal(legacy, source, foreign_tenant):
    engine, url = legacy
    other_tenant = "other_tenant" if foreign_tenant else _TENANT
    other_principal = _PRINCIPAL if foreign_tenant else "other_principal"
    if foreign_tenant:
        with engine.begin() as conn:
            seed_tenant(conn, tenant_id=other_tenant, tenant_name="Other", subdomain="other")
    seed_principal(engine, tenant_id=other_tenant, principal_id=other_principal, name="Other")
    _buy(engine, request={"account": _ref(True)})
    if source == "workflow":
        _workflow(
            engine, tenant=other_tenant, principal=other_principal, request={"account": {"account_id": "acc_sandbox"}}
        )
    else:
        _cache(engine, tenant=other_tenant, principal=other_principal)
    with pytest.raises(RuntimeError, match="account_identity_unproven"):
        run_alembic_upgrade(url, _REVISION)
    assert _accounts(engine) == {"buy": None}


@pytest.mark.parametrize("override", [{"tool": "update_media_buy"}, {"action": "update"}, {"step_type": "approval"}])
def test_non_creation_workflow_cannot_supply_ownership(legacy, override):
    engine, url = legacy
    _buy(engine, request={"account": _ref(True)})
    _workflow(engine, request={"account": {"account_id": "acc_sandbox"}}, **override)
    with pytest.raises(RuntimeError, match="account_identity_unproven"):
        run_alembic_upgrade(url, _REVISION)


def test_submitted_task_mapped_to_multiple_buys_is_ambiguous(legacy):
    engine, url = legacy
    _buy(engine, request={"account": _ref(True)})
    step = _workflow(engine, request={"account": _ref(True)})
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO object_workflow_mapping (object_type, object_id, step_id, action) "
                "VALUES ('media_buy', 'other', :step, 'create')"
            ),
            {"step": step},
        )
    _cache(engine, response={"task_id": step})
    with pytest.raises(RuntimeError, match="account_identity_unproven"):
        run_alembic_upgrade(url, _REVISION)


def test_existing_account_rows_are_not_rewritten(legacy):
    engine, url = legacy
    _buy(engine, account="acc_live")
    with engine.connect() as conn:
        before = conn.execute(text("SELECT xmin::text, to_jsonb(media_buys) FROM media_buys")).one()
    run_alembic_upgrade(url, _REVISION)
    with engine.connect() as conn:
        after = conn.execute(text("SELECT xmin::text, to_jsonb(media_buys) FROM media_buys")).one()
    assert after == before


def test_workflow_account_disagreement_is_not_hidden_by_cache(legacy):
    engine, url = legacy
    _buy(engine)
    _workflow(engine, request={"account": {"account_id": "acc_live"}})
    _cache(engine)
    with pytest.raises(RuntimeError) as failure:
        run_alembic_upgrade(url, _REVISION)
    assert "account_evidence_conflict" in str(failure.value)
    assert _accounts(engine) == {"buy": None}


@pytest.mark.parametrize("arm", ["submitted_with_buy_id", "submitted_mode_only", "completed_with_task_id"])
def test_cache_response_arm_must_match_its_persisted_status(legacy, arm):
    engine, url = legacy
    _buy(engine, "mb_legacy_1", request={"account": _ref(True)})
    step = _workflow(engine, "mb_legacy_1")
    _cache(
        engine,
        "mb_legacy_1",
        response={"media_buy_id": "mb_legacy_1", "sandbox": True}
        if arm == "submitted_with_buy_id"
        else {"task_id": step, "sandbox": True},
    )
    if arm != "submitted_mode_only":
        status = "submitted" if arm == "submitted_with_buy_id" else "completed"
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE idempotency_attempts SET response_envelope = "
                    "jsonb_set(response_envelope, '{status}', to_jsonb(CAST(:status AS text)))"
                ),
                {"status": status},
            )
    with pytest.raises(RuntimeError, match="conflicting_create_response") as failure:
        run_alembic_upgrade(url, _REVISION)
    assert "mb_legacy_1" in str(failure.value)
    assert _accounts(engine) == {"mb_legacy_1": None}


@pytest.mark.parametrize("submitted", [False, True])
def test_successful_cache_advisories_do_not_erase_account_evidence(legacy, submitted):
    from src.core.schemas import CreateMediaBuySubmitted
    from tests.helpers.idempotency_seeds import make_active_cached_success

    engine, url = legacy
    _buy(engine, request={"account": _ref(True)} if submitted else {})
    response = make_active_cached_success("buy")
    if submitted:
        step = _workflow(engine, request={"account": _ref(True)})
        response = CreateMediaBuySubmitted(task_id=step, errors=response.errors)
    else:
        response.sandbox = True
    _cache(engine, response=response.model_dump(mode="json"))

    run_alembic_upgrade(url, _REVISION)

    assert _accounts(engine) == {"buy": "acc_sandbox"}


def test_missing_historical_timestamp_reports_the_buy_and_preserves_all_rows(legacy):
    engine, url = legacy
    columns = {column["name"]: column for column in inspect(engine).get_columns("media_buys")}
    assert columns["created_at"]["nullable"] is True
    _buy(engine, "safe")
    _cache(engine, "safe", key="safe_key")
    _buy(engine)
    _cache(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE media_buys SET created_at=NULL WHERE media_buy_id='buy'"))

    with pytest.raises(RuntimeError) as failure:
        run_alembic_upgrade(url, _REVISION)

    assert all(value in str(failure.value) for value in (_TENANT, _PRINCIPAL, "buy", "missing_historical_timestamp"))
    assert _accounts(engine) == {"buy": None, "safe": None}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT created_at FROM media_buys WHERE media_buy_id='buy'")).scalar_one() is None
