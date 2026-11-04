"""Steps for the local media buy status sweep (local-media-buy-status-sweep.feature).

The sweep runs in this process. get_media_buys is the buyer-facing read, dispatched
through the scenario's transport. The database fault is a trigger installed before
the rows exist, so a live scheduler cannot flip them first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pytest_bdd import given, parsers, then, when

from src.services.media_buy_status_scheduler import STATUS_BATCH_SUMMARY_PREFIX
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.principal import plaintext_token_for
from tests.helpers.credentials import credential_headers
from tests.helpers.metrics import samples_with

_PAST = "past_pending"
_MID = "mid_flight"


@dataclass
class _PlannedBuy:
    tenant_key: str
    media_buy_id: str
    kind: str


def _plan(ctx: dict, tenant_key: str, count: int, kind: str) -> list[str]:
    """Choose ids for *count* buys. The rows are inserted after any trigger."""
    stamp = ctx.setdefault("sweep_stamp", uuid4().hex[:8])
    existing = ctx.setdefault("sweep_ids", {}).setdefault(tenant_key, [])
    ids: list[str] = []
    plans: list[_PlannedBuy] = ctx.setdefault("sweep_plans", [])
    for offset in range(count):
        media_buy_id = f"sweep-{stamp}-{tenant_key}-{len(existing) + offset}"
        plans.append(_PlannedBuy(tenant_key, media_buy_id, kind))
        ids.append(media_buy_id)
    existing.extend(ids)
    return ids


def _party(ctx: dict, tenant_key: str) -> dict[str, str]:
    parties = ctx.setdefault("sweep_parties", {})
    if tenant_key in parties:
        return parties[tenant_key]
    from tests.factories import PrincipalFactory, TenantFactory

    stamp = ctx["sweep_stamp"]
    tenant_id = f"sweep_{tenant_key}_{stamp}"
    principal_id = f"buyer_{tenant_key}_{stamp}"
    tenant = TenantFactory(tenant_id=tenant_id, ad_server="mock")
    principal = PrincipalFactory(tenant=tenant, principal_id=principal_id)
    parties[tenant_key] = {
        "tenant_id": tenant.tenant_id,
        "token": plaintext_token_for(principal.principal_id),
        "tenant": tenant,
        "principal": principal,
    }
    return parties[tenant_key]


def _flush(ctx: dict) -> None:
    """Insert every planned buy. A trigger installed before this call sees the writes."""
    if ctx.get("sweep_flushed"):
        return
    from tests.factories import MediaBuyFactory

    now = datetime.now(UTC)
    past = now - timedelta(days=2)
    future = now + timedelta(days=7)
    for plan in ctx.get("sweep_plans", []):
        party = _party(ctx, plan.tenant_key)
        if plan.kind == _PAST:
            MediaBuyFactory(
                tenant=party["tenant"],
                principal=party["principal"],
                media_buy_id=plan.media_buy_id,
                status="pending_start",
                start_time=past,
                end_time=future,
                start_date=past.date(),
                end_date=future.date(),
            )
        else:
            MediaBuyFactory(
                tenant=party["tenant"],
                principal=party["principal"],
                media_buy_id=plan.media_buy_id,
                status="active",
                start_time=past,
                end_time=future,
                start_date=past.date(),
                end_date=future.date(),
            )
    ctx["env"]._commit_factory_data()
    ctx["sweep_flushed"] = True


def _isolation_count(tenant_id: str, error_type: str) -> float:
    """The isolation counter for one tenant and error type."""
    return sum(
        samples_with(
            "scheduler_isolation_errors_total",
            scheduler="media_buy_status",
            tenant_id=tenant_id,
            error_type=error_type,
        ).values()
    )


def _poison(ctx: dict, ids: list[str], *, terminate: bool) -> None:
    ctx.setdefault("sweep_poisoned", []).extend(ids)
    ctx["env"].poison_media_buy_status_writes(ids, terminate=terminate)
    _flush(ctx)
    _snapshot(ctx)


def _snapshot(ctx: dict) -> None:
    counts = {}
    for tenant_key, party in ctx.get("sweep_parties", {}).items():
        counts[tenant_key] = {
            "db_error": _isolation_count(party["tenant_id"], "db_error"),
            "other": _isolation_count(party["tenant_id"], "other"),
        }
    ctx["sweep_metric_before"] = counts


@given(parsers.parse('tenant "{tenant}" has three pending_start buys past their flight start'))
def given_three_past_pending(ctx: dict, tenant: str) -> None:
    _plan(ctx, tenant, 3, _PAST)


@given(parsers.parse('tenant "{tenant}" has one pending_start buy past its flight start'))
def given_one_past_pending(ctx: dict, tenant: str) -> None:
    _plan(ctx, tenant, 1, _PAST)


@given(parsers.parse('the database will fail the status write for tenant "{tenant}" buy {slot:d}'))
def given_fail_status_write(ctx: dict, tenant: str, slot: int) -> None:
    _poison(ctx, [ctx["sweep_ids"][tenant][slot]], terminate=False)


@given(
    parsers.parse('the database will drop the connection serving the status write for tenant "{tenant}" buy {slot:d}')
)
def given_drop_status_write(ctx: dict, tenant: str, slot: int) -> None:
    _poison(ctx, [ctx["sweep_ids"][tenant][slot]], terminate=True)


@given("three buys whose status writes all fail")
def given_all_writes_fail(ctx: dict) -> None:
    _poison(ctx, _plan(ctx, "A", 3, _PAST), terminate=False)


@given("four mid-flight buys and one failing buy")
def given_noops_and_one_failure(ctx: dict) -> None:
    _plan(ctx, "A", 4, _MID)
    _poison(ctx, _plan(ctx, "A", 1, _PAST), terminate=False)


@given("one mid-flight buy with nothing to change")
def given_quiet_tick(ctx: dict) -> None:
    _plan(ctx, "A", 1, _MID)


@when("the status sweep runs")
def when_status_sweep_runs(ctx: dict) -> None:
    _flush(ctx)
    if "sweep_metric_before" not in ctx:
        _snapshot(ctx)
    ctx["sweep_records"] = ctx["env"].run_status_sweep()


def _read_status(ctx: dict, tenant_key: str, media_buy_id: str) -> str:
    party = ctx["sweep_parties"][tenant_key]
    dispatch_request(
        ctx,
        credential=credential_headers(token=party["token"], tenant=party["tenant_id"]),
        media_buy_ids=[media_buy_id],
    )
    result = ctx["result"]
    assert result.is_success, f"get_media_buys failed for {media_buy_id}: {result.error or result.envelope}"
    buys = result.payload.media_buys or []
    matched = [buy for buy in buys if buy.media_buy_id == media_buy_id]
    assert len(matched) == 1, f"get_media_buys returned {[b.media_buy_id for b in buys]} for {media_buy_id}"
    status = matched[0].status
    return status.value if hasattr(status, "value") else str(status)


@when("the buyer reads the swept buys")
def when_buyer_reads_swept_buys(ctx: dict) -> None:
    statuses: dict[str, str] = {}
    for tenant_key, ids in ctx["sweep_ids"].items():
        for media_buy_id in ids:
            statuses[media_buy_id] = _read_status(ctx, tenant_key, media_buy_id)
    ctx["sweep_statuses"] = statuses


@then(parsers.parse('tenant "{tenant}" buy {slot:d} reads "{status}" from get_media_buys'))
def then_one_buy_reads(ctx: dict, tenant: str, slot: int, status: str) -> None:
    media_buy_id = ctx["sweep_ids"][tenant][slot]
    assert ctx["sweep_statuses"][media_buy_id] == status


@then('the other tenant "A" buys and the tenant "B" buy read "active"')
def then_siblings_read_active(ctx: dict) -> None:
    poisoned = set(ctx.get("sweep_poisoned", []))
    checked = 0
    for ids in ctx["sweep_ids"].values():
        for media_buy_id in ids:
            if media_buy_id in poisoned:
                continue
            assert ctx["sweep_statuses"][media_buy_id] == "active", media_buy_id
            checked += 1
    assert checked >= 1, "the scenario has no sibling buy to grade"


@then('all three buys read "pending_start" from get_media_buys')
def then_all_three_still_pending(ctx: dict) -> None:
    for media_buy_id in ctx["sweep_ids"]["A"]:
        assert ctx["sweep_statuses"][media_buy_id] == "pending_start", media_buy_id


@then(parsers.parse('the isolation error count for tenant "{tenant}" rose by {delta:d} with error_type "{error_type}"'))
def then_isolation_rose(ctx: dict, tenant: str, delta: int, error_type: str) -> None:
    tenant_id = ctx["sweep_parties"][tenant]["tenant_id"]
    before = ctx["sweep_metric_before"][tenant][error_type]
    assert _isolation_count(tenant_id, error_type) == before + delta


@then(parsers.parse('the isolation error count for tenant "{tenant}" did not move'))
def then_isolation_unchanged(ctx: dict, tenant: str) -> None:
    tenant_id = ctx["sweep_parties"][tenant]["tenant_id"]
    before = ctx["sweep_metric_before"].get(tenant, {})
    for error_type in ("db_error", "other"):
        assert _isolation_count(tenant_id, error_type) == before.get(error_type, 0.0)


def _summary_lines(ctx: dict) -> list[tuple[str, str]]:
    lines = []
    for record in ctx["sweep_records"]:
        message = record.getMessage()
        if STATUS_BATCH_SUMMARY_PREFIX in message:
            lines.append((record.levelname, message))
    return lines


@then("the sweep summary is absent")
def then_summary_absent(ctx: dict) -> None:
    assert _summary_lines(ctx) == []


@then(parsers.re(r'the sweep summary is the (?P<severity>WARNING|INFO) "(?P<text>[^"]+)"'))
def then_summary_is(ctx: dict, severity: str, text: str) -> None:
    expected = f"{STATUS_BATCH_SUMMARY_PREFIX}: {text}"
    assert _summary_lines(ctx) == [(severity, expected)]
