"""Domain step definitions for BR-CODES-001 (salesagent-3dawm.6).

Grades, as a Gherkin scenario across a2a/mcp/rest/e2e_rest, the obligation that the
error code a seller DECLARES is the code the buyer RECEIVES — no table, no translator,
no collapse between the raise site and the envelope.

Given: reuses the shared create_media_buy defaults, then names a brand domain carrying
    the mock adapter's ``[REJECT:...]`` keyword so the rejection is DETERMINISTIC. The
    adapter's other rejection path (``_simulate_approval``) is driven by
    ``random.random()`` and would make this scenario flaky.
When: the existing "the Buyer Agent sends the create_media_buy request" step —
    dispatch_request is the ONE sanctioned writer of ctx["result"].
Then: assert_wire_error on the real envelope. Recovery is pinned by the shared
    'the error recovery should be "..."' step, which is wire-first via assert_wire_error.

Why BOTH halves are pinned: before the rewriters were deleted the buyer received
POLICY_VIOLATION/correctable for this outcome. A scenario grading only the code would
pass while the buyer was still told to retry a decision the seller has made — and AdCP
3.1.1 core/error.json makes ``recovery`` the mandated decode path for a code the
published enum does not name, so the recovery half is the half that makes an
open-vocabulary code usable at all.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then

from src.core.errors.details import RejectionReasonDetails
from tests.bdd.steps._outcome_helpers import wire_error_envelope_or_none

# Surfaced to the buyer in the rejection details by the mock adapter.
_REJECTION_REASON = "Budget too low for this campaign"


@given("the buyer requests a media buy the seller will reject")
def given_seller_will_reject(ctx: dict) -> None:
    """Make the seller reject this create, on EVERY transport.

    Two mechanisms, because the transports genuinely differ and one alone grades half
    the matrix:

    IN-PROCESS (mcp/a2a/rest): MediaBuyCreateEnv patches ``get_adapter`` with a MagicMock,
    so the real adapter never runs — the rejection has to be the mock's side_effect. Same
    shape as the "ad server adapter returns an error" Given.

    E2E_REST: the live server has no patches and runs the REAL mock adapter inside Docker,
    reachable only through ``AdapterConfig.config_json["test_behavior"]`` — the sanctioned
    cross-transport channel (``set_adapter_test_behavior``), which exists precisely "so the
    same BDD Given steps also drive the real adapter over E2E". A bespoke poke at the
    principal's platform_mappings would bypass that layer AND be inert: the adapter's
    ``approval_simulation`` is only consulted on the sync-with-delay and async paths, never
    from ``_create_media_buy_immediate``, which is what an immediate create dispatches.

    Keyed on ``env._tenant_id``, NOT ``ctx["tenant"]``. Those can differ: a Given earlier in
    a scenario may replace ctx["tenant"] with a tenant of its own, and then this row lands on
    a tenant the request never dispatches as — the live server reads the real row for the
    tenant it DOES use, finds nothing, and completes the buy. The env is the authority for
    which tenant this scenario runs as.

    Auto-approval is deliberately NOT seeded here: MediaBuyCreateEnv already seeds its own
    tenant ``human_review_required=False``, precisely because "the live e2e_rest server has no
    patches and reads the REAL tenant row". A second seeding call would be redundant, and if
    aimed at the wrong tenant it would appear to work while doing nothing.
    """
    from src.core.exceptions import AdCPMediaBuyRejectedError
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.core import set_adapter_test_behavior

    env = ctx["env"]

    # In-process branch.
    mock_adapter = env.mock["adapter"].return_value
    mock_adapter.create_media_buy.side_effect = AdCPMediaBuyRejectedError(
        details=RejectionReasonDetails(rejection_reason=_REJECTION_REASON)
    )

    # Live-server branch: the sanctioned injection channel, keyed on the env's own tenant.
    set_adapter_test_behavior(
        env,
        env._tenant_id,
        reject_on_create=True,
        rejection_reason=_REJECTION_REASON,
        manual_approval_required=False,
    )

    ctx.setdefault("account_ref", None)
    _ensure_request_defaults(ctx)


@then(parsers.parse('the response is an error carrying the seller\'s own code "{code}"'))
def then_error_carries_declared_code(ctx: dict, code: str) -> None:
    """Assert the wire envelope carries the code the raise site declared, unrewritten.

    ``assert_wire_error`` reads the REAL envelope — this obligation is only meaningful
    on the wire, since the rewriting it grades happened at the transport boundary.
    Recovery is left to the shared recovery step so each Then pins one thing.

    No suggestion is asserted HERE, though the envelope now carries one: since
    salesagent-3dawm.8 the table owns the suggestion (``AdCPSalesAgentError.suggestion`` is a
    read-only property over CODE_TABLE), so this
    error resolves the pin's suggestion rather than omitting the field. This step pins the
    two fields a buyer DISPATCHES on; the suggestion's presence on this same bare path is
    graded by BR-CODES-002.
    """
    ctx["result"].assert_wire_error(code, recovery=None)


# ---------------------------------------------------------------------------
# BR-CODES-002 (salesagent-3dawm.8) — the suggestion resolves from the table
# ---------------------------------------------------------------------------
# Asserts PRESENCE, never text. extract_wire_suggestion reads the protocol
# position only (a suggestion buried in ``details`` deliberately does not
# satisfy it), and there is NO reconstructed fallback here: the generic
# 'the suggestion should contain "..."' step falls back to ctx["error"] when the
# wire suggestion is None, which would let a MISSING wire field pass a scenario
# whose entire claim is that the field is present.


@then("the wire error carries a non-empty suggestion")
def then_wire_suggestion_present(ctx: dict) -> None:
    """Assert the wire envelope carries a non-empty suggestion at the protocol position.

    The envelope is read through ``wire_error_envelope_or_none`` — the single
    guarded accessor for ``TransportResult.wire_error_envelope`` — rather than by
    reaching for the attribute here. The tolerant accessor, not ``wire_error_dict``,
    because this step owns the no-wire diagnosis below: its message names why a
    no-wire run cannot satisfy a scenario whose whole claim is about a WIRE field,
    which the accessor's generic guard would not say.
    """
    from tests.harness.transport import extract_wire_suggestion

    envelope = wire_error_envelope_or_none(ctx)
    assert envelope is not None, (
        "No wire error envelope captured — this scenario grades a WIRE field, so a "
        "no-wire (IMPL) run cannot satisfy it. Wire the env rather than relaxing this."
    )
    suggestion = extract_wire_suggestion(envelope)
    assert suggestion is not None, (
        f"Wire envelope carries no suggestion at the protocol position: {envelope!r}. "
        "Before salesagent-3dawm.8 this bare raise site emitted no suggestion at all; "
        "the field resolving from CODE_TABLE is the obligation under test."
    )
    assert suggestion.strip() != "", f"Wire suggestion is present but empty: {suggestion!r}"
