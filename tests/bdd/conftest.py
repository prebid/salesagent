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
with a reason (e.g., "MCP wrapper does not accept disclosure_positions").
"""

from __future__ import annotations

import os
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
    "tests.bdd.steps.domain.uc004_delivery",
    "tests.bdd.steps.domain.uc002_create_media_buy",
    "tests.bdd.steps.domain.uc006_sync_creatives",
    "tests.bdd.steps.domain.uc011_accounts",
    "tests.bdd.steps.domain.admin_accounts",
    "tests.bdd.steps.domain.uc_get_products_inventory",
    "tests.bdd.steps.domain.compat_normalization",
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
    """Auto-xfail scenarios that fail due to genuinely missing step definitions.

    Only StepDefinitionNotFoundError and NotImplementedError are converted to
    xfail. KeyError is NOT caught — use pytest.skip() in _harness_env for
    scenarios without a harness instead of relying on runtime KeyError interception.
    """
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed and call.excinfo is not None:
        from pytest_bdd.exceptions import StepDefinitionNotFoundError

        if call.excinfo.errisinstance(StepDefinitionNotFoundError):
            report.outcome = "skipped"
            report.wasxfail = f"Step definition not found: {call.excinfo.value}"
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
    # adcp 3.12: FormatCategory/type filter removed from ListCreativeFormatsRequest.
    # Scenarios that rely on type filter or type-based sorting can no longer pass.
    "T-UC-005-main-filtered": "adcp 3.12: type filter removed from ListCreativeFormatsRequest",
    "T-UC-005-inv-031-1-holds": "adcp 3.12: type filter removed — combined type+asset_types AND filter not possible",
    "T-UC-005-inv-031-1-violated": "adcp 3.12: type filter removed — combined type+asset_types AND filter not possible",
    "T-UC-005-inv-031-2-holds": "adcp 3.12: type field removed — sort by type then name not possible",
    "T-UC-005-inv-049-1-holds": "adcp 3.12: type filter removed from ListCreativeFormatsRequest",
    "T-UC-005-inv-049-1-violated": "adcp 3.12: type filter removed from ListCreativeFormatsRequest",
    # Un-graduated: T-UC-005-sandbox-happy — sandbox=True not set on response (all transports)
    "T-UC-005-sandbox-happy": "sandbox mode not implemented in list_creative_formats response — spec-production gap",
    # Un-graduated: T-UC-005-sandbox-validation — sandbox validation not triggered (all transports)
    "T-UC-005-sandbox-validation": "sandbox validation not triggered for invalid filters — spec-production gap",
    # Un-graduated: T-UC-005-main-referrals — creative agent referrals empty in response (all transports)
    "T-UC-005-main-referrals": "creative agent referrals not populated in list_creative_formats response — spec-production gap",
    # FIXME: T-UC-005-main — format 'audio-spot' has no assets or renders (all transports)
    "T-UC-005-main": "some formats (e.g. audio-spot) lack asset_requirements and render_capabilities — spec-production gap",
    # Partially graduated: dispatch fix landed (salesagent-40kk); error code mismatch remains
    # FIXME(salesagent-40kk): production raises AUTH_TOKEN_INVALID, spec expects TENANT_REQUIRED
    "T-UC-005-ext-a": "error code AUTH_TOKEN_INVALID instead of TENANT_REQUIRED — spec-production gap",
    # Graduated: creative agent partition/boundary tests (salesagent-7fqx)
    # Steps now dispatch through harness — all 34 tests pass across 4 transports.
    # FIXME(beads-dul): suggestion field not in production error model
    "T-UC-005-ext-b": "suggestion field not implemented in error responses",
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
    # RESOLVED: T-UC-004-webhook-hmac — DB setup fix exposed that Then steps are pending (no-op).
    # Test passes trivially; real HMAC assertion gap tracked separately.
    # RESOLVED: T-UC-004-webhook-creds-short — DB setup fix exposed that Then steps are pending (no-op).
    # Test passes trivially; real credential assertion gap tracked separately.
    # FIXME(salesagent-n3y): UC-002 account field absent — production doesn't require account field
    # Spec says account is required (BR-RULE-080 INV-1), but production accepts requests without it.
    "T-UC-002-inv-080-1": "account field not required by production — spec-production gap",
    # FIXME(salesagent-9vgz.92): rate limiting + payload size validation not implemented
    # Rate limiting middleware does not exist (AdCPRateLimitError never raised).
    # No ASGI middleware checks content-length for oversized bodies.
    "T-UC-002-nfr-001": "rate limiting + payload size validation not implemented — spec-production gap",
}

# FIXME(beads-dul): Selective xfail for parametrized scenarios where only
# some examples exercise unimplemented features. Each entry: (tag, node_id
# substrings that should xfail, reason).
_SELECTIVE_XFAIL: list[tuple[str, set[str], str]] = [
    (
        "T-UC-005-partition-disclosure",
        {"duplicate_positions"},
        "disclosure_positions filter/validation not implemented",
    ),
    # Graduated: all_positions, no_matching_formats on impl (disclosure filter now partially works)
    # Non-impl transports still fail — handled in transport-aware section below.
    # MCP-specific disclosure xfails are in _MCP_SELECTIVE_XFAIL
    (
        "T-UC-005-boundary-disclosure",
        {"duplicate positions"},
        "disclosure_positions filter/validation not implemented",
    ),
    # Graduated: "all 8 positions", "format has no" on impl (disclosure filter now partially works)
    # Non-impl transports still fail — handled in transport-aware section below.
    # MCP-specific boundary disclosure xfails are in _MCP_SELECTIVE_XFAIL
    # adcp 3.12: type filter removed — only "invalid" examples fail (valid rows dispatch unfiltered and pass)
    (
        "T-UC-005-partition-type-filter",
        {"invalid_type"},
        "adcp 3.12: type filter removed from ListCreativeFormatsRequest — invalid type no longer rejected",
    ),
    (
        "T-UC-005-boundary-type-filter",
        {"invalid type (rejected)"},
        "adcp 3.12: type filter removed from ListCreativeFormatsRequest — invalid type no longer rejected",
    ),
    # Graduated: T-UC-005-boundary-asset-types (all 4 transports pass — brief/catalog now in enum)
    # Graduated: T-UC-005-partition-agent-type, T-UC-005-boundary-agent-type,
    # T-UC-005-boundary-agent-asset — all pass now that When steps dispatch through harness.
    # FIXME(salesagent-4ydt): BR-RULE-029 defines 4 notification types but production
    # WebhookDeliveryService only emits {scheduled, final, adjusted}. No is_delayed flag.
    (
        "T-UC-004-webhook-notification-type",
        {"delayed"},
        "BR-RULE-029: production webhook service has no is_delayed flag — only scheduled/final/adjusted emitted",
    ),
]


# MCP selective xfails: the MCP wrapper doesn't accept disclosure_positions.
# Only xfail examples that actually SEND the param — "omitted"/"not_provided"
# variants send no param and pass fine.
# (tag, example_substrings, reason, strict)
# strict=True  → must fail (genuine xfail)
# strict=False → may pass vacuously (MCP errors → empty list → exclusion assertions pass)
_MCP_SELECTIVE_XFAIL: list[tuple[str, set[str], str, bool]] = [
    # MCP wrapper does not accept disclosure_positions keyword (strict=False: some variants xpass)
    (
        "T-UC-005-partition-disclosure",
        {"single_position", "multiple_positions_all_match", "duplicate_positions"},
        "MCP wrapper: disclosure_positions not accepted or not validated",
        False,
    ),
    (
        "T-UC-005-boundary-disclosure",
        {"single position", "duplicate positions"},
        "MCP wrapper: disclosure_positions not accepted or not validated",
        False,
    ),
]

# REST xfails: REST endpoint drops all filter params (build_rest_body returns {}).
# Only xfail scenarios that genuinely fail — many invariant "holds" scenarios
# pass coincidentally because unfiltered results include the expected format.
_REST_XFAIL_TAGS: set[str] = {
    # Invariant filter scenarios where REST unfiltered results break assertions
    # NOTE: inv-049-1-holds/violated moved to _XFAIL_TAGS (adcp 3.12 type filter removal affects all transports)
    "T-UC-005-inv-049-2-holds",  # format_ids filter
    "T-UC-005-inv-049-3-violated",  # asset_types filter
    "T-UC-005-inv-049-4-violated",  # dimension filter
    "T-UC-005-inv-049-4-edge",  # dimension filter (formats without dimensions)
    "T-UC-005-inv-049-4-nodim",  # dimension filter (no dimensions)
    "T-UC-005-inv-049-5-holds",  # responsive=true filter
    "T-UC-005-inv-049-6-holds",  # responsive=false filter
    "T-UC-005-inv-049-7-holds",  # name_search filter
    "T-UC-005-inv-049-7-violated",
    # Un-graduated: inv-049-9 and inv-049-10 (REST drops output_format_ids/input_format_ids again)
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
        is_impl = "[impl]" in nodeid or "[impl-" in nodeid
        is_e2e_rest = "[e2e_rest]" in nodeid or "[e2e_rest-" in nodeid

        # Graduated: UC-005 creative agent type/asset_type filter tests now pass —
        # When steps dispatch through harness (blanket xfail removed).

        # Transport-specific xfails: MCP wrappers don't accept certain filter params
        if is_mcp:
            for tag, substrings, reason, strict in _MCP_SELECTIVE_XFAIL:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                    break

        # UC-011 REST: per-request auth implemented (salesagent-xms)
        # UC-011 MCP: billing policy and approval mode now populated from DB via
        # account_approval_mode column + proper harness writes (#1184 complete).

        # Graduated: T-UC-011-ext-d-push — push notification test now passes
        # (approval workflow implemented or assertion adjusted)

        # Graduated (salesagent-9d5): UC-006 REST account resolution — REST route
        # now forwards account param correctly (our branch fixed this).
        # NOTE: success-path works but error-path still fails — REST endpoint
        # returns 200 OK when account resolution should raise an error.
        # FIXME: sync_creatives REST endpoint does not propagate account
        # resolution errors (ACCOUNT_NOT_FOUND, ACCOUNT_AMBIGUOUS, etc.).
        if is_rest and marker_names & {"T-UC-006-partition-account", "T-UC-006-boundary-account"}:
            _acct_error_substrings = {
                "not_found",
                "not found",
                "no match",
                "key_ambiguous",
                "multiple matches",
                "setup_required",
                "setup incomplete",
                "payment_required",
                "payment due",
                "suspended",
            }
            if any(s in nodeid for s in _acct_error_substrings):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="REST: sync_creatives endpoint returns 200 instead of account resolution error",
                        strict=False,
                    )
                )

        # RESOLVED: MCP transport suggestion field now correctly unpacked by
        # _unwrap_mcp_tool_error (was double-nesting the extra JSON blob).

        # Transport-specific xfails: REST drops all filter params
        if is_rest:
            for tag in _REST_XFAIL_TAGS:
                if tag in marker_names:
                    item.add_marker(pytest.mark.xfail(reason="REST endpoint drops filter params", strict=True))
                    break

        # E2E_REST: Docker always has the creative agent — can't test empty catalog
        if is_e2e_rest and "T-UC-005-empty-catalog" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="E2E Docker always has creative agents — cannot test empty catalog",
                    strict=True,
                )
            )

        # FIXME(salesagent-nmg9, salesagent-rwly, salesagent-hamk): E2E_REST —
        # set_registry_formats has no sidecar mock path. Docker's real creative
        # agent serves its own catalog, so scenarios that inject specific format
        # fixtures via Given steps and assert on those names can't run against
        # E2E. Remove when E2E gains catalog-injection.
        _UC005_E2E_FIXTURE_INJECTION_TAGS: set[str] = {
            "T-UC-005-inv-031-1-holds",
            # Graduated e2e_rest: inv-031-1-violated, inv-049-3-violated,
            # inv-049-4-violated, inv-049-4-nodim (pass with strong assertions)
            "T-UC-005-inv-031-2-holds",
            "T-UC-005-inv-049-1-holds",
            "T-UC-005-inv-049-1-violated",
            "T-UC-005-inv-049-2-holds",
            "T-UC-005-inv-049-2-violated",
            "T-UC-005-inv-049-3-holds",
            "T-UC-005-inv-049-3-group",
            "T-UC-005-inv-049-4-holds",
            "T-UC-005-inv-049-5-holds",
            "T-UC-005-inv-049-6-holds",
            "T-UC-005-inv-049-7-holds",
            "T-UC-005-inv-049-7-violated",
            # Graduated: inv-049-9 and inv-049-10 (u04y: no e2e_rest variants exist)
            "T-UC-005-dim-boundary",
        }
        if is_e2e_rest and (marker_names & _UC005_E2E_FIXTURE_INJECTION_TAGS):
            item.add_marker(
                pytest.mark.xfail(
                    reason="E2E: set_registry_formats has no sidecar mock — real creative agent catalog used",
                    strict=False,
                )
            )

        # FIXME(salesagent-got8): E2E_REST — webhook/circuit assertions observe
        # env.mock['post'] or CircuitBreaker state, neither of which is visible
        # through the Docker HTTP path. Remove when an E2E webhook receiver or
        # circuit-breaker introspection is available.
        _UC004_E2E_WEBHOOK_INTERNAL_TAGS: set[str] = {
            "T-UC-004-webhook-bearer",
            "T-UC-004-webhook-hmac",
            "T-UC-004-webhook-notification-type",
            "T-UC-004-webhook-no-aggregated",
            "T-UC-004-webhook-circuit-open",
            "T-UC-004-webhook-circuit-recovery",
            "T-UC-004-webhook-retry-success",
        }
        if is_e2e_rest and (marker_names & _UC004_E2E_WEBHOOK_INTERNAL_TAGS):
            item.add_marker(
                pytest.mark.xfail(
                    reason="E2E: webhook POST mock + CircuitBreaker state not observable through Docker HTTP",
                    strict=False,
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
        # keyword additions but returns empty affected_packages. All transports pass the When
        # step (no error) but the Then step "affected_packages including pkg_001" fails.
        if "T-UC-003-alt-keyword-ops" in marker_names:
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
        if "T-UC-003-alt-creatives-inline" in marker_names:
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
            # Graduated: T-UC-003-ext-o (rczc: adapter failure returns correct shape on all 4 transports)
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

        # workflow_step_id is an internal field (exclude=True in schema).
        # impl/a2a return raw Python objects where the attribute is accessible
        # via hasattr/getattr even with exclude=True. mcp/rest/e2e_rest serialize
        # via model_dump() which drops exclude=True fields — xfail only those.
        if "T-UC-002-alt-manual" in marker_names and (is_mcp or is_rest or is_e2e_rest):
            item.add_marker(
                pytest.mark.xfail(
                    reason="workflow_step_id is internal (exclude=True), dropped during serialization",
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
            # Graduated MCP: inv-049-8-violated, inv-049-8-nofield
            # (MCP now passes with strong assertions; impl/a2a/rest still xfail)
            "T-UC-005-inv-049-8-violated",
            "T-UC-005-inv-049-8-nofield",
        }
        if marker_names & _UC005_PARTIAL_TAGS and not is_mcp and not is_e2e_rest:
            item.add_marker(pytest.mark.xfail(reason="disclosure/asset partial impl", strict=False))
            # Skip selective xfails for these — the strict=False above covers them
        else:
            # Graduated disclosure examples on impl — still fail on a2a/mcp/rest
            if not is_impl and not is_e2e_rest:
                if "T-UC-005-partition-disclosure" in marker_names:
                    if any(s in item.nodeid for s in ("all_positions", "no_matching_formats")):
                        item.add_marker(
                            pytest.mark.xfail(
                                reason="disclosure_positions filter not implemented on non-impl transports",
                                strict=True,
                            )
                        )
                elif "T-UC-005-boundary-disclosure" in marker_names:
                    if any(s in item.nodeid for s in ("all 8 positions", "format has no")):
                        item.add_marker(
                            pytest.mark.xfail(
                                reason="disclosure_positions filter not implemented on non-impl transports",
                                strict=True,
                            )
                        )

            # Selective xfail for parametrized scenarios
            for tag, substrings, reason in _SELECTIVE_XFAIL:
                if tag in marker_names:
                    if any(s in item.nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break  # tag matched — skip remaining selective entries

        # Original rejection scenario missing webhook Given step.
        # Replaced by BR-UC-002-manual-overrides.feature with webhook config.
        if "T-UC-002-alt-manual-reject" in marker_names and "T-UC-002-alt-manual-reject-override" not in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="missing webhook Given step — see test_uc002_manual_overrides.py",
                    strict=False,
                )
            )

        # NFR-006: original dispatch-in-Then scenario replaced by
        # BR-UC-002-nfr-enforcement.feature with proper Given/When/Then structure.
        if "T-UC-002-nfr-006" in marker_names:
            item.add_marker(
                pytest.mark.skip(
                    reason="replaced by test_uc002_nfr_enforcement.py::test_budget_below_minimum_order_size_is_rejected",
                )
            )

        # UC-002: e2e_rest auth middleware — unauthenticated_request graduated (pzqp),
        # but identity_missing still fails (error shape differs from spec).
        if is_e2e_rest and "T-UC-002-nfr-001-enforcement" in marker_names:
            if "unauthenticated_request" not in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: Docker auth middleware rejects with AUTH_REQUIRED "
                        "before business logic — error shape differs from spec",
                        strict=True,
                    )
                )

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
        _UC006_AUTH_XFAIL = {"T-UC-006-ext-a"}
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
            # boundary-format-id: error-path examples need "suggestion" field
            (
                "T-UC-006-boundary-format-id",
                {"suggestion"},
                "SPEC-PRODUCTION GAP: _SyntheticError lacks suggestion field",
            ),
        ]
        if any(t.startswith("T-UC-006") for t in marker_names):
            for tag, substrings, reason in _UC006_VALIDATION_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break

        # --- UC-006: spec-production gaps surfaced by Wave 1B step implementations ---
        # Production uses generic error codes / plain-string errors where the spec
        # demands specific codes and structured AdCPError with suggestion fields.
        _UC006_SPECGAP_XFAIL_TAGS: dict[str, str] = {
            # Error-path scenarios: production returns CREATIVE_VALIDATION_FAILED or
            # plain-string errors[] instead of spec-specific error codes / AdCPError.
            # See _processing.py error handling paths.
            "T-UC-006-ext-d-whitespace": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion"
            ),
            "T-UC-006-ext-f": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_VALIDATION_FAILED, spec expects CREATIVE_FORMAT_UNKNOWN"
            ),
            "T-UC-006-ext-g": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_VALIDATION_FAILED, spec expects CREATIVE_AGENT_UNREACHABLE"
            ),
            "T-UC-006-ext-h": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion "
                "(preview-failure path, _processing.py:712-737)"
            ),
            "T-UC-006-ext-i": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion "
                "(GEMINI_API_KEY not configured path)"
            ),
            # Creative unchanged: production returns action "updated" not "unchanged"
            "T-UC-006-main-unchanged": (
                "SPEC-PRODUCTION GAP: production returns action 'updated', "
                "spec expects 'unchanged' when creative data is identical"
            ),
            # ext-c: schema violation — wrong error code
            "T-UC-006-ext-c": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_FORMAT_REQUIRED, "
                "spec expects CREATIVE_VALIDATION_FAILED for schema violations"
            ),
            # ext-d: empty name — _SyntheticError lacks suggestion field
            "T-UC-006-ext-d": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion"
            ),
            # ext-e: missing format_id — wrong error code
            "T-UC-006-ext-e": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_VALIDATION_FAILED, "
                "spec expects CREATIVE_FORMAT_REQUIRED for missing format_id"
            ),
            # Invariant scenarios: production behaviour diverges from spec
            "T-UC-006-rule-039-inv2": (
                "SPEC-PRODUCTION GAP: AdCPValidationError has no details dict — "
                "cannot contain 'suggestion' field (spec requires suggestion for "
                "format mismatch per BR-RULE-039 INV-2)"
            ),
            # FIXME(#TBD): ext-k: format mismatch raises VALIDATION_ERROR, spec expects FORMAT_MISMATCH
            # _assignments.py:146 raises AdCPValidationError(error_msg) which has
            # error_code='VALIDATION_ERROR'. Spec expects 'FORMAT_MISMATCH' with suggestion.
            "T-UC-006-ext-k": (
                "SPEC-PRODUCTION GAP: format mismatch raises AdCPValidationError "
                "(VALIDATION_ERROR) — spec expects FORMAT_MISMATCH with suggestion "
                "and list_creative_formats hint (BR-RULE-039)"
            ),
            # FIXME(#TBD): inv5-lenient: lenient mode format mismatch doesn't populate assigned_to
            # In lenient mode, the compatible package assignment should be created
            # and incompatible reported in assignment_errors. Production skips both
            # because the creative-not-found guard or format check logic prevents
            # the compatible assignment from completing.
            "T-UC-006-rule-039-inv5-lenient": (
                "SPEC-PRODUCTION GAP: lenient format mismatch does not create "
                "compatible assignment — assigned_to is empty (BR-RULE-039 INV-5)"
            ),
            # T-UC-006-rule-037-inv5: e2e_rest only — handled below with transport check
            # Sandbox: sync_creatives does not set sandbox=true on response
            "T-UC-006-sandbox-happy": (
                "SPEC-PRODUCTION GAP: sync_creatives does not set sandbox=true on "
                "response for sandbox accounts (BR-RULE-209 INV-4)"
            ),
            # Sandbox: invalid format_id does not trigger validation error at _impl level
            "T-UC-006-sandbox-validation": (
                "SPEC-PRODUCTION GAP: production does not validate format_id pattern "
                "at _impl level — invalid format_id processed without error (BR-RULE-209 INV-7)"
            ),
        }
        for tag, reason in _UC006_SPECGAP_XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))

        # UC-006: assignment_package_validation — PACKAGE_NOT_FOUND outcome not
        # wired in the Then step dispatch (raises ValueError). The production
        # error is AdCPNotFoundError('NOT_FOUND'), spec demands 'PACKAGE_NOT_FOUND'.
        if "T-UC-006-partition-assignment-pkg" in marker_names and "package_not_found" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason=(
                        "SPEC-PRODUCTION GAP: outcome 'PACKAGE_NOT_FOUND' not in Then dispatch — "
                        "production returns AdCPNotFoundError(code='NOT_FOUND'), spec expects "
                        "'PACKAGE_NOT_FOUND'. See _assignments.py:62-69"
                    ),
                    strict=True,
                )
            )

        # UC-006: format_validation_boundary agent-unreachable — production returns
        # success with per-creative action="failed" instead of raising an error.
        if "T-UC-006-boundary-format-id" in marker_names and "agent unreachable" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason=(
                        "SPEC-PRODUCTION GAP: agent-unreachable returns success with "
                        "per-creative action='failed', not a top-level error — "
                        "Then step expects ctx['error'] but gets ctx['response']"
                    ),
                    strict=True,
                )
            )

        # Graduated: T-UC-004-webhook-bearer, T-UC-004-webhook-hmac,
        # T-UC-004-webhook-no-aggregated, T-UC-004-webhook-notification-type
        # (integration CircuitBreakerEnv now has make_webhook_config/set_db_webhooks
        # so webhook POST fires on all transports)

        # --- UC-004: boundary-account a2a — dict serialization mismatch ---
        # A2A transport returns dict objects that lack .root attribute needed
        # by the Then step's account validation logic.
        if is_a2a and "T-UC-004-boundary-account" in marker_names:
            _acc_valid_a2a = any(s in nodeid for s in ("account exists", "single match"))
            if _acc_valid_a2a:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="A2A transport: account dict lacks .root attribute — serialization gap",
                        strict=False,
                    )
                )

        # --- UC-004: xfails for unimplemented production features ---
        # FIXME(salesagent-ckb): These production features are not yet implemented.
        # strict=True: test MUST fail. strict=False: test MAY pass (some examples work).
        _UC004_XFAIL_TAGS: dict[str, tuple[str, bool]] = {
            # Empty array validation: schema allows [] but spec says reject
            "T-UC-004-identify-empty": ("empty media_buy_ids=[] not rejected by schema", True),
            "T-UC-004-identify-buyer-refs-empty": (
                "buyer_refs removed in adcp 3.12 — empty buyer_refs=[] is now an unknown field, silently ignored",
                True,
            ),
            # Invalid status filter: production doesn't validate enum values
            "T-UC-004-filter-invalid": ("invalid status_filter values not rejected", True),
            # Date range validation: production doesn't validate start>end
            "T-UC-004-daterange-invalid": ("date range validation (start>end) not implemented", True),
            "T-UC-004-daterange-equal": ("date range validation (start==end) not implemented", True),
            # Webhook delivery: not yet in production
            "T-UC-004-webhook-scheduled": ("webhook delivery not implemented", True),
            # Graduated: T-UC-004-webhook-sequence (production fixed: sequence numbers now strictly ascending)
            # Graduated: T-UC-004-webhook-circuit-halfopen (merge from main fixed circuit breaker probe timing)
            # Graduated: T-UC-004-webhook-retry-5xx (production fixed: retry count now correct)
            # Graduated: T-UC-004-webhook-retry-network (ebb527c6 fixed the off-by-one)
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
            # T-UC-004-attr-supported: resolved — steps now assert attribution_window model and echo
            # T-UC-004-attr-unsupported: resolved — xfail now in step function for specific production gap
            # T-UC-004-attr-echo: resolved — vvx9 + ral2 fixed enum→str handling
            # T-UC-004-attr-omitted: resolved — vvx9 + ral2 fixed enum→str handling
            # T-UC-004-attr-campaign-valid: resolved — _impl now resolves campaign unit to days
            # campaign unit interval validation: _impl doesn't validate attribution_window
            "T-UC-004-attr-campaign-invalid": (
                "attribution_window campaign unit validation not implemented in _impl",
                True,
            ),
            # FIXME(salesagent-7ag5): _impl uses str(enum) instead of enum.value for sort_by metric
            # T-UC-004-dim-sortby-valid: resolved — sort_by now works in _impl
            # Graduated: T-UC-004-dim-sortby-fallback (impl, mcp, rest pass — only a2a still fails)
            # FIXME(#1376): _impl only supports by_placement, not by_device_type/by_geo/truncation
            "T-UC-004-dim-supported": ("by_device_type breakdown not implemented in _impl (only by_placement)", True),
            "T-UC-004-dim-truncated": ("truncation flags (by_*_truncated) not implemented in _impl", True),
            "T-UC-004-dim-complete": ("by_device_type_truncated flag not implemented in _impl", True),
            # T-UC-004-dim-geo-system: resolved — by_geo now populated by _impl
            # T-UC-004-dim-geo-postal: resolved — by_geo now populated by _impl
            "T-UC-004-dim-multi": ("by_device_type breakdown not implemented on PackageDelivery (by_geo works)", True),
            # Partial-success Error model lacks suggestion field and rich messages
            "T-UC-004-ext-a": ("partial-success Error needs suggestion field + authentication in message", True),
            "T-UC-004-ext-b": ("partial-success Error model needs suggestion field — production enhancement", True),
            "T-UC-004-ext-c": ("partial-success Error model needs suggestion field — production enhancement", True),
            "T-UC-004-ext-d": ("partial-success Error model needs suggestion field — production enhancement", True),
            # Graduated: T-UC-004-identify-partial, T-UC-004-identify-batch-ownership
            # (merge from main fixed _impl to silently omit missing/non-owned IDs per BR-RULE-030 INV-5)
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

        # Graduated: T-UC-004-dim-sortby-fallback — impl, mcp, rest pass; only a2a fails
        if "T-UC-004-dim-sortby-fallback" in marker_names and is_a2a:
            item.add_marker(
                pytest.mark.xfail(
                    reason="sort_by fallback: A2A transport drops by_placement from response — serialization gap",
                    strict=False,
                )
            )

        # UC-004 status filter: "active" works, other values may not
        _UC004_FILTER_SELECTIVE: list[tuple[str, set[str], str]] = [
            (
                # Graduated rejected/canceled: the status_filter family was masked
                # by a step-parser bug (the generic `with {request_params}` step
                # dropped the space-separated `status_filter "value"` form), not a
                # production gap. With the parser fixed, the filter correctly
                # excludes non-matching buys. paused/completed still xfail on a
                # separate d.status reporting gap. (pending_activation was stale —
                # not a real MediaBuyStatus value.)
                "T-UC-004-filter",
                {"paused", "completed"},
                "status_filter paused/completed: reported d.status not derived from persisted status",
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

        # UC-004 date range strict=False entry from main covers T-UC-004-daterange
        # (custom dates partially applied). T-UC-004-daterange-end-only is
        # promoted to strict=True in _UC004_GENUINE_XFAIL_ROWS below (debt C7).
        _UC004_DATE_SELECTIVE: list[tuple[str, set[str], str]] = [
            ("T-UC-004-daterange", set(), "custom date range partially applied"),
        ]
        if any(t.startswith("T-UC-004-daterange") for t in marker_names):
            for tag, substrings, reason in _UC004_DATE_SELECTIVE:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # Per-row strict=True xfails for partition/boundary scenarios where
        # blanket markers were removed and production gaps are real and named
        # (see docs/test-debt-bdd-strict-markers.md). strict=True forces marker
        # removal the moment the underlying gap closes.
        _UC004_GENUINE_XFAIL_ROWS: list[tuple[str, set[str], str]] = [
            (
                "T-UC-004-partition-reporting-dims",
                {"geo_missing_geo_level", "geo_metro_missing_system", "limit_zero", "limit_negative"},
                "Pydantic raises ValidationError, not AdCPError(INVALID_REQUEST, suggestion). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            (
                "T-UC-004-partition-attribution",
                {"interval_zero", "interval_negative", "invalid_unit", "invalid_model", "campaign_interval_not_one"},
                "Pydantic raises ValidationError, not AdCPError(INVALID_REQUEST, suggestion). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            (
                "T-UC-004-boundary-reporting-dims",
                {"geo with geo_level=metro but no system"},
                "AdCP spec defines metro/postal_area system requirement only in field description; no validator. See docs/test-debt-bdd-strict-markers.md item C10.",
            ),
            (
                "T-UC-004-boundary-attribution",
                {"unit=campaign with interval=2"},
                "AdCP Duration spec defines 'interval=1 when unit=campaign' only in description; no validator. Same gap as T-UC-004-attr-campaign-invalid. See docs/test-debt-bdd-strict-markers.md item C10.",
            ),
            # reporting-dims / attribution boundary invalid-rows: Pydantic DOES
            # reject these (missing geo_level / limit>=1 / enum), but the
            # error is not normalized to AdCPError(INVALID_REQUEST) at the
            # transport boundary — a2a wraps ValidationError in a bare
            # RuntimeError, rest returns a 422 detail dict — so the BDD
            # outcome assertion (expects AdCPError/ValidationError) fails.
            # Same C4 transport-boundary error-normalization gap. These rows
            # were previously covered by the blanket _UC004_BOUNDARY_TAGS
            # strict=False, which 18h.10 Phase-2 (salesagent-04zf et al.)
            # emptied; restored here as PRECISE strict=True tied to the real
            # gap (no vacuous blanket). Forces marker removal when the
            # transport-boundary error translator lands.
            # Transport-scoped: impl genuinely PASSES these (production raises
            # a bare ValidationError the outcome assertion accepts as a real
            # rejection). Only a2a (RuntimeError-wrap) / mcp / rest (422 detail)
            # fail the AdCPError/ValidationError type check — so xfail only
            # those three, never impl.
            (
                "T-UC-004-boundary-reporting-dims",
                {
                    "a2a-geo without geo_level",
                    "mcp-geo without geo_level",
                    "rest-geo without geo_level",
                    "a2a-limit=0 (below minimum)",
                    "mcp-limit=0 (below minimum)",
                    "rest-limit=0 (below minimum)",
                    "a2a-limit negative",
                    "mcp-limit negative",
                    "rest-limit negative",
                },
                "Pydantic rejects (missing geo_level / limit>=1) but error not normalized to "
                "AdCPError(INVALID_REQUEST) at the a2a/mcp/rest transport boundary "
                "(a2a RuntimeError-wrap, rest 422 detail). impl passes. "
                "See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            (
                "T-UC-004-boundary-attribution",
                {
                    "a2a-interval=0 (below minimum)",
                    "mcp-interval=0 (below minimum)",
                    "rest-interval=0 (below minimum)",
                    "a2a-unit=weeks (not in enum)",
                    "mcp-unit=weeks (not in enum)",
                    "rest-unit=weeks (not in enum)",
                    "a2a-model=last_click (not in enum)",
                    "mcp-model=last_click (not in enum)",
                    "rest-model=last_click (not in enum)",
                },
                "Pydantic rejects (interval>=1 / unit enum / model enum) but error not normalized to "
                "AdCPError(INVALID_REQUEST) at the a2a/mcp/rest transport boundary "
                "(a2a RuntimeError-wrap, rest 422 detail). impl passes. "
                "See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # C11 retired (salesagent-18h.1): the "production ignores buyer
            # start_date" failure was an artefact of the greedy with-params
            # step shadowing when_request_date_range and mis-parsing the
            # request. With correct step routing, production echoes the
            # buyer-supplied start_date/end_date in response.reporting_period,
            # so T-UC-004-daterange now genuinely passes (no strict xfail).
            #
            # date-range partition/boundary (salesagent-04zf, 18h.10 Phase-2):
            # when_partition/boundary_date_range now translate the descriptor
            # into real start_date/end_date (previously the axis name was sent
            # as a literal request field and rejected by extra=forbid, so the
            # blanket _UC004_{PARTITION,BOUNDARY}_TAGS strict=False masked a
            # broken step). With real wiring: the "valid" rows
            # (start_before_end / dates_omitted) genuinely PASS on all 4
            # transports (no marker). Only the "invalid" rows genuinely fail —
            # production does not reject start>=end (same real gap as
            # T-UC-004-daterange-invalid / -equal). strict=True forces marker
            # removal the moment start>=end validation lands. See
            # docs/test-debt-bdd-strict-markers.md item C4.
            (
                "T-UC-004-partition-date-range",
                {"start_after_end", "start_equals_end"},
                "production does not validate start_date>=end_date (same gap as "
                "T-UC-004-daterange-invalid/-equal). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # Transport-scoped: impl genuinely PASSES start>=end on the _impl
            # path now. a2a/mcp/rest still don't enforce the gap.
            (
                "T-UC-004-boundary-date-range",
                {
                    "a2a-start_date after end_date",
                    "mcp-start_date after end_date",
                    "rest-start_date after end_date",
                    "a2a-start_date equals end_date",
                    "mcp-start_date equals end_date",
                    "rest-start_date equals end_date",
                },
                "production does not validate start_date>=end_date on a2a/mcp/rest "
                "(impl passes). Same gap as T-UC-004-daterange-invalid/-equal. "
                "See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # end-only date_range default (salesagent-losz / debt C7, Gap G40):
            # when only end_date is provided, the spec says start_date defaults
            # to MediaBuy.created_at but production sets start = today-30d
            # (src/core/tools/media_buy_delivery.py:162-165). The scenario's
            # Then-step asserts the exact creation-date (2025-12-01), so the
            # row genuinely fails today — upgraded from the former vacuous
            # strict=False in _UC004_DATE_SELECTIVE to strict=True here.
            (
                "T-UC-004-daterange-end-only",
                set(),
                "production defaults start_date to today-30d when only end_date is given; "
                "spec says default to MediaBuy.created_at. See docs/test-debt-bdd-strict-markers.md item C7.",
            ),
            # ---- 18h.10 Phase-2: 7 more UC-004 fields reconciled ----
            # Each field's when_partition/boundary_<field> now translates the
            # Gherkin descriptor into the real request kwargs/setup it
            # represents (mirroring the typed when_request_* steps) instead of
            # routing the axis name through _dispatch_partition. With real
            # wiring the "valid" descriptors genuinely PASS (no marker); only
            # the descriptors below genuinely fail for a real, named
            # production gap, so they carry strict=True (forces marker removal
            # the moment the gap closes). See docs/test-debt-bdd-strict-markers.md.
            #
            # daily-breakdown (salesagent-1pl): include_package_daily_breakdown
            # is a real bool field; production lax-coerces non-boolean strings
            # ("yes"/"true" → True) instead of raising INVALID_REQUEST.
            (
                "T-UC-004-partition-daily-breakdown",
                {"non_boolean"},
                "production lax-coerces non-boolean strings to bool (no strict-bool "
                "validation, no AdCPError(INVALID_REQUEST)). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            (
                "T-UC-004-boundary-daily-breakdown",
                {"string 'true' (non-boolean type)"},
                "production lax-coerces non-boolean strings to bool (no strict-bool "
                "validation). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # account (salesagent-8n9): only the omitted/(field absent) rows
            # pass on every transport. The other rows fail transport-asym-
            # metrically — a2a/mcp/rest never parse/resolve AccountReference
            # at the boundary (resolve_account does account_ref.root on a raw
            # dict → RuntimeError); the invalid-account rows raise Pydantic
            # ValidationError instead of AdCPError(INVALID_REQUEST/
            # ACCOUNT_NOT_FOUND). Substrings are transport-prefixed so only
            # the genuinely-failing rows are marked (impl valid rows pass).
            (
                "T-UC-004-partition-account",
                {
                    "impl-invalid_oneOf_both",
                    "impl-account_not_found",
                    "impl-empty_object",
                    "a2a-explicit_account_id",
                    "a2a-natural_key",
                    "a2a-invalid_oneOf_both",
                    "a2a-account_not_found",
                    "a2a-empty_object",
                    "mcp-explicit_account_id",
                    "mcp-natural_key",
                    "mcp-invalid_oneOf_both",
                    "mcp-empty_object",
                    "rest-explicit_account_id",
                    "rest-natural_key",
                    "rest-invalid_oneOf_both",
                    "rest-empty_object",
                },
                "a2a/mcp/rest do not parse/resolve AccountReference at the transport "
                "boundary; invalid-account rows raise ValidationError not AdCPError. "
                "See docs/test-debt-bdd-strict-markers.md items C1/C2/C4.",
            ),
            (
                "T-UC-004-boundary-account",
                {
                    "impl-account_id present + not found",
                    "a2a-account_id present + account exists",
                    "a2a-brand + operator present",
                    "a2a-both account_id and brand/operator",
                    "a2a-account_id present + not found",
                    "a2a-empty object {}",
                    "mcp-account_id present + account exists",
                    "mcp-brand + operator present",
                    "mcp-both account_id and brand/operator",
                    # mcp-account_id present + not found genuinely passes
                    # (ValidationError satisfies 'invalid') — NOT marked.
                    "mcp-empty object {}",
                    "rest-account_id present + account exists",
                    "rest-brand + operator present",
                },
                "a2a/mcp/rest do not parse/resolve AccountReference at the transport "
                "boundary; invalid-account rows raise ValidationError not AdCPError. "
                "See docs/test-debt-bdd-strict-markers.md items C1/C2/C4.",
            ),
            # sampling (salesagent-03q): sampling_method is NOT a
            # GetMediaBuyDeliveryRequest field — the artifact-sampling feature
            # is entirely unimplemented. Only (omitted)/not_provided genuinely
            # pass; rest silently drops the unknown param so its named-method
            # rows accidentally "pass" (must NOT be marked). impl/a2a/mcp
            # named-method + every unknown_value/systematic row fails.
            (
                "T-UC-004-partition-sampling",
                {
                    "impl-random-random",
                    "impl-stratified",
                    "impl-recent",
                    "impl-failures_only",
                    "impl-unknown_value-systematic",
                    "a2a-random-random",
                    "a2a-stratified",
                    "a2a-recent",
                    "a2a-failures_only",
                    "a2a-unknown_value-systematic",
                    "mcp-random-random",
                    "mcp-stratified",
                    "mcp-recent",
                    "mcp-failures_only",
                    "mcp-unknown_value-systematic",
                    "rest-unknown_value-systematic",
                },
                "sampling_method is unimplemented in get_media_buy_delivery (no schema "
                "field); ValidationError not AdCPError (rest silently drops it). "
                "See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            (
                "T-UC-004-boundary-sampling",
                {
                    "impl-random (first enum value)",
                    "impl-failures_only (last enum value)",
                    "a2a-random (first enum value)",
                    "a2a-failures_only (last enum value)",
                    "a2a-Unknown string not in enum",
                    "mcp-random (first enum value)",
                    "mcp-failures_only (last enum value)",
                    "mcp-Unknown string not in enum",
                    "rest-Unknown string not in enum",
                },
                "sampling_method is unimplemented in get_media_buy_delivery (no schema "
                "field); ValidationError not AdCPError (rest silently drops it). "
                "See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # resolution (salesagent-lghk): all valid resolution modes pass on
            # all transports; the empty-array reject passes on impl/mcp/rest
            # (Pydantic ValidationError satisfies 'invalid'). Only a2a fails —
            # the A2A boundary wraps the min_length ValidationError in a bare
            # RuntimeError instead of AdCPError(INVALID_REQUEST).
            (
                "T-UC-004-partition-resolution",
                {"a2a-empty_array"},
                "A2A wraps the empty-array Pydantic ValidationError in a bare RuntimeError "
                "(not AdCPError). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            (
                "T-UC-004-boundary-resolution",
                {"a2a-empty array (schema reject)"},
                "A2A wraps the empty-array Pydantic ValidationError in a bare RuntimeError "
                "(not AdCPError). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # ownership (salesagent-lzf3): owner-matches rows pass on all
            # transports. owner-mismatch is the C3 security gap — cross-
            # principal access returns 200+empty instead of MEDIA_BUY_NOT_FOUND.
            (
                "T-UC-004-partition-ownership",
                {"owner_mismatch"},
                "cross-principal access returns 200+empty instead of "
                "AdCPError(MEDIA_BUY_NOT_FOUND). See docs/test-debt-bdd-strict-markers.md item C3.",
            ),
            # Transport-scoped: impl genuinely PASSES "principal differs from owner"
            # (production raises AdCPError on cross-principal access at the _impl
            # boundary). a2a/mcp/rest still return 200+empty — C3 gap remains there.
            (
                "T-UC-004-boundary-ownership",
                {
                    "a2a-principal differs from owner",
                    "mcp-principal differs from owner",
                    "rest-principal differs from owner",
                },
                "cross-principal access returns 200+empty instead of "
                "AdCPError(MEDIA_BUY_NOT_FOUND). impl genuinely passes. "
                "See docs/test-debt-bdd-strict-markers.md item C3.",
            ),
            # status-filter (salesagent-6vu): all valid single statuses +
            # arrays + (field absent) pass. pending_activation rows fail
            # (Gherkin uses a non-spec MediaBuyStatus — item B1); empty-array /
            # unknown-value "failed" rows raise ValidationError not
            # AdCPError(INVALID_REQUEST) — item C4.
            # partition: impl now genuinely PASSES single_pending (production
            # normalizes the legacy 'pending_activation' label). a2a/mcp/rest
            # still fail on the unknown-value/empty-array C4 normalization.
            (
                "T-UC-004-partition-status-filter",
                {
                    "a2a-single_pending",
                    "mcp-single_pending",
                    "rest-single_pending",
                    "a2a-empty_array",
                    "mcp-empty_array",
                    "rest-empty_array",
                    "a2a-unknown_value",
                    "mcp-unknown_value",
                    "rest-unknown_value",
                },
                "single_pending: Gherkin 'pending_activation' is not a valid AdCP "
                "MediaBuyStatus (item B1) — impl normalizes the legacy label, "
                "a2a/mcp/rest do not. empty_array/unknown_value: ValidationError "
                "not AdCPError(INVALID_REQUEST) (item C4). "
                "See docs/test-debt-bdd-strict-markers.md.",
            ),
            # boundary: pending_activation fails everywhere; the 'failed' /
            # '[] (empty array...)' rows pass on impl/rest (ValidationError
            # satisfies 'invalid') but fail on a2a/mcp — transport-prefixed
            # substrings so only the genuinely-failing rows are marked.
            (
                "T-UC-004-boundary-status-filter",
                {
                    "impl-pending_activation (first enum value)",
                    "a2a-pending_activation (first enum value)",
                    "a2a-failed (not in AdCP enum",
                    "a2a-[] (empty array, violates minItems)",
                    "mcp-pending_activation (first enum value)",
                    "mcp-failed (not in AdCP enum",
                    "rest-pending_activation (first enum value)",
                },
                "pending_activation: Gherkin value not a valid AdCP MediaBuyStatus "
                "(item B1). failed/[]: ValidationError not AdCPError on a2a/mcp (item C4). "
                "See docs/test-debt-bdd-strict-markers.md.",
            ),
            # credentials (salesagent-f8u4): FULLY reconciled — the When step
            # now validates the real AdCP reporting_webhook Authentication
            # model (scheme enum + credentials min_length=32). All 40 rows
            # genuinely PASS on all transports; NO strict=True entry needed
            # (same shape as the reconciled date-range valid rows).
        ]
        for tag, substrings, reason in _UC004_GENUINE_XFAIL_ROWS:
            if tag in marker_names and (not substrings or any(s in nodeid for s in substrings)):
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break

        # UC-004 boundary scenarios: strict=False because some examples pass.
        # Invalid boundary values SHOULD fail validation but production doesn't validate.
        # Valid boundary values pass through fine.
        # Graduated to transport-aware selective xfail:
        # T-UC-004-boundary-attribution, T-UC-004-boundary-daily-breakdown,
        # T-UC-004-boundary-account, T-UC-004-boundary-status-filter,
        # T-UC-004-boundary-resolution, T-UC-004-boundary-ownership,
        # T-UC-004-boundary-reporting-dims, T-UC-004-boundary-sampling,
        # T-UC-004-boundary-date-range
        _UC004_BOUNDARY_TAGS: set[str] = set()
        # Graduated: T-UC-004-boundary-credentials (transport-aware selective below)
        # Graduated: T-UC-004-boundary-reporting-dims (transport-aware selective below)
        # Graduated: T-UC-004-boundary-sampling (transport-aware selective below)
        # Graduated: T-UC-004-boundary-date-range (transport-aware selective below)
        # Graduated: T-UC-004-boundary-ownership (transport-aware below)
        if marker_names & _UC004_BOUNDARY_TAGS:
            item.add_marker(pytest.mark.xfail(reason="boundary validation partially implemented", strict=False))

        # Graduated: T-UC-004-boundary-credentials — impl passes invalid examples,
        # rest passes valid examples. A2A and MCP still fail on all examples.
        if "T-UC-004-boundary-credentials" in marker_names:
            _cred_invalid = any(s in nodeid for s in ("31 chars (rejected)", "Unknown auth scheme"))
            _cred_valid = any(s in nodeid for s in ("Bearer scheme", "HMAC-SHA256 scheme", "credentials = 32 chars"))
            _cred_passes = (is_impl and _cred_invalid) or (is_rest and _cred_valid)
            if not _cred_passes:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="webhook credentials boundary: validation gaps on this transport", strict=False
                    )
                )

        # Graduated: T-UC-004-boundary-ownership — impl-"differs" and rest-"matches" pass
        # Remaining failures: impl-matches, a2a-both, mcp-both, rest-differs
        if "T-UC-004-boundary-ownership" in marker_names:
            _ownership_passes = (not is_a2a and not is_mcp) and (
                (not is_rest and not is_e2e_rest and "differs from owner" in nodeid)
                or (is_rest and "matches owner" in nodeid)
                or (is_e2e_rest and "matches owner" in nodeid)
            )
            if not _ownership_passes:
                item.add_marker(
                    pytest.mark.xfail(reason="ownership boundary: validation gaps on some transports", strict=False)
                )

        # Graduated: T-UC-004-boundary-reporting-dims — all pass except:
        # "metro but no system" fails on all transports;
        # "geo without geo_level", "limit=0", "limit negative" fail on a2a only.
        if "T-UC-004-boundary-reporting-dims" in marker_names:
            _rdim_all_transport_fail = "geo_level=metro but no system" in nodeid
            # Post-merge: MCP and REST also return ToolError instead of AdCPError
            # for invalid reporting_dimensions (transport wrapping changed in adcp 3.12)
            _rdim_non_impl_fail = (is_a2a or is_mcp or is_rest) and any(
                s in nodeid for s in ("geo without geo_level", "limit=0 (below minimum)", "limit negative")
            )
            if _rdim_all_transport_fail or _rdim_non_impl_fail:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="reporting_dimensions boundary: validation gaps on some transports", strict=False
                    )
                )
            # Graduated: e2e_rest invalid reporting_dimensions examples now return 500
            # (not empty body), so the test handles them correctly.

        # Graduated: T-UC-004-boundary-sampling — "Not provided" passes everywhere;
        # "random"/"failures_only" pass on rest only; "Unknown string" passes on impl only.
        if "T-UC-004-boundary-sampling" in marker_names:
            _samp_not_rest_fail = (
                not is_rest
                and not is_e2e_rest
                and any(s in nodeid for s in ("random (first enum", "failures_only (last enum"))
            )
            _samp_not_impl_fail = not is_impl and not is_e2e_rest and "Unknown string not in enum" in nodeid
            if _samp_not_rest_fail or _samp_not_impl_fail:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="sampling_method boundary: not implemented on this transport", strict=False
                    )
                )
            # FIXME(#1270): e2e_rest: Docker doesn't validate sampling_method —
            # invalid enum value succeeds instead of failing.
            if is_e2e_rest and "Unknown string not in enum" in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: Docker does not validate sampling_method — invalid value succeeds",
                        strict=True,
                    )
                )

        # Graduated: T-UC-004-boundary-date-range — valid examples (before, omitted)
        # pass on rest; invalid examples (equals, after) pass on impl.
        if "T-UC-004-boundary-date-range" in marker_names:
            _dr_valid_fail = (
                not is_rest
                and not is_e2e_rest
                and any(s in nodeid for s in ("start_date before end_date", "dates omitted"))
            )
            _dr_invalid_fail = (
                not is_impl
                and not is_e2e_rest
                and any(s in nodeid for s in ("start_date equals end_date", "start_date after end_date"))
            )
            if _dr_valid_fail or _dr_invalid_fail:
                item.add_marker(
                    pytest.mark.xfail(reason="date_range boundary: validation gaps on some transports", strict=False)
                )
            # FIXME(#1270): e2e_rest: Docker doesn't validate date range params —
            # invalid cases (equals, after) succeed instead of failing.
            if is_e2e_rest and any(s in nodeid for s in ("start_date equals end_date", "start_date after end_date")):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: Docker does not validate date range — invalid cases succeed",
                        strict=True,
                    )
                )

        # Graduated: T-UC-004-boundary-attribution — valid examples now pass on ALL transports.
        # Invalid examples: most fail on a2a/mcp/rest; "campaign with interval=2" fails
        # on ALL 4 transports (production doesn't validate interval>1 for campaign unit).
        if "T-UC-004-boundary-attribution" in marker_names:
            _aw_invalid_all = {"interval=0", "unit=weeks", "model=last_click"}
            _aw_invalid_all_transports = {"unit=campaign with interval=2"}
            _aw_is_invalid_all = any(s in nodeid for s in _aw_invalid_all)
            _aw_is_invalid_all_transports = any(s in nodeid for s in _aw_invalid_all_transports)
            # Graduated: valid examples now pass on all transports (impl/a2a/mcp/rest).
            # Only invalid examples still need xfails.
            if _aw_is_invalid_all and (is_a2a or is_mcp or is_rest):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="attribution_window boundary: production gaps on this transport", strict=False
                    )
                )
            elif _aw_is_invalid_all_transports:
                # "campaign with interval=2": production accepts it on all transports
                # — spec says must be 1, production doesn't validate
                item.add_marker(
                    pytest.mark.xfail(
                        reason="attribution_window boundary: campaign unit interval=2 not validated — spec-production gap",
                        strict=False,
                    )
                )
            # Graduated: e2e_rest invalid attribution_window examples now return 500
            # (not empty body), so the test handles them correctly.

        # Graduated: T-UC-004-boundary-account — transport-aware.
        # "account_id present"/"brand + operator" (valid): fail on mcp/rest only.
        # "both account_id"/"empty object" (invalid): fail on a2a only.
        # "account_id not found" (invalid): fail on impl/a2a only.
        # "omitted": already PASS everywhere.
        if "T-UC-004-boundary-account" in marker_names:
            _acc_valid_fail = (is_mcp or is_rest) and any(s in nodeid for s in ("account exists", "single match"))
            _acc_invalid_fail = (is_a2a or is_mcp) and any(s in nodeid for s in ("both account_id", "empty object"))
            _acc_notfound_fail = (is_impl or is_a2a) and "not found" in nodeid
            if _acc_valid_fail or _acc_invalid_fail or _acc_notfound_fail:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="delivery account boundary: production gaps on this transport", strict=False
                    )
                )
            # e2e_rest: account fixture created in-process not visible to Docker DB
            # Graduated: "not found", "both account_id", "empty object" pass on e2e_rest
            if is_e2e_rest and any(s in nodeid for s in ("account exists", "single match")):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: account fixture not in Docker DB — lookup/validation fails",
                        strict=False,
                    )
                )

        # --- UC-004 boundary: selective xfail for graduated strong groups ---
        # Only the failing subset gets xfailed; clean-pass examples graduate to PASS.
        _UC004_BOUNDARY_SELECTIVE: list[tuple[str, set[str], str]] = [
            # include_package_daily_breakdown: only non_boolean fails (all transports)
            (
                "T-UC-004-boundary-daily-breakdown",
                {"non-boolean", "non_boolean", "string 'true'"},
                "include_package_daily_breakdown boundary: non-boolean validation not implemented",
            ),
            # media_buy_resolution: partial still fails on all transports
            # Graduated: "buyer_refs only" and "zero resolution" (all 4 transports pass)
            # Graduated: "empty array" passes on impl/mcp/rest (only a2a fails)
            # Clean-pass: media_buy_ids only, both provided, neither provided
            (
                "T-UC-004-boundary-resolution",
                {"partial resolution"},
                "media_buy_resolution boundary: production gaps on some transports",
            ),
            # Graduated: status_filter "not in AdCP enum" passes on impl+rest,
            # "empty array, violates" passes on impl+mcp+rest (transport-aware below)
        ]
        for tag, substrings, reason in _UC004_BOUNDARY_SELECTIVE:
            if tag in marker_names:
                if any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # Graduated boundary entries with transport-specific failures:
        # T-UC-004-boundary-resolution "empty array": only a2a still fails
        if "T-UC-004-boundary-resolution" in marker_names and is_a2a and "empty array" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason="media_buy_resolution boundary: empty array validation gap on a2a",
                    strict=False,
                )
            )
        # T-UC-004-boundary-status-filter: graduated per-transport
        # "not in AdCP enum" (failed): only a2a + mcp still fail
        # "empty array, violates" ([]): only a2a still fails
        if "T-UC-004-boundary-status-filter" in marker_names:
            if "not in AdCP enum" in nodeid and (is_a2a or is_mcp):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="status_filter boundary: invalid enum validation not implemented on a2a/mcp",
                        strict=False,
                    )
                )
            elif "empty array, violates" in nodeid and is_a2a:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="status_filter boundary: empty array validation not implemented on a2a",
                        strict=False,
                    )
                )
            # Graduated: e2e_rest invalid status_filter examples now return 500
            # (not empty body), so the test handles them correctly.
            # adcp 3.12: pending_activation renamed to pending_start — feature file
            # still uses old name, schema rejects it as unknown enum value.
            if "pending_activation" in nodeid or "all 6 statuses" in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="adcp 3.12: pending_activation renamed to pending_start — feature file needs update",
                        strict=True,
                    )
                )

        # adcp 3.12: buyer_refs removed — "both provided" resolution tests
        # send both media_buy_ids and buyer_refs, but buyer_refs no longer exists.
        if "T-UC-004-boundary-resolution" in marker_names and "both provided" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason="adcp 3.12: buyer_refs removed — 'both provided' resolution test is obsolete",
                    strict=True,
                )
            )

        # Graduated: e2e_rest media_buy_resolution "empty array" now returns 500
        # (not empty body), so the test handles it correctly.

        # e2e_rest: principal_ownership "differs from owner" — ownership check not enforced
        # through REST layer; test succeeds when it should fail (strict=True xfail).
        if "T-UC-004-boundary-ownership" in marker_names and is_e2e_rest and "differs from owner" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason="e2e_rest: ownership boundary not enforced through REST — test succeeds unexpectedly",
                    strict=True,
                )
            )

        # e2e_rest: sort_by_metric_not_available — no by_placement breakdown in e2e_rest response
        if "T-UC-004-dim-sortby-fallback" in marker_names and is_e2e_rest:
            item.add_marker(
                pytest.mark.xfail(
                    reason="e2e_rest: by_placement breakdown not present in REST response — sort_by fallback untestable",
                    strict=True,
                )
            )

        # UC-004 partition scenarios: adcp 3.10 changed schema validation behavior.
        # Partition tests exercise valid/invalid value ranges per field.
        # strict=False: some partition values pass, others fail depending on schema version.
        _UC004_PARTITION_TAGS: set[str] = set()
        # Graduated (all 4 transports pass with strong assertions):
        # T-UC-004-partition-reporting-dims, T-UC-004-partition-attribution,
        # T-UC-004-partition-daily-breakdown, T-UC-004-partition-account,
        # T-UC-004-partition-sampling, T-UC-004-partition-status-filter,
        # T-UC-004-partition-date-range, T-UC-004-partition-resolution,
        # T-UC-004-partition-ownership
        # Graduated: T-UC-004-partition-credentials (transport-aware selective below)
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
            # Graduated: T-UC-004-partition-sampling (transport-aware block below)
            # "not_provided" passes all transports; valid named methods pass on REST only.
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

        # Graduated: T-UC-004-partition-credentials — impl passes invalid examples,
        # rest passes valid examples. A2A and MCP still fail on all examples.
        if "T-UC-004-partition-credentials" in marker_names:
            _pcred_invalid = any(s in nodeid for s in ("credentials_too_short", "unknown_scheme"))
            _pcred_valid = any(s in nodeid for s in ("hmac_sha256", "bearer_auth", "credentials_at_minimum"))
            _pcred_passes = (is_impl and _pcred_invalid) or (is_rest and _pcred_valid)
            if not _pcred_passes:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="webhook credentials partition: validation gaps on this transport", strict=False
                    )
                )

        # Graduated: T-UC-004-partition-sampling — "not_provided" passes all transports;
        # valid named methods (random, stratified, recent, failures_only) pass on REST only.
        # Non-REST + named method → still fails; unknown_value → fails on all transports.
        if "T-UC-004-partition-sampling" in marker_names and "not_provided" not in nodeid:
            _samp_named = {"random", "stratified", "recent", "failures_only"}
            _samp_is_named = any(s in nodeid for s in _samp_named)
            if _samp_is_named and (is_rest or is_e2e_rest):
                pass  # REST/e2e_rest + named method → passes, no xfail
            else:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="sampling_method not implemented in delivery _impl or transport wrappers",
                        strict=False,
                    )
                )

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
        # Graduated (k31s): status_computation active variants, default_status_filter
        # simple variants, status_filter boundary simple variants, inv-150-2/4,
        # inv-151-1, inv-152-1/2/3/5, inv-154-tenant, sandbox-production,
        # snapshot available variants, principal_scoping valid variants.
        _UC019_XFAIL_TAGS: set[str] = {
            # Status filter invalid — all parametrizations still fail
            "T-UC-019-partition-status-filter-invalid",
            # Creative approval mapping — not implemented
            "T-UC-019-partition-approval",
            "T-UC-019-partition-approval-invalid",
            "T-UC-019-boundary-approval",
            # Invariants that still fail entirely
            "T-UC-019-inv-150-1",
            "T-UC-019-inv-150-3",
            # Graduated: T-UC-019-inv-150-5 (status filter no longer blocks by-ID queries)
            "T-UC-019-inv-151-4",
            "T-UC-019-inv-153-3",
            "T-UC-019-inv-153-4",
            "T-UC-019-inv-153-5",
            # Sandbox mode — not implemented
            "T-UC-019-sandbox-happy",
            "T-UC-019-sandbox-validation",
            # Graduated: T-UC-019-partition-principal-invalid identity_missing (impl/a2a/mcp pass)
            # — moved to _UC019_PARAM_XFAIL for selective identity_missing exclusion.
            # Graduated: T-UC-019-ext-a (impl/a2a/mcp pass) — moved to selective block below.
            # Extension errors — error code mismatches / not implemented
            "T-UC-019-ext-b",
            "T-UC-019-ext-c",
            "T-UC-019-ext-d",
            "T-UC-019-ext-e",
            # Main flow snapshots — adapter not wired
            "T-UC-019-main-snapshot",
            # Transport-agnostic main scenario
            "T-UC-019-main",
        }
        if marker_names & _UC019_XFAIL_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-019 spec-production gap — feature not yet implemented",
                    strict=False,
                )
            )

        # --- UC-019: principal_id=null/empty/ghost boundary — unreachable via HTTP ---
        # BR-RULE-154 INV-3 tests defensive behavior when _impl receives a broken
        # identity (principal_id null/empty/not-found). This can't happen through
        # HTTP: a valid token always resolves to a real principal; an invalid token
        # gets rejected by auth middleware before _impl runs. These scenarios are
        # only testable at the _impl level (impl/a2a/mcp pass the identity directly).
        if (is_rest or is_e2e_rest) and "T-UC-019-boundary-principal" in marker_names:
            if any(
                s in nodeid
                for s in (
                    "principal_id is null",
                    "principal_id is empty string",
                    "principal_id not in registry",
                )
            ):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="HTTP transport: principal_id=null/empty/ghost is unreachable — "
                        "valid token always resolves to a real principal; invalid token "
                        "rejected by auth middleware before _impl. Test only valid at _impl level.",
                        strict=True,
                    )
                )

        # --- UC-019: HTTP transport xfails for auth suggestion mismatch ---
        # impl/a2a/mcp graduated (kb7y); REST/e2e_rest suggestion string differs
        # from spec ("authenticate" vs "authentication").
        if (is_rest or is_e2e_rest) and "T-UC-019-ext-a" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="HTTP transport: auth error suggestion says 'authenticate' not 'authentication' — spec-production gap",
                    strict=False,
                )
            )
        if (is_rest or is_e2e_rest) and "T-UC-019-partition-principal-invalid" in marker_names:
            if "identity_missing" in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="HTTP transport: auth error suggestion says 'authenticate' not 'authentication' — spec-production gap",
                        strict=False,
                    )
                )

        # --- UC-019: parametrization-specific xfails for partially-passing scenarios ---
        # These scenario outlines have some parametrizations that pass (graduated)
        # and some that still fail. Only the failing variants are xfailed.
        _UC019_PARAM_XFAIL: list[tuple[str, set[str], str]] = [
            # Graduated: T-UC-019-partition-status pre_flight/post_flight
            # (status filter no longer blocks by-ID queries)
            # Graduated: T-UC-019-boundary-status day before/day after
            # (status filter no longer blocks by-ID queries)
            # Default status filter: multi-status queries fail
            (
                "T-UC-019-partition-status-filter",
                {"multiple_statuses", "all_statuses"},
                "UC-019: multi-status filter not implemented",
            ),
            # Status filter boundary: complex filter variants fail
            (
                "T-UC-019-boundary-status-filter",
                {"all six", "empty array", "unknown enum", "mix of valid"},
                "UC-019: complex status filter boundary not implemented",
            ),
            # Snapshot: not-requested variant fails (include_snapshot=false path)
            (
                "T-UC-019-partition-snapshot",
                {"snapshot_not_requested"},
                "UC-019: snapshot_not_requested path not implemented",
            ),
            # Snapshot boundary: omitted/false/mixed variants fail
            (
                "T-UC-019-boundary-snapshot",
                {"include_snapshot omitted", "include_snapshot explicitly false", "mixed"},
                "UC-019: snapshot boundary omitted/false/mixed paths not implemented",
            ),
            # Graduated: identity_missing (impl/a2a/mcp) — only missing_principal_id
            # and principal_not_found still fail.
            (
                "T-UC-019-partition-principal-invalid",
                {"missing_principal_id", "principal_not_found"},
                "UC-019: principal_id missing/not-found not implemented",
            ),
        ]
        if any(t.startswith("T-UC-019") for t in marker_names):
            for tag, substrings, reason in _UC019_PARAM_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # --- UC-019: e2e_rest xfails for datetime-mock-dependent tests ---
        # These scenarios use `And today is "<date>"` which patches datetime
        # in-process. The patch has no effect on Docker — real datetime.now()
        # is used, so status assertions fail.
        if is_e2e_rest and any(t.startswith("T-UC-019") for t in marker_names):
            _UC019_E2E_DATETIME_TAGS: set[str] = {
                "T-UC-019-partition-status",
                "T-UC-019-boundary-status",
                "T-UC-019-inv-150-2",
                "T-UC-019-inv-150-4",
                "T-UC-019-inv-150-5",
                # Default filter test creates flight dates relative to mock_today
                # (default 2026-03-15), making both buys "completed" on real date.
                "T-UC-019-inv-151-1",
            }
            _UC019_E2E_MOCK_TAGS: set[str] = {
                # Adapter mock (get_adapter patch) has no effect in Docker.
                "T-UC-019-partition-snapshot",
                "T-UC-019-boundary-snapshot",
            }
            # Graduated e2e_rest examples that pass despite datetime/mock concern:
            # These variants have expected status=completed, which matches the
            # real date (all flight dates are in the past).
            _UC019_E2E_DT_GRADUATED = {
                ("T-UC-019-partition-status", "post_flight"),
                ("T-UC-019-boundary-status", "day after end_date"),
                ("T-UC-019-boundary-status", "start_date equals end_date and today is day after"),
            }
            _dt_graduated = any(tag in marker_names and substr in nodeid for tag, substr in _UC019_E2E_DT_GRADUATED)
            _inv150_5_graduated = "T-UC-019-inv-150-5" in marker_names  # all examples pass
            if marker_names & _UC019_E2E_DATETIME_TAGS and not _dt_graduated and not _inv150_5_graduated:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: datetime.now() mock has no effect in Docker — status computed from real date",
                        strict=False,
                    )
                )
            _UC019_E2E_MOCK_GRADUATED = {
                ("T-UC-019-partition-snapshot", "supported_but_unavailable"),
                # Only "snapshot null" passes on e2e_rest: Docker's mock adapter
                # has no test media buy data, so get_packages_snapshot returns None,
                # and production maps that to SNAPSHOT_TEMPORARILY_UNAVAILABLE —
                # matching the expected outcome. Other variants FAIL because:
                # - "snapshot returned"/"all packages" expect real snapshot data
                # - "does not support" expects UNSUPPORTED but mock says supported=True
                ("T-UC-019-boundary-snapshot", "snapshot null"),
            }
            _mock_graduated = any(tag in marker_names and substr in nodeid for tag, substr in _UC019_E2E_MOCK_GRADUATED)
            if marker_names & _UC019_E2E_MOCK_TAGS and not _mock_graduated:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: adapter mock has no effect in Docker — snapshot data not controllable",
                        strict=False,
                    )
                )
            # Un-graduated: T-UC-019-inv-154-tenant returns empty response on e2e_rest
            # because in-process fixture data doesn't populate Docker DB.
            if "T-UC-019-inv-154-tenant" in marker_names:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: cross-principal isolation test returns empty set — "
                        "in-process fixtures don't populate Docker DB",
                        strict=False,
                    )
                )
            # Graduated: T-UC-019-inv-152-1/2/5 (salesagent-kgmm: creative approval data seeded)
            # — only in-process transports graduated; e2e_rest still fails (below).

            # principal_scoping_boundary error cases are excluded on e2e_rest
            # (handled by the REST+e2e_rest block below, outside this if-block).

            # Graduated: T-UC-019-inv-152-1, T-UC-019-inv-152-2, T-UC-019-inv-152-5
            # (salesagent-pzqp: creative approval data now visible to e2e_rest Docker)

        # --- UC-026: xfails for spec-production gaps ---
        # Transport wiring done (a3xo: MediaBuyDualEnv routes updates correctly).
        # Remaining failures are production-level: AffectedPackage lacks full state,
        # keyword targeting ops not implemented, error codes/suggestions missing.
        # FIXME(salesagent-av7): UC-026 production gaps in update response and validation.
        _UC026_XFAIL_TAGS: set[str] = {
            # Graduated: T-UC-026-main-explicit-formats (qq6f: format_ids now echoed)
            # Full-config: optimization_goals missing `kind`, targeting_overlay.audiences extra_forbidden
            "T-UC-026-main-full-config",
            # Update alt-flows: AffectedPackage lacks budget/targeting_overlay/format_ids;
            # keyword_targets_add/remove and negative_keywords_add/remove not implemented
            "T-UC-026-alt-update",
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
            # Invariant scenarios — production validation gaps
            # Graduated: T-UC-026-inv-194-1 (all 4 transports pass)
            "T-UC-026-inv-194-2",
            "T-UC-026-inv-195-1",
            "T-UC-026-inv-195-2",
            # Graduated: T-UC-026-inv-195-3 (rczc: bid_price ceiling semantics pass all 4 transports)
            # Graduated: T-UC-026-inv-195-4 (rczc: bid_price exact semantics pass all 4 transports)
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
            # Graduated: T-UC-026-inv-089-2 (t8iq: catalogs now echoed, default pkg fields added)
            # Graduated: T-UC-026-inv-089-3 (all 4 transports pass)
            # Graduated to _UC026_PARTITION_SELECTIVE (x2l0): keyword boundary/partition
            # tags now mostly pass — only REST update dispatch + specific cross-transport
            # validation gaps remain. Selective xfail handles the narrower failure set.
        }
        if marker_names & _UC026_XFAIL_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-026 spec-production gap — AffectedPackage lacks full state / "
                    "keyword ops not implemented / error codes missing",
                    strict=False,
                )
            )

        # --- UC-026 partition/boundary: selective xfail for graduated tags ---
        # FIXME(salesagent-7wan): Remaining failures are production-level gaps.
        # x2l0: narrowed from set() (all-fail) after a3xo MediaBuyDualEnv wiring
        # graduated most partition/boundary examples. Two failure patterns remain:
        #   1. REST update dispatch: REST success-path update tests fail (error-path
        #      tests and create-path tests pass because validation catches them first)
        #   2. Cross-transport production gaps: conflict_with_overlay validation,
        #      creative_assignments/optimization_goals replacement, empty keyword
        #      validation not implemented
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
            # Graduated: T-UC-026-partition-format-ids (all 4 transports pass after a3xo)
            # max_bid validation: production requires bid_price for auction-based pricing
            (
                "T-UC-026-partition-pricing-option",
                {"valid_with_max_bid"},
                "max_bid pricing validation rejects valid ceiling semantics — spec-production gap",
            ),
            # FIXME(salesagent-e4ij): pricing option not-found / wrong-product returns
            # 'validation_error' instead of AdCP-spec 'INVALID_REQUEST'.
            (
                "T-UC-026-partition-pricing-option",
                {"pricing_option_not_found", "pricing_option_wrong_product"},
                "Production returns 'validation_error' instead of AdCP-spec 'INVALID_REQUEST' — "
                "AdCPValidationError caught and re-raised as plain ValueError, stripping error code",
            ),
            # Immutable: only REST success-path update tests fail (error tests pass)
            (
                "T-UC-026-partition-immutable",
                {"rest-update_mutable_only", "rest-no_immutable_fields_present"},
                "REST update dispatch not wired for partition immutable success tests",
            ),
            (
                "T-UC-026-boundary-immutable",
                {"rest-update with only mutable"},
                "REST update dispatch not wired for boundary immutable success tests",
            ),
            # Keyword add partition: only REST success-path tests fail
            (
                "T-UC-026-partition-keyword-add",
                {
                    "rest-new_keyword",
                    "rest-existing_keyword_update_bid",
                    "rest-mixed_new_and_update",
                    "rest-same_keyword_different_match",
                },
                "REST update dispatch not wired for partition keyword-add success tests",
            ),
            # Keyword remove partition: only REST success-path tests fail
            (
                "T-UC-026-partition-keyword-remove",
                {
                    "rest-remove_existing_pair",
                    "rest-remove_nonexistent_pair",
                    "rest-remove_all_keywords",
                    "rest-mixed_existing_and_nonexistent",
                },
                "REST update dispatch not wired for partition keyword-remove success tests",
            ),
            # Keyword boundary add: empty keyword string on impl/a2a/mcp +
            # REST success-path tests fail
            (
                "T-UC-026-boundary-keyword-add",
                {
                    "impl-empty keyword string",
                    "a2a-empty keyword string",
                    "mcp-empty keyword string",
                    "rest-single new keyword target",
                    "rest-existing (keyword, match_type) pair",
                    "rest-same keyword with broad and exact",
                    "rest-bid_price = 0",
                },
                "empty keyword validation not implemented / REST update not wired",
            ),
            # Keyword boundary remove: empty keyword string on impl/a2a/mcp +
            # REST success-path tests fail
            (
                "T-UC-026-boundary-keyword-remove",
                {
                    "impl-empty keyword string",
                    "a2a-empty keyword string",
                    "mcp-empty keyword string",
                    "rest-remove single existing",
                    "rest-remove non-existent pair",
                    "rest-remove all keyword targets",
                    "rest-mix of existing and non-existent",
                },
                "empty keyword validation not implemented / REST update not wired",
            ),
            # Keyword shared partition: conflict_with_overlay on impl/a2a/mcp +
            # REST success-path tests fail
            (
                "T-UC-026-partition-kw-add-shared",
                {
                    "impl-conflict_with_overlay",
                    "a2a-conflict_with_overlay",
                    "mcp-conflict_with_overlay",
                    "rest-typical_add",
                    "rest-add_with_bid_price",
                    "rest-add_without_bid_price",
                    "rest-all_match_types",
                    "rest-boundary_min_array",
                    "rest-boundary_min_keyword",
                    "rest-cross_dimension_valid",
                    "rest-upsert_existing",
                    "rest-zero_bid_price",
                },
                "conflict_with_overlay not implemented / REST update not wired",
            ),
            (
                "T-UC-026-partition-kw-remove-shared",
                {
                    "impl-conflict_with_overlay",
                    "a2a-conflict_with_overlay",
                    "mcp-conflict_with_overlay",
                    "rest-typical_remove",
                    "rest-all_match_types",
                    "rest-boundary_min_array",
                    "rest-boundary_min_keyword",
                    "rest-cross_dimension_valid",
                    "rest-remove_nonexistent",
                },
                "conflict_with_overlay not implemented / REST update not wired",
            ),
            # Keyword shared boundary: overlay conflict on impl/a2a/mcp +
            # REST success-path tests fail
            (
                "T-UC-026-boundary-kw-add-shared",
                {
                    "impl-keyword_targets_add WITH targeting_overlay.keyword_targets-error",
                    "a2a-keyword_targets_add WITH targeting_overlay.keyword_targets-error",
                    "mcp-keyword_targets_add WITH targeting_overlay.keyword_targets-error",
                    "rest-array length 1",
                    "rest-keyword length 1",
                    "rest-keyword_targets_add WITH targeting_overlay.negative_keywords",
                    "rest-keyword_targets_add WITHOUT",
                    "rest-match_type = 'broad'",
                    "rest-match_type = 'exact'",
                    "rest-match_type = 'phrase'",
                },
                "overlay conflict validation not implemented / REST update not wired",
            ),
            (
                "T-UC-026-boundary-kw-remove-shared",
                {
                    "impl-keyword_targets_remove WITH targeting_overlay.keyword_targets-error",
                    "a2a-keyword_targets_remove WITH targeting_overlay.keyword_targets-error",
                    "mcp-keyword_targets_remove WITH targeting_overlay.keyword_targets-error",
                    "rest-array length 1",
                    "rest-keyword length 1",
                    "rest-keyword_targets_remove WITHOUT",
                    "rest-match_type = 'broad'",
                    "rest-match_type = 'exact'",
                    "rest-match_type = 'phrase'",
                    "rest-remove pair that does NOT exist",
                    "rest-remove pair that exists",
                },
                "overlay conflict validation not implemented / REST update not wired",
            ),
            # Negative keyword partition: conflict_with_overlay on impl/a2a/mcp +
            # REST success-path tests fail
            (
                "T-UC-026-partition-neg-kw-add",
                {
                    "impl-conflict_with_overlay",
                    "a2a-conflict_with_overlay",
                    "mcp-conflict_with_overlay",
                    "rest-typical_add",
                    "rest-add_duplicate",
                    "rest-all_match_types",
                    "rest-boundary_min_array",
                    "rest-boundary_min_keyword",
                    "rest-cross_dimension_valid",
                },
                "conflict_with_overlay not implemented / REST update not wired",
            ),
            (
                "T-UC-026-partition-neg-kw-remove",
                {
                    "impl-conflict_with_overlay",
                    "a2a-conflict_with_overlay",
                    "mcp-conflict_with_overlay",
                    "rest-typical_remove",
                    "rest-all_match_types",
                    "rest-boundary_min_array",
                    "rest-boundary_min_keyword",
                    "rest-cross_dimension_valid",
                    "rest-remove_nonexistent",
                },
                "conflict_with_overlay not implemented / REST update not wired",
            ),
            # Negative keyword boundary: overlay conflict on impl/a2a/mcp +
            # REST success-path tests fail
            (
                "T-UC-026-boundary-neg-kw-add",
                {
                    "impl-negative_keywords_add WITH targeting_overlay.negative_keywords-error",
                    "a2a-negative_keywords_add WITH targeting_overlay.negative_keywords-error",
                    "mcp-negative_keywords_add WITH targeting_overlay.negative_keywords-error",
                    "rest-negative_keywords_add WITHOUT",
                    "rest-negative_keywords_add WITH targeting_overlay.keyword_targets",
                    "rest-add pair that already exists",
                    "rest-array length 1",
                    "rest-keyword length 1",
                    "rest-match_type = 'broad'",
                    "rest-match_type = 'exact'",
                    "rest-match_type = 'phrase'",
                },
                "overlay conflict validation not implemented / REST update not wired",
            ),
            (
                "T-UC-026-boundary-neg-kw-remove",
                {
                    "impl-negative_keywords_remove WITH targeting_overlay.negative_keywords-error",
                    "a2a-negative_keywords_remove WITH targeting_overlay.negative_keywords-error",
                    "mcp-negative_keywords_remove WITH targeting_overlay.negative_keywords-error",
                    "rest-negative_keywords_remove WITHOUT",
                    "rest-array length 1",
                    "rest-keyword length 1",
                    "rest-match_type = 'broad'",
                    "rest-match_type = 'exact'",
                    "rest-match_type = 'phrase'",
                    "rest-remove pair that does NOT exist",
                    "rest-remove pair that exists",
                },
                "overlay conflict validation not implemented / REST update not wired",
            ),
            # Paused: only REST update-path tests fail (create-path passes)
            (
                "T-UC-026-partition-paused",
                {"rest-pause_on_update", "rest-resume_on_update"},
                "REST update dispatch not wired for partition paused update tests",
            ),
            # d09y: boundary scenarios exposing real production gaps after step-parser fix.
            (
                "T-UC-026-boundary-pricing-option",
                {"empty string", "different product", "max_bid=true", "not in product", "matches last entry"},
                "pricing_option validation returns 'validation_error' instead of AdCP 'INVALID_REQUEST' / "
                "max_bid pricing requires bid_price / last-entry pricing_option rejects valid id — spec-production gap",
            ),
            # Paused boundary: only REST update-path tests fail (create-path passes)
            (
                "T-UC-026-boundary-paused",
                {
                    "rest-paused=false on update",
                    "rest-paused=true on update",
                    "rest-paused=true on already-paused",
                },
                "REST update dispatch not wired for boundary paused update tests",
            ),
            # Replacement: REST all tests fail (update dispatch) +
            # creative_assignments/optimization_goals on impl/a2a/mcp
            (
                "T-UC-026-partition-replacement",
                {
                    "creative_assignments",
                    "optimization_goals",
                    "rest-omit_array_fields",
                    "rest-replace_catalogs",
                    "rest-replace_targeting_overlay",
                },
                "creative_assignments/optimization_goals replacement not implemented / REST update not wired",
            ),
            (
                "T-UC-026-boundary-replacement",
                {
                    "creative_assignments",
                    "optimization_goals",
                    "rest-all array fields omitted",
                    "rest-catalogs provided",
                    "rest-only scalar fields updated",
                    "rest-targeting_overlay replacement",
                },
                "creative_assignments/optimization_goals replacement not implemented / REST update not wired",
            ),
        ]
        for tag, substrings, reason in _UC026_PARTITION_SELECTIVE:
            if tag in marker_names:
                if not substrings or any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))

        # --- UC-011: xfails for spec-production gaps ---
        # FIXME(salesagent-7wan): Production doesn't implement these UC-011 features.
        # Graduated: T-UC-011-list-status-filter payment_required (all 4 transports pass — status now mapped)
        # Graduated: T-UC-011-ext-g-echo list_accounts (all 4 transports pass — context echo implemented)

        # Graduated: no-token/no-principal scenarios now pass after Gherkin
        # correction to AUTH_REQUIRED (commit 13b4ca8d). Production returns
        # AUTH_REQUIRED on rest/e2e_rest, matching the corrected Gherkin.
        # Graduated: expired-token also passes — AUTH_TOKEN_INVALID matches.

        # Graduated: T-UC-011-ext-g-echo-error (all 4 transports pass — context echo now works in error response)
        # Graduated: T-UC-011-sync-missing-brand (all 4 transports pass — ValidationError now structured)
        # Graduated: T-UC-011-sync-missing-operator (all 4 transports pass — ValidationError now structured)
        # Graduated: T-UC-011-ext-f-scoped (all 4 transports now pass — deactivation scoping works on a2a)

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

    # ── Single-transport optimization for strict xfails ──────────────
    # Scenarios that xfail(strict=True) on ALL transports waste 3/4 of
    # their runtime running the same failure path on mcp/rest/a2a after
    # impl already proved it xfails. Deselect redundant transports.
    #
    # How it works: after the loop above, every item has its xfail markers.
    # We find items with strict xfail and deselect the non-impl variants.
    # The impl variant still runs → catches when production catches up (xpass).
    #
    # Opt out: set BDD_ALL_TRANSPORTS=1 to run everything (for full runs).
    if not os.environ.get("BDD_ALL_TRANSPORTS"):
        deselected: list[pytest.Item] = []
        remaining: list[pytest.Item] = []
        for item in items:
            nodeid = item.nodeid
            is_redundant_transport = (
                "[mcp]" in nodeid
                or "[mcp-" in nodeid
                or "[a2a]" in nodeid
                or "[a2a-" in nodeid
                or "[rest]" in nodeid
                or "[rest-" in nodeid
            )
            if not is_redundant_transport:
                remaining.append(item)
                continue
            # Check if this item has a strict xfail marker
            has_strict_xfail = any(m.name == "xfail" and m.kwargs.get("strict", False) for m in item.iter_markers())
            if has_strict_xfail:
                deselected.append(item)
            else:
                remaining.append(item)

        if deselected:
            items[:] = remaining
            config = items[0].config if items else None
            if config:
                config.hook.pytest_deselected(items=deselected)


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

    transports = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]
    ids = ["impl", "a2a", "mcp", "rest"]

    if os.environ.get("BDD_E2E_ENABLED") == "true":
        transports.append(Transport.E2E_REST)
        ids.append("e2e_rest")

    metafunc.parametrize("ctx", transports, ids=ids, indirect=True)


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
    if "inventory_profile" in marker_names:
        return "UC-GET-PRODUCTS"
    if any(t.startswith("T-COMPAT") for t in marker_names):
        return "COMPAT"
    return None


def _detect_uc011_harness(marker_names: set[str]) -> str:
    """Detect which UC-011 harness a scenario needs based on tags.

    When both @sync and @list are present (cross-cutting scenarios like
    sync-then-list), use sync harness — it's the superset and already has
    a cross-cutting list path via _list_accounts_impl.
    """
    has_list = "list" in marker_names
    has_sync = "sync" in marker_names
    if has_sync and has_list:
        return "sync"
    if has_list:
        return "list"
    if has_sync:
        return "sync"
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
            pytest.xfail("UC-002 harness not yet wired for non-account scenarios")

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
            pytest.xfail("UC-006 harness not yet wired for non-account scenarios")

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
            pytest.xfail(f"UC-011 harness not yet wired for markers: {marker_names}")

    elif uc == "ADMIN":
        request.getfixturevalue("integration_db")
        from tests.harness.admin_accounts import AdminAccountEnv

        # BDD suite always uses integration mode (Flask test_client).
        # E2E mode (requests.Session + Docker) is tested separately.
        with AdminAccountEnv(mode="integration") as env:
            ctx["env"] = env
            yield

    elif uc == "COMPAT":
        request.getfixturevalue("integration_db")
        from tests.harness.product import ProductEnv

        with ProductEnv() as env:
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
            request.getfixturevalue("integration_db")
            from tests.harness.delivery_webhook import WebhookEnv

            with WebhookEnv() as env:
                env.setup_default_data()
                ctx["env"] = env
                yield
        elif harness_type == "circuit-breaker":
            request.getfixturevalue("integration_db")
            from tests.harness.delivery_circuit_breaker import CircuitBreakerEnv

            with CircuitBreakerEnv() as env:
                env.setup_default_data()
                ctx["env"] = env
                yield
        else:
            pytest.xfail(f"UC-004 harness not yet wired for type: {harness_type}")
    elif uc == "UC-GET-PRODUCTS":
        request.getfixturevalue("integration_db")
        from tests.harness.product import ProductEnv

        with ProductEnv() as env:
            ctx["env"] = env
            yield
    else:
        pytest.xfail(f"No harness wired for {uc}")
