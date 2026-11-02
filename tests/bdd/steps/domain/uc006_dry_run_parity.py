"""Steps for local-uc006-dry-run-preview-parity.feature.

One tenant, one payload, two dispatches: first with ``dry_run`` true, then live. The
live run's answer is the oracle for the preview's -- the pinned request schema says a
preview "returns what would be created/updated/deleted", and the only thing that knows
what WOULD happen is what does happen from the same starting state. The preview
persists nothing, so the live run starts from exactly the state the preview saw.

The case table is data, not a second set of expectations: each case builds the payload
(and the seeded state it needs) and states the precondition the LIVE run must meet for
the comparison to grade anything -- a preview that matches a live run which did nothing
has matched nothing. Those preconditions are the ones the integration tests this
replaces carried; what changed is the surface: the two documents compared are the two
the buyer received.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.domain.uc006_sync_creatives import (
    _assignments_for_the_wire,
    _format_payload,
    _persisted_creative_fingerprints,
)
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import CreativeFactory, MediaBuyFactory, MediaPackageFactory, ProductFactory
from tests.factories.request import CreativeAssetRequestFactory
from tests.harness.transport import TransportResult

#: A format id that differs from the transport's default on EVERY transport. It is never
#: sent to a creative agent: the mismatch case puts it on the PRODUCT (a product may
#: declare any format), and the format-update case seeds the library ROW on it (a row is
#: not validated) and moves the creative to the default, which the agent serves. The
#: first version of this file used display_300x250_image, which is the e2e stack's
#: default format -- so on e2e_rest both cases quietly became "same format" and failed.
_OTHER_FORMAT_ID = "display_300x250_other"


def _creative(ctx: dict, creative_id: str, name: str, *, format_id: str | None = None) -> dict:
    fmt, agent_url, assets = _format_payload(ctx, ctx["env"])
    return CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=name,
        format_id={"id": format_id or fmt, "agent_url": agent_url},
        assets=assets,
    )


def _seed_row(ctx: dict, creative_id: str, name: str, *, format_id: str | None = None) -> None:
    """A creative already in the caller's library, on the request's default format unless told otherwise."""
    fmt, agent_url, _ = _format_payload(ctx, ctx["env"])
    CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id=creative_id,
        name=name,
        agent_url=agent_url,
        format=format_id or fmt,
    )


def _seed_package(ctx: dict, *, product_format_id: str | None = None, media_buy: dict[str, Any] | None = None) -> str:
    """A package whose product accepts *product_format_id* (the default format unless told otherwise)."""
    fmt, agent_url, _ = _format_payload(ctx, ctx["env"])
    buy = MediaBuyFactory(tenant=ctx["tenant"], principal=ctx["principal"], **(media_buy or {"status": "active"}))
    product = ProductFactory(
        tenant=ctx["tenant"], format_ids=[{"agent_url": agent_url, "id": product_format_id or fmt}]
    )
    package = MediaPackageFactory(media_buy=buy, package_config={"product_id": product.product_id, "budget": 1000.0})
    ctx["package"] = package
    return package.package_id


def _entries(result: TransportResult) -> list[dict]:
    return list(result.require_wire().get("creatives") or [])


def _actions(result: TransportResult) -> list[str]:
    return [entry.get("action") for entry in _entries(result)]


def _entry(result: TransportResult, creative_id: str) -> dict:
    matches = [e for e in _entries(result) if e.get("creative_id") == creative_id]
    assert len(matches) == 1, f"expected one creatives entry for {creative_id!r}, got {matches}"
    return matches[0]


# --- the cases -----------------------------------------------------------------------
#
# Each builder returns (kwargs for the sync request, precondition on the LIVE result).


def _case_two_identical(ctx: dict):
    payload = [_creative(ctx, "dup_c1", "Dup Creative"), _creative(ctx, "dup_c1", "Dup Creative")]

    def expect(live: TransportResult) -> None:
        # Live, entry 1 is created and flushed, so entry 2 resolves against it -- and
        # since it carries the same data, it is "unchanged" (enums/creative-action.json).
        assert _actions(live) == ["created", "unchanged"], _actions(live)

    return {"creatives": payload}, expect


def _case_two_differ(ctx: dict):
    payload = [_creative(ctx, "diff_c1", "First Name"), _creative(ctx, "diff_c1", "Second Name")]

    def expect(live: TransportResult) -> None:
        assert _actions(live) == ["created", "updated"], _actions(live)
        assert "name" in (_entries(live)[1].get("changes") or []), _entries(live)[1]

    return {"creatives": payload}, expect


def _case_third_entry(ctx: dict):
    payload = [
        _creative(ctx, "trip_c1", "First Name"),
        _creative(ctx, "trip_c1", "Second Name"),
        _creative(ctx, "trip_c1", "Second Name"),
    ]

    def expect(live: TransportResult) -> None:
        # Entry 3 repeats entry 2's data, so it resolves to "unchanged" against the row
        # entry 2 left -- a preview graded against entry 1's state would say "updated".
        assert _actions(live) == ["created", "updated", "unchanged"], _actions(live)

    return {"creatives": payload}, expect


def _case_distinct_ids(ctx: dict):
    payload = [_creative(ctx, "sep_c1", "One"), _creative(ctx, "sep_c2", "Two")]

    def expect(live: TransportResult) -> None:
        assert _actions(live) == ["created", "created"], _actions(live)

    return {"creatives": payload}, expect


def _case_single_update(ctx: dict):
    _seed_row(ctx, "upd_c1", "Original")
    payload = [_creative(ctx, "upd_c1", "Renamed")]

    def expect(live: TransportResult) -> None:
        assert _actions(live) == ["updated"], _actions(live)
        assert _entries(live)[0].get("changes"), _entries(live)[0]

    return {"creatives": payload}, expect


def _case_delete_missing(ctx: dict):
    _seed_row(ctx, "del_keep", "Kept")
    _seed_row(ctx, "del_orphan", "Orphan")
    payload = [_creative(ctx, "del_new", "New")]

    def expect(live: TransportResult) -> None:
        assert sorted(_actions(live)) == ["created", "deleted", "deleted"], _actions(live)

    return {"creatives": payload, "delete_missing": True}, expect


def _case_existing_valid_assignment(ctx: dict):
    pkg = _seed_package(ctx, media_buy={"status": "draft", "approved_at": datetime(2025, 1, 1, tzinfo=UTC)})
    _seed_row(ctx, "asg_a", "Existing")
    ctx["assignments"] = {"asg_a": [pkg]}
    payload = [_creative(ctx, "asg_a", "Existing")]

    def expect(live: TransportResult) -> None:
        assert _entry(live, "asg_a").get("assigned_to") == [pkg], _entry(live, "asg_a")

    return {
        "creatives": payload,
        "assignments": _assignments_for_the_wire(ctx["assignments"]),
        "validation_mode": "lenient",
    }, expect


def _case_new_creative_assigned(ctx: dict):
    pkg = _seed_package(ctx)
    ctx["assignments"] = {"asg_b": [pkg]}
    payload = [_creative(ctx, "asg_b", "Brand New")]

    def expect(live: TransportResult) -> None:
        assert _actions(live) == ["created"], _actions(live)
        assert _entry(live, "asg_b").get("assigned_to") == [pkg], _entry(live, "asg_b")

    return {
        "creatives": payload,
        "assignments": _assignments_for_the_wire(ctx["assignments"]),
        "validation_mode": "lenient",
    }, expect


def _case_format_update_and_assignment(ctx: dict):
    # The seeded row is on a format the product does NOT accept; the payload moves the
    # creative to the default format, which the product accepts -- so the assignment must
    # grade the POST-sync state, not the stale row.
    pkg = _seed_package(ctx)
    _seed_row(ctx, "asg_b2", "Reformat Me", format_id=_OTHER_FORMAT_ID)
    ctx["assignments"] = {"asg_b2": [pkg]}
    payload = [_creative(ctx, "asg_b2", "Reformat Me")]

    def expect(live: TransportResult) -> None:
        assert _actions(live) == ["updated"], _actions(live)
        entry = _entry(live, "asg_b2")
        assert "format" in (entry.get("changes") or []), entry
        assert entry.get("assigned_to") == [pkg], entry

    return {
        "creatives": payload,
        "assignments": _assignments_for_the_wire(ctx["assignments"]),
        "validation_mode": "lenient",
    }, expect


def _case_package_not_found_lenient(ctx: dict):
    ctx["assignments"] = {"asg_c": ["pkg_void_c"]}
    payload = [_creative(ctx, "asg_c", "Orphaned")]

    def expect(live: TransportResult) -> None:
        assert "pkg_void_c" in (_entry(live, "asg_c").get("assignment_errors") or {}), _entry(live, "asg_c")

    return {
        "creatives": payload,
        "assignments": _assignments_for_the_wire(ctx["assignments"]),
        "validation_mode": "lenient",
    }, expect


def _case_assignment_only_missing_creative(ctx: dict):
    ctx["assignments"] = {"asg_d_ghost": ["pkg_asg_d"]}
    payload = [_creative(ctx, "c_sync_anchor", "Anchor")]

    def expect(live: TransportResult) -> None:
        ghost = _entry(live, "asg_d_ghost")
        assert ghost.get("action") == "failed", ghost
        assert "pkg_asg_d" in (ghost.get("assignment_errors") or {}), ghost

    return {
        "creatives": payload,
        "assignments": _assignments_for_the_wire(ctx["assignments"]),
        "validation_mode": "lenient",
    }, expect


def _case_strict_package_not_found(ctx: dict):
    ctx["assignments"] = {"asg_e": ["pkg_void_e"]}
    payload = [_creative(ctx, "asg_e", "Strict")]

    def expect(live: TransportResult) -> None:
        assert live.is_error, "strict mode must refuse an assignment to a package that does not exist"
        live.assert_wire_error("PACKAGE_NOT_FOUND")

    return {
        "creatives": payload,
        "assignments": _assignments_for_the_wire(ctx["assignments"]),
        "validation_mode": "strict",
    }, expect


def _case_format_mismatch_lenient(ctx: dict):
    pkg = _seed_package(ctx, product_format_id=_OTHER_FORMAT_ID)
    ctx["assignments"] = {"asg_f": [pkg]}
    payload = [_creative(ctx, "asg_f", "Wrong Format")]

    def expect(live: TransportResult) -> None:
        entry = _entry(live, "asg_f")
        assert not entry.get("assigned_to"), entry
        assert pkg in (entry.get("assignment_errors") or {}), entry

    return {
        "creatives": payload,
        "assignments": _assignments_for_the_wire(ctx["assignments"]),
        "validation_mode": "lenient",
    }, expect


_CASES: dict[str, Callable[[dict], tuple[dict, Callable[[TransportResult], None]]]] = {
    "two identical entries on one id": _case_two_identical,
    "two entries on one id that differ": _case_two_differ,
    "third entry resolves against what the second left": _case_third_entry,
    "distinct creative ids are not collapsed": _case_distinct_ids,
    "single entry update of a seeded creative": _case_single_update,
    "delete_missing with two seeded and one new": _case_delete_missing,
    "existing creative with a valid assignment": _case_existing_valid_assignment,
    "new creative synced and assigned in one payload": _case_new_creative_assigned,
    "format update and assignment in one payload": _case_format_update_and_assignment,
    "package not found in lenient mode": _case_package_not_found_lenient,
    "assignment-only reference to a missing creative": _case_assignment_only_missing_creative,
    "strict package not found refuses both arms": _case_strict_package_not_found,
    "format mismatch in lenient mode": _case_format_mismatch_lenient,
}


@given(parsers.parse('a dry-run parity payload "{case}"'))
def given_parity_payload(ctx: dict, case: str) -> None:
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    build = _CASES[case]
    kwargs, expect = build(ctx)
    env._commit_factory_data()
    ctx["creatives"] = kwargs["creatives"]
    ctx["parity_kwargs"] = kwargs
    ctx["parity_expect"] = expect


def _assignment_rows(ctx: dict) -> int:
    """How many creative_assignments rows the tenant holds for the case's creatives."""
    from src.core.database.models import CreativeAssignment as DBAssignment

    ids = {c["creative_id"] for c in ctx["creatives"]} | set(ctx.get("assignments", {}))
    return sum(len(ctx["env"].query(DBAssignment, tenant_id=ctx["tenant"].tenant_id, creative_id=cid)) for cid in ids)


@when("the Buyer Agent previews the sync and then syncs it live")
def when_preview_then_live(ctx: dict) -> None:
    kwargs = dict(ctx["parity_kwargs"])
    kwargs["account"] = ctx.get("account_ref")
    before_library = _persisted_creative_fingerprints(ctx)
    before_rows = _assignment_rows(ctx)

    dispatch_request(ctx, dry_run=True, **copy.deepcopy(kwargs))
    ctx["preview_result"] = ctx["result"]

    after_library = _persisted_creative_fingerprints(ctx)
    ctx["preview_library_delta"] = {
        "added": sorted(set(after_library) - set(before_library)),
        "removed": sorted(set(before_library) - set(after_library)),
        "modified": sorted(
            k for k in set(after_library) & set(before_library) if after_library[k] != before_library[k]
        ),
        "assignment_rows_added": _assignment_rows(ctx) - before_rows,
    }

    dispatch_request(ctx, dry_run=False, **copy.deepcopy(kwargs))


@then("the live run produces the outcome the case expects")
def then_live_outcome(ctx: dict) -> None:
    """The non-vacuity control: the case's precondition on the LIVE run holds."""
    ctx["parity_expect"](ctx["result"])


@then("the preview and the live run answer the same")
def then_preview_matches_live(ctx: dict) -> None:
    preview: TransportResult = ctx["preview_result"]
    live: TransportResult = ctx["result"]
    if live.is_error:
        assert preview.is_error, (
            f"the live run refused ({live.wire_error_code()}) but the preview answered success: "
            f"{preview.require_wire()}"
        )
        assert preview.wire_error_code() == live.wire_error_code(), (
            f"dry_run refused with {preview.wire_error_code()!r}, the live run with {live.wire_error_code()!r}"
        )
        return
    assert not preview.is_error, f"the preview refused ({preview.wire_error_code()}) what the live run accepted"
    previewed = _entries(preview)
    actual = wire_field(ctx, "creatives")
    assert previewed == actual, (
        f"dry_run previewed a result the live run does not produce\n  DRY : {previewed}\n  LIVE: {actual}"
    )


@then("the preview added nothing to the library")
def then_preview_added_nothing(ctx: dict) -> None:
    delta = ctx["preview_library_delta"]
    assert delta == {"added": [], "removed": [], "modified": [], "assignment_rows_added": 0}, (
        f"the preview changed the library before the live run: {delta}"
    )
