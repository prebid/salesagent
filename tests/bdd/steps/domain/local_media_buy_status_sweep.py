"""Steps for the local media buy status sweep (local-media-buy-status-sweep.feature).

The sweep runs in this process. get_media_buys is the buyer-facing read, dispatched
through the scenario's transport. The trigger fires only for the poisoned row.
The e2e server's own sweep is held off by a long MEDIA_BUY_STATUS_CHECK_INTERVAL,
so it does not flip the other rows before this tick.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from pytest_bdd import given, parsers, then, when

if TYPE_CHECKING:
    from src.core.database.models import Principal, Tenant

from src.core.metrics import SCHEDULER_ERROR_TYPE_VALUES
from tests.bdd.steps._outcome_helpers import wire_entry
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.principal import plaintext_token_for
from tests.helpers.credentials import credential_headers
from tests.helpers.metrics import isolation_error_count
from tests.helpers.scheduler_isolation import (
    assert_no_tick_summary,
    assert_tick_not_stopped,
    assert_tick_summary,
    flip_lines,
    seed_status_buys,
)


@dataclass
class _PlannedBuy:
    tenant_key: str
    media_buy_id: str
    status: str


def _plan(ctx: dict, tenant_key: str, count: int, status: str) -> list[str]:
    """Choose ids for *count* buys. The rows are inserted after any trigger."""
    stamp = ctx.setdefault("sweep_stamp", uuid4().hex[:8])
    existing = ctx.setdefault("sweep_ids", {}).setdefault(tenant_key, [])
    ids: list[str] = []
    plans: list[_PlannedBuy] = ctx.setdefault("sweep_plans", [])
    for offset in range(count):
        media_buy_id = f"sweep-{stamp}-{tenant_key}-{len(existing) + offset}"
        plans.append(_PlannedBuy(tenant_key, media_buy_id, status))
        ids.append(media_buy_id)
    existing.extend(ids)
    return ids


@dataclass
class _Party:
    tenant_id: str
    token: str
    tenant: Tenant
    principal: Principal


def _party(ctx: dict, tenant_key: str) -> _Party:
    """The scenario's tenant *tenant_key* and its buyer, built once through the env's ``setup_party``."""
    parties = ctx.setdefault("sweep_parties", {})
    if tenant_key not in parties:
        stamp = ctx["sweep_stamp"]
        tenant_id = f"sweep_{tenant_key}_{stamp}"
        principal_id = f"buyer_{tenant_key}_{stamp}"
        tenant, principal = ctx["env"].setup_party(tenant_id, principal_id, ad_server="mock")
        parties[tenant_key] = _Party(tenant_id, plaintext_token_for(principal_id), tenant, principal)
    return parties[tenant_key]


def _flush(ctx: dict) -> None:
    """Insert every planned buy. A trigger installed before this call sees the writes.

    Inserted in reverse id order: the sweep must visit buys in ``media_buy_id`` order
    because it asks for it, and a table filled in that same order would hide the ask.
    """
    if ctx.get("sweep_flushed"):
        return
    now = datetime.now(UTC)
    past = now - timedelta(days=2)
    future = now + timedelta(days=7)
    for plan in sorted(ctx.get("sweep_plans", []), key=lambda plan: plan.media_buy_id, reverse=True):
        party = _party(ctx, plan.tenant_key)
        seed_status_buys(
            party.tenant,
            party.principal,
            [plan.media_buy_id],
            status=plan.status,
            start_time=past,
            end_time=future,
        )
    ctx["env"]._commit_factory_data()
    ctx["sweep_flushed"] = True


def _arm(ctx: dict, ids: list[str], install: Callable[[list[str]], None]) -> None:
    """Install a database fault on *ids* before their rows exist, then insert the rows and snapshot."""
    ctx.setdefault("sweep_poisoned", []).extend(ids)
    install(ids)
    _flush(ctx)
    _snapshot(ctx)


def _poison(ctx: dict, ids: list[str], *, terminate: bool) -> None:
    _arm(ctx, ids, partial(ctx["env"].poison_media_buy_status_writes, terminate=terminate))


def _snapshot(ctx: dict) -> None:
    counts = {}
    for tenant_key, party in ctx.get("sweep_parties", {}).items():
        counts[tenant_key] = {
            error_type: isolation_error_count(party.tenant_id, error_type) for error_type in SCHEDULER_ERROR_TYPE_VALUES
        }
    ctx["sweep_metric_before"] = counts


@given(parsers.parse('tenant "{tenant}" has three pending_start buys past their flight start'))
def given_three_past_pending(ctx: dict, tenant: str) -> None:
    _plan(ctx, tenant, 3, "pending_start")


@given(parsers.parse('tenant "{tenant}" has one pending_start buy past its flight start'))
def given_one_past_pending(ctx: dict, tenant: str) -> None:
    _plan(ctx, tenant, 1, "pending_start")


@given(parsers.parse('the database will fail the status write for tenant "{tenant}" buy {slot:d}'))
def given_fail_status_write(ctx: dict, tenant: str, slot: int) -> None:
    _poison(ctx, [ctx["sweep_ids"][tenant][slot]], terminate=False)


@given(
    parsers.parse('the database will drop the connection serving the status write for tenant "{tenant}" buy {slot:d}')
)
def given_drop_status_write(ctx: dict, tenant: str, slot: int) -> None:
    _poison(ctx, [ctx["sweep_ids"][tenant][slot]], terminate=True)


@given(parsers.parse('the database will refuse the sweep\'s commit for tenant "{tenant}"'))
def given_refuse_commit(ctx: dict, tenant: str) -> None:
    _arm(ctx, ctx["sweep_ids"][tenant], ctx["env"].refuse_media_buy_status_commit)


@given("three buys whose status writes all fail")
def given_all_writes_fail(ctx: dict) -> None:
    _poison(ctx, _plan(ctx, "A", 3, "pending_start"), terminate=False)


@given("four mid-flight buys and one failing buy")
def given_noops_and_one_failure(ctx: dict) -> None:
    _plan(ctx, "A", 4, "active")
    _poison(ctx, _plan(ctx, "A", 1, "pending_start"), terminate=False)


@given("one mid-flight buy with nothing to change")
def given_quiet_tick(ctx: dict) -> None:
    _plan(ctx, "A", 1, "active")


@when("the status sweep runs")
def when_status_sweep_runs(ctx: dict) -> None:
    _flush(ctx)
    if "sweep_metric_before" not in ctx:
        _snapshot(ctx)
    ctx["sweep_records"] = ctx["env"].run_status_sweep()


def _read_status(ctx: dict, tenant_key: str, media_buy_id: str) -> str:
    party = _party(ctx, tenant_key)
    dispatch_request(
        ctx,
        credential=credential_headers(token=party.token, tenant=party.tenant_id),
        media_buy_ids=[media_buy_id],
    )
    result = ctx["result"]
    assert result.is_success, f"get_media_buys failed for {media_buy_id}: {result.error or result.envelope}"
    return wire_entry(ctx, "media_buys", media_buy_id=media_buy_id)["status"]


@then(parsers.parse('tenant "{tenant}" buy {slot:d} reads "{status}" from get_media_buys'))
def then_one_buy_reads(ctx: dict, tenant: str, slot: int, status: str) -> None:
    media_buy_id = ctx["sweep_ids"][tenant][slot]
    assert _read_status(ctx, tenant, media_buy_id) == status


@then('the other tenant "A" buys and the tenant "B" buy read "active"')
def then_siblings_read_active(ctx: dict) -> None:
    poisoned = set(ctx.get("sweep_poisoned", []))
    checked = 0
    for tenant_key, ids in ctx["sweep_ids"].items():
        for media_buy_id in ids:
            if media_buy_id in poisoned:
                continue
            assert _read_status(ctx, tenant_key, media_buy_id) == "active", media_buy_id
            checked += 1
    assert checked >= 1, "the scenario has no sibling buy to grade"


@then('all three buys read "pending_start" from get_media_buys')
def then_all_three_still_pending(ctx: dict) -> None:
    for media_buy_id in ctx["sweep_ids"]["A"]:
        assert _read_status(ctx, "A", media_buy_id) == "pending_start", media_buy_id


def _moved(ctx: dict, tenant: str, error_type: str) -> float:
    """How far the tenant's isolation count for *error_type* moved since the snapshot."""
    tenant_id = ctx["sweep_parties"][tenant].tenant_id
    return isolation_error_count(tenant_id, error_type) - ctx["sweep_metric_before"][tenant][error_type]


@then(parsers.parse('the isolation error count for tenant "{tenant}" rose by {delta:d} with error_type "{error_type}"'))
def then_isolation_rose(ctx: dict, tenant: str, delta: int, error_type: str) -> None:
    assert _moved(ctx, tenant, error_type) == delta


@then(parsers.parse('the isolation error count for tenant "{tenant}" did not move'))
def then_isolation_unchanged(ctx: dict, tenant: str) -> None:
    for error_type in SCHEDULER_ERROR_TYPE_VALUES:
        assert _moved(ctx, tenant, error_type) == 0, error_type


@then("the sweep logged no status flip")
def then_no_flip_logged(ctx: dict) -> None:
    assert flip_lines(ctx["sweep_records"]) == []


@then("the sweep did not stop on an error")
def then_sweep_did_not_stop(ctx: dict) -> None:
    assert_tick_not_stopped(ctx["sweep_records"])


@then("the sweep summary is absent")
def then_summary_absent(ctx: dict) -> None:
    assert_no_tick_summary(ctx["sweep_records"])


@then(parsers.re(r'the sweep summary is the (?P<severity>WARNING|INFO) "(?P<text>[^"]+)"'))
def then_summary_is(ctx: dict, severity: str, text: str) -> None:
    assert_tick_summary(ctx["sweep_records"], severity, text)
