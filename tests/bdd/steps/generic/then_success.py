"""Then steps for success assertions (response status, fields, sandbox).

These steps assert on the dispatch payload (``require_payload``), which is a real response
object from production code (any use case).
"""

from __future__ import annotations

from pytest_bdd import parsers, then

from src.core.helpers import enum_value
from tests.bdd.steps._outcome_helpers import require_payload
from tests.bdd.steps.generic.then_media_buy import then_no_media_buy_persisted as _then_no_media_buy_persisted


# ── No-persistence assertion (all-or-nothing failure) ────────────────────
# then_media_buy.py is a helper module that is not registered as a pytest-bdd
# plugin (its other steps duplicate domain-file steps). Re-expose only the
# no-persistence assertion here, in a registered module, delegating to the
# single implementation (DRY) so adapter-failure scenarios (UC-002 ext-j) can
# assert the all-or-nothing rollback on the wire.
@then("no media buy record should be persisted in the database")
@then("no media buy record should be persisted")
def then_no_media_buy_record_persisted(ctx: dict) -> None:
    """Assert no new media buy was persisted (delegates to then_media_buy)."""
    _then_no_media_buy_persisted(ctx)


# ── Response status ──────────────────────────────────────────────────


# Success collections that prove a status-less response is genuinely
# "completed" — at least one must be present and a list, not None.
_STATUSLESS_SUCCESS_ATTRS: tuple[str, ...] = (
    "formats",  # ListCreativeFormatsResponse
    "media_buy_deliveries",  # GetMediaBuyDeliveryResponse
    "aggregated_totals",  # GetMediaBuyDeliveryResponse
)


@then(parsers.parse('the response status should be "{status}"'))
def then_response_status(ctx: dict, status: str) -> None:
    """Assert the operation completed with expected status.

    Works across use cases:
    - UC-004 (GetMediaBuyDeliveryResponse): has explicit ``status`` field —
      assert it equals the expected value directly.
    - UC-005 (ListCreativeFormatsResponse): no ``status`` field. Such
      response types can only represent the *completed* state, so
      "completed" is proven by (a) no error recorded for the operation and
      (b) the schema-required success payload being present. Any requested
      status other than "completed" against a status-less response fails.
    """
    resp = require_payload(ctx)

    # Determine if response type declares a ``status`` field via Pydantic metadata.
    # Uses getattr on the class (not instance) to handle non-Pydantic test doubles.
    resp_fields = getattr(type(resp), "model_fields", {})
    if "status" in resp_fields:
        # SDK 5.7: status may be a non-StrEnum; enum_value normalizes to str.
        actual_str = enum_value(resp.status)
        assert actual_str == status, f"Expected status '{status}', got '{actual_str}'"
        return

    # Status-less response: only the completed/success state is representable.
    if status != "completed":
        raise AssertionError(
            f"Status '{status}' requested but response {type(resp).__name__} "
            f"has no status field — status-less responses can only be 'completed'"
        )

    # "completed" must be proven, not assumed from a non-None object.
    error = ctx.get("error")
    assert error is None, f"Status 'completed' claimed but the operation recorded an error: {error!r}"

    # Verify at least one schema-required success collection is present and populated.
    found_count = 0
    for attr in _STATUSLESS_SUCCESS_ATTRS:
        if attr not in resp_fields:
            continue
        found_count += 1
        value = getattr(resp, attr)
        assert value is not None, (
            f"Status 'completed' claimed but response.{attr} is None — the schema-required success payload is missing"
        )
        if attr in ("formats", "media_buy_deliveries"):
            assert isinstance(value, list), (
                f"Status 'completed' claimed but response.{attr} is {type(value).__name__}, expected a list"
            )
    assert found_count >= 1, (
        f"Status-less response {type(resp).__name__} exposes none of the "
        f"expected success collections {_STATUSLESS_SUCCESS_ATTRS} — cannot "
        f"prove the operation completed successfully"
    )


# ── Response contains field ──────────────────────────────────────────


@then(parsers.parse('the response should contain "{field}" array'))
def then_response_contains_array(ctx: dict, field: str) -> None:
    """Assert response contains a field that is an array (list)."""
    resp = require_payload(ctx)
    value = getattr(resp, field, None)
    assert value is not None, f"Expected '{field}' in response, got attrs: {dir(resp)}"
    assert isinstance(value, list), f"Expected '{field}' to be a list, got {type(value)}"


# ── Sandbox flag assertions ──────────────────────────────────────────


@then("the response should include sandbox equals true")
def then_sandbox_true(ctx: dict) -> None:
    """Assert response includes sandbox: true."""
    resp = require_payload(ctx)
    assert getattr(resp, "sandbox", None) is True, f"Expected sandbox=True, got {getattr(resp, 'sandbox', None)}"


@then("the response should not include a sandbox field")
def then_no_sandbox_field(ctx: dict) -> None:
    """Assert serialized response does not contain a sandbox field.

    Checks model_dump() (what API consumers see), not the Python attribute.
    A field present with value None still serializes as ``{"sandbox": null}``
    which counts as "including a sandbox field".
    """
    resp = require_payload(ctx)
    dumped = resp.model_dump()
    assert "sandbox" not in dumped, (
        f"Expected no sandbox field in serialized response, got sandbox={dumped.get('sandbox')}"
    )


# ── Natural-key sandbox resolution (UC-002 sandbox-natural-key) ───────


@then("the reference should resolve to the sandbox account for that brand and operator")
def then_natural_key_resolves_to_sandbox(ctx: dict) -> None:
    """Assert the natural-key reference resolved to a sandbox account on success.

    BR-RULE-209 INV-8: a brand+operator+sandbox:true reference resolves to the
    sandbox account WITHOUT prior provisioning. The create must succeed (no
    error) and the response must carry the resolved media buy.
    """
    assert "error" not in ctx, (
        f"Expected the natural-key reference to resolve to a sandbox account, but the create failed: "
        f"{ctx.get('error')!r}"
    )
    resp = require_payload(ctx)
    media_buy_id = getattr(resp, "media_buy_id", None)
    assert media_buy_id, f"Expected a media_buy_id from the sandbox-account create, got {media_buy_id!r}"


# then_no_real_orders_created is DELETED, and its two scenario lines in
# BR-UC-002-create-media-buy.feature are collapsed onto "the response should include
# sandbox equals true", which both scenarios already carried on the line above.
#
# It was a character-for-character duplicate of then_sandbox_true: require_payload(ctx)
# followed by ``assert getattr(resp, "sandbox", None) is True``, differing only in its
# Gherkin sentence and its error message. The duplicate-step guard
# (tests/unit/test_architecture_bdd_no_duplicate_steps.py) did not catch it because it
# fires at THREE identical bodies, so a pair is invisible to it.
#
# Nothing that was observed stops being observed. Its two binders,
# @T-UC-002-sandbox-happy and @T-UC-002-sandbox-natural-key, are xfailed on every
# transport in the box run, so the collapse changes no grading -- and on the day they
# graduate, the sandbox flag they assert is graded by the step that owns it.
