"""Steps for local-constraint-relaxation-rejections.feature.

Four rows, one per request-schema bound that a LOCAL model dropped when it
redeclared a field its adcp parent already constrained.

Two things shape every step here:

* **The offending body is dispatched RAW.** Constructing a typed request in the
  step — which is what ``uc002_create_media_buy._dispatch_full_create`` and
  ``uc003_update_media_buy.when_send_update_request`` do — would raise the
  ``ValidationError`` inside the TEST process once the constraint lands, stash it
  as ``ctx["error"]``, and never reach a transport. There would be no wire
  envelope to grade, and the row would "pass" on an exception the buyer never
  sees. The create side already has this seam (``dispatch_mode="create_raw"``);
  the update side gained it in ``media_buy_dual._is_update_request``.

* **The oracle is the wire envelope, per tests/CLAUDE.md § Error Verification
  Policy** — ``result.assert_wire_error(...)``, with no
  fallback to the reconstructed ``ctx["error"]``. The generic
  ``the error code should be "..."`` step does carry such a fallback; for these
  rows that fallback is precisely the failure mode to exclude, because a
  request-model rejection that never reaches a boundary still produces a
  reconstructable exception.

The expected code/recovery/field are MEASURED, not predicted: with the four
constraints applied in-memory, every one of a2a/mcp/rest emits
``VALIDATION_ERROR`` / ``correctable`` and names the offending field at the
protocol position the harness grades. (The human-readable message is NOT asserted — it legitimately
differs between the MCP boundary's short form and the a2a/rest long form.)
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic._dispatch import dispatch_request

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — put the out-of-bounds value on the request
# ═══════════════════════════════════════════════════════════════════════


def _raw_create_kwargs(ctx: dict) -> dict[str, Any]:
    """Return the shared create-request dict, routed to the RAW wire dispatch."""
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    ctx["dispatch_mode"] = "create_raw"
    return kwargs


@given(parsers.parse("a package carries an impression goal of {value:d}"))
def given_package_impressions(ctx: dict, value: int) -> None:
    """Set a package's legacy impression goal to an out-of-range value.

    pin: media-buy/package-request.json .properties.impressions.minimum = 0
    """
    kwargs = _raw_create_kwargs(ctx)
    assert kwargs["packages"], "the base create request must carry a package to mutate"
    kwargs["packages"][0]["impressions"] = value


@given("the request carries an empty packages array")
def given_empty_packages(ctx: dict) -> None:
    """Replace the create request's packages with [].

    pin: media-buy/create-media-buy-request.json .properties.packages.minItems = 1
    """
    _raw_create_kwargs(ctx)["packages"] = []


@given("a package carries an empty creatives array")
def given_empty_creatives(ctx: dict) -> None:
    """Set a package's inline creatives to [].

    pin: media-buy/package-request.json .properties.creatives.minItems = 1
    """
    kwargs = _raw_create_kwargs(ctx)
    assert kwargs["packages"], "the base create request must carry a package to mutate"
    kwargs["packages"][0]["creatives"] = []


@given("an existing media buy to update")
def given_existing_media_buy(ctx: dict) -> None:
    """Adopt the media buy the conftest UC-003 branch seeded, and record its revision.

    The revision is captured BEFORE the update so the post-condition compares
    against the observed pre-state rather than a literal — a seeded default that
    changed would otherwise quietly turn the post-condition into a tautology.
    """
    media_buy = ctx["existing_media_buy"]
    ctx["update_target_id"] = media_buy.media_buy_id
    ctx["revision_before"] = _persisted_revision(ctx, media_buy.media_buy_id)


@given("the update carries an empty packages array")
def given_update_empty_packages(ctx: dict) -> None:
    """Build a raw update body whose only package-updates array is empty.

    pin: media-buy/update-media-buy-request.json .properties.packages.minItems = 1
    """
    # account and idempotency_key are AdCP 3.1.1 /required. Without them validation stops
    # there and the envelope names "account", so the row silently stops grading the thing it
    # is about -- the empty packages array. A scenario that asserts on a specific field must
    # get past every OTHER required field first.
    ctx["raw_update_kwargs"] = {
        "media_buy_id": ctx["update_target_id"],
        "account": {"account_id": "acct_test"},
        "idempotency_key": "test-idem-key-0001",
        "packages": [],
    }


# ═══════════════════════════════════════════════════════════════════════
# WHEN step — raw update dispatch
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent sends the update_media_buy request as raw wire parameters")
def when_send_raw_update(ctx: dict) -> None:
    """Dispatch the flat update body through the parametrized wire transport.

    Deliberately NOT ``uc003_update_media_buy.when_send_update_request``: that step USED to
    construct ``UpdateMediaBuyRequest(**update_kwargs)`` in the test process and catch the
    ``ValidationError``, so the rejection was a test-process exception rather than a wire
    envelope. That step now dispatches the raw bag too, so the divergence this step was
    written to avoid is gone -- but a raw dispatch remains the right shape here, and having
    it named separately keeps the intent legible.
    """
    dispatch_request(ctx, **ctx["raw_update_kwargs"])


# ═══════════════════════════════════════════════════════════════════════
# THEN steps
# ═══════════════════════════════════════════════════════════════════════


@then(
    parsers.parse('the request is refused on the wire with code "{code}" recovery "{recovery}" naming field "{field}"')
)
def then_refused_on_wire(ctx: dict, code: str, recovery: str, field: str) -> None:
    """Grade the buyer-facing rejection: two-layer envelope, code, recovery, field.

    ``wire_error_envelope`` only — never ``ctx["error"]``. On a wire transport a
    missing envelope means the request was ACCEPTED (or failed before a boundary
    could translate it), and both are failures of this row.
    """
    result = ctx.get("result")
    assert result is not None, f"no dispatch recorded; ctx error: {ctx.get('error')!r}"
    assert result.is_error, f"the request was ACCEPTED — expected a {code} refusal. Payload: {result.payload!r}"

    # ``field`` is passed through the sanctioned surface rather than read off the
    # envelope here: the refusal must name the offending field — the buyer's
    # remediation target — and a generic "something was invalid" answer is what the
    # pre-constraint SERVICE_UNAVAILABLE path produced. Where the spec puts that
    # pointer (one protocol position, or both mirrored layers) is decided ONCE in
    # ``assert_envelope_shape``, which ``assert_wire_error`` forwards to; a local
    # re-assertion here would be a second copy of the two-layer invariant that
    # drifts from the primitive, and — since it would have to subscript
    # ``adcp_error``/``errors`` — a hand-rolled envelope parse that
    # ``test_architecture_bdd_wire_discipline.py`` Check C forbids outright.
    #
    # Reading the envelope through ``wire_error_envelope_or_none(ctx)`` first (the
    # guarded accessor Check E requires of any direct reader) is likewise
    # unnecessary rather than merely optional: with no direct read left there is
    # nothing for Check E to guard, and ``assert_wire_error`` raises the
    # never-reached-a-boundary diagnosis this step used to raise itself, naming the
    # swallowed-typed-error case as well.
    result.assert_wire_error(code, recovery=recovery, field=field)


@then("no media buy is persisted for the tenant")
def then_no_media_buy_persisted(ctx: dict) -> None:
    """A refused create leaves the store empty — read the rows, not the error.

    The generic ``no new media buy should have been created`` step short-circuits
    on ``ctx["error"]`` being set ("an error means nothing was created"), which
    for this row would assert the same fact the previous step already asserted.
    This one queries.
    """
    from src.core.database.models import MediaBuy

    env = ctx["env"]
    persisted = env.query(MediaBuy, tenant_id=ctx["tenant"].tenant_id)
    assert persisted == [], f"expected no media buy rows, found {[m.media_buy_id for m in persisted]}"


@then("the media buy's persisted revision is unchanged")
def then_revision_unchanged(ctx: dict) -> None:
    """A refused update does not bump the optimistic-concurrency revision.

    The revision is the buyer's concurrency token: an update that is refused must
    not consume one, or the buyer's next conditional update fails against a
    revision that no successful write produced.
    """
    after = _persisted_revision(ctx, ctx["update_target_id"])
    assert after == ctx["revision_before"], f"revision moved {ctx['revision_before']} -> {after} on a refused update"


def _persisted_revision(ctx: dict, media_buy_id: str) -> int:
    """Read one media buy's revision through the harness-bound session."""
    from src.core.database.models import MediaBuy

    env = ctx["env"]
    return env.get_one(MediaBuy, media_buy_id=media_buy_id).revision
