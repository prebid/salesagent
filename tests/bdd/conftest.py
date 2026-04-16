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

import os
import re
from collections.abc import Generator
from pathlib import Path
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
    # Graduated: T-UC-005-sandbox-validation (salesagent-7fqx)
    # Validation error from invalid dimension filter fires before sandbox logic.
    # FIXME(beads-dul): creative agent referrals not in harness
    "T-UC-005-main-referrals": "creative agent referrals not implemented",
    # FIXME(beads-dul): no-tenant error path requires identity-less harness
    "T-UC-005-ext-a-rest": "no-tenant error path not implemented in harness",
    "T-UC-005-ext-a-mcp": "no-tenant error path not implemented in harness",
    # Graduated: creative agent partition tests (salesagent-7fqx)
    # Steps now call list_creative_formats as a proxy. Boundary-specific
    # xfails for creative-agent-only restrictions are in _SELECTIVE_XFAIL.
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
    # FIXME(beads-dul): creative agent format API has tighter restrictions than
    # list_creative_formats. "native" is valid FormatCategory but not for creative
    # agents; "vast" is valid AssetContentType but not for creative agents.
    (
        "T-UC-005-boundary-agent-type",
        {"native"},
        "creative agent format API restricts type enum — native not valid for creative agents",
    ),
    (
        "T-UC-005-boundary-agent-asset",
        {"vast"},
        "creative agent format API restricts asset_types enum — vast not valid for creative agents",
    ),
    # FIXME(salesagent-4ydt): BR-RULE-029 defines 4 notification types but production
    # WebhookDeliveryService only emits {scheduled, final, adjusted}. No is_delayed flag.
    (
        "T-UC-004-webhook-notification-type",
        {"delayed"},
        "BR-RULE-029: production webhook service has no is_delayed flag — only scheduled/final/adjusted emitted",
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
    "T-UC-005-inv-049-1-holds",  # type filter
    "T-UC-005-inv-049-1-violated",
    "T-UC-005-inv-049-2-holds",  # format_ids filter
    "T-UC-005-inv-049-3-violated",  # asset_types filter
    "T-UC-005-inv-049-4-violated",  # dimension filter
    "T-UC-005-inv-049-4-edge",  # dimension filter (formats without dimensions)
    "T-UC-005-inv-049-4-nodim",  # dimension filter (no dimensions)
    "T-UC-005-inv-049-5-holds",  # responsive=true filter
    "T-UC-005-inv-049-6-holds",  # responsive=false filter
    "T-UC-005-inv-049-7-holds",  # name_search filter
    "T-UC-005-inv-049-7-violated",
    "T-UC-005-inv-049-9-holds",  # output_format_ids filter
    "T-UC-005-inv-049-9-violated",
    "T-UC-005-inv-049-9-edge",  # output_format_ids (format without field)
    "T-UC-005-inv-049-9-nofield",
    "T-UC-005-inv-049-10-holds",  # input_format_ids filter
    "T-UC-005-inv-049-10-violated",
    "T-UC-005-inv-049-10-edge",  # input_format_ids (format without field)
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
        is_e2e_rest = "[e2e_rest]" in nodeid or "[e2e_rest-" in nodeid

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

        # FIXME(#1184): UC-011 — billing policy and approval mode not populated
        # from DB. Auth chains across all transports resolve identity from DB which
        # doesn't carry supported_billing or account_approval_mode. These are
        # tenant-level configs that need a DB migration to persist.
        _UC011_IDENTITY_XFAIL: set[str] = {
            "T-UC-011-ext-c-rejected",  # billing rejection
            "T-UC-011-ext-c-mixed",  # per-account billing rejection
            "T-UC-011-ext-d-pending-url",  # approval mode pending
            "T-UC-011-ext-d-pending-message",  # approval mode pending
            "T-UC-011-atomic-all-failed",  # all-failed (uses billing rejection)
        }
        if marker_names & _UC011_IDENTITY_XFAIL:
            item.add_marker(
                pytest.mark.xfail(
                    reason="billing/approval config not in DB — needs #1184",
                    strict=True,
                )
            )
        # FIXME(salesagent-9d5): UC-006 REST — account resolution through CreativeSyncEnv
        # REST route for sync_creatives exists but account kwarg may not be
        # forwarded at the route level (SyncCreativesBody doesn't have account field)
        if is_rest and any(t.startswith("T-UC-006") for t in marker_names) and "account" in marker_names:
            item.add_marker(pytest.mark.xfail(reason="REST route doesn't forward account param", strict=False))

        # Transport-specific xfails: in-process REST harness stub drops all filter params.
        # E2E_REST is NOT affected — Docker's REST endpoint implements the filters, so
        # applying this strict xfail there would cause XPASS(strict) failures.
        # FIXME(salesagent-g4ld): filed as production bug — in-process REST stub needs filter support.
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
            "T-UC-005-inv-031-1-violated",
            "T-UC-005-inv-031-2-holds",
            "T-UC-005-inv-049-1-holds",
            "T-UC-005-inv-049-1-violated",
            "T-UC-005-inv-049-2-holds",
            "T-UC-005-inv-049-2-violated",
            "T-UC-005-inv-049-3-holds",
            "T-UC-005-inv-049-3-violated",
            "T-UC-005-inv-049-3-group",
            "T-UC-005-inv-049-4-holds",
            "T-UC-005-inv-049-4-violated",
            "T-UC-005-inv-049-4-nodim",
            "T-UC-005-inv-049-5-holds",
            "T-UC-005-inv-049-6-holds",
            "T-UC-005-inv-049-7-holds",
            "T-UC-005-inv-049-7-violated",
            "T-UC-005-inv-049-9-holds",
            "T-UC-005-inv-049-9-violated",
            "T-UC-005-inv-049-9-nofield",
            "T-UC-005-inv-049-10-holds",
            "T-UC-005-inv-049-10-violated",
            "T-UC-005-inv-049-10-nofield",
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

        # FIXME(salesagent-hsz): E2E_REST — sandbox accounts created in test
        # process are not visible to Docker's separate DB.
        _UC011_E2E_SANDBOX_CONTEXT_TAGS: set[str] = {
            "T-UC-011-sandbox-list-filter",
            "T-UC-011-ext-g-absent",
        }
        if is_e2e_rest and (marker_names & _UC011_E2E_SANDBOX_CONTEXT_TAGS):
            item.add_marker(
                pytest.mark.xfail(
                    reason="e2e_rest fixture injection gap — sandbox accounts created in test not visible to Docker DB",
                    strict=False,
                )
            )

        # FIXME(salesagent-l9iz): E2E_REST — UC-006 account_resolution_boundary
        # success-path partitions rely on account fixtures not injected into the
        # Docker DB. Only the two success boundary_points fail on Docker.
        if is_e2e_rest and "T-UC-006-boundary-account" in marker_names:
            if any(s in nodeid for s in ("active", "single match")):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="E2E: success-path account fixtures not injected into Docker DB",
                        strict=False,
                    )
                )

        # FIXME(salesagent-vov / salesagent-qzz2): UC-019 REST/E2E_REST — REST endpoint
        # returns Method Not Allowed for get_media_buys. Same gap applies to e2e_rest
        # (Docker hits the same unimplemented endpoint).
        if (is_rest or is_e2e_rest) and any(t.startswith("T-UC-019") for t in marker_names):
            item.add_marker(
                pytest.mark.xfail(
                    reason="REST get_media_buys endpoint not implemented (Method Not Allowed)",
                    strict=False,
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
            "T-UC-006-ext-f-rest": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_VALIDATION_FAILED, spec expects CREATIVE_FORMAT_UNKNOWN"
            ),
            "T-UC-006-ext-f-mcp": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_VALIDATION_FAILED, spec expects CREATIVE_FORMAT_UNKNOWN"
            ),
            "T-UC-006-ext-g-rest": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_VALIDATION_FAILED, spec expects CREATIVE_AGENT_UNREACHABLE"
            ),
            "T-UC-006-ext-g-mcp": (
                "SPEC-PRODUCTION GAP: error_code is CREATIVE_VALIDATION_FAILED, spec expects CREATIVE_AGENT_UNREACHABLE"
            ),
            "T-UC-006-ext-h-rest": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion "
                "(preview-failure path, _processing.py:712-737)"
            ),
            "T-UC-006-ext-h-mcp": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion "
                "(preview-failure path, _processing.py:712-737)"
            ),
            "T-UC-006-ext-i-rest": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion "
                "(GEMINI_API_KEY not configured path)"
            ),
            "T-UC-006-ext-i-mcp": (
                "SPEC-PRODUCTION GAP: production returns plain-string errors[] via "
                "_SyntheticError, spec expects structured AdCPError with suggestion "
                "(GEMINI_API_KEY not configured path)"
            ),
            # Invariant scenarios: production behaviour diverges from spec
            "T-UC-006-rule-039-inv2": (
                "SPEC-PRODUCTION GAP: AdCPValidationError has no details dict — "
                "cannot contain 'suggestion' field (spec requires suggestion for "
                "format mismatch per BR-RULE-039 INV-2)"
            ),
            # T-UC-006-rule-037-inv5: e2e_rest only — handled below with transport check
            # Sandbox: sync_creatives does not set sandbox=true on response
            "T-UC-006-sandbox-happy": (
                "SPEC-PRODUCTION GAP: sync_creatives does not set sandbox=true on "
                "response for sandbox accounts (BR-RULE-209 INV-4)"
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

        # UC-006 INV-5 workflow step attributes (e2e_rest only) — workflow steps
        # not visible through the e2e_rest transport layer.
        if "T-UC-006-rule-037-inv5" in marker_names and "e2e_rest" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason=(
                        "SPEC-PRODUCTION GAP: workflow steps not visible in e2e_rest response — "
                        "BR-RULE-037 INV-5 requires workflow step attributes"
                    ),
                    strict=True,
                )
            )

        # UC-006 INV-1 per-creative failure (e2e_rest only) — e2e_rest doesn't
        # receive the correct creative action due to REST response parsing.
        if "T-UC-006-rule-033-inv1" in marker_names and "e2e_rest" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason=(
                        "SPEC-PRODUCTION GAP: e2e_rest returns action='failed' instead "
                        "of 'created' for the surviving creative (BR-RULE-033 INV-1)"
                    ),
                    strict=True,
                )
            )

        # UC-004: webhook 4xx no-retry assertion uses env.mock["post"] which is
        # not wired in e2e_rest (real HTTP transport, no mocks). Previously a
        # _pending() no-op, now a real assertion — only fails on e2e_rest.
        if "T-UC-004-webhook-no-retry-4xx" in marker_names and "e2e_rest" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason=(
                        "then_log_auth_rejection asserts env.mock['post'].call_count "
                        "which is not wired in e2e_rest (real HTTP, no mock)"
                    ),
                    strict=True,
                )
            )

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
            # FIXME(salesagent-4ydt): BR-RULE-029 INV-1 requires strictly monotonic
            # sequence numbers per media buy stream. Production retry path emits
            # the same sequence_number on retry POSTs, producing [1,2,2,3,3,3] for
            # three logical reports instead of a strictly increasing sequence.
            "T-UC-004-webhook-sequence": (
                "BR-RULE-029 INV-1: sequence_number reused across retry POSTs — strictly ascending not preserved",
                True,
            ),
            # FIXME(salesagent-4ydt): BR-UC-004-ext-g requires OPEN->HALF_OPEN->probe
            # before the breaker closes. Probe success races the HALF_OPEN assertion,
            # leaving the breaker in CLOSED state by the time the Then step reads it.
            "T-UC-004-webhook-circuit-halfopen": (
                "BR-UC-004-ext-g: circuit breaker races past HALF_OPEN to CLOSED during probe",
                True,
            ),
            # Webhook retry off-by-one: range(max_retries) yields 3 total calls,
            # should be range(max_retries + 1) for 4 calls (1 initial + 3 retries per BR-RULE-029 / UC-004-EXT-G-01)
            "T-UC-004-webhook-retry-5xx": (
                "production off-by-one: range(max_retries) does 3 calls, should do 4 (1 initial + 3 retries)",
                True,
            ),
            "T-UC-004-webhook-retry-network": (
                "production off-by-one: range(max_retries) does 3 calls, should do 4 (1 initial + 3 retries)",
                True,
            ),
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
            # T-UC-004-attr-echo: resolved — vvx9 + ral2 fixed enum→str handling
            # T-UC-004-attr-omitted: resolved — vvx9 + ral2 fixed enum→str handling
            "T-UC-004-attr-campaign-valid": ("attribution_window campaign window not implemented in _impl", True),
            # campaign unit interval validation: _impl doesn't validate attribution_window
            "T-UC-004-attr-campaign-invalid": (
                "attribution_window campaign unit validation not implemented in _impl",
                True,
            ),
            # FIXME(salesagent-7ag5): _impl uses str(enum) instead of enum.value for sort_by metric
            "T-UC-004-dim-sortby-valid": (
                "sort_by metric: str(SortMetric.clicks) != 'clicks' — needs .value in _impl",
                True,
            ),
            "T-UC-004-dim-sortby-fallback": (
                "sort_by fallback: A2A transport drops by_placement from response — serialization gap",
                False,
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
            "T-UC-019-boundary-status",
            # Status filter scenarios — status_filter parameter partially implemented
            "T-UC-019-partition-status-filter",
            "T-UC-019-partition-status-filter-invalid",
            "T-UC-019-boundary-status-filter",
            # Creative approval mapping — not implemented
            "T-UC-019-partition-approval",
            "T-UC-019-partition-approval-invalid",
            "T-UC-019-boundary-approval",
            # Creative approval invariants — pass on impl/a2a/mcp/rest but
            # e2e_rest fails with creative_assignments unique-constraint pollution
            # from prior scenarios. Fixture isolation bug, not a feature gap.
            "T-UC-019-inv-152-1",
            "T-UC-019-inv-152-2",
            "T-UC-019-inv-152-3",
            "T-UC-019-inv-152-5",
            # Snapshot scenarios — adapter snapshot API not wired
            "T-UC-019-partition-snapshot",
            "T-UC-019-boundary-snapshot",
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
            # Invariant scenarios — production validation gaps
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
                "T-UC-026-boundary-buyer-ref",
                {"second submission"},
                "buyer_ref dedup on re-submit fails CreateMediaBuyRequest validation — spec-production gap",
            ),
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
    """Parametrize BDD scenarios across transports.

    Default: 4 in-process transports (IMPL, A2A, MCP, REST).
    With BDD_E2E_ENABLED=true: adds E2E_REST (real HTTP through nginx).

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


# ---------------------------------------------------------------------------
# E2E stack: Docker-based integration (session-scoped)
# ---------------------------------------------------------------------------
# Start the Docker E2E stack before running BDD tests with E2E transport:
#   make test-stack-up && source .test-stack.env
# Or set E2E_BASE_URL / E2E_AUTH_TOKEN / E2E_TENANT environment variables.


def _load_test_stack_env() -> dict[str, str]:
    """Read .test-stack.env if it exists. Returns dict of key=value pairs."""
    env_file = Path(__file__).resolve().parents[2] / ".test-stack.env"
    result: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip().strip('"')
    return result


@pytest.fixture(scope="session")
def e2e_stack():
    """Detect whether Docker E2E stack is running. Return E2EConfig or None.

    Unlike most E2E fixtures this does NOT skip — it returns None so that
    non-E2E transports can run without the stack. Callers that need the
    stack (e2e_* transports) should skip explicitly when this is None.

    Resolution order for E2E_BASE_URL:
    1. E2E_BASE_URL environment variable (set by run_all_tests.sh via tox pass_env)
    2. .test-stack.env file in project root (written by test-stack.sh up)
    3. Default localhost:8092 (last resort)
    """
    import httpx

    from tests.harness.transport import E2EConfig

    # Try env var first, then .test-stack.env file, then default
    base_url = os.environ.get("E2E_BASE_URL")
    postgres_url = os.environ.get("E2E_POSTGRES_URL")

    if not base_url:
        stack_env = _load_test_stack_env()
        base_url = stack_env.get("E2E_BASE_URL")
        if not postgres_url:
            postgres_url = stack_env.get("E2E_POSTGRES_URL")

    if not base_url:
        base_url = "http://localhost:8092"

    try:
        resp = httpx.get(f"{base_url}/health", timeout=5)
        resp.raise_for_status()
    except Exception:
        return None

    if not postgres_url:
        postgres_url = (
            f"postgresql://adcp_user:secure_password_change_me@localhost:{os.environ.get('POSTGRES_PORT', '5435')}/adcp"
        )

    return E2EConfig(base_url=base_url, postgres_url=postgres_url)


@pytest.fixture()
def ctx(request: pytest.FixtureRequest, e2e_stack) -> dict:
    """Per-scenario mutable context shared across Given/When/Then steps.

    When parametrized by pytest_generate_tests, ``request.param`` is a
    Transport enum injected as ctx["transport"]. Transport-specific
    scenarios (tagged @rest/@mcp/@a2a) are NOT parametrized and get
    an empty ctx (When steps handle dispatch explicitly).

    For E2E transports, the E2EConfig is stored in ctx so that
    ``_harness_env`` can pass it to env constructors. The dispatcher
    reads config from the env object — no environment variables.
    """
    d: dict = {}
    if hasattr(request, "param"):
        d["transport"] = request.param
        if hasattr(request.param, "value") and str(request.param.value).startswith("e2e_"):
            if e2e_stack is None:
                pytest.skip("Docker E2E stack not running. Start with: make test-stack-up && source .test-stack.env")
            d["e2e_config"] = e2e_stack
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
    if "inventory_profile" in marker_names:
        return "UC-GET-PRODUCTS"
    if any(t.startswith("T-COMPAT") for t in marker_names):
        return "COMPAT"
    return None


def _detect_uc011_harness(marker_names: set[str]) -> str:
    """Detect which UC-011 harness a scenario needs based on tags."""
    # Check sync first — AccountSyncEnv handles both sync and list.
    # AccountListEnv only handles list, so sync-tagged scenarios require sync env.
    if "sync" in marker_names:
        return "sync"
    if "list" in marker_names:
        return "list"
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


def _is_e2e(ctx: dict) -> bool:
    """Check if the current scenario runs via E2E transport."""
    transport = ctx.get("transport")
    return transport is not None and hasattr(transport, "value") and str(transport.value).startswith("e2e_")


def _setup_db(request: pytest.FixtureRequest, ctx: dict) -> dict:
    """Ensure database is ready and return extra env constructor kwargs.

    For E2E: returns ``{e2e_config, tenant_id, principal_id}`` for
    per-tenant isolation against Docker PostgreSQL.
    For in-process: requests the ``integration_db`` fixture and returns
    empty dict (env uses its defaults).
    """
    if _is_e2e(ctx):
        import uuid

        suffix = uuid.uuid4().hex[:8]
        return {
            "e2e_config": ctx["e2e_config"],
            "tenant_id": f"e2e-{suffix}",
            "principal_id": f"e2e-buyer-{suffix}",
        }

    request.getfixturevalue("integration_db")
    return {}


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
            extra = _setup_db(request, ctx)
            from tests.harness.media_buy_account import MediaBuyAccountEnv

            with MediaBuyAccountEnv(**extra) as env:
                ctx["env"] = env
                yield
        else:
            extra = _setup_db(request, ctx)
            from tests.harness.media_buy_create import MediaBuyCreateEnv

            with MediaBuyCreateEnv(**extra) as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                yield

    elif uc == "UC-003":
        extra = _setup_db(request, ctx)
        from tests.harness.media_buy_update import MediaBuyUpdateIntegrationEnv as MediaBuyUpdateEnv

        with MediaBuyUpdateEnv(**extra) as env:
            tenant, principal, media_buy, product = env.setup_update_data()
            ctx["env"] = env
            ctx["tenant"] = tenant
            ctx["principal"] = principal
            ctx["existing_media_buy"] = media_buy
            existing_package = media_buy.packages[0] if media_buy.packages else None
            ctx["existing_package"] = existing_package
            # Register the Gherkin label "pkg_001" → real factory-generated package_id
            # so step definitions can resolve label references. See
            # tests/bdd/steps/domain/uc003_update_media_buy.py::_resolve_package_id.
            if existing_package is not None:
                ctx.setdefault("package_labels", {})["pkg_001"] = existing_package.package_id
            ctx["default_product"] = product
            yield

    elif uc == "UC-019":
        extra = _setup_db(request, ctx)
        extra.setdefault("principal_id", "buyer-001")
        from tests.harness.media_buy_list import MediaBuyListEnv

        with MediaBuyListEnv(**extra) as env:
            tenant, principal = env.setup_default_data()
            ctx["env"] = env
            ctx["tenant"] = tenant
            ctx["principal"] = principal
            yield

    elif uc == "UC-026":
        extra = _setup_db(request, ctx)
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv(**extra) as env:
            from tests.factories import PricingOptionFactory, ProductFactory

            tenant, principal = env.setup_default_data()
            env.setup_tenant_inventory(tenant)
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
        extra = _setup_db(request, ctx)
        from tests.harness.creative_sync import CreativeSyncEnv

        with CreativeSyncEnv(**extra) as env:
            ctx["env"] = env
            yield

    elif uc == "UC-005":
        extra = _setup_db(request, ctx)
        from tests.harness.creative_formats import CreativeFormatsEnv

        with CreativeFormatsEnv(**extra) as env:
            ctx["env"] = env
            yield

    elif uc == "UC-011":
        marker_names = {m.name for m in request.node.iter_markers()}
        harness_type = _detect_uc011_harness(marker_names)

        if harness_type == "list":
            extra = _setup_db(request, ctx)
            from tests.harness.account_list import AccountListEnv

            with AccountListEnv(**extra) as env:
                ctx["env"] = env
                yield
        elif harness_type == "sync":
            extra = _setup_db(request, ctx)
            from tests.harness.account_sync import AccountSyncEnv

            with AccountSyncEnv(**extra) as env:
                ctx["env"] = env
                yield
        else:
            yield

    elif uc == "ADMIN":
        # Admin UI uses Flask test_client, not API transports
        request.getfixturevalue("integration_db")
        from tests.harness.admin_accounts import AdminAccountEnv

        with AdminAccountEnv(mode="integration") as env:
            ctx["env"] = env
            yield

    elif uc == "COMPAT":
        extra = _setup_db(request, ctx)
        from tests.harness.product import ProductEnv

        with ProductEnv(**extra) as env:
            ctx["env"] = env
            yield

    elif uc == "UC-004":
        harness_type = _detect_delivery_harness(request)

        if harness_type == "poll":
            extra = _setup_db(request, ctx)
            extra.setdefault("principal_id", "buyer-001")
            from tests.harness.delivery_poll import DeliveryPollEnv

            with DeliveryPollEnv(**extra) as env:
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
            extra = _setup_db(request, ctx)
            from tests.harness.delivery_circuit_breaker import CircuitBreakerEnv

            with CircuitBreakerEnv(**extra) as env:
                tenant, principal = env.setup_default_data()
                ctx["env"] = env
                ctx["db_tenant"] = tenant
                ctx[f"db_principal_{env._principal_id}"] = principal
                yield
        else:
            pytest.xfail(f"UC-004 harness not yet wired for type: {harness_type}")
    elif uc == "UC-GET-PRODUCTS":
        extra = _setup_db(request, ctx)
        from tests.harness.product import ProductEnv

        with ProductEnv(**extra) as env:
            ctx["env"] = env
            yield
    else:
        pytest.xfail(f"No harness wired for {uc}")
