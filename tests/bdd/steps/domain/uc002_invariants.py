"""UC-002 create_media_buy step definitions for the BR-RULE invariant scenarios.

Every step here rides the shared create When (``when_send_create_media_buy``), so what a
Then reads came out of one dispatch through the parametrized transport. Three kinds of
evidence, and nothing else:

- the WIRE the buyer received (``wire_field`` / ``wire_dict`` / ``assert_wire_error``);
- the seller's RECORD, through the repositories inside a ``MediaBuyUoW`` -- the database the
  live e2e server writes is the one this process reads, so the read answers on all four
  transports;
- the AD SERVER's receipt, through ``MediaBuyCreateEnv.adapter_create_calls``, which reads
  the mock in process and the ``MockAdServer`` audit row over e2e_rest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import (
    WIRE_MISSING,
    parked_media_buy,
    wire_dict,
    wire_error_envelope_or_none,
    wire_field,
    wire_lookup,
)
from tests.bdd.steps.domain.uc002_create_media_buy import _first_package, given_request_with_account_id
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.bdd.steps.generic.given_media_buy import (
    _add_creative_ids_to_package,
    _create_approved_creative,
    _ensure_request_defaults,
    given_creative_format_mismatch,
)

#: How far a resolved ASAP start may sit from the moment the seller recorded the buy.
_ASAP_TOLERANCE_SECONDS = 120

#: CreateMediaBuySuccess fields CreateMediaBuyError does not declare; none may reach a refusal.
_SUCCESS_ONLY_FIELDS = (
    "media_buy_id",
    "packages",
    "confirmed_at",
    "revision",
    "media_buy_status",
    "creative_deadline",
    "planned_delivery",
)

#: The wait the throttled ad server names; the buyer must receive it as ``retry_after``.
_RATE_LIMIT_RETRY_AFTER = 30


# ═══════════════════════════════════════════════════════════════════════
# Readers
# ═══════════════════════════════════════════════════════════════════════


def _tenant_id(ctx: dict) -> str:
    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx; the UC-002 route seeds one"
    return str(tenant.tenant_id)


def _created_buy_id(ctx: dict) -> str:
    """The ``media_buy_id`` the synchronous success put on the wire."""
    media_buy_id = wire_field(ctx, "media_buy_id")
    assert isinstance(media_buy_id, str) and media_buy_id, f"expected a media_buy_id string, got {media_buy_id!r}"
    return media_buy_id


def _answered_buy_id(ctx: dict) -> str:
    """The buy this create produced, whichever branch answered.

    A synchronous success names it on the wire. A submitted task does not -- per
    create-media-buy-response.json the id lands on the completion artifact -- so the parked
    buy is found by tenant instead (``parked_media_buy``).
    """
    if wire_lookup(ctx, "media_buy_id") is WIRE_MISSING:
        assert wire_field(ctx, "status") == "submitted", (
            f"the success wire names no media_buy_id and is not a submitted task: {sorted(wire_dict(ctx))}"
        )
        return str(parked_media_buy(ctx).media_buy_id)
    return _created_buy_id(ctx)


def _stored_buy(ctx: dict, media_buy_id: str) -> SimpleNamespace:
    """The seller's record of *media_buy_id*, read while the unit is open."""
    from src.core.database.repositories.uow import MediaBuyUoW

    with MediaBuyUoW(_tenant_id(ctx)) as uow:
        assert uow.media_buys is not None and uow.assignments is not None
        buy = uow.media_buys.get_by_id(media_buy_id)
        assert buy is not None, f"the seller stored no media buy {media_buy_id!r}"
        return SimpleNamespace(
            status=buy.status,
            principal_id=buy.principal_id,
            start_time=buy.start_time,
            end_time=buy.end_time,
            is_paused=buy.is_paused,
            revision=buy.revision,
            created_at=buy.created_at,
            updated_at=buy.updated_at,
            package_ids=sorted(p.package_id for p in uow.media_buys.get_packages(media_buy_id)),
            assigned=sorted((a.creative_id, a.package_id) for a in uow.assignments.get_by_media_buy(media_buy_id)),
        )


def _stored_buys(ctx: dict) -> list[SimpleNamespace]:
    """Every buy the tenant holds, with its package ids."""
    from src.core.database.repositories.uow import MediaBuyUoW

    with MediaBuyUoW(_tenant_id(ctx)) as uow:
        assert uow.media_buys is not None
        return [
            SimpleNamespace(
                media_buy_id=buy.media_buy_id,
                package_ids=[p.package_id for p in uow.media_buys.get_packages(buy.media_buy_id)],
            )
            for buy in uow.media_buys.list_all()
        ]


def _requested_packages(ctx: dict) -> list[dict[str, Any]]:
    packages = ctx["request_kwargs"].get("packages") or []
    assert packages, "the request carried no packages, so there is nothing to compare the answer with"
    return list(packages)


def _requested_creative_ids(ctx: dict) -> list[str]:
    """The creatives the request put on its first package: library ids and inline uploads."""
    package = _requested_packages(ctx)[0]
    inline = [c["creative_id"] for c in package.get("creatives") or []]
    return sorted([*(package.get("creative_ids") or []), *inline])


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _assert_start_resolved_to(start: datetime | None, reference: datetime, what: str) -> None:
    """An ``asap`` start resolved to *reference*, the instant the seller took *what*."""
    assert start is not None, f"the seller stored no start_time for {what}"
    drift = abs((_as_utc(start) - _as_utc(reference)).total_seconds())
    assert drift <= _ASAP_TOLERANCE_SECONDS, (
        f"start_time {start} is {drift:.0f}s from {what} at {reference}; 'asap' resolves to that instant"
    )


def _assert_created(ctx: dict) -> list[dict[str, Any]]:
    """The synchronous success, package by package against the request it answers.

    ``product_id`` and ``pricing_option_id`` are buyer fields the response package echoes
    (core/package.json: sellers MUST echo the request package's product_id), so a match
    says the package the seller created is the one that was asked for.
    """
    assert wire_field(ctx, "status") == "completed", "expected the synchronous success task status"
    _created_buy_id(ctx)
    answered = wire_field(ctx, "packages")
    requested = _requested_packages(ctx)
    assert len(answered) == len(requested), f"asked for {len(requested)} package(s), got {answered!r}"
    for index, (sent, got) in enumerate(zip(requested, answered, strict=True)):
        assert got.get("package_id"), f"packages[{index}] carries no package_id: {got!r}"
        for key in ("product_id", "pricing_option_id"):
            if key in sent:
                assert got.get(key) == sent[key], f"packages[{index}].{key}: sent {sent[key]!r}, got {got.get(key)!r}"
    return list(answered)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN
# ═══════════════════════════════════════════════════════════════════════


@given("the system returns a transient error (RATE_LIMITED)")
def given_ad_server_rate_limits(ctx: dict) -> None:
    """The ad server throttles the create: RATE_LIMITED with the wait it named."""
    from src.core.exceptions import AdCPRateLimitError

    ctx["env"].fail_adapter_create(
        AdCPRateLimitError(retry_after=_RATE_LIMIT_RETRY_AFTER),
        rate_limit_retry_after=_RATE_LIMIT_RETRY_AFTER,
    )


@given("a create_media_buy request with account_id that does not exist")
def given_request_unknown_account(ctx: dict) -> None:
    """Name an account id no row carries; nothing seeds it, so resolution cannot find it."""
    given_request_with_account_id(ctx, "acc-inv018-does-not-exist")


@given(parsers.parse('the resolved account has terminal lifecycle status "{status}"'))
def given_account_terminal_status(ctx: dict, status: str) -> None:
    """Move the account the request names into a terminal state (enums/account-status.json)."""
    env = ctx["env"]
    account = env.setup_default_account()
    assert _ensure_request_defaults(ctx)["account"] == {"account_id": account.account_id}, (
        "the request must name the account whose status this step sets"
    )
    account.status = status
    env._commit_factory_data()


@given('the request carries the deprecated "X-Dry-Run" header')
def given_request_carries_dry_run_header(ctx: dict) -> None:
    """Present the deprecated header beside the buyer's own credential headers.

    ``dispatch_request`` sends ``ctx["credential"]`` as the request headers on every
    transport, so the header reaches the seller the way any buyer would send it.
    """
    ctx["credential"] = {**ctx["env"].credential(), "X-Dry-Run": "true"}


@given("a referenced creative is missing its required assets")
def given_creative_missing_assets(ctx: dict) -> None:
    """A stored creative on a served format with no assets at all, referenced by the package."""
    creative = _create_approved_creative(ctx, "cr-inv020-no-assets")
    creative.data = {}
    ctx["env"]._commit_factory_data()
    _add_creative_ids_to_package(ctx, [creative.creative_id])


@given("all referenced creatives exist in valid state with compatible formats")
def given_valid_compatible_creatives(ctx: dict) -> None:
    """An approved creative on the format the default product accepts, referenced by the package."""
    creative = _create_approved_creative(ctx, "cr-inv026-valid")
    _add_creative_ids_to_package(ctx, [creative.creative_id])


@given(parsers.parse('a referenced creative is in "{state}" state'))
def given_creative_in_state(ctx: dict, state: str) -> None:
    """A creative otherwise valid, in *state*, referenced by the package."""
    creative = _create_approved_creative(ctx, f"cr-inv026-{state}")
    creative.status = state
    ctx["env"]._commit_factory_data()
    _add_creative_ids_to_package(ctx, [creative.creative_id])


@given("a creative format is incompatible with the product's supported formats")
def given_creative_format_incompatible(ctx: dict) -> None:
    """The ext-p setup: a served format the default product does not accept."""
    given_creative_format_mismatch(ctx)


@given("a package has two optimization goals with the same priority value")
def given_goals_duplicate_priority(ctx: dict) -> None:
    _first_package(ctx)["optimization_goals"] = [
        {"kind": "metric", "metric": "clicks", "priority": 1},
        {"kind": "metric", "metric": "views", "priority": 1},
    ]


@given("a package has optimization_goals as an empty array")
def given_goals_empty(ctx: dict) -> None:
    _first_package(ctx)["optimization_goals"] = []


@given(parsers.parse('a package has an event kind optimization goal with target kind "{target_kind}"'))
def given_event_goal_with_target(ctx: dict, target_kind: str) -> None:
    _first_package(ctx)["optimization_goals"] = [
        {
            "kind": "event",
            "event_sources": [{"event_source_id": "evt-inv087", "event_type": "purchase", "value_field": "value"}],
            "target": {"kind": target_kind, "value": 4.0},
        }
    ]


@given("no event_sources entry has value_field set")
def given_event_sources_without_value_field(ctx: dict) -> None:
    for goal in _first_package(ctx).get("optimization_goals") or []:
        for source in goal.get("event_sources") or []:
            source.pop("value_field", None)


@given(parsers.parse("a package in packages[] carries its own start_time of {value}"))
def given_package_start_time(ctx: dict, value: str) -> None:
    _first_package(ctx)["start_time"] = value


@given("each creative has a valid format_id, name, and assets with URL and dimensions")
def given_inline_creatives_well_formed(ctx: dict) -> None:
    """Precondition: every inline creative is named, and its image asset carries url and size."""
    creatives = _requested_packages(ctx)[0].get("creatives") or []
    assert creatives, "no inline creatives on the request; a prior Given must add them"
    for creative in creatives:
        assert creative.get("name") and creative.get("format_id"), f"inline creative lacks name/format_id: {creative}"
        images = [a for a in creative["assets"].values() if isinstance(a, dict) and a.get("asset_type") == "image"]
        assert images and all(a.get("url") and a.get("width") and a.get("height") for a in images), (
            f"inline creative image asset lacks url or dimensions: {creative['assets']}"
        )


@given("the creative agent has the referenced formats registered")
def given_inline_formats_served(ctx: dict) -> None:
    """Precondition: the pinned capture the creative agent serves carries every inline format."""
    from tests.factories.creative import served_format_spec

    for creative in _requested_packages(ctx)[0].get("creatives") or []:
        fmt = creative["format_id"]
        assert served_format_spec(fmt["agent_url"], fmt["id"]) is not None, f"the creative agent does not serve {fmt}"


# ═══════════════════════════════════════════════════════════════════════
# THEN -- the success branch
# ═══════════════════════════════════════════════════════════════════════


@then("the pricing validation should pass")
@then("the date validation should pass")
@then("the media buy should be created")
def then_media_buy_created(ctx: dict) -> None:
    """The create completed synchronously with the packages that were asked for."""
    _assert_created(ctx)


@then("the response should have success fields")
def then_success_fields(ctx: dict) -> None:
    """The success branch's own fields, with values (create-media-buy-response.json CreateMediaBuySuccess)."""
    _assert_created(ctx)
    revision = wire_field(ctx, "revision")
    assert isinstance(revision, int) and revision >= 1, f"revision must be an integer >= 1, got {revision!r}"
    confirmed_at = wire_lookup(ctx, "confirmed_at")
    assert confirmed_at is not WIRE_MISSING, "confirmed_at is required on the success branch (string or null)"


@then("the approval path should be auto-approved")
def then_auto_approved(ctx: dict) -> None:
    """Auto-approval answers the synchronous success, not a submitted task."""
    assert wire_field(ctx, "status") == "completed", "an auto-approved create completes synchronously"
    _created_buy_id(ctx)


@then("the media buy should proceed to adapter execution")
def then_adapter_executed(ctx: dict) -> None:
    """The ad server was handed exactly this one buy, and the seller recorded it."""
    assert ctx["env"].adapter_create_calls() == 1, "expected exactly one create_media_buy handed to the ad server"
    stored = _stored_buy(ctx, _created_buy_id(ctx))
    assert stored.package_ids == sorted(p["package_id"] for p in wire_field(ctx, "packages")), (
        f"stored packages {stored.package_ids} differ from the answered ones"
    )


@then("the media buy record should be persisted in the database")
def then_media_buy_persisted(ctx: dict) -> None:
    """The seller recorded the answered buy, owned by the calling principal."""
    stored = _stored_buy(ctx, _answered_buy_id(ctx))
    assert stored.principal_id == ctx["principal"].principal_id, (
        f"stored buy belongs to {stored.principal_id!r}, not the caller {ctx['principal'].principal_id!r}"
    )


@then(parsers.parse('the media buy record should be persisted with status "{status}"'))
def then_media_buy_persisted_with_status(ctx: dict, status: str) -> None:
    stored = _stored_buy(ctx, _answered_buy_id(ctx))
    assert stored.status == status, f"stored status {stored.status!r}, expected {status!r}"


@then("the package records should be persisted")
def then_package_records_persisted(ctx: dict) -> None:
    """One stored package per requested package -- the same ones the wire names, when it names any."""
    stored = _stored_buy(ctx, _answered_buy_id(ctx))
    assert len(stored.package_ids) == len(_requested_packages(ctx)), (
        f"asked for {len(_requested_packages(ctx))} package(s), the seller stored {stored.package_ids}"
    )
    answered = wire_lookup(ctx, "packages")
    if answered is not WIRE_MISSING:
        assert stored.package_ids == sorted(p["package_id"] for p in answered)


@then("the creative assignment should proceed")
@then("the creative assignment records should be persisted")
@then("the system should assign the uploaded creatives to packages")
def then_creatives_assigned(ctx: dict) -> None:
    """Every creative the request put on the package is assigned to the package the seller created."""
    stored = _stored_buy(ctx, _created_buy_id(ctx))
    package_id = wire_field(ctx, "packages")[0]["package_id"]
    expected = [(creative_id, package_id) for creative_id in _requested_creative_ids(ctx)]
    assert stored.assigned == expected, f"stored assignments {stored.assigned}, expected {expected}"


@then("the system should upload the creatives to the creative library")
def then_inline_creatives_in_library(ctx: dict) -> None:
    """Each inline creative is in the caller's library under the id it was sent with."""
    from src.core.database.repositories.uow import MediaBuyUoW

    _created_buy_id(ctx)
    expected = _requested_creative_ids(ctx)
    with MediaBuyUoW(_tenant_id(ctx)) as uow:
        assert uow.creatives is not None
        stored = sorted(c.creative_id for c in uow.creatives.get_by_ids(expected, ctx["principal"].principal_id))
    assert stored == expected, f"library holds {stored}, expected the uploaded {expected}"


@then("the response should include the created media buy with creative assignments")
def then_response_carries_assignments(ctx: dict) -> None:
    """core/package.json ``creative_assignments``: the creatives assigned to this package."""
    _created_buy_id(ctx)
    assignments = wire_field(ctx, "packages")[0].get("creative_assignments") or []
    assert sorted(a["creative_id"] for a in assignments) == _requested_creative_ids(ctx), (
        f"packages[0].creative_assignments on the wire: {assignments!r}"
    )


@when("the Buyer Agent reads the created media buy back with get_media_buys")
def when_read_back_created_buy(ctx: dict) -> None:
    """get_media_buys for the buy the create answered, on the same transport and identity.

    The created package's id is kept first: the readback replaces the result the create's
    Thens read. get_media_buys.mdx: a seller without a creative library reports inline
    creatives on ``media_buys[].packages[].creative_approvals``.
    """
    from src.core.schemas._base import GetMediaBuysRequest

    ctx["created_package_id"] = wire_field(ctx, "packages")[0]["package_id"]
    dispatch_request(ctx, req=GetMediaBuysRequest(media_buy_ids=[_created_buy_id(ctx)]))


@then("the created package should report creative approvals for exactly the inline creatives")
def then_created_package_reports_inline_creatives(ctx: dict) -> None:
    """The readback names exactly the creatives the create carried inline, on the wire."""
    from tests.bdd.steps.domain.uc019_query_media_buys import _reported_creative_ids

    reported = _reported_creative_ids(ctx, ctx["created_package_id"])
    assert reported == _requested_creative_ids(ctx), (
        f"get_media_buys reports creatives {reported} for the created package, "
        f"expected the inline uploads {_requested_creative_ids(ctx)}"
    )


@then("the system should resolve start_time to current UTC")
def then_start_time_resolved(ctx: dict) -> None:
    """The stored flight start is the moment the seller took the buy, not a literal."""
    stored = _stored_buy(ctx, _created_buy_id(ctx))
    _assert_start_resolved_to(stored.start_time, stored.created_at, "the buy's creation")


def assert_start_time_stored_as_requested(ctx: dict) -> None:
    """A concrete start_time the seller accepted is the flight start it stored, unchanged.

    A buy id alone does not show the start was taken: a seller that dropped it, or kept a
    default date, answers the same. create_media_buy.mdx lists start_time as "ISO 8601
    date-time"; nothing in the pin lets a seller move an accepted future start.
    """
    requested = ctx["request_kwargs"]["start_time"]
    expected = _as_utc(datetime.fromisoformat(requested.replace("Z", "+00:00")))
    stored = _stored_buy(ctx, _created_buy_id(ctx))
    assert stored.start_time is not None and _as_utc(stored.start_time) == expected, (
        f"the buyer sent start_time {requested}, the seller stored {stored.start_time}"
    )


@then("the campaign should be immediately activating")
def then_campaign_activating(ctx: dict) -> None:
    """Created synchronously and not waiting for a flight date (media-buy-status.json pending_start)."""
    from tests.helpers.pinned_schema import load

    assert wire_field(ctx, "status") == "completed", "an immediately activating buy completes synchronously"
    media_buy_status = wire_field(ctx, "media_buy_status")
    assert media_buy_status in load("enums/media-buy-status.json")["enum"], (
        f"not a MediaBuyStatus: {media_buy_status!r}"
    )
    assert media_buy_status != "pending_start", "an 'asap' buy is waiting for a future flight date"


@then('the response should include resolved start_time (not literal "asap")')
def then_packages_carry_resolved_start(ctx: dict) -> None:
    """core/package.json start_time: "Sellers SHOULD always include the resolved value in responses"."""
    stored = _stored_buy(ctx, _created_buy_id(ctx))
    for index, package in enumerate(wire_field(ctx, "packages")):
        start = package.get("start_time")
        assert isinstance(start, str) and start != "asap", f"packages[{index}].start_time is {start!r}"
        resolved = datetime.fromisoformat(start.replace("Z", "+00:00"))
        _assert_start_resolved_to(resolved, stored.created_at, f"the buy's creation (packages[{index}])")


@then("the approval path should be manual")
def then_manual_approval(ctx: dict) -> None:
    """A buy held for the seller's human review answers the submitted task (task_id, no buy id)."""
    assert wire_field(ctx, "status") == "submitted", "a buy awaiting human review stays a submitted task"
    task_id = wire_field(ctx, "task_id")
    assert isinstance(task_id, str) and task_id, f"the submitted task carries no task_id: {task_id!r}"
    assert wire_lookup(ctx, "media_buy_id") is WIRE_MISSING, "a submitted task names no media_buy_id"


@then("the media buy should enter pending state")
def then_parked(ctx: dict) -> None:
    """The seller parked the buy for the decision and did not hand it to the ad server."""
    assert parked_media_buy(ctx).status == "pending_approval", "the parked buy is not awaiting approval"
    assert ctx["env"].adapter_create_calls() == 0, "a buy awaiting approval was handed to the ad server"


@then('"pending_approval" should never be used as a MediaBuyStatus value')
def then_no_pending_approval_status(ctx: dict) -> None:
    """specification.mdx: "there is no pending_approval MediaBuy status" -- not on this answer."""
    from tests.helpers.pinned_schema import load

    assert wire_field(ctx, "status") != "pending_approval", "pending_approval is not a task status either"
    media_buy_status = wire_lookup(ctx, "media_buy_status")
    if media_buy_status is not WIRE_MISSING:
        assert media_buy_status in load("enums/media-buy-status.json")["enum"], (
            f"media_buy_status {media_buy_status!r} is not a pinned MediaBuyStatus"
        )


@then("the ad server adapter should never be invoked")
def then_adapter_never_invoked(ctx: dict) -> None:
    assert ctx["env"].adapter_create_calls() == 0, "the create reached the ad server"


@then("no package records should be persisted")
def then_no_packages_persisted(ctx: dict) -> None:
    """No buy -- hence no package -- beyond the ones a Given seeded."""
    seeded = {ctx.get("existing_media_buy_id")} - {None}
    created = [b for b in _stored_buys(ctx) if b.media_buy_id not in seeded and b.package_ids]
    assert created == [], f"packages were stored for buys this request produced: {created}"


# ═══════════════════════════════════════════════════════════════════════
# THEN -- the error branch
# ═══════════════════════════════════════════════════════════════════════


@then("the response should NOT have success fields (media_buy_id, packages)")
def then_no_success_fields(ctx: dict) -> None:
    """The error branch carries no buy: none of CreateMediaBuySuccess's own fields."""
    envelope = ctx["result"].error_envelope()
    leaked = {f: envelope[f] for f in _SUCCESS_ONLY_FIELDS if f in envelope}
    assert leaked == {}, f"the error envelope carries success fields: {leaked}"


@then('each error should include "suggestion" field')
def then_each_error_has_suggestion(ctx: dict) -> None:
    """Every entry, not only ``errors[0]``."""
    errors = ctx["result"].wire_error_objects()
    assert errors, "expected a non-empty errors array on the wire"
    missing = [index for index, err in enumerate(errors) if not err.get("suggestion")]
    assert missing == [], f"errors{missing} carry no suggestion: {errors}"


@then('both the protocol envelope "adcp_error" field and the payload "errors" array should be populated')
def then_both_error_layers(ctx: dict) -> None:
    """error-handling.mdx: "A fatal task failure SHOULD populate both layers" -- the same code in each.

    ``assert_wire_error`` checks both layers through ``assert_envelope_shape``: ``adcp_error``
    present, ``errors[]`` non-empty, and the two agreeing on code and recovery. The expected
    code is read off ``errors[0]`` because this Then grades agreement; the scenario's own
    code Then pins which code it is.
    """
    result = ctx["result"]
    code = result.wire_error_code()
    assert code is not None, "the refusal carries no payload errors[] entry"
    result.assert_wire_error(code)


@then('the envelope should not emit the legacy "task_status" or "response_status" fields')
def then_no_legacy_status_fields(ctx: dict) -> None:
    """task-lifecycle.mdx: "Agents MUST NOT emit the legacy task_status or response_status fields"."""
    envelope = wire_error_envelope_or_none(ctx)
    document = envelope if envelope is not None else wire_dict(ctx)
    legacy = sorted({"task_status", "response_status"} & set(document))
    assert legacy == [], f"the response carries legacy status field(s) {legacy}"
