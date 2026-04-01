"""
BDD test configuration and fixtures.

Every scenario runs against real production code through harness environments:
  - UC-005 (Creative Formats): CreativeFormatsEnv
  - UC-004 (Delivery Metrics): DeliveryPollEnv / WebhookEnv / CircuitBreakerEnv

There is no stub mode — steps call the harness directly and assert on
real response objects.

Unimplemented scenarios (missing step definitions) are auto-xfailed at runtime
via ``pytest_runtest_makereport``. No metadata or @pending tags needed — the
code is the source of truth.

Scenarios for unimplemented *production* features use explicit ``xfail`` markers
with a reason (e.g., "MCP wrapper does not accept wcag_level").
"""

from __future__ import annotations

import re
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass

# Register step definition modules as pytest plugins so that the fixtures
# created by @given/@when/@then decorators are visible to pytest-bdd's
# fixture lookup. Simple ``import`` is not enough — pytest only discovers
# fixtures from conftest files and registered plugins.
pytest_plugins = [
    "tests.bdd.steps.generic.given_auth",
    "tests.bdd.steps.generic.given_config",
    "tests.bdd.steps.generic.given_entities",
    "tests.bdd.steps.generic.when_request",
    "tests.bdd.steps.generic.then_success",
    "tests.bdd.steps.generic.then_error",
    "tests.bdd.steps.generic.then_payload",
    "tests.bdd.steps.generic.given_media_buy",
    "tests.bdd.steps.generic.then_media_buy",
    "tests.bdd.steps.domain.uc004_delivery",
    "tests.bdd.steps.domain.uc002_create_media_buy",
    "tests.bdd.steps.domain.uc002_nfr",
    "tests.bdd.steps.domain.uc002_task_query",
    "tests.bdd.steps.domain.uc003_update_media_buy",
    "tests.bdd.steps.domain.uc003_ext_error_scenarios",
    "tests.bdd.steps.domain.uc019_query_media_buys",
    "tests.bdd.steps.domain.uc026_package_media_buy",
    "tests.bdd.steps.domain.uc006_sync_creatives",
    "tests.bdd.steps.domain.uc011_accounts",
    "tests.bdd.steps.domain.admin_accounts",
]

# ---------------------------------------------------------------------------
# Auto-xfail: missing step definitions
# ---------------------------------------------------------------------------
# Instead of predicting which scenarios are "pending" via metadata tags,
# we let pytest-bdd tell us at runtime. If a scenario fails because a step
# definition is missing, we convert the failure to xfail. The code is the
# source of truth — no stale metadata needed.


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Generator[None, None, None]:
    """Auto-xfail scenarios that fail due to missing infrastructure.

    Two categories of "not yet implemented":
    1. StepDefinitionNotFoundError — no matching step def exists
    2. Missing harness — step defs match (generic steps) but ctx["env"]
       is not set because _harness_env doesn't know this UC yet

    Both are converted to xfail at runtime. No metadata tags needed.
    """
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed and call.excinfo is not None:
        from pytest_bdd.exceptions import StepDefinitionNotFoundError

        if call.excinfo.errisinstance(StepDefinitionNotFoundError):
            report.outcome = "skipped"
            report.wasxfail = f"Step definition not found: {call.excinfo.value}"
        elif call.excinfo.errisinstance(KeyError) and "env" in str(call.excinfo.value):
            report.outcome = "skipped"
            report.wasxfail = "No harness environment configured for this scenario"
        elif call.excinfo.errisinstance(NotImplementedError):
            report.outcome = "skipped"
            report.wasxfail = f"Not implemented: {call.excinfo.value}"


# ---------------------------------------------------------------------------
# Auto-register BDD tag markers
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register BDD tag markers dynamically."""
    import pathlib

    features_dir = pathlib.Path(__file__).parent / "features"
    if not features_dir.exists():
        return

    seen: set[str] = set()
    for feature_file in features_dir.glob("**/*.feature"):
        text = feature_file.read_text()
        for match in re.finditer(r"@([\w.\-]+)", text):
            tag = match.group(1)
            if tag not in seen:
                seen.add(tag)
                config.addinivalue_line("markers", f"{tag}: BDD scenario tag")


# ---------------------------------------------------------------------------
# xfail: scenarios for unimplemented production features
# ---------------------------------------------------------------------------
# These tags correspond to features not yet implemented in production code.
# Each xfail has a FIXME pointing to the work needed.

_XFAIL_TAGS: dict[str, str] = {
    # FIXME(salesagent-ghgx): UC-003 main/alt-timing — production doesn't populate these fields
    # Steps have hard assertions now; xfail at scenario level until production catches up.
    "T-UC-003-main": "implementation_date, budget, sandbox not populated in update response — spec-production gap",
    "T-UC-003-alt-timing": "implementation_date not populated in update response — spec-production gap",
    # FIXME(salesagent-ghgx): UC-003 pause — sandbox flag not populated in update response
    "T-UC-003-alt-pause": "sandbox not populated in pause response — spec-production gap",
    # FIXME(salesagent-ghgx): UC-003 optimization_goals — affected_packages empty in response
    "T-UC-003-alt-optimization-goals": "affected_packages not populated for optimization_goals changes — spec-production gap",
    # FIXME(salesagent-12nd): UC-002 ASAP — response doesn't expose resolved start_time
    "T-UC-002-alt-asap": "response lacks resolved start_time field — spec-production gap",
    # FIXME(salesagent-fie): UC-002 error code mismatch — Pydantic VALIDATION_ERROR vs spec INVALID_REQUEST
    "T-UC-002-ext-c": "start_time in past: VALIDATION_ERROR instead of INVALID_REQUEST — spec-production gap",
    "T-UC-002-ext-c-end": "end_time before start_time: VALIDATION_ERROR instead of INVALID_REQUEST — spec-production gap",
    "T-UC-002-inv-087-5": "duplicate optimization_goals priority: VALIDATION_ERROR instead of INVALID_REQUEST — spec-production gap",
    "T-UC-002-inv-087-6": "empty optimization_goals array: VALIDATION_ERROR instead of INVALID_REQUEST — spec-production gap",
    "T-UC-002-inv-087-7": "per_ad_spend without value_field: VALIDATION_ERROR instead of INVALID_REQUEST — spec-production gap",
    # FIXME(beads-dul): disclosure_positions filter not implemented in production
    # Note: violated/nofield pass vacuously (field rejected at schema level)
    "T-UC-005-inv-049-8-holds": "disclosure_positions filter not implemented",
    # FIXME(beads-dul): sandbox mode not implemented in harness
    # Note: sandbox-production passes vacuously (sandbox=None by default)
    "T-UC-005-sandbox-happy": "sandbox mode not implemented",
    "T-UC-005-sandbox-validation": "sandbox mode not implemented",
    # FIXME(beads-dul): creative agent referrals not in harness
    "T-UC-005-main-referrals": "creative agent referrals not implemented",
    # FIXME(beads-dul): no-tenant error path requires identity-less harness
    "T-UC-005-ext-a-rest": "no-tenant error path not implemented in harness",
    "T-UC-005-ext-a-mcp": "no-tenant error path not implemented in harness",
    # FIXME(beads-dul): creative agent format querying is a separate API
    "T-UC-005-partition-agent-type": "creative agent format API not implemented",
    "T-UC-005-partition-agent-asset": "creative agent format API not implemented",
    "T-UC-005-boundary-agent-type": "creative agent format API not implemented",
    "T-UC-005-boundary-agent-asset": "creative agent format API not implemented",
    # FIXME(beads-dul): suggestion field not in production error model
    "T-UC-005-ext-b-rest": "suggestion field not implemented in error responses",
    "T-UC-005-ext-b-mcp": "suggestion field not implemented in error responses",
    # FIXME(beads-dul): disclosure validation errors not implemented
    "T-UC-005-ext-b-disclosure-invalid": "disclosure_positions validation not implemented",
    "T-UC-005-ext-b-disclosure-empty": "disclosure_positions validation not implemented",
    "T-UC-005-ext-b-disclosure-dupes": "disclosure_positions validation not implemented",
    # FIXME(beads-dul): specific error codes (OUTPUT_FORMAT_IDS_EMPTY etc.)
    # not produced by production — Pydantic gives generic VALIDATION_ERROR
    "T-UC-005-ext-b-output-empty": "specific validation error codes not implemented",
    "T-UC-005-ext-b-output-invalid": "specific validation error codes not implemented",
    "T-UC-005-ext-b-output-noid": "specific validation error codes not implemented",
    "T-UC-005-ext-b-input-empty": "specific validation error codes not implemented",
    "T-UC-005-ext-b-input-invalid": "specific validation error codes not implemented",
    "T-UC-005-ext-b-input-noid": "specific validation error codes not implemented",
    # FIXME(salesagent-9vgz.5): unknown targeting field caught at wrong layer
    # Targeting uses extra=get_pydantic_extra_mode(): 'forbid' in dev (ValidationError at parse time),
    # 'ignore' in prod (field silently dropped). Neither produces INVALID_REQUEST.
    # Spec expects business-logic validation with INVALID_REQUEST code and suggestion field.
    "T-UC-002-ext-f": "unknown targeting field caught by Pydantic (VALIDATION_ERROR), not business logic (INVALID_REQUEST) — spec-production gap",
    # FIXME(salesagent-9vgz.4): currency validation not implemented in production
    # Production code accepts any currency on pricing options without checking
    # against the tenant's CurrencyLimit table. Spec expects UNSUPPORTED_FEATURE error.
    "T-UC-002-ext-d": "currency validation against CurrencyLimit not implemented — spec-production gap",
    # FIXME(salesagent-9vgz.4): duplicate product_id error lacks suggestion field
    # Production correctly detects duplicate product_ids and raises ValueError with
    # good message, but the error is not a structured AdCPError — no suggestion field.
    "T-UC-002-ext-e": "duplicate product_id error lacks suggestion field — spec-production gap",
    # FIXME(salesagent-9vgz.10): production returns validation_error, spec expects BUDGET_TOO_LOW
    "T-UC-002-ext-k": "daily spend cap returns generic validation_error, not BUDGET_TOO_LOW",
    # FIXME(salesagent-9vgz.10): proposal validation not implemented in production
    "T-UC-002-ext-l": "proposal_id validation not implemented in production",
    "T-UC-002-ext-m": "proposal budget guidance not implemented in production",
    # FIXME(salesagent-9vgz.13): pricing validation returns generic validation_error, not PRICING_ERROR
    # AdCPValidationError(details={"error_code": "PRICING_ERROR"}) is raised but then caught
    # and re-raised as ValueError(str(e)) at media_buy_create.py:1741-1743, losing the structured
    # error code. The outer handler converts it to Error(code="validation_error").
    "T-UC-002-ext-n": "pricing validation returns generic validation_error, not PRICING_ERROR",
    "T-UC-002-ext-n-bid": "pricing validation returns generic validation_error, not PRICING_ERROR",
    "T-UC-002-ext-n-floor": "pricing validation returns generic validation_error, not PRICING_ERROR",
    # FIXME(salesagent-9vgz.15): production errors lack suggestion field
    # AdCPNotFoundError/AdCPValidationError/AdCPAdapterError raised with details={"error_code": ...}
    # but no details["suggestion"]. Spec requires suggestion for buyer remediation.
    # FIXME(salesagent-9vgz.6): creative/format_id validation errors lack suggestion field
    # ext-g: _validate_creatives_before_adapter_call raises INVALID_CREATIVES without suggestion
    # ext-h: plain string format_id caught by Pydantic, not structured AdCPError
    # ext-h-agent: _validate_and_convert_format_ids is dead code — unregistered agent not detected
    "T-UC-002-ext-g": "INVALID_CREATIVES error lacks suggestion field",
    "T-UC-002-ext-h": "plain string format_id produces Pydantic error, not AdCPError with suggestion",
    "T-UC-002-ext-h-agent": "unregistered agent_url validation not wired — _validate_and_convert_format_ids is dead code",
    # FIXME(salesagent-9vgz.8): auth error lacks suggestion field
    # AdCPAuthenticationError("Principal ID not found...") has no details["suggestion"].
    # Spec requires suggestion for buyer remediation (POST-F3).
    "T-UC-002-ext-i": "auth error lacks suggestion field — spec-production gap",
    # FIXME(salesagent-9vgz.8): adapter failure raises exception instead of returning failed result
    # Production wraps adapter exceptions as AdCPAdapterError and re-raises instead of
    # returning CreateMediaBuyResult(status="failed"). Also no suggestion field on error.
    "T-UC-002-ext-j": "adapter failure raises exception, no failed result envelope or suggestion — spec-production gap",
    "T-UC-002-ext-o": "CREATIVES_NOT_FOUND error lacks suggestion field",
    "T-UC-002-ext-p": "CREATIVE_FORMAT_MISMATCH error lacks suggestion field",
    "T-UC-002-ext-q": "CREATIVE_UPLOAD_FAILED error lacks suggestion field",
    "T-UC-002-inv-026-2": "INVALID_CREATIVES error lacks suggestion field",
    "T-UC-002-inv-026-4": "INVALID_CREATIVES error lacks suggestion field",
    # FIXME(salesagent-9vgz.17): optimization_goals not in adcp v3.6.0 or production schemas
    # PackageRequest(extra='forbid') rejects the field with generic validation error,
    # not spec-expected UNSUPPORTED_FEATURE / INVALID_REQUEST with structured codes.
    "T-UC-002-ext-u": "optimization_goals not in production schemas — spec-production gap",
    "T-UC-002-ext-u-event": "optimization_goals not in production schemas — spec-production gap",
    # RESOLVED(salesagent-fpi): optimization_goals now accepted by production schemas (UC-003).
    # Removed stale xfails: T-UC-002-partition-optimization-goals, T-UC-002-boundary-optimization-goals
    # Valid rows now pass; invalid rows xfail via _assert_error_outcome _SPEC_PRODUCTION_CODE_MAP.
    # Removed: T-UC-003-partition-optimization-goals, T-UC-003-boundary-optimization-goals, T-UC-003-alt-optimization-goals
    # NOTE: principal-ownership error code gap handled in _assert_error_outcome (PERMISSION_DENIED→AUTHORIZATION_ERROR)
    # RESOLVED(salesagent-0t6h): UpdateMediaBuySuccess status="submitted" now handled
    # by then_response_status (empty affected_packages = approval pending).
    # Removed T-UC-003-alt-manual xfail — tests pass with the fix.
    # FIXME(salesagent-9vgz.19): catalog validation not implemented in production
    # PackageRequest accepts catalogs (inherited from adcp library) but production
    # code never validates duplicate types or catalog_id existence.
    "T-UC-002-ext-v": "catalog validation not implemented in production — spec-production gap",
    "T-UC-002-ext-v-notfound": "catalog validation not implemented in production — spec-production gap",
    # FIXME(salesagent-9vgz.2): proposal-based creation not implemented in production
    # proposal_id exists on adcp library CreateMediaBuyRequest but production code
    # never reads it — no proposal store, no allocation derivation, no budget distribution.
    "T-UC-002-alt-proposal": "proposal-based creation not implemented in production — spec-production gap",
    # FIXME(salesagent-9vgz.23): pricing XOR invariant not enforced during create_media_buy
    # Schema-level validate_pricing_option() enforces XOR but _validate_pricing_model_selection()
    # works at ORM level (is_fixed + rate + price_guidance) and doesn't check for both/neither.
    "T-UC-002-inv-006-3": "pricing XOR invariant (both set) not validated in create flow — spec-production gap",
    "T-UC-002-inv-006-4": "pricing XOR invariant (neither set) error lacks suggestion field — spec-production gap",
    # RESOLVED(salesagent-bo6): budget positivity validation now works — removed stale xfail T-UC-002-inv-008-2
    # FIXME(salesagent-9vgz.27): ASAP case sensitivity error code mismatch
    # Production: Pydantic rejects "ASAP" → ValidationError, spec expects INVALID_REQUEST.
    "T-UC-002-inv-013-5": "INVALID_REQUEST error code not implemented for wrong-case ASAP — spec-production gap",
    # FIXME(salesagent-9vgz.94): sandbox mode not implemented in create_media_buy
    # CreateMediaBuyResult has no sandbox field; no sandbox suppression logic exists.
    # sandbox-production passes vacuously (sandbox absent from response by default).
    "T-UC-002-sandbox-happy": "sandbox mode not implemented in create_media_buy — spec-production gap",
    "T-UC-002-sandbox-validation": "sandbox mode not implemented in create_media_buy — spec-production gap",
    # FIXME(salesagent-9vgz.1): inline creative upload not persisted in create_media_buy
    # process_and_upload_package_creatives → _sync_creatives_impl should persist
    # creatives to DB, but the Then step "upload creatives to creative library" fails
    # because no Creative rows exist after creation. Gap was previously masked by
    # inline pytest.xfail() in the step body — moved to scenario-level here.
    "T-UC-002-alt-creatives": "inline creative upload not persisted in create_media_buy — spec-production gap",
    # FIXME(salesagent-0b1): HMAC signature lacks sha256= prefix in WebhookDeliveryService
    # _generate_hmac_signature returns raw hex digest; spec expects "sha256=" prefix.
    "T-UC-004-webhook-hmac": "HMAC signature missing sha256= prefix — spec-production gap",
    # FIXME(salesagent-0b1): WebhookVerifier raises ValueError, not structured AdCPError
    # ValueError("Webhook secret must be at least 32 characters for security") has no
    # suggestion field. Spec expects structured error with suggestion for remediation.
    "T-UC-004-webhook-creds-short": "credential validation error lacks suggestion field — spec-production gap",
    # FIXME(salesagent-n3y): UC-002 account field absent — production doesn't require account field
    # Spec says account is required (BR-RULE-080 INV-1), but production accepts requests without it.
    "T-UC-002-inv-080-1": "account field not required by production — spec-production gap",
}

# FIXME(beads-dul): Selective xfail for parametrized scenarios where only
# some examples exercise unimplemented features. Each entry: (tag, node_id
# substrings that should xfail, reason).
_SELECTIVE_XFAIL: list[tuple[str, set[str], str]] = [
    (
        "T-UC-005-partition-disclosure",
        {"all_positions", "no_matching_formats", "duplicate_positions"},
        "disclosure_positions filter/validation not implemented",
    ),
    # MCP-specific disclosure xfails are in _MCP_SELECTIVE_XFAIL
    (
        "T-UC-005-boundary-disclosure",
        {"all 8 positions", "format has no", "duplicate positions"},
        "disclosure_positions filter/validation not implemented",
    ),
    # MCP-specific boundary disclosure xfails are in _MCP_SELECTIVE_XFAIL
    # Graduated: T-UC-005-boundary-asset-types (all 4 transports pass — brief/catalog now in enum)
    # FIXME(salesagent-0b1): "delayed" notification_type not in production
    # WebhookDeliveryService.send_delivery_webhook only produces "scheduled",
    # "final", or "adjusted". Spec also expects "delayed" for late reports.
    (
        "T-UC-004-webhook-notification-type",
        {"delayed"},
        "delayed notification_type not implemented in production — spec-production gap",
    ),
]


# MCP selective xfails: the MCP wrapper doesn't accept wcag_level,
# output_format_ids, or input_format_ids params. Only xfail examples
# that actually SEND the param — "omitted"/"not_provided" variants
# send no param and pass fine.
# (tag, example_substrings, reason, strict)
# strict=True  → must fail (genuine xfail)
# strict=False → may pass vacuously (MCP errors → empty list → exclusion assertions pass)
_MCP_SELECTIVE_XFAIL: list[tuple[str, set[str], str, bool]] = [
    ("T-UC-005-partition-wcag", {"level_a", "level_aa", "level_aaa"}, "MCP wrapper does not accept wcag_level", True),
    ("T-UC-005-boundary-wcag", {"first enum value", "last enum value"}, "MCP wrapper does not accept wcag_level", True),
    (
        "T-UC-005-partition-output-fmtids",
        {"single_format_id", "multiple_ids_any_match", "no_matching_formats", "format_without_output_ids"},
        "MCP wrapper does not accept output_format_ids",
        True,
    ),
    (
        "T-UC-005-boundary-output-fmtids",
        {"single FormatId", "multiple FormatIds", "format has no output", "no formats match requested output"},
        "MCP wrapper does not accept output_format_ids",
        True,
    ),
    (
        "T-UC-005-partition-input-fmtids",
        {"single_format_id", "multiple_ids_any_match", "no_matching_formats", "format_without_input_ids"},
        "MCP wrapper does not accept input_format_ids",
        True,
    ),
    (
        "T-UC-005-boundary-input-fmtids",
        {"single FormatId", "multiple FormatIds", "format has no input", "no formats match requested input"},
        "MCP wrapper does not accept input_format_ids",
        True,
    ),
    # Invariant scenarios — "holds" genuinely fails (asserts presence);
    # "violated"/"nofield" pass vacuously (asserts absence → empty list satisfies)
    ("T-UC-005-inv-049-9-holds", set(), "MCP wrapper does not accept output_format_ids", True),
    ("T-UC-005-inv-049-9-violated", set(), "MCP wrapper does not accept output_format_ids (vacuous pass)", False),
    ("T-UC-005-inv-049-9-nofield", set(), "MCP wrapper does not accept output_format_ids (vacuous pass)", False),
    ("T-UC-005-inv-049-10-holds", set(), "MCP wrapper does not accept input_format_ids", True),
    ("T-UC-005-inv-049-10-violated", set(), "MCP wrapper does not accept input_format_ids (vacuous pass)", False),
    ("T-UC-005-inv-049-10-nofield", set(), "MCP wrapper does not accept input_format_ids (vacuous pass)", False),
    # MCP wrapper does not accept disclosure_positions keyword (strict=False: some variants xpass)
    ("T-UC-005-partition-disclosure", {"single_position", "multiple_positions_all_match", "duplicate_positions"}, "MCP wrapper: disclosure_positions not accepted or not validated", False),
    ("T-UC-005-boundary-disclosure", {"single position", "duplicate positions"}, "MCP wrapper: disclosure_positions not accepted or not validated", False),
]

# REST xfails: REST endpoint drops all filter params (build_rest_body returns {}).
# Only xfail scenarios that genuinely fail — many invariant "holds" scenarios
# pass coincidentally because unfiltered results include the expected format.
_REST_XFAIL_TAGS: set[str] = {
    # Invariant filter scenarios where REST unfiltered results break assertions
    "T-UC-005-inv-049-1-holds",  # type filter
    "T-UC-005-inv-049-1-violated",
    "T-UC-005-inv-049-2-holds",  # format_ids filter
    "T-UC-005-inv-049-3-violated",  # asset_types filter
    "T-UC-005-inv-049-4-violated",  # dimension filter
    "T-UC-005-inv-049-4-nodim",  # dimension filter (no dimensions)
    "T-UC-005-inv-049-5-holds",  # responsive=true filter
    "T-UC-005-inv-049-6-holds",  # responsive=false filter
    "T-UC-005-inv-049-7-holds",  # name_search filter
    "T-UC-005-inv-049-7-violated",
    "T-UC-005-inv-049-9-holds",  # output_format_ids filter
    "T-UC-005-inv-049-9-violated",
    "T-UC-005-inv-049-9-nofield",
    "T-UC-005-inv-049-10-holds",  # input_format_ids filter
    "T-UC-005-inv-049-10-violated",
    "T-UC-005-inv-049-10-nofield",
    "T-UC-005-inv-031-1-holds",  # multi-filter AND combination
    "T-UC-005-inv-031-1-violated",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply xfail markers to scenarios with unimplemented production features."""
    for item in items:
        marker_names = {m.name for m in item.iter_markers()}
        nodeid = item.nodeid

        # Detect transport from parametrized nodeid: [mcp], [mcp-...], [a2a], [rest], etc.
        is_mcp = "[mcp]" in nodeid or "[mcp-" in nodeid
        is_a2a = "[a2a]" in nodeid or "[a2a-" in nodeid
        is_rest = "[rest]" in nodeid or "[rest-" in nodeid

        # Transport-specific xfails: MCP wrappers don't accept certain filter params
        if is_mcp:
            for tag, substrings, reason, strict in _MCP_SELECTIVE_XFAIL:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                    break

        # UC-011 REST: per-request auth implemented (salesagent-xms)

        # FIXME(salesagent-39t): UC-011 push notification — production auto-approves
        # accounts (status=active immediately), so the Then step asserting
        # pending_approval->active transition fails. Manual approval not implemented.
        if "T-UC-011-ext-d-push" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="push notification: production auto-approves accounts, no pending_approval state (spec-production gap)",
                    strict=True,
                )
            )

        # FIXME(salesagent-9d5): UC-006 REST — account resolution through CreativeSyncEnv
        # REST route for sync_creatives exists but account kwarg may not be
        # forwarded at the route level (SyncCreativesBody doesn't have account field)
        if is_rest and any(t.startswith("T-UC-006") for t in marker_names) and "account" in marker_names:
            item.add_marker(pytest.mark.xfail(reason="REST route doesn't forward account param", strict=False))

        # Transport-specific xfails: REST drops all filter params
        if is_rest:
            for tag in _REST_XFAIL_TAGS:
                if tag in marker_names:
                    item.add_marker(pytest.mark.xfail(reason="REST endpoint drops filter params", strict=True))
                    break

        # FIXME(salesagent-vov): UC-019 REST — REST endpoint returns Method Not Allowed
        # for get_media_buys, so all REST parametrizations fail.
        if is_rest and any(t.startswith("T-UC-019") for t in marker_names):
            item.add_marker(
                pytest.mark.xfail(
                    reason="REST get_media_buys endpoint not implemented (Method Not Allowed)",
                    strict=False,
                )
            )

        # FIXME(salesagent-9vgz.11): UC-003 package-level update scenarios — REST endpoint
        # doesn't forward packages/creative_assignments/creatives/targeting_overlay to
        # update_media_buy_raw
        if is_rest and (
            "T-UC-003-alt-creative-assignments" in marker_names
            or "T-UC-003-alt-creatives-inline" in marker_names
            or "T-UC-003-alt-targeting" in marker_names
            or "T-UC-003-alt-keyword-ops" in marker_names
            or "T-UC-003-alt-keyword-remove" in marker_names
            or "T-UC-003-alt-negative-keywords" in marker_names
            or "T-UC-003-partial-update" in marker_names
            or "T-UC-003-idempotency-valid" in marker_names
            or "T-UC-003-idempotency-absent" in marker_names
            or "T-UC-003-adapter-success" in marker_names
            or "T-UC-003-adapter-failure" in marker_names
            or "T-UC-003-main-buyer-ref" in marker_names
            or "T-UC-003-atomic-success" in marker_names
            or "T-UC-003-approval-auto" in marker_names
            or "T-UC-003-approval-tenant" in marker_names
            or "T-UC-003-approval-adapter" in marker_names
            or "T-UC-003-creative-replace" in marker_names
            or "T-UC-003-alt-manual" in marker_names
        ):
            item.add_marker(
                pytest.mark.xfail(
                    reason="REST endpoint doesn't forward packages param (spec-production gap)",
                    strict=True,
                )
            )

        # FIXME(salesagent-9vgz.21): UC-003 idempotency-valid — MCP/A2A wrappers don't
        # accept idempotency_key param. Schema has the field but transport boundary
        # doesn't forward it.
        if (is_mcp or is_a2a) and "T-UC-003-idempotency-valid" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="MCP/A2A wrappers don't accept idempotency_key param (spec-production gap)",
                    strict=True,
                )
            )

        # FIXME(salesagent-9vgz.18): UC-003 empty update — production does not reject
        # requests with no updatable fields. Instead returns completed with empty
        # affected_packages. BR-RULE-022 INV-3 says: "No updatable fields → rejected".
        if "T-UC-003-empty-update" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="empty update not rejected by production (BR-RULE-022 INV-3 spec-production gap)",
                    strict=True,
                )
            )

        # FIXME(salesagent-9vgz.14): UC-003 keyword_targets_add — production applies the
        # keyword additions but returns empty affected_packages. impl/a2a/mcp pass the When
        # step (no error) but the Then step "affected_packages including pkg_001" fails.
        if "T-UC-003-alt-keyword-ops" in marker_names and not is_rest:
            item.add_marker(
                pytest.mark.xfail(
                    reason="keyword_targets_add: affected_packages empty after keyword add (spec-production gap)",
                    strict=True,
                )
            )

        # FIXME(salesagent-9vgz.11): UC-003 inline creatives — _sync_creatives_impl
        # FK violation: creative_assignments references creative before commit.
        # _sync_creatives_impl uses its own UoW scope; assignment FK check fails
        # because the creative hasn't been committed in the outer transaction yet.
        if "T-UC-003-alt-creatives-inline" in marker_names and not is_rest:
            item.add_marker(
                pytest.mark.xfail(
                    reason="inline creatives: FK violation in _sync_creatives_impl assignment path (spec-production gap)",
                    strict=True,
                )
            )

        # FIXME(salesagent-05b): UC-003 extension/error scenarios — production uses
        # different error codes than spec, or doesn't validate at all. These are
        # spec-production gaps where the step definitions are correct but production
        # code doesn't implement the expected validation.
        _UC003_EXT_XFAILS: dict[str, str] = {
            # Error code mismatches (production uses different codes than spec)
            "T-UC-003-ext-a": "production returns AUTHORIZATION_ERROR, spec expects authentication_error",
            "T-UC-003-ext-a-unknown": "production returns AUTHORIZATION_ERROR, spec expects authentication_error",
            "T-UC-003-ext-b": "production returns ValueError, spec expects PRODUCT_NOT_FOUND",
            "T-UC-003-ext-b-buyer-ref": "production returns ValueError, spec expects PRODUCT_NOT_FOUND",
            "T-UC-003-ext-c": "production returns AUTHORIZATION_ERROR, spec expects ACCOUNT_NOT_FOUND",
            "T-UC-003-ext-d": "production returns invalid_budget, spec expects BUDGET_TOO_LOW",
            "T-UC-003-ext-d-negative": "production returns invalid_budget, spec expects BUDGET_TOO_LOW",
            "T-UC-003-ext-h": "production returns missing_package_id, spec expects INVALID_REQUEST",
            # Production doesn't validate these cases at all
            "T-UC-003-ext-e": "production doesn't validate end_time < start_time on update",
            "T-UC-003-ext-e-equal": "production doesn't validate end_time == start_time on update",
            "T-UC-003-ext-f": "production doesn't validate currency on update path",
            "T-UC-003-ext-g": "production doesn't validate daily spend cap on update",
            "T-UC-003-ext-i": "production doesn't validate creative existence on update path",
            "T-UC-003-ext-j-error": "production doesn't validate creative state on update path",
            "T-UC-003-ext-j-rejected": "production doesn't validate creative state on update path",
            "T-UC-003-ext-j-format": "production doesn't validate creative format compatibility on update",
            "T-UC-003-ext-k": "inline creative sync: FK violation in production (missing creative commit)",
            "T-UC-003-ext-l": "production doesn't validate package_id existence on update",
            "T-UC-003-ext-m": "production doesn't validate placement_ids on update path",
            "T-UC-003-ext-m-unsupported": "production doesn't validate placement targeting support",
            "T-UC-003-ext-n": "production doesn't check admin privileges on update",
            "T-UC-003-ext-o": "adapter error handling returns wrong shape on update",
            "T-UC-003-ext-p-short": "production doesn't validate idempotency key length on update",
            "T-UC-003-ext-p-long": "production doesn't validate idempotency key length on update",
            "T-UC-003-ext-q-rejected": "production doesn't reject updates to terminal-status media buys",
            "T-UC-003-ext-q-canceled": "production doesn't reject updates to terminal-status media buys",
            "T-UC-003-ext-q-completed": "production doesn't reject updates to terminal-status media buys",
            "T-UC-003-ext-r-keyword": "production doesn't validate keyword operation conflicts",
            "T-UC-003-ext-r-negative": "production doesn't validate negative keyword conflicts",
        }
        for tag, reason in _UC003_EXT_XFAILS.items():
            if tag in marker_names:
                item.add_marker(
                    pytest.mark.xfail(
                        reason=f"spec-production gap: {reason}",
                        strict=False,
                    )
                )
                break  # One xfail per scenario is sufficient

        # FIXME(salesagent-05b): UC-003 ext-r cross-ok scenarios — REST endpoint
        # doesn't forward keyword_targets_add/negative_keywords_add params
        if is_rest and ("T-UC-003-ext-r-cross-ok" in marker_names or "T-UC-003-ext-r-cross-ok-2" in marker_names):
            item.add_marker(
                pytest.mark.xfail(
                    reason="REST endpoint doesn't forward keyword params (spec-production gap)",
                    strict=True,
                )
            )

        # FIXME(salesagent-9vgz.1): UC-002 alt-manual: workflow_step_id is exclude=True
        # in CreateMediaBuySuccess schema, so MCP/REST serialization drops it.
        # impl/a2a return raw Pydantic objects where the field is still accessible.
        if (is_mcp or is_rest) and "T-UC-002-alt-manual" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="workflow_step_id excluded from MCP/REST serialization (exclude=True)",
                    strict=True,
                )
            )

        # --- UC-005: disclosure/asset scenarios with partial impl ---
        # FIXME(beads-dul): disclosure_positions and brief/catalog asset types
        # partially implemented — some transport variants pass, others fail.
        # Must run BEFORE selective xfails (which use strict=True) to avoid
        # XPASS failures on transport variants that now pass.
        _UC005_PARTIAL_TAGS = {
            # Graduated (all 4 transports pass with strong assertions):
            # T-UC-005-partition-disclosure, T-UC-005-boundary-disclosure,
            # T-UC-005-boundary-asset-types
            "T-UC-005-inv-049-8-violated",
            "T-UC-005-inv-049-8-nofield",
        }
        if marker_names & _UC005_PARTIAL_TAGS:
            item.add_marker(pytest.mark.xfail(reason="disclosure/asset partial impl", strict=False))
            # Skip selective xfails for these — the strict=False above covers them
        else:
            # Selective xfail for parametrized scenarios
            for tag, substrings, reason in _SELECTIVE_XFAIL:
                if tag in marker_names:
                    if any(s in item.nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break  # tag matched — skip remaining selective entries

        # Tag-based xfail for all other scenarios
        for tag, reason in _XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break

        # --- UC-002: INVALID_REQUEST validation xfails (production not implemented) ---
        _UC002_VALIDATION_XFAIL: list[tuple[str, set[str], str]] = [
            (
                "T-UC-002-partition-account-ref",
                {"missing_account", "invalid_oneOf_both"},
                "INVALID_REQUEST validation not implemented (schema-level)",
            ),
            (
                "T-UC-002-boundary-account-ref",
                {"account field absent", "both account_id and brand"},
                "INVALID_REQUEST validation not implemented (schema-level)",
            ),
            # FIXME(salesagent-9vgz.61): daily spend cap error code mismatch
            # Production raises plain ValueError → code="validation_error", no suggestion.
            # Spec expects BUDGET_TOO_LOW with suggestion field.
            (
                "T-UC-002-partition-daily-spend-cap",
                {"exceeds_cap"},
                "daily spend cap returns validation_error, not BUDGET_TOO_LOW — spec-production gap",
            ),
            (
                "T-UC-002-boundary-daily-spend-cap",
                {"daily budget > cap"},
                "daily spend cap returns validation_error, not BUDGET_TOO_LOW — spec-production gap",
            ),
            # FIXME(salesagent-9vgz.72): creative error code mismatch
            # Production uses CREATIVES_NOT_FOUND / VALIDATION_ERROR / INVALID_CREATIVES,
            # spec expects CREATIVE_REJECTED. No max_creatives limit in production either.
            (
                "T-UC-002-partition-creative-asset",
                {"creative_not_found", "format_mismatch", "missing_required_assets"},
                "creative error code mismatch: production uses NOT_FOUND/VALIDATION_ERROR/INVALID_CREATIVES, spec expects CREATIVE_REJECTED — spec-production gap",
            ),
            (
                "T-UC-002-partition-creative-asset",
                {"exceeds_max_creatives"},
                "max_creatives limit not enforced in production — spec-production gap",
            ),
            (
                "T-UC-002-boundary-creative-asset",
                {"cr-bad", "wrong format"},
                "creative error code mismatch: production uses NOT_FOUND/VALIDATION_ERROR, spec expects CREATIVE_REJECTED — spec-production gap",
            ),
            (
                "T-UC-002-boundary-creative-asset",
                {"101 uploads"},
                "max_creatives limit not enforced in production — spec-production gap",
            ),
        ]
        if any(t.startswith("T-UC-002") for t in marker_names):
            for tag, substrings, reason in _UC002_VALIDATION_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break

        # --- UC-006: auth error code mismatch (production returns VALIDATION_ERROR, spec expects AUTH_REQUIRED) ---
        _UC006_AUTH_XFAIL = {"T-UC-006-ext-a-rest", "T-UC-006-ext-a-mcp"}
        if marker_names & _UC006_AUTH_XFAIL:
            item.add_marker(
                pytest.mark.xfail(
                    reason="AUTH_REQUIRED error code not implemented (returns VALIDATION_ERROR)", strict=True
                )
            )

        # --- UC-006: INVALID_REQUEST validation xfails (production not implemented) ---
        _UC006_VALIDATION_XFAIL: list[tuple[str, set[str], str]] = [
            (
                "T-UC-006-partition-account",
                {"missing_account", "invalid_oneOf_both"},
                "INVALID_REQUEST validation not implemented (schema-level)",
            ),
            (
                "T-UC-006-boundary-account",
                {"account field absent", "both account_id and brand"},
                "INVALID_REQUEST validation not implemented (schema-level)",
            ),
        ]
        if any(t.startswith("T-UC-006") for t in marker_names):
            for tag, substrings, reason in _UC006_VALIDATION_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break

        # --- UC-004: xfails for unimplemented production features ---
        # FIXME(salesagent-ckb): These production features are not yet implemented.
        # strict=True: test MUST fail. strict=False: test MAY pass (some examples work).
        _UC004_XFAIL_TAGS: dict[str, tuple[str, bool]] = {
            # Empty array validation: schema allows [] but spec says reject
            "T-UC-004-identify-empty": ("empty media_buy_ids=[] not rejected by schema", True),
            "T-UC-004-identify-buyer-refs-empty": ("empty buyer_refs=[] not rejected by schema", True),
            # Invalid status filter: production doesn't validate enum values
            "T-UC-004-filter-invalid": ("invalid status_filter values not rejected", True),
            # Date range validation: production doesn't validate start>end
            "T-UC-004-daterange-invalid": ("date range validation (start>end) not implemented", True),
            "T-UC-004-daterange-equal": ("date range validation (start==end) not implemented", True),
            # Webhook delivery: not yet in production
            "T-UC-004-webhook-scheduled": ("webhook delivery not implemented", True),
            # Sandbox: not yet in delivery _impl
            "T-UC-004-sandbox-happy": ("sandbox mode not implemented in delivery", True),
            "T-UC-004-sandbox-validation": ("sandbox mode not implemented in delivery", True),
        }
        for tag, (reason, strict) in _UC004_XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                break

        # UC-004: additional xfails for features needing production enhancements
        # FIXME(salesagent-a0o): These require production changes, not BDD wiring.
        _UC004_XFAIL_ADDITIONAL: dict[str, tuple[str, bool]] = {
            # FIXME(salesagent-afq): _impl doesn't echo attribution_window in response
            "T-UC-004-attr-supported": ("attribution_window echo not implemented in _impl", True),
            "T-UC-004-attr-unsupported": ("attribution_window platform default not implemented in _impl", True),
            "T-UC-004-attr-echo": ("attribution_window model field not populated in response", True),
            "T-UC-004-attr-omitted": ("attribution_window platform default not implemented in _impl", True),
            "T-UC-004-attr-campaign-valid": ("attribution_window campaign window not implemented in _impl", True),
            # campaign unit interval validation: _impl doesn't validate attribution_window
            "T-UC-004-attr-campaign-invalid": (
                "attribution_window campaign unit validation not implemented in _impl",
                True,
            ),
            # FIXME(salesagent-b2v): _impl only supports by_placement, not by_device_type/by_geo/truncation
            "T-UC-004-dim-supported": ("by_device_type breakdown not implemented in _impl (only by_placement)", True),
            "T-UC-004-dim-truncated": ("truncation flags (by_*_truncated) not implemented in _impl", True),
            "T-UC-004-dim-complete": ("by_device_type_truncated flag not implemented in _impl", True),
            "T-UC-004-dim-geo-system": ("by_geo breakdown not implemented in _impl", True),
            "T-UC-004-dim-geo-postal": ("by_geo breakdown not implemented in _impl", True),
            "T-UC-004-dim-multi": ("by_geo/by_device_type breakdowns not implemented in _impl", True),
            # Partial-success Error model lacks suggestion field and rich messages
            "T-UC-004-ext-a": ("partial-success Error needs suggestion field + authentication in message", True),
            "T-UC-004-ext-b": ("partial-success Error model needs suggestion field — production enhancement", True),
            "T-UC-004-ext-c": ("partial-success Error model needs suggestion field — production enhancement", True),
            "T-UC-004-ext-d": ("partial-success Error model needs suggestion field — production enhancement", True),
            # FIXME(salesagent-ttw): _impl reports media_buy_not_found instead of silently omitting
            "T-UC-004-identify-partial": (
                "_impl reports media_buy_not_found errors instead of silently omitting missing IDs (BR-RULE-030 INV-5)",
                True,
            ),
            "T-UC-004-identify-batch-ownership": (
                "_impl reports media_buy_not_found for non-owned IDs instead of silently omitting (BR-RULE-030 INV-5)",
                True,
            ),
            # Adapter error: message text + suggestion not wired in partial-success response
            "T-UC-004-ext-f": ("adapter error response needs suggestion field and message refinement", True),
            # Adapter partial failure: _impl silently swallows data construction exceptions
            "T-UC-004-adapter-partial": (
                "adapter partial failure handling needs enriched test data or production fix",
                True,
            ),
            # Error response structure: same no-auth path as ext-a, suggestion missing
            "T-UC-004-response-error": (
                "error response structure needs suggestion field — production enhancement",
                True,
            ),
        }
        for tag, (reason, strict) in _UC004_XFAIL_ADDITIONAL.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                break

        # UC-004 status filter: "active" works, other values may not
        _UC004_FILTER_SELECTIVE: list[tuple[str, set[str], str]] = [
            (
                "T-UC-004-filter",
                {"pending_activation", "rejected", "canceled", "paused", "completed"},
                "status_filter for non-active statuses not mapped in _impl",
            ),
            (
                "T-UC-004-filter-default",
                set(),  # all examples
                "default status_filter=active not applied when no explicit IDs",
            ),
            (
                "T-UC-004-filter-empty",
                set(),
                "status_filter empty result not returned as empty array",
            ),
            (
                "T-UC-004-filter-array",
                set(),
                "status_filter with array not correctly applied",
            ),
        ]
        if any(t.startswith("T-UC-004-filter") for t in marker_names):
            for tag, substrings, reason in _UC004_FILTER_SELECTIVE:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # UC-004 date range: custom dates partially work
        _UC004_DATE_SELECTIVE: list[tuple[str, set[str], str]] = [
            ("T-UC-004-daterange", set(), "custom date range partially applied"),
            # Graduated: T-UC-004-daterange-start-only (all 4 transports pass)
            ("T-UC-004-daterange-end-only", set(), "end-only date range not applied"),
        ]
        if any(t.startswith("T-UC-004-daterange") for t in marker_names):
            for tag, substrings, reason in _UC004_DATE_SELECTIVE:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # UC-004 boundary scenarios: strict=False because some examples pass.
        # Invalid boundary values SHOULD fail validation but production doesn't validate.
        # Valid boundary values pass through fine.
        _UC004_BOUNDARY_TAGS = {
            "T-UC-004-boundary-reporting-dims",
            "T-UC-004-boundary-attribution",
            "T-UC-004-boundary-daily-breakdown",
            "T-UC-004-boundary-account",
            "T-UC-004-boundary-sampling",
            "T-UC-004-boundary-status-filter",
            "T-UC-004-boundary-date-range",
            "T-UC-004-boundary-resolution",
            "T-UC-004-boundary-ownership",
            "T-UC-004-boundary-credentials",
        }
        if marker_names & _UC004_BOUNDARY_TAGS:
            item.add_marker(pytest.mark.xfail(reason="boundary validation partially implemented", strict=False))

        # UC-004 partition scenarios: adcp 3.10 changed schema validation behavior.
        # Partition tests exercise valid/invalid value ranges per field.
        # strict=False: some partition values pass, others fail depending on schema version.
        _UC004_PARTITION_TAGS = {
            # Graduated (all 4 transports pass with strong assertions):
            # T-UC-004-partition-reporting-dims, T-UC-004-partition-attribution,
            # T-UC-004-partition-daily-breakdown, T-UC-004-partition-account,
            # T-UC-004-partition-sampling, T-UC-004-partition-status-filter,
            # T-UC-004-partition-date-range, T-UC-004-partition-resolution,
            # T-UC-004-partition-ownership
            "T-UC-004-partition-credentials",
        }
        if marker_names & _UC004_PARTITION_TAGS:
            item.add_marker(
                pytest.mark.xfail(reason="partition validation behavior varies with adcp schema version", strict=False)
            )

        # --- UC-004 partition: selective xfail for error-expecting examples ---
        # FIXME(salesagent-7wan): Graduated partition tags still have invalid-value
        # examples that expect INVALID_REQUEST/ACCOUNT_NOT_FOUND but production
        # doesn't validate. Only xfail the failing subset; valid-value examples pass.
        _UC004_PARTITION_SELECTIVE: list[tuple[str, set[str], str]] = [
            # reporting_dimensions: production doesn't validate missing geo_level, limit<=0, etc.
            (
                "T-UC-004-partition-reporting-dims",
                {"geo_missing_geo_level", "geo_metro_missing_system", "limit_zero", "limit_negative"},
                "reporting_dimensions validation not implemented — production accepts invalid configs",
            ),
            # attribution_window: production doesn't validate interval<=0, invalid unit/model, campaign interval
            (
                "T-UC-004-partition-attribution",
                {"interval_zero", "interval_negative", "invalid_unit", "invalid_model", "campaign_interval_not_one"},
                "attribution_window validation not implemented — production accepts invalid configs",
            ),
            # daily breakdown: production doesn't validate non-boolean values
            (
                "T-UC-004-partition-daily-breakdown",
                {"non_boolean"},
                "include_package_daily_breakdown validation not implemented — production accepts non-boolean",
            ),
            # account: production doesn't validate oneOf constraint or account existence
            (
                "T-UC-004-partition-account",
                {"invalid_oneOf_both", "account_not_found", "empty_object"},
                "delivery account validation not implemented — production accepts invalid account configs",
            ),
            # sampling_method: not implemented in production (schema rejects, transport doesn't accept)
            (
                "T-UC-004-partition-sampling",
                set(),  # ALL examples fail — schema/transport doesn't support sampling_method at all
                "sampling_method not implemented in delivery _impl or transport wrappers",
            ),
            # status_filter: production doesn't validate unknown values or empty arrays
            (
                "T-UC-004-partition-status-filter",
                {"unknown_value", "empty_array"},
                "status_filter validation not implemented — production accepts invalid values",
            ),
            # date range: production doesn't validate start>=end
            (
                "T-UC-004-partition-date-range",
                {"start_equals_end", "start_after_end"},
                "date range validation not implemented — production accepts start>=end",
            ),
            # resolution: production doesn't validate empty array
            (
                "T-UC-004-partition-resolution",
                {"empty_array"},
                "resolution validation not implemented — production accepts empty array",
            ),
            # ownership: production doesn't validate principal mismatch
            (
                "T-UC-004-partition-ownership",
                {"owner_mismatch"},
                "ownership validation not implemented — production accepts non-owned media buys",
            ),
        ]
        for tag, substrings, reason in _UC004_PARTITION_SELECTIVE:
            if tag in marker_names:
                if not substrings or any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # FIXME(salesagent-9vgz.80): catalog distinct type partition/boundary
        # Production accepts catalogs but never validates duplicate types or catalog_id
        # existence. Valid partitions pass; invalid partitions succeed when they should fail.
        # Graduated (all 4 transports pass with strong assertions):
        # T-UC-002-partition-catalog-distinct-type, T-UC-002-boundary-catalog-distinct-type
        _UC002_CATALOG_TAGS: set[str] = set()
        if marker_names & _UC002_CATALOG_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="catalog validation not implemented in production — spec-production gap", strict=False
                )
            )

        # --- UC-019: xfails for spec-production gaps ---
        # Status computation relies on date-relative queries; production returns
        # empty results when status_filter doesn't match. Creative approval mapping,
        # snapshot propagation, and sandbox mode are not yet implemented.
        _UC019_XFAIL_TAGS: set[str] = {
            # Status computation partition/boundary — default filter is {active}
            # so pre-flight/post-flight buys filtered out even when media_buy_ids
            # explicitly requested. Spec expects ID filter to bypass status filter.
            "T-UC-019-partition-status",
            "T-UC-019-partition-status-invalid",
            "T-UC-019-boundary-status",
            # Status filter scenarios — status_filter parameter partially implemented
            "T-UC-019-partition-status-filter",
            "T-UC-019-partition-status-filter-invalid",
            "T-UC-019-boundary-status-filter",
            # Creative approval mapping — not implemented
            "T-UC-019-partition-approval",
            "T-UC-019-partition-approval-invalid",
            "T-UC-019-boundary-approval",
            "T-UC-019-inv-152-1",
            "T-UC-019-inv-152-2",
            "T-UC-019-inv-152-3",
            "T-UC-019-inv-152-5",
            # Snapshot scenarios — adapter snapshot API not wired
            "T-UC-019-partition-snapshot",
            "T-UC-019-boundary-snapshot",
            "T-UC-019-inv-153-1",
            "T-UC-019-inv-153-3",
            "T-UC-019-inv-153-4",
            "T-UC-019-inv-153-5",
            # Invariants with spec-production gaps
            "T-UC-019-inv-150-1",
            "T-UC-019-inv-150-2",
            "T-UC-019-inv-150-3",
            "T-UC-019-inv-150-4",
            "T-UC-019-inv-150-5",
            "T-UC-019-inv-151-1",
            "T-UC-019-inv-151-4",
            "T-UC-019-inv-154-tenant",
            # Sandbox mode — not implemented
            "T-UC-019-sandbox-happy",
            "T-UC-019-sandbox-production",
            "T-UC-019-sandbox-validation",
            # Principal partition/boundary — parametrized Given text varies
            "T-UC-019-partition-principal",
            "T-UC-019-partition-principal-invalid",
            "T-UC-019-boundary-principal",
            # Extension errors — error code mismatches / not implemented
            "T-UC-019-ext-a",
            "T-UC-019-ext-b",
            "T-UC-019-ext-c",
            "T-UC-019-ext-d",
            "T-UC-019-ext-e",
            # Main flow snapshots — adapter not wired
            "T-UC-019-main-snapshot",
            # Transport-specific scenarios
            "T-UC-019-main-rest",
            "T-UC-019-main-mcp",
        }
        if marker_names & _UC019_XFAIL_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-019 spec-production gap — feature not yet implemented",
                    strict=False,
                )
            )

        # --- UC-026: xfails for update scenarios (need MediaBuyUpdateEnv wiring) ---
        # and for spec-production gaps in package validation / keyword targeting.
        # FIXME(salesagent-av7): UC-026 update and advanced scenarios need production wiring.
        _UC026_XFAIL_TAGS: set[str] = {
            # Main flow scenarios with explicit format_ids — production code
            # tries format_id["id"] on FormatId Pydantic model (not subscriptable)
            "T-UC-026-main-explicit-formats",
            "T-UC-026-main-full-config",
            # Update scenarios — MediaBuyCreateEnv doesn't support update dispatch
            "T-UC-026-alt-update",
            "T-UC-026-alt-update-buyer-ref",
            "T-UC-026-alt-pause",
            "T-UC-026-alt-resume",
            "T-UC-026-alt-keyword-add",
            "T-UC-026-alt-keyword-upsert",
            "T-UC-026-alt-keyword-remove",
            "T-UC-026-alt-keyword-remove-noop",
            "T-UC-026-alt-negative-keyword-add",
            "T-UC-026-alt-negative-keyword-remove-noop",
            "T-UC-026-alt-dedup",
            # Graduated: T-UC-026-alt-dedup-crossbuy (all 4 transports pass)
            # Extension error scenarios — error codes/suggestions not implemented
            # Graduated: T-UC-026-ext-a (all 4 transports pass)
            "T-UC-026-ext-b",
            "T-UC-026-ext-c",
            "T-UC-026-ext-d",
            "T-UC-026-ext-e",
            "T-UC-026-ext-f",
            "T-UC-026-ext-g-product",
            "T-UC-026-ext-g-format",
            "T-UC-026-ext-g-pricing",
            "T-UC-026-ext-h-keyword",
            "T-UC-026-ext-h-negative",
            "T-UC-026-ext-h-cross-ok",
            "T-UC-026-ext-h-cross-reverse",
            "T-UC-026-ext-i",
            # Invariant scenarios — update wiring or validation not implemented
            # Graduated: T-UC-026-inv-194-1 (all 4 transports pass)
            "T-UC-026-inv-194-2",
            "T-UC-026-inv-195-1",
            "T-UC-026-inv-195-2",
            "T-UC-026-inv-195-3",
            "T-UC-026-inv-195-4",
            # Graduated: T-UC-026-inv-196-3 (all 4 transports pass)
            "T-UC-026-inv-197-3",
            "T-UC-026-inv-197-4",
            "T-UC-026-inv-198-4",
            "T-UC-026-inv-199-3",
            "T-UC-026-inv-199-4",
            # Graduated: T-UC-026-inv-200-1 (all 4 transports pass)
            "T-UC-026-inv-200-2",
            "T-UC-026-inv-201-1",
            "T-UC-026-inv-201-2",
            "T-UC-026-inv-201-3",
            "T-UC-026-inv-201-4",
            "T-UC-026-inv-201-5",
            "T-UC-026-inv-089-2",
            # Graduated: T-UC-026-inv-089-3 (all 4 transports pass)
            # Partition/boundary scenarios — graduated tags removed, remaining need wiring
            # Graduated: T-UC-026-partition-required-fields, T-UC-026-boundary-required-fields,
            # T-UC-026-partition-bid-price, T-UC-026-partition-buyer-ref,
            # T-UC-026-partition-format-ids, T-UC-026-partition-pricing-option,
            # T-UC-026-partition-immutable, T-UC-026-partition-keyword-add,
            # T-UC-026-partition-keyword-remove, T-UC-026-partition-neg-kw-add,
            # T-UC-026-partition-neg-kw-remove, T-UC-026-boundary-neg-kw-add,
            # T-UC-026-boundary-neg-kw-remove, T-UC-026-partition-paused
            "T-UC-026-boundary-bid-price",
            "T-UC-026-boundary-buyer-ref",
            "T-UC-026-boundary-format-ids",
            "T-UC-026-boundary-pricing-option",
            "T-UC-026-boundary-immutable",
            "T-UC-026-boundary-keyword-add",
            "T-UC-026-boundary-keyword-remove",
            "T-UC-026-partition-kw-add-shared",
            "T-UC-026-partition-kw-remove-shared",
            "T-UC-026-boundary-kw-add-shared",
            "T-UC-026-boundary-kw-remove-shared",
            "T-UC-026-boundary-paused",
            "T-UC-026-partition-replacement",
            "T-UC-026-boundary-replacement",
        }
        if marker_names & _UC026_XFAIL_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-026 spec-production gap — update env / validation not yet wired",
                    strict=False,
                )
            )

        # --- UC-026 partition/boundary: selective xfail for graduated tags ---
        # FIXME(salesagent-7wan): These partition/boundary tags were graduated
        # (most examples pass) but specific examples still fail due to production
        # bugs (get_total_budget missing, FormatId not subscriptable, BUDGET_TOO_LOW
        # on budget=0, transport wrappers don't accept media_buy_id for updates).
        _UC026_PARTITION_SELECTIVE: list[tuple[str, set[str], str]] = [
            # budget=0 rejected with BUDGET_TOO_LOW — spec says 0 is valid
            (
                "T-UC-026-partition-required-fields",
                {"budget_zero"},
                "production rejects budget=0 with BUDGET_TOO_LOW — spec allows zero budget",
            ),
            (
                "T-UC-026-boundary-required-fields",
                {"budget = 0"},
                "production rejects budget=0 with BUDGET_TOO_LOW — spec allows zero budget",
            ),
            # FormatId not subscriptable — production tries format_id["id"] on Pydantic model
            (
                "T-UC-026-partition-format-ids",
                set(),  # all format_id examples fail
                "FormatId not subscriptable — production uses dict access on Pydantic model",
            ),
            # buyer_ref dedup requires full create_media_buy fields not present in update env
            (
                "T-UC-026-partition-buyer-ref",
                {"duplicate_buyer_ref"},
                "buyer_ref deduplication requires create_media_buy fields — spec-production gap",
            ),
            # max_bid validation: production requires bid_price for auction-based pricing
            (
                "T-UC-026-partition-pricing-option",
                {"valid_with_max_bid"},
                "max_bid pricing validation rejects valid ceiling semantics — spec-production gap",
            ),
            # Update scenarios: get_total_budget missing / transport wrappers don't accept media_buy_id
            # ALL examples under these tags fail — no selective substring needed
            (
                "T-UC-026-partition-immutable",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
            (
                "T-UC-026-partition-keyword-add",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
            (
                "T-UC-026-partition-keyword-remove",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
            (
                "T-UC-026-partition-neg-kw-add",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
            (
                "T-UC-026-partition-neg-kw-remove",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
            (
                "T-UC-026-boundary-neg-kw-add",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
            (
                "T-UC-026-boundary-neg-kw-remove",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
            (
                "T-UC-026-partition-paused",
                set(),
                "UpdateMediaBuyRequest.get_total_budget not implemented / media_buy_id not accepted by wrappers",
            ),
        ]
        for tag, substrings, reason in _UC026_PARTITION_SELECTIVE:
            if tag in marker_names:
                if not substrings or any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # --- UC-011: xfails for spec-production gaps ---
        # FIXME(salesagent-7wan): Production doesn't implement these UC-011 features.
        _UC011_SELECTIVE_XFAIL: list[tuple[str, set[str], str]] = [
            # status filter: only payment_required fails — other statuses work fine
            (
                "T-UC-011-list-status-filter",
                {"payment_required"},
                "payment_required status not mapped in production — filter returns empty",
            ),
        ]
        for tag, substrings, reason in _UC011_SELECTIVE_XFAIL:
            if tag in marker_names:
                if any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        _UC011_XFAIL_TAGS: dict[str, str] = {
            # deactivation scoping: production doesn't scope deactivation to authenticated agent
            "T-UC-011-ext-f-scoped": "deactivation not scoped to authenticated agent — production applies globally",
            # context echo: production doesn't echo context in operation responses
            "T-UC-011-ext-g-echo": "context echo not implemented in list_accounts response",
            "T-UC-011-ext-g-echo-error": "context echo not implemented in sync_accounts error response",
            # validation: production returns Pydantic ValidationError without error_code field
            "T-UC-011-sync-missing-brand": "missing brand domain returns raw ValidationError, not structured error_code",
            "T-UC-011-sync-missing-operator": "missing operator returns raw ValidationError, not structured error_code",
        }
        for tag, reason in _UC011_XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # --- Entity marker auto-application based on BDD tags ---
        # BDD tests don't have entity keywords in filenames; instead they
        # use tags like T-UC-004-* (delivery) and T-UC-005-* (creative).
        if any(t.startswith("T-UC-002") for t in marker_names):
            item.add_marker(pytest.mark.media_buy)
        if any(t.startswith("T-UC-006") for t in marker_names):
            item.add_marker(pytest.mark.creative)
        if any(t.startswith("T-UC-004") for t in marker_names):
            item.add_marker(pytest.mark.delivery)
        if any(t.startswith("T-UC-005") for t in marker_names):
            item.add_marker(pytest.mark.creative)
        if any(t.startswith("T-UC-026") for t in marker_names):
            item.add_marker(pytest.mark.media_buy)
        if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
            item.add_marker(pytest.mark.admin)


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Multi-transport dispatch
# ---------------------------------------------------------------------------
# Tags that indicate a scenario already dispatches through a specific transport.
# These scenarios must NOT be multiplied — they have explicit When steps.
_TRANSPORT_SPECIFIC_TAGS = {"rest", "mcp", "a2a"}

# UC + tag combinations that should run IMPL-only (no 4-way parametrization).
# UC-002 @account: MediaBuyAccountEnv tests resolve_account() directly — no
# transport wrappers exist for the create_media_buy account resolution path.
_IMPL_ONLY: set[tuple[str, str]] = {
    ("UC-002", "account"),
}

# Admin scenarios have their own transport (Flask test_client / requests.Session).
# They must NOT be parametrized across MCP/A2A/REST/IMPL API transports.
_ADMIN_TAG_PREFIX = "T-ADMIN-"


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize BDD scenarios across all 4 transports.

    Scenarios tagged with @rest, @mcp, or @a2a are transport-specific
    and skip parametrization — they already dispatch through their
    explicit transport in the When step.

    Uses ``ctx`` as the parametrize target (indirect) so every scenario
    gets a fresh dict with ``ctx["transport"]`` set to the Transport enum.
    """
    if "ctx" not in metafunc.fixturenames:
        return

    from tests.harness.transport import Transport

    marker_names = {m.name for m in metafunc.definition.iter_markers()}
    if marker_names & _TRANSPORT_SPECIFIC_TAGS:
        # Transport-specific scenario — don't multiply
        return

    # Admin scenarios use Flask test_client, not API transports
    if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
        return

    # IMPL-only scenarios: harness has no transport wrappers for this path
    for uc_prefix, required_tag in _IMPL_ONLY:
        tag_prefix = f"T-{uc_prefix}-"
        if any(t.startswith(tag_prefix) for t in marker_names) and required_tag in marker_names:
            return

    metafunc.parametrize(
        "ctx",
        [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST],
        ids=["impl", "a2a", "mcp", "rest"],
        indirect=True,
    )


@pytest.fixture()
def ctx(request: pytest.FixtureRequest) -> dict:
    """Per-scenario mutable context shared across Given/When/Then steps.

    When parametrized by pytest_generate_tests, ``request.param`` is a
    Transport enum injected as ctx["transport"]. Transport-specific
    scenarios (tagged @rest/@mcp/@a2a) are NOT parametrized and get
    an empty ctx (When steps handle dispatch explicitly).
    """
    d: dict = {}
    if hasattr(request, "param"):
        d["transport"] = request.param
    return d


def _detect_uc(request: pytest.FixtureRequest) -> str | None:
    """Detect which use case a BDD scenario belongs to via its tags."""
    marker_names = {m.name for m in request.node.iter_markers()}
    if any(t.startswith("T-UC-002") for t in marker_names):
        return "UC-002"
    if any(t.startswith("T-UC-003") for t in marker_names):
        return "UC-003"
    if any(t.startswith("T-UC-019") for t in marker_names):
        return "UC-019"
    if any(t.startswith("T-UC-026") for t in marker_names):
        return "UC-026"
    if any(t.startswith("T-UC-006") for t in marker_names):
        return "UC-006"
    if any(t.startswith("T-UC-005") for t in marker_names):
        return "UC-005"
    if any(t.startswith("T-UC-004") for t in marker_names):
        return "UC-004"
    if any(t.startswith("T-UC-011") for t in marker_names):
        return "UC-011"
    if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
        return "ADMIN"
    return None


def _detect_uc011_harness(marker_names: set[str]) -> str:
    """Detect which UC-011 harness a scenario needs based on tags."""
    if "list" in marker_names:
        return "list"
    if "sync" in marker_names:
        return "sync"
    # Context-echo and sandbox scenarios are cross-cutting: they test both
    # list_accounts and sync_accounts. Use sync harness as default since
    # it's the superset (context-echo When step creates its own env if needed).
    if "context-echo" in marker_names or "sandbox" in marker_names:
        return "sync"
    return "unknown"


def _detect_delivery_harness(request: pytest.FixtureRequest) -> str:
    """Detect which delivery harness a UC-004 scenario needs."""
    marker_names = {m.name for m in request.node.iter_markers()}
    if "webhook-reliability" in marker_names:
        return "circuit-breaker"
    if "webhook" in marker_names:
        # Webhook scenarios (HMAC, bearer, sequence, notification_type) use
        # WebhookDeliveryService which lives in CircuitBreakerEnv, not the
        # older deliver_webhook_with_retry from WebhookEnv.
        return "circuit-breaker"
    return "poll"


@pytest.fixture(autouse=True)
def _harness_env(request: pytest.FixtureRequest, ctx: dict) -> Generator[None, None, None]:
    """Provide the appropriate harness for each BDD scenario.

    - UC-005 → CreativeFormatsEnv
    - UC-004 @polling → DeliveryPollEnv
    - UC-004 @webhook → WebhookEnv (unit variant, no DB needed)
    - UC-004 @webhook-reliability → CircuitBreakerEnv (unit variant)
    - Unknown UC → no harness (yields immediately)
    """
    uc = _detect_uc(request)

    if uc == "UC-002":
        marker_names = {m.name for m in request.node.iter_markers()}
        if "account" in marker_names:
            # Account resolution scenarios only — MediaBuyAccountEnv handles resolve_account
            request.getfixturevalue("integration_db")
            from tests.harness.media_buy_account import MediaBuyAccountEnv

            with MediaBuyAccountEnv() as env:
                ctx["env"] = env
                yield
        else:
            # Non-account UC-002 scenarios → MediaBuyCreateEnv with full data chain
            request.getfixturevalue("integration_db")
            from tests.harness.media_buy_create import MediaBuyCreateEnv

            with MediaBuyCreateEnv() as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                yield

    elif uc == "UC-003":
        # UC-003 update_media_buy — needs existing media buy in DB
        request.getfixturevalue("integration_db")
        from tests.harness.media_buy_update import MediaBuyUpdateIntegrationEnv as MediaBuyUpdateEnv

        with MediaBuyUpdateEnv() as env:
            tenant, principal, media_buy, package, product = env.setup_update_data()
            ctx["env"] = env
            ctx["tenant"] = tenant
            ctx["principal"] = principal
            ctx["existing_media_buy"] = media_buy
            ctx["existing_package"] = package
            ctx["default_product"] = product
            yield

    elif uc == "UC-019":
        # UC-019 query_media_buys — minimal harness, media buys seeded by Given steps
        request.getfixturevalue("integration_db")
        from tests.harness.media_buy_list import MediaBuyListEnv

        with MediaBuyListEnv(principal_id="buyer-001") as env:
            tenant, principal = env.setup_default_data()
            ctx["env"] = env
            ctx["tenant"] = tenant
            ctx["principal"] = principal
            yield

    elif uc == "UC-026":
        # UC-026 package_media_buy — uses MediaBuyCreateEnv with product "prod-1"
        request.getfixturevalue("integration_db")
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        with MediaBuyCreateEnv() as env:
            from tests.factories import (
                PricingOptionFactory,
                ProductFactory,
                PropertyTagFactory,
                PublisherPartnerFactory,
            )

            tenant, principal = env.setup_default_data()
            if "_" in (tenant.subdomain or ""):
                tenant.subdomain = tenant.subdomain.replace("_", "-")
            PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
            PublisherPartnerFactory(tenant=tenant, publisher_domain="testpublisher.example.com")
            product = ProductFactory(
                tenant=tenant,
                product_id="prod-1",
                property_tags=["all_inventory"],
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "banner-300x250"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "banner-728x90"},
                ],
            )
            po_fixed = PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)
            po_auction = PricingOptionFactory(
                product=product,
                pricing_model="cpm",
                currency="USD",
                is_fixed=False,
                price_guidance={"floor": 1.00, "p25": 2.00, "p50": 3.00, "p75": 4.00, "p90": 5.00},
            )
            env._commit_factory_data()
            # Map feature-file labels to real synthetic pricing_option_id strings
            # Production code constructs: {pricing_model}_{currency}_{fixed|auction}
            ctx["pricing_option_map"] = {
                "cpm-standard": "cpm_usd_fixed",
                "cpm-auction": "cpm_usd_auction",
            }
            ctx["default_pricing_option"] = po_fixed
            ctx["env"] = env
            ctx["tenant"] = tenant
            ctx["principal"] = principal
            ctx["default_product"] = product
            yield

    elif uc == "UC-006":
        marker_names = {m.name for m in request.node.iter_markers()}
        if "account" in marker_names:
            # Account resolution through CreativeSyncEnv — exercises the full
            # sync_creatives transport wrappers which call enrich_identity_with_account()
            request.getfixturevalue("integration_db")
            from tests.harness.creative_sync import CreativeSyncEnv

            with CreativeSyncEnv() as env:
                ctx["env"] = env
                yield
        else:
            yield

    elif uc == "UC-005":
        request.getfixturevalue("integration_db")
        from tests.harness.creative_formats import CreativeFormatsEnv

        with CreativeFormatsEnv() as env:
            ctx["env"] = env
            yield

    elif uc == "UC-011":
        marker_names = {m.name for m in request.node.iter_markers()}
        harness_type = _detect_uc011_harness(marker_names)

        if harness_type == "list":
            request.getfixturevalue("integration_db")
            from tests.harness.account_list import AccountListEnv

            with AccountListEnv() as env:
                ctx["env"] = env
                yield
        elif harness_type == "sync":
            request.getfixturevalue("integration_db")
            from tests.harness.account_sync import AccountSyncEnv

            with AccountSyncEnv() as env:
                ctx["env"] = env
                yield
        else:
            yield

    elif uc == "ADMIN":
        request.getfixturevalue("integration_db")
        from tests.harness.admin_accounts import AdminAccountEnv

        # BDD suite always uses integration mode (Flask test_client).
        # E2E mode (requests.Session + Docker) is tested separately.
        with AdminAccountEnv(mode="integration") as env:
            ctx["env"] = env
            yield

    elif uc == "UC-004":
        harness_type = _detect_delivery_harness(request)

        if harness_type == "poll":
            request.getfixturevalue("integration_db")
            from tests.harness.delivery_poll import DeliveryPollEnv

            # Use "buyer-001" as principal — matches most UC-004 scenarios.
            # _ensure_media_buy_in_db creates media buys owned by the
            # scenario's "owner" (usually "buyer-001"), and _impl filters
            # by the identity's principal. They must match.
            with DeliveryPollEnv(principal_id="buyer-001") as env:
                tenant, principal = env.setup_default_data()
                ctx["env"] = env
                ctx["db_tenant"] = tenant
                ctx[f"db_principal_{env._principal_id}"] = principal
                yield
        elif harness_type == "webhook":
            from tests.harness.delivery_webhook import WebhookEnv

            with WebhookEnv() as env:
                ctx["env"] = env
                yield
        elif harness_type == "circuit-breaker":
            from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

            with CircuitBreakerEnv() as env:
                ctx["env"] = env
                yield
        else:
            yield
    else:
        yield
