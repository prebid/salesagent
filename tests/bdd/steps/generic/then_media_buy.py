"""Then steps for create_media_buy response assertions.

THIS MODULE IS NOT REGISTERED in tests/bdd/conftest.py's ``pytest_plugins``, so none of
these definitions is reachable: pytest-bdd resolves a step to a FIXTURE, and a module that
is neither a registered plugin nor imported by a test module contributes none. Registering
it was measured and rejected -- it steals 127 already-bound step instances across 5 modules
(salesagent-uokuq). Its remaining steps are candidates for PROMOTION into a registered
module when a scenario needs them, one at a time, which is how the two UC-002 package Thens
left here (see salesagent-9p7oe.4).

Eleven patterns across seven functions have been removed rather than left: five functions
whose six patterns were SHADOWED by a registered module (so they were dead twice over --
unregistered and outranked), plus the two UC-002 package Thens that moved. A shadowed copy
is worse than an absent one: it reads as the definition when you grep, and the two bodies
drift apart silently -- 'the response should include a "{field}"' had already diverged from
its live twin.

Asserts on the dispatch payload (CreateMediaBuyResult or CreateMediaBuySuccess)
and ``ctx["error"]`` (AdCPSalesAgentError or CreateMediaBuyError).
"""

from __future__ import annotations

from pytest_bdd import parsers, then

from tests.bdd.steps._harness_db import db_session as _db_session

# RE-EXPORTED, therefore LIVE despite this module being unregistered. then_success.py
# imports this function and re-declares it under its own @then, and `from ... import name`
# moves the FUNCTION, never the fixture -- so the registration that counts is the one over
# there. Its own @then decorators are removed here: two registrations of one sentence, one
# of them unreachable, is the shadowing this file was just cleaned of.


def then_no_media_buy_persisted(ctx: dict) -> None:
    """Assert no new media buy was created in the database."""
    from sqlalchemy import func, select

    from src.core.database.models import MediaBuy

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with _db_session(ctx) as session:
        count = session.scalar(select(func.count()).select_from(MediaBuy).filter_by(tenant_id=tenant.tenant_id))
        # Allow existing media buys created by Given steps
        existing_count = 1 if ctx.get("existing_media_buy") else 0
        assert count == existing_count, f"Expected {existing_count} media buy(s) in DB, found {count}"


# ═══════════════════════════════════════════════════════════════════════
# Response success assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the pricing validation should pass")
def then_pricing_validation_passes(ctx: dict) -> None:
    """Assert pricing validation passed — no error, response has media_buy_id."""
    assert "error" not in ctx, f"Expected pricing validation to pass but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "Expected media_buy_id in response — pricing validation passed but no media buy created"


@then("the date validation should pass")
def then_date_validation_passes(ctx: dict) -> None:
    """Assert date validation passed — no error, response has media_buy_id."""
    assert "error" not in ctx, f"Expected date validation to pass but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "Expected media_buy_id in response — date validation passed but no media buy created"


@then(parsers.parse('the response should include "{field}" matching "{value}"'))
def then_response_field_matches(ctx: dict, field: str, value: str) -> None:
    """Assert response field matches the expected value."""
    resp = require_payload(ctx)
    actual = _get_response_field(resp, field)
    assert str(actual) == value, f"Expected {field}='{value}', got '{actual}'"


# "the response should include packages with allocations" and "each package should include
# product_id, budget, and pricing details" are NOT defined here any more. They moved to
# tests/bdd/steps/domain/uc002_create_media_buy.py, which is REGISTERED in pytest_plugins --
# this module is not, so both definitions were unreachable and the only scenario declaring
# either sentence never graded it. Both were also existence checks on result.payload; the
# replacements compare values on the wire against the request. Registering this module
# instead was measured and rejected: it steals 127 already-bound step instances across 5
# modules (salesagent-uokuq).


# ═══════════════════════════════════════════════════════════════════════
# Approval workflow assertions (BR-RULE-017)
# ═══════════════════════════════════════════════════════════════════════


@then("the approval path should be auto-approved")
def then_approval_auto(ctx: dict) -> None:
    """Assert the media buy was auto-approved — no manual approval step required.

    Auto-approval means:
    1. status='completed' (full pipeline ran synchronously)
    2. No workflow_step_id on response packages (no pending approval task created)

    This distinguishes auto-approved from manually-approved-then-completed,
    where a workflow step would have been created even if it was later resolved.
    """
    resp = require_payload(ctx)
    status = _get_response_field(resp, "status")
    assert status == "completed", (
        f"Expected auto-approval (status='completed' per BR-RULE-017), got '{status}'. "
        "Auto-approved media buys complete the full pipeline synchronously."
    )
    # Auto-approved means no manual approval workflow step was created.
    # Check that no package carries a workflow_step_id (which would indicate
    # a pending approval task was created, even if it was later completed).
    inner = getattr(resp, "response", resp)
    pkgs = getattr(inner, "packages", None) or []
    for pkg in pkgs:
        wf_step_id = getattr(pkg, "workflow_step_id", None)
        assert wf_step_id is None, (
            f"Package {getattr(pkg, 'package_id', '?')} has workflow_step_id={wf_step_id} — "
            "auto-approved media buys should not create approval workflow steps"
        )


@then("the media buy should proceed to adapter execution")
def then_adapter_executed(ctx: dict) -> None:
    """Assert the adapter executed -- outcome check (DB state) + mock bonus for in-process."""
    from tests.bdd.steps._outcome_helpers import assert_adapter_executed, is_e2e

    # Primary: outcome assertion works in ALL transports including E2E
    assert_adapter_executed(ctx)

    # Bonus: mock call count for in-process transports (fast, precise)
    if not is_e2e(ctx):
        env = ctx["env"]
        adapter_mock = env.mock["adapter"].return_value
        assert adapter_mock.create_media_buy.call_count == 1, (
            f"Expected adapter.create_media_buy to be called exactly once (auto-approval path), "
            f"but it was called {adapter_mock.create_media_buy.call_count} time(s)"
        )
        call_args = adapter_mock.create_media_buy.call_args
        assert call_args is not None, "adapter.create_media_buy was called but call_args is None"
        assert len(call_args.args) > 0 or len(call_args.kwargs) > 0, (
            "adapter.create_media_buy was called with no arguments"
        )


@then("the approval path should be manual")
def then_approval_manual(ctx: dict) -> None:
    """Assert the response indicates manual approval (task status 'submitted').

    Production: manual approval -> DB status=pending_approval, task status=submitted.
    E2E: Docker mock adapter auto-approves, so status may be 'completed' or 'active'.
    """
    from tests.bdd.steps._outcome_helpers import is_e2e

    resp = require_payload(ctx)
    status = _get_response_field(resp, "status")
    if is_e2e(ctx):
        # E2E Docker mock adapter auto-approves -- allow terminal statuses
        e2e_valid = ("submitted", "completed", "active", "pending_approval")
        assert status in e2e_valid, f"Expected manual approval status (one of {e2e_valid} in E2E), got '{status}'"
    else:
        assert status == "submitted", f"Expected manual approval (status='submitted'), got '{status}'"


@then("the media buy should enter pending state")
def then_pending_state(ctx: dict) -> None:
    """Assert the media buy was persisted with status 'pending_approval' in DB.

    E2E: Docker mock adapter auto-approves, so status may advance past pending.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy
    from tests.bdd.steps._outcome_helpers import is_e2e

    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    with _db_session(ctx) as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
        if is_e2e(ctx):
            # E2E Docker mock adapter auto-approves -- status may have advanced
            valid = ("pending_approval", "active", "completed", "submitted")
            assert mb.status in valid, f"Expected DB status in {valid} (E2E), got '{mb.status}'"
        else:
            assert mb.status == "pending_approval", f"Expected DB status 'pending_approval', got '{mb.status}'"


# ═══════════════════════════════════════════════════════════════════════
# Status and workflow assertions
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Notification assertions
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Persistence assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the media buy record should be persisted in the database")
@then("the media buy record should be persisted")
def then_media_buy_persisted(ctx: dict) -> None:
    """Assert a media buy was persisted in the database with correct field values."""
    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response to verify persistence"

    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    with _db_session(ctx) as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in database"
        # Verify key field values are populated (not just existence)
        tenant = ctx.get("tenant")
        assert tenant is not None, "No tenant in ctx — cannot verify tenant_id on persisted media buy"
        assert mb.tenant_id == tenant.tenant_id, f"Expected tenant_id '{tenant.tenant_id}', got '{mb.tenant_id}'"
        assert mb.status is not None, f"Media buy {media_buy_id} persisted with no status"
        # Verify principal linkage
        principal = ctx.get("principal")
        if principal is not None:
            assert mb.principal_id is not None, (
                f"Media buy {media_buy_id} persisted without principal_id — "
                "step claims record is 'persisted' but identity linkage is missing"
            )


@then(parsers.parse('the media buy record should be persisted with status "{status}"'))
def then_media_buy_persisted_with_status(ctx: dict, status: str) -> None:
    """Assert media buy is persisted with expected status."""
    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    with _db_session(ctx) as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found"
        assert mb.status == status, f"Expected status '{status}', got '{mb.status}'"


@then("the package records should be persisted")
def then_package_records_persisted(ctx: dict) -> None:
    """Assert media buy packages were persisted in the database with correct count."""
    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response to verify package persistence"

    from sqlalchemy import func, select

    from src.core.database.models import MediaPackage

    with _db_session(ctx) as session:
        count = session.scalar(select(func.count()).select_from(MediaPackage).filter_by(media_buy_id=media_buy_id))
        assert count and count > 0, f"No package records found for media buy {media_buy_id}"
        # Verify count matches the number of packages in the request
        request_kwargs = ctx.get("request_kwargs", {})
        expected_count = len(request_kwargs.get("packages", []))
        if expected_count > 0:
            assert count == expected_count, (
                f"Expected {expected_count} package record(s) for media buy {media_buy_id}, found {count}"
            )


@then("no package records should be persisted")
def then_no_package_records_persisted(ctx: dict) -> None:
    """Assert no package records were created for the tenant."""
    from sqlalchemy import func, select

    from src.core.database.models import MediaBuy, MediaPackage

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with _db_session(ctx) as session:
        count = session.scalar(
            select(func.count())
            .select_from(MediaPackage)
            .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
            .filter(MediaBuy.tenant_id == tenant.tenant_id)
        )
        # Allow existing packages created by Given steps
        existing_count = 0
        if ctx.get("existing_media_buy"):
            existing_mb = ctx["existing_media_buy"]
            existing_count = len(getattr(existing_mb, "packages", []) or [])
        assert count == existing_count, f"Expected {existing_count} package record(s) in DB, found {count}"


@then(parsers.parse("the package budget should be persisted as {budget:d}"))
def then_package_budget_persisted(ctx: dict, budget: int) -> None:
    """Assert the package budget was persisted in the database with the expected value.

    Queries the real DB for the package referenced in the update request and
    verifies its budget matches the expected value. This checks actual persistence,
    not just the response payload.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaPackage

    # Determine package_id from the update request or existing package
    update_kwargs = ctx.get("update_kwargs", {})
    packages = update_kwargs.get("packages", [])
    if packages:
        package_id = packages[0].get("package_id")
    else:
        pkg = ctx.get("existing_package")
        assert pkg is not None, "No package in update request or ctx — cannot verify budget"
        package_id = pkg.package_id

    assert package_id, "No package_id found to verify budget persistence"

    with _db_session(ctx) as session:
        db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=package_id)).first()
        assert db_pkg is not None, f"Package {package_id} not found in DB"
        assert db_pkg.budget == budget, (
            f"Expected package budget {budget}, got {db_pkg.budget} — "
            "BR-RULE-020 INV-1: adapter success should persist changes"
        )


@then("the creative assignment records should be persisted")
def then_creative_assignment_records_persisted(ctx: dict) -> None:
    """Assert creative assignment records were persisted in the database.

    Verifies: (1) records exist, and (2) count matches the total number of
    creative_ids across all packages in the request.
    If no creative_ids were requested, this step passes (no assignments expected).
    """
    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    expected_ids = ctx.get("expected_creative_ids")
    assert expected_ids, (
        "No expected_creative_ids in ctx — Given step must register "
        "ctx['expected_creative_ids'] for Then steps to verify assignment"
    )

    from src.core.database.repositories.creative import CreativeAssignmentRepository

    with _db_session(ctx) as session:
        repo = CreativeAssignmentRepository(session, ctx["tenant"].tenant_id)
        assignments = repo.get_by_media_buy(str(media_buy_id))
        actual_ids = {a.creative_id for a in assignments}
        missing = expected_ids - actual_ids
        assert not missing, (
            f"Expected creatives {sorted(expected_ids)} persisted as assignments "
            f"for media buy {media_buy_id}, but missing {sorted(missing)}. "
            f"Actual: {sorted(actual_ids)}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Response field rejection
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the response should include "rejection_reason" containing "{text}"'))
def then_rejection_reason_contains(ctx: dict, text: str) -> None:
    """Assert rejection_reason field contains expected text."""
    resp = payload_or_none(ctx)
    if resp is None:
        resp = ctx.get("existing_media_buy")
    assert resp is not None, "No response or media buy to check"
    reason = _get_response_field(resp, "rejection_reason") or ""
    assert text.lower() in reason.lower(), f"Expected '{text}' in rejection_reason: {reason}"


# ═══════════════════════════════════════════════════════════════════════
# ASAP start_time resolution assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the system should resolve start_time to current UTC")
def then_start_time_resolved_to_utc(ctx: dict) -> None:
    """Assert the persisted media buy has start_time close to now (ASAP resolved)."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    with _db_session(ctx) as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
        assert mb.start_time is not None, "start_time not set on persisted media buy"
        now = datetime.now(UTC)
        delta = abs((mb.start_time - now).total_seconds())
        assert delta < 30, f"start_time {mb.start_time} is {delta}s from now — expected within 30s for ASAP"


@then("the campaign should be immediately activating")
def then_campaign_immediately_activating(ctx: dict) -> None:
    """Assert the campaign is immediately activating: auto-approved AND start_time near now.

    "Immediately activating" means:
    1. Task status is "completed" (workflow succeeded, not stuck in manual approval)
    2. DB media buy status is NOT "pending_approval" (bypassed manual approval)
    3. start_time is near-now (ASAP was resolved to current UTC)
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    resp = require_payload(ctx)
    # Task status "completed" means the create_media_buy workflow step finished
    # successfully — the adapter was called (not held for manual approval).
    # "submitted" would indicate manual approval pending.
    status = _get_response_field(resp, "status")
    assert status == "completed", (
        f"Expected task status 'completed' for immediate activation (got '{status}'). "
        f"'submitted' would mean manual approval is pending; 'failed' means adapter error."
    )
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"
    with _db_session(ctx) as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
        # DB status must NOT be "pending_approval" — that means manual approval was required,
        # contradicting "immediately activating"
        assert mb.status != "pending_approval", (
            f"Media buy {media_buy_id} has DB status 'pending_approval' — "
            f"campaign is waiting for manual approval, not 'immediately activating'"
        )
        # start_time must be near-now (ASAP resolved to current UTC)
        assert mb.start_time is not None, "start_time not set — campaign cannot be 'immediately activating'"
        now = datetime.now(UTC)
        delta = abs((mb.start_time - now).total_seconds())
        assert delta < 60, (
            f"start_time {mb.start_time} is {delta}s from now — expected within 60s for 'immediately activating'"
        )


@then('the response should include resolved start_time (not literal "asap")')
def then_response_includes_resolved_start_time(ctx: dict) -> None:
    """Assert the response contains a resolved start_time, not the literal 'asap'.

    Checks the response at two levels:
      1. Top-level start_time on the response object.
      2. Package-level start_time on each package in the response.

    If found, verifies the value is a real datetime within 60s of now (proving
    'asap' was resolved to current UTC). No DB fallback — this step tests the
    response content only. If neither level has start_time, the assertion fails.
    """
    from datetime import UTC, datetime

    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    # Check top-level start_time on the response.
    resp_start_time = _get_response_field(resp, "start_time")
    if resp_start_time is not None:
        resp_str = str(resp_start_time)
        assert resp_str != "asap", "Response start_time is literal 'asap', not resolved"
        if isinstance(resp_start_time, str):
            parsed = datetime.fromisoformat(resp_start_time.replace("Z", "+00:00"))
            delta = abs((parsed - datetime.now(UTC)).total_seconds())
            assert delta < 60, (
                f"Response start_time {resp_start_time} is {delta:.1f}s from now — "
                "expected within 60s of current UTC for 'asap' resolution"
            )
        return

    # Check package-level start_time.
    inner = getattr(resp, "response", resp)
    pkgs = getattr(inner, "packages", None) or getattr(resp, "packages", None) or []
    for pkg in pkgs:
        pkg_start = getattr(pkg, "start_time", None)
        if pkg_start is not None and str(pkg_start) != "asap":
            if isinstance(pkg_start, str):
                parsed = datetime.fromisoformat(pkg_start.replace("Z", "+00:00"))
                delta = abs((parsed - datetime.now(UTC)).total_seconds())
                assert delta < 60, (
                    f"Package start_time {pkg_start} is {delta:.1f}s from now — "
                    "expected within 60s for 'asap' resolution"
                )
            return

    raise AssertionError(
        f"Response has no resolved start_time — checked top-level and {len(pkgs)} package(s). "
        "Step text claims 'response should include resolved start_time (not literal asap)'."
    )


# ═══════════════════════════════════════════════════════════════════════
# Atomic response shape assertions (BR-RULE-018)
# ═══════════════════════════════════════════════════════════════════════


@then("the response should have success fields")
def then_response_has_success_fields(ctx: dict) -> None:
    """Assert response contains success-only fields with valid values.

    Success fields for BR-RULE-018: media_buy_id (non-empty string),
    packages (non-empty list), and status (valid completion status).
    """
    resp = require_payload(ctx)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert isinstance(media_buy_id, str) and media_buy_id, (
        f"Expected non-empty string media_buy_id in success response, got: {media_buy_id!r}"
    )
    packages = _get_response_field(resp, "packages")
    assert isinstance(packages, list), f"Expected packages to be a list, got: {type(packages).__name__}"
    assert packages, "Expected at least one package in success response"
    # Verify each package has a product_id proving allocation
    for i, pkg in enumerate(packages):
        pkg_dict = pkg if isinstance(pkg, dict) else pkg.model_dump()
        assert pkg_dict.get("product_id"), f"Package {i} missing product_id in success response"
    status = _get_response_field(resp, "status")
    assert status is not None, "Expected status field in success response"
    valid_statuses = ("completed", "submitted", "pending_approval", "activating", "pending_start")
    assert status in valid_statuses, f"Expected valid success status (one of {valid_statuses}), got: {status!r}"


@then('the response should NOT have an "errors" field')
def then_response_no_errors_field(ctx: dict) -> None:
    """Assert the success response has no errors field on the wire.

    Step says 'NOT have an "errors" field' — the key must be absent, not merely an empty
    list. Asserted on the WIRE rather than by unwrapping the typed result: the old form
    hand-rolled a CreateMediaBuyResult unwrap plus a dict/object branch, both of which are
    shape-guessing that wire_absent does not need.
    """
    wire_absent(ctx, "errors")


@then('the response should have an "errors" array')
@then('the response should contain an "errors" array')
def then_response_has_errors_array(ctx: dict) -> None:
    """Assert the wire rejection carries a non-empty, conformant errors array.

    The helper asserts the array is non-empty and validates every entry against pinned
    ``core/error.json``, which subsumes the per-entry code-and-message checks the two
    hand-rolled versions of this step used to spell out.
    """
    ctx["result"].assert_wire_error_is_schema_conformant()


@then("the response should NOT have success fields (media_buy_id, packages)")
def then_response_no_success_fields(ctx: dict) -> None:
    """Assert the error response shape excludes ALL success-only fields.

    BR-RULE-018 INV-2: a validation failure must produce an error response with
    an errors array AND NO success-only domain fields. CreateMediaBuyError
    legitimately has {errors, context, ext}; everything else from
    CreateMediaBuySuccess (media_buy_id, buyer_campaign_ref, account,
    creative_deadline, packages, planned_delivery, sandbox, workflow_step_id)
    must be absent from the envelope the buyer received.

    Graded on that envelope. The previous version read success fields off a typed model
    the harness had rebuilt and then re-serialized it, so it proved the model and its
    serializer agreed with each other — which is true however wrong the wire is.
    """
    envelope = ctx["result"].error_envelope()

    # Success-only fields (CreateMediaBuySuccess minus CreateMediaBuyError's own).
    disallowed_fields = (
        "media_buy_id",
        "buyer_campaign_ref",
        "account",
        "creative_deadline",
        "packages",
        "planned_delivery",
        "sandbox",
        "workflow_step_id",
    )
    leaked = {field: envelope[field] for field in disallowed_fields if field in envelope}
    assert not leaked, (
        f"Error envelope leaked success-only fields: {leaked}. "
        f"Per BR-RULE-018 INV-2, an error response must carry errors only — "
        f"no media_buy_id, packages, or other success payload."
    )


@then('each error should include "suggestion" field')
def then_each_error_has_suggestion(ctx: dict) -> None:
    """Assert every error on the wire carries a non-empty suggestion.

    ``each`` is what separates this from the singular
    ``the error should include a "suggestion" field`` in then_error.py: that one grades
    ``errors[0]``, this one grades the whole array.
    """
    errors = ctx["result"].wire_error_objects()
    assert errors, "Expected a non-empty errors array on the wire"
    for index, err in enumerate(errors):
        assert err.get("suggestion"), f"errors[{index}] carries no 'suggestion': {err}"


def _retry_after_from_error_object(error_object: dict) -> tuple[object, str] | None:
    """Read ``retry_after`` off one wire error object, spec slot first.

    ``core/error.json`` @3.1.1 declares ``retry_after`` as a TOP-LEVEL member of
    the error object (``"type": "number"``, ``minimum: 1``, ``maximum: 3600``),
    and ``error-details/rate-limited.json`` enumerates limit / remaining /
    window_seconds / scope with NO ``retry_after`` member — so ``details`` is not
    the modelled home for the value.

    The ``details`` read is kept as a LEGACY fallback rather than deleted: at
    least one pinned test-kit (``dist/compliance/3.1.1/test-kits/
    rate-limit-trip-runner.yaml``) still reads ``error.details.retry_after``, and
    call sites that have not yet moved to the spec slot must fail this step on
    the VALUE, not on where they happened to put it.
    """
    if error_object.get("retry_after") is not None:
        return error_object["retry_after"], "top-level (core/error.json)"
    details = error_object.get("details") or {}
    if details.get("retry_after") is not None:
        return details["retry_after"], "details (legacy slot)"
    return None


def _retry_after_from_wire(result: object) -> tuple[object, str] | None:
    """Read ``retry_after`` off the wire error object of *result*.

    Takes the ``TransportResult``, not a bare envelope dict, and resolves the
    error object through ``TransportResult.wire_error_object()``. Two reasons,
    and the first is the one that makes this a correctness fix rather than a
    style change:

    * The reader is wire-only by construction. ``wire_error_object`` reads
      ``wire_error_envelope``, the field reserved for real wire bytes, so this
      extractor cannot be handed a harness-side reconstruction that happens to
      have the right keys. Accepting an untyped ``dict`` made "is this what the
      buyer received?" the CALLER's question, asked afresh at every call site.
    * WHERE the spec puts the per-error fields is the harness's business. The
      former body re-derived it — ``(envelope.get("errors") or [{}])[0]`` with an
      ``adcp_error`` fallback behind it — which is a second answer to the
      question ``locate_envelope_error`` already answers once, and the shape
      ``test_architecture_bdd_wire_discipline`` forbids in a step module.

    ``errors[0]`` only, dropping the former ``adcp_error`` mirror leg, for the
    reason ``then_error.py``'s ``_wire_error_object`` gives for the same drop:
    ``core/error.json`` @3.1.1 defines the per-error members on the payload-layer
    object, so the envelope-level mirror is the wrong region to grade them in.
    Nothing is lost — ``assert_envelope_shape`` pins ``retry_after`` on BOTH
    layers when a caller passes it, so a value that reaches only the mirror is an
    interop bug this step should not be papering over.

    The read WITHIN that object stays at the top level (with the legacy
    ``details`` slot behind it): see :func:`_retry_after_from_error_object`, which
    owns that choice and its citation.
    """
    error_object = result.wire_error_object() if result is not None else None
    if error_object is None:
        return None
    found = _retry_after_from_error_object(error_object)
    if found is None:
        return None
    value, slot = found
    return value, f"errors[0] {slot}"


def _retry_after_from_exception(error: object) -> tuple[object, str] | None:
    """Read ``retry_after`` off a reconstructed exception — the IMPL / no-wire path.

    ``AdCPSalesAgentError`` has a first-class ``retry_after`` attribute that serializes to
    the envelope's top level, so that attribute is read first here for the same
    reason the wire top level is read first above.
    """
    value = getattr(error, "retry_after", None)
    if value is not None:
        return value, "error.retry_after attribute"
    details = getattr(error, "details", None) or {}
    if isinstance(details, dict) and details.get("retry_after") is not None:
        return details["retry_after"], "error.details (legacy slot)"
    return None


@then('the error should include "retry_after" field')
def then_error_has_retry_after(ctx: dict) -> None:
    """Assert the WIRE envelope carries a positive retry_after hint.

    **Authority: the WIRE envelope.** Per ``tests/CLAUDE.md`` § "Error Verification
    Policy" the buyer-facing contract is the envelope, not a reconstructed
    exception, and per ``core/error.json`` @3.1.1 the field's home in that envelope
    is the error object's TOP LEVEL — ``retry_after`` is a member of the Error
    object itself, bounded [1, 3600]. ``details["retry_after"]`` is read only as a
    legacy fallback for call sites that still emit into the slot the spec does not
    model.

    The wire branch first asserts that an error code was actually captured, so a
    green pass cannot come from a dispatch that never reached a transport. The
    former implementation instead inspected a reconstructed ``AdCPSalesAgentError``
    and fell back to ``getattr(error, "retry_after", None)`` on anything else — a
    read that silently yields ``None`` for an object that is not the expected
    shape, which is indistinguishable from a wire that genuinely omitted the hint
    (salesagent-3dawm.18). The reconstructed exception survives ONLY as the IMPL /
    no-wire branch, which has no envelope by definition, and absence there still
    fails loudly.

    **DORMANT — this body executes nowhere today.** ``then_media_buy`` is not in
    ``tests/bdd/conftest.py``'s ``pytest_plugins``, so pytest-bdd never registers
    this step; the ten feature usages either xfail at the harness gate (BR-UC-002)
    or have no binding test module at all (BR-UC-016/020/022). Wiring the module
    is #2132's scope. Stated here so a reader does not take the wire-authority
    reasoning above as evidence that anything grades it.
    """
    from tests.bdd.steps._outcome_helpers import error_envelope_or_none

    envelope = error_envelope_or_none(ctx)
    if envelope is not None:
        code = ctx["result"].wire_error_code()
        assert code is not None, (
            "expected a wire rejection carrying retry_after, but the captured envelope has no "
            "errors[0].code — the operation either succeeded or errored before reaching a "
            f"transport: {envelope!r}"
        )
        found = _retry_after_from_wire(ctx["result"])
        assert found is not None, (
            f"expected a retry_after hint on the wire envelope for {code}, but it is absent from "
            f"both errors[0].retry_after and the legacy errors[0].details slot: {envelope!r}"
        )
    else:
        # No envelope: an MCP dispatch can fail with a ToolError that is genuinely not an
        # AdCP envelope, and then the raised error IS the product.
        error = ctx.get("error")
        assert error is not None, (
            "No error recorded in ctx (checked the result's error envelope and 'error') — "
            "step claims error should include retry_after but no error was captured"
        )
        found = _retry_after_from_exception(error)
        assert found is not None, (
            "Expected retry_after on the error, but it is absent from every slot. "
            f"No wire error envelope was captured. Reconstructed error: {error!r}"
        )

    retry_after, slot = found
    # retry_after should be a positive number (seconds to wait before retrying)
    assert isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool), (
        f"Expected retry_after to be a number (seconds), got {type(retry_after).__name__}: "
        f"{retry_after!r} (read from {slot})"
    )
    assert retry_after > 0, f"Expected positive retry_after value, got {retry_after} (read from {slot})"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

# Single source of truth lives in _outcome_helpers; re-exported for backward compat.
from tests.bdd.steps._outcome_helpers import _get_response_field as _get_response_field  # noqa: F811, PLC0414
from tests.bdd.steps._outcome_helpers import payload_or_none, require_payload, wire_absent
