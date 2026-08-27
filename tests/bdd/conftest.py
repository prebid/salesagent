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
import ssl
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# Known mock-incompatible e2e_rest BDD scenarios — these dispatch over real HTTP
# to the separate server, so in-process mock injection (set_registry_formats /
# set_adapter_response / account billing-state fixtures) is invisible to it and
# the scenario cannot pass. xfail(strict=False)'d by exact nodeid in the
# collection hook. Regenerate from a clean in-network e2e_rest run. See the beads
# ledger task. File lives next to this conftest.
_E2E_REST_KNOWN_FAILURES: frozenset[str] = frozenset(
    line.strip()
    for line in (Path(__file__).parent / "e2e_rest_known_failures.txt").read_text().splitlines()
    if line.strip() and not line.lstrip().startswith("#")
)

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
    "tests.bdd.steps.generic.given_media_buy",
    "tests.bdd.steps.generic.when_request",
    "tests.bdd.steps.generic.then_success",
    "tests.bdd.steps.generic.then_error",
    "tests.bdd.steps.generic.then_payload",
    "tests.bdd.steps.domain.uc004_delivery",
    "tests.bdd.steps.domain.uc002_create_media_buy",
    "tests.bdd.steps.domain.uc002_nfr",
    "tests.bdd.steps.domain.uc003_update_media_buy",
    "tests.bdd.steps.domain.uc003_ext_error_scenarios",
    "tests.bdd.steps.domain.uc006_sync_creatives",
    "tests.bdd.steps.domain.uc005_format_id_shape",
    "tests.bdd.steps.domain.uc005_format_id_roundtrip",
    "tests.bdd.steps.domain.uc005_format_id_third_party",
    "tests.bdd.steps.domain.signing_enforcement",
    "tests.bdd.steps.domain.uc010_capabilities",
    "tests.bdd.steps.domain.uc011_accounts",
    "tests.bdd.steps.domain.admin_accounts",
    "tests.bdd.steps.domain.uc_get_products_inventory",
    "tests.bdd.steps.domain.uc_brand_shorthand",
    "tests.bdd.steps.domain.compat_normalization",
]

# ---------------------------------------------------------------------------
# Auto-xfail: missing step definitions
# ---------------------------------------------------------------------------
# Instead of predicting which scenarios are "pending" via metadata tags,
# we let pytest-bdd tell us at runtime. If a scenario fails because a step
# definition is missing, we convert the failure to xfail. The code is the
# source of truth — no stale metadata needed.

# nodeid -> human-readable classification of the step that actually failed,
# populated by the pytest_bdd_step_* hooks below. Consumed (and popped) by
# pytest_runtest_makereport's dormancy-vs-production-gap tripwire (#1721 M4):
# a strict-xfail whose reason claims a graded "production gap" but whose real
# failure is a missing step binding or a Given-side setup error is a
# MISCLASSIFIED entry -- dormancy masquerading as a graded gap, exactly the
# R1-2 pattern six independent reviewers converged on. This is a bounded
# conftest function extending the existing tripwire, not a new guard file.
_STEP_ERROR_CLASSIFICATION: dict[str, str] = {}


def pytest_bdd_step_func_lookup_error(request, feature, scenario, step, exception) -> None:  # noqa: ANN001
    """Record that this scenario's failure is a missing step BINDING (dormancy)."""
    _STEP_ERROR_CLASSIFICATION[request.node.nodeid] = (
        f"a missing step definition for {step.type} {step.name!r} (line {step.line_number})"
    )


def pytest_bdd_step_error(request, feature, scenario, step, step_func, step_func_args, exception) -> None:  # noqa: ANN001
    """Record a Given-side setup failure -- test-wiring, not the graded behavior.

    Only the FIRST failing step's classification is kept (a scenario has one
    failure); only Given steps are flagged here -- a When/Then failure is (by
    construction) the scenario grading the behavior it exists to grade, never
    dormancy.
    """
    if step.type == "given" and request.node.nodeid not in _STEP_ERROR_CLASSIFICATION:
        _STEP_ERROR_CLASSIFICATION[request.node.nodeid] = (
            f"a Given-side setup error on {step.name!r} (line {step.line_number}): {exception!r}"
        )


def _classify_strict_xfail_dormancy(item: pytest.Item, report: pytest.TestReport) -> None:
    """Fail loud when a strict-xfail claiming a production/spec gap is actually dormancy.

    Checks BOTH an explicit ``xfail`` marker's reason AND a ``wasxfail`` string
    this same hook may have just set (the missing-step-definition auto-convert
    above) -- either can carry the misleading "production gap" wording R1-2
    exhibited. Leaves alone any xfail that already reports honestly (e.g. "UC-010
    harness wiring not extended... dormant, never graded" names itself
    correctly) or that grades a real Then/When failure.
    """
    classification = _STEP_ERROR_CLASSIFICATION.pop(item.nodeid, None)
    if classification is None:
        return
    reasons = [str(report.wasxfail)] if getattr(report, "wasxfail", None) else []
    reasons += [str(m.kwargs.get("reason", "")) for m in item.iter_markers("xfail")]
    if not any("production gap" in r.lower() or "spec-production" in r.lower() for r in reasons):
        return
    if report.outcome not in ("skipped", "failed"):
        return
    report.outcome = "failed"
    report.wasxfail = ""
    report.longrepr = (
        f"MISCLASSIFIED strict-xfail: {item.nodeid} is cited as a production/spec gap "
        f"but the underlying failure is {classification} -- this is DORMANCY (test-wiring), "
        "not a graded production gap (R1-2 class). Fix the wiring, or correct the xfail reason "
        "to say so honestly, before recording an xfail."
    )


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

        from tests.harness._realize import E2EUnsupportedSetup

        if call.excinfo.errisinstance(StepDefinitionNotFoundError):
            report.outcome = "skipped"
            report.wasxfail = f"Step definition not found: {call.excinfo.value}"
        elif call.excinfo.errisinstance(NotImplementedError):
            report.outcome = "skipped"
            report.wasxfail = f"Not implemented: {call.excinfo.value}"
        elif call.excinfo.errisinstance(E2EUnsupportedSetup):
            # A mock-setup intent the live e2e stack has no surface for. The
            # reason is declared at the env method (not a nodeid ledger), so it
            # is visible in the report. Non-strict xfail — in-process transports
            # of the same scenario still run normally.
            report.outcome = "skipped"
            report.wasxfail = f"impl-only setup declared in env: {call.excinfo.value}"

    if report.when == "call":
        _classify_strict_xfail_dormancy(item, report)


# ---------------------------------------------------------------------------
# Auto-register BDD tag markers
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register BDD tag markers dynamically."""
    # Guard: BDD_E2E_ENABLED is incompatible with xdist. Under -n>0 the e2e_rest
    # transport is silently dropped at collection (the worker's
    # pytest_generate_tests never appends it), so the suite goes green WITHOUT
    # ever running the 5th transport. The ctx fixture's hard-error can't catch
    # this — collection never happens. Turn the silent drop into a hard error.
    # In-network bdd already pins BDD_XDIST_N=0 (docker-compose.e2e.yml). (#1420)
    # Exception: with E2E_PER_WORKER=1 each xdist worker targets its OWN server +
    # DB (Phase B), so e2e_rest CAN run in parallel. The worker inherits
    # BDD_E2E_ENABLED, and the bdd_e2e env runs `-k e2e_rest`, which pytest exits
    # 5 (no tests selected) on if the transport were dropped — so a silent drop
    # can't pass unnoticed. Keep the guard for the shared-server case (where the
    # silent-drop hazard genuinely remains).
    if os.environ.get("BDD_E2E_ENABLED") == "true" and os.environ.get("E2E_PER_WORKER") != "1":
        numprocesses = getattr(config.option, "numprocesses", None)
        if numprocesses not in (None, 0, "0"):
            raise pytest.UsageError(
                f"BDD_E2E_ENABLED=true is incompatible with xdist (-n {numprocesses!r}): "
                "the e2e_rest transport is silently dropped at collection and the suite "
                "passes without ever running it. Run serially (BDD_XDIST_N=0) or use "
                "per-worker servers (E2E_PER_WORKER=1)."
            )

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
    # FIXME(salesagent-javy): UC-003 ext-t — invoice_recipient authorization (BR-RULE-214) not implemented;
    # production accepts the override without an authorization check, so no VALIDATION_ERROR is raised.
    "T-UC-003-ext-t": "invoice_recipient authorization not implemented (BR-RULE-214) — production gap salesagent-javy",
    # FIXME(salesagent-u35g): UC-003 ext-u — new_packages midflight-additions capability check
    # (BR-RULE-217 -> UNSUPPORTED_FEATURE) not implemented; production accepts new_packages unhandled.
    "T-UC-003-ext-u": "new_packages midflight capability check not implemented (BR-RULE-217) — production gap salesagent-u35g",
    # FIXME(salesagent-12nd): UC-002 ASAP — response doesn't expose resolved start_time
    "T-UC-002-alt-asap": "response lacks resolved start_time field — spec-production gap",
    # FIXME(salesagent-fie): UC-002 error code mismatch — Pydantic VALIDATION_ERROR vs spec INVALID_REQUEST
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
    # T-UC-005-main-referrals: in-process ONLY (the registry is mocked and returns no agents).
    # GRADUATED for e2e_rest in the apply loop below (#1417) — with a seeded tenant
    # the live server populates creative_agents (>=DEFAULT_AGENT). NOT a spec-production gap.
    "T-UC-005-main-referrals": "creative agent referrals empty — in-process registry mock returns no agents; "
    "production populates >=DEFAULT_AGENT over real transports (mock limitation, not a spec-production gap)",
    # FIXME: T-UC-005-main — format 'audio-spot' has no assets or renders (all transports)
    "T-UC-005-main": "some formats (e.g. audio-spot) lack asset_requirements and render_capabilities — spec-production gap",
    # Partially graduated: dispatch fix landed (salesagent-40kk); error code mismatch remains
    # FIXME(salesagent-40kk): production raises AUTH_REQUIRED, spec expects TENANT_REQUIRED
    "T-UC-005-ext-a": "error code AUTH_REQUIRED instead of TENANT_REQUIRED — spec-production gap",
    # Graduated: creative agent partition/boundary tests (salesagent-7fqx)
    # Steps now dispatch through harness — all 34 tests pass across 4 transports.
    # FIXME(beads-dul): suggestion field not in production error model
    # NOTE(ah98 red-step inspection, 2026-07-06): NOT graduatable as-is — the
    # When step no-ops (type filter removed in adcp 3.12), so the scenario
    # fails on "operation should fail", not on the missing suggestion.
    # Suggestion parity for list_creative_formats is pinned instead by
    # tests/integration/test_request_validation_suggestion_parity.py.
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
    # FIXME(salesagent-scxw): the error CODE is fixed (currency-not-supported now
    # raises AdCPCapabilityNotSupportedError -> UNSUPPORTED_FEATURE, verified by
    # tests/integration/test_currency_not_supported_error_code.py). But this scenario
    # selects a non-default-currency pricing_option_id, and create_media_buy derives
    # request_currency from the product's FIRST pricing option — it never validates the
    # SELECTED option's currency — so the create SUCCEEDS instead of failing. Graduates
    # once selected-option currency validation lands (#1417).
    "T-UC-002-ext-d": "selected pricing-option currency not validated against CurrencyLimit; create succeeds instead of UNSUPPORTED_FEATURE — spec-production gap (salesagent-scxw)",
    # Graduated (#1417/gh8p.10): duplicate product_id now raises AdCPValidationError
    # with a buyer-facing suggestion ("Each package must reference a distinct
    # product_id ..."), surfaced on the wire. T-UC-002-ext-e passes.
    # FIXME(salesagent-lp0x): stale .feature expectation, NOT a production gap.
    # Production correctly emits BUDGET_EXCEEDED for "daily budget exceeds cap"
    # (AdCPBudgetExceededError; verified at wire on mcp/rest/a2a). v3.1 renamed the
    # code BUDGET_TOO_LOW -> BUDGET_EXCEEDED for BR-RULE-012 "exceeds cap"
    # (adcp-req .impl-coverage/BR-UC-002.yaml:1198); the generated .feature still
    # asserts the pre-v3.1 BUDGET_TOO_LOW. Graduates once adcp-req is reconciled and
    # BR-UC-002 is regenerated (#1417). Strict xfail; assertion unchanged.
    "T-UC-002-ext-k": "generated .feature asserts pre-v3.1 BUDGET_TOO_LOW; production correctly emits BUDGET_EXCEEDED — stale spec, pending upstream regen (salesagent-lp0x)",
    # FIXME(#1417): proposal-based create_media_buy is an unbuilt spec feature.
    # BR-UC-002-alt-proposal (status: active) + BR-UC-002-ext-l/ext-m define a full
    # proposal flow: resolve proposal_id, expiry check (PROPOSAL_EXPIRED), and
    # total_budget vs total_budget_guidance.min (BUDGET_TOO_LOW). The pinned
    # adcp library CreateMediaBuyRequest carries proposal_id, but production
    # src/core/tools/media_buy_create.py never reads it — no resolve_proposal,
    # no validate_proposal_budget, no proposal store. Scenario-level strict xfail
    # until the proposal feature is built (no proposal masking; Then steps still
    # hard-assert the BR error codes).
    "T-UC-002-ext-l": "BR-UC-002-ext-l: proposal_id resolution / PROPOSAL_EXPIRED unbuilt — proposal feature not implemented in production (spec-production gap)",
    "T-UC-002-ext-m": "BR-UC-002-ext-m: proposal total_budget_guidance.min validation / BUDGET_TOO_LOW unbuilt — proposal feature not implemented in production (spec-production gap)",
    # Graduated (#1417): the .feature now asserts the standard VALIDATION_ERROR
    # (PRICING_ERROR is not in the AdCP vocabulary @04f59d2d5) and production emits it with
    # a recovery suggestion. T-UC-002-ext-n / -ext-n-bid / -ext-n-floor pass; xfails removed.
    # FIXME(salesagent-9vgz.15): production errors lack suggestion field
    # AdCPNotFoundError/AdCPValidationError/AdCPAdapterError raised with details={"error_code": ...}
    # but no details["suggestion"]. Spec requires suggestion for buyer remediation.
    # FIXME(salesagent-9vgz.6): creative/format_id validation errors lack suggestion field
    # ext-g: _validate_creatives_before_adapter_call raises INVALID_CREATIVES without suggestion
    # ext-h: plain string format_id caught by Pydantic, not structured AdCPError
    # ext-h-agent: _validate_and_convert_format_ids is dead code — unregistered agent not detected
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
    "T-UC-002-inv-026-2": "INVALID_CREATIVES error lacks suggestion field",
    "T-UC-002-inv-026-4": "INVALID_CREATIVES error lacks suggestion field",
    # Graduated (#1417/gh8p.10): the request-construction boundary now derives a
    # field-aware suggestion (suggest_validation_fix) and attaches it to the
    # AdCPValidationError, so a missing idempotency_key rejects with a non-empty
    # wire suggestion. T-UC-002-v31-idempotency-missing passes.
    # FIXME(salesagent-9vgz.17): optimization_goals not in adcp v3.6.0 or production schemas
    # PackageRequest(extra='forbid') rejects the field with generic validation error,
    # not spec-expected UNSUPPORTED_FEATURE / INVALID_REQUEST with structured codes.
    "T-UC-002-ext-u": "optimization_goals not in production schemas — spec-production gap",
    "T-UC-002-ext-u-event": "optimization_goals not in production schemas — spec-production gap",
    # RESOLVED(salesagent-fpi): optimization_goals now accepted by production schemas (UC-003).
    # Removed stale xfails: T-UC-002-partition-optimization-goals, T-UC-002-boundary-optimization-goals
    # Valid rows now pass; invalid rows xfail via _assert_error_outcome _SPEC_PRODUCTION_CODE_MAP.
    # Removed: T-UC-003-partition-optimization-goals, T-UC-003-boundary-optimization-goals, T-UC-003-alt-optimization-goals
    # NOTE: principal-ownership error code gap — spec expects ACCOUNT_NOT_FOUND,
    # production raises AdCPAuthorizationError (PERMISSION_DENIED, salesagent-otc5)
    # — see T-UC-003-ext-c below
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
    # FIXME(salesagent-gh8p.13 / production-gap bead): natural-key sandbox resolution
    # without prior provisioning is unimplemented. _resolve_by_natural_key
    # (account_helpers.py:110) requires the sandbox account to already exist —
    # raises ACCOUNT_NOT_FOUND rather than auto-provisioning — and
    # CreateMediaBuyResult exposes no sandbox field to echo. Step dispatches the
    # real natural-key create on the wire; flips to a pass when sandbox
    # auto-provisioning + the sandbox echo land. BR-RULE-209 INV-8.
    "T-UC-002-sandbox-natural-key": "natural-key sandbox auto-provisioning + sandbox echo not implemented "
    "in create_media_buy (ACCOUNT_NOT_FOUND without prior provisioning) — spec-production gap (salesagent-gh8p.13)",
    # FIXME(salesagent-9vgz.1): inline creative upload not persisted in create_media_buy
    # process_and_upload_package_creatives → _sync_creatives_impl should persist
    # creatives to DB, but the Then step "upload creatives to creative library" fails
    # because no Creative rows exist after creation. Gap was previously masked by
    # inline pytest.xfail() in the step body — moved to scenario-level here.
    "T-UC-002-alt-creatives": "inline creative upload not persisted in create_media_buy — spec-production gap",
    # RESOLVED: T-UC-004-webhook-hmac — no longer xfailed on any transport as of
    # salesagent-n78j0.13. The sentence that stood here ("Then steps are pending (no-op),
    # test passes trivially") was TRUE WHEN WRITTEN and has been false since #1291 C1 /
    # salesagent-n78j0.1.4: all three Thens assert, through `env.last_delivery()`, and the
    # last recomputes the HMAC over the received bytes. Corrected rather than deleted
    # because it was read as current during the e2e_rest graduation and pointed the
    # opposite way from the tree.
    # RESOLVED: T-UC-004-webhook-creds-short — DB setup fix exposed that Then steps are pending (no-op).
    # Test passes trivially; real credential assertion gap tracked separately.
    # FIXME(salesagent-n3y): UC-002 account field absent — production doesn't require account field
    # Spec says account is required (BR-RULE-080 INV-1), but production accepts requests without it.
    "T-UC-002-inv-080-1": "account field not required by production — spec-production gap",
    # FIXME(salesagent-9vgz.92): rate limiting + payload size validation not implemented
    # Rate limiting middleware does not exist (AdCPRateLimitError never raised).
    # No ASGI middleware checks content-length for oversized bodies.
    "T-UC-002-nfr-001": "rate limiting + payload size validation not implemented — spec-production gap",
    # ── UC-010 batch-1 wiring — remaining per-family gaps re-cited to their GH homes ──
    # Verified against a real run 2026-07-14: every entry below fails on all
    # three wire transports (strict holds); per-row / per-transport gaps use
    # _SELECTIVE_XFAIL / _MCP_SELECTIVE_XFAIL instead.
    # Wired non-dormant + strengthened (#1721 M4): the missing account_sandbox=false
    # Given (models.py:82 default True) is fixed -- the scenario now runs all the
    # way through account.*, media_buy.execution.targeting.geo_*, and portfolio
    # asserts (the acceptance mechanism six reviewers found masked) before
    # reaching its new, honest stopping point: media_buy.reporting_delivery_methods
    # is not declarable under the STRICT capability policy without RFC 9421
    # signing (same root as T-UC-010-v31-reporting-delivery-methods). Transport-
    # independent: all 3 transports reach this same assert.
    "T-UC-010-main": "media_buy.reporting_delivery_methods not emitted -- not declarable under the STRICT "
    "capability policy without RFC 9421 signing (same gap as T-UC-010-v31-reporting-delivery-methods) — #1291",
    # Graduated (salesagent-rldj): _build_adcp_block() now always emits
    # adcp.supported_versions (derived from SUPPORTED_ADCP_VERSIONS) on both
    # the no-tenant and tenant-resolved paths. T-UC-010-ext-a removed.
    # Graduated (salesagent-dn2s): T-UC-010-auth-data-identity — capability
    # discovery now resolves the adapter CLASS tenant-only (INV-4), identical
    # for anonymous and authenticated callers.
    # Graduated (salesagent-7moz): T-UC-010-ext-c-a2a — A2A public-skill list
    # now always validates a presented token (adcp_a2a_server.py), rejecting
    # an invalid one with AUTH_INVALID regardless of skill-level auth
    # requirement, matching v3.1.1 error-code.json.
    # Graduated (salesagent-rrz8): T-UC-010-ext-c-mcp — MCP ToolResult now
    # pre-serializes via model_dump(mode="json"), so audience_targeting is
    # correctly omitted instead of serialized as null.
    # T-UC-010-ext-d-filter FULLY GRADUATED (salesagent-5yik): the new POST
    # /api/v1/capabilities route carries protocols/context/adcp_version on all
    # 3 transports, so a2a/mcp/rest all now pass (removed from both this dict
    # and the _SELECTIVE_XFAIL rest-only entry below).
    # T-UC-010-ext-d-invalid-value / -empty / T-UC-010-ext-e-echo / -nested / -empty
    # FULLY GRADUATED (salesagent-5yik): build_get_adcp_capabilities_request now
    # constructs a real typed GetAdcpCapabilitiesRequest (Pydantic enforces the
    # protocols enum + minItems:1), and _get_adcp_capabilities_impl echoes
    # req.context verbatim onto the response on every transport.
    "T-UC-010-ext-d-all-protocols": "signals/governance/sponsored_intelligence/creative sections never emitted — #1724",
    # Graduated (salesagent-rldj): T-UC-010-v31-supported-versions removed —
    # see T-UC-010-ext-a graduation note above (same _build_adcp_block fix).
    # Graduated (salesagent-rldj): version negotiation now implemented
    # (src/core/version_negotiation.py) — a bad adcp_version/adcp_major_version
    # pin raises AdCPVersionUnsupportedError -> VERSION_UNSUPPORTED on all
    # transports. T-UC-010-v31-version-unsupported /
    # -major-fallback / -build-version-advisory removed from this dict.
    # Wired non-dormant + strengthened (salesagent-e4ad): steps execute and grade the
    # spec-pinned shape, then fail on the unemitted/hard-coded block (strict xfail on all transports).
    "T-UC-010-v31-compliance-testing": "compliance_testing block not emitted by the capabilities builder; no comply_test_controller surface — #1724",
    # Re-cited #1592 -> #1724 (salesagent-3xmz batch B3). The OLD reason ("hard-coded,
    # not derived from tenant config") is now FALSE: specialisms ARE declaration-driven
    # and registry-validated. The scenario stays xfailed for a different, permanent
    # reason — it claims postures this deployment does not back.
    "T-UC-010-v31-specialisms": "scenario claims unbacked postures the STRICT policy forbids declaring: `creative-generative` (no generative creative implemented) and the `creative` protocol (bundle required_tools unimplemented) — #1724",
    # Ledger SHRINK (salesagent-3xmz batch B5): T-UC-010-v31-advisory-errors removed —
    # the capabilities builder now emits top-level advisory errors[] for genuinely
    # faulted discovery lookups (except-path only), so the gap the row recorded is closed.
    # T-UC-010-account-supported-billing / T-UC-010-account-block-presence GRADUATED
    # (salesagent-3s5a): account.supported_billing now derives from resolve_supported_billing(tenant)
    # and the account block is now emitted on the tenant-resolved path.
    # Graduated (salesagent-y9ld R1): media_buy.supported_pricing_models now derives from
    # adapter.get_supported_pricing_models() (mirrors products.py:721). T-UC-010-pricing removed.
    "T-UC-010-audience-caps": "media_buy.audience_targeting not emitted by the capabilities builder — #1855",
    # Wired non-dormant + strengthened (salesagent-ytq6): steps execute and grade the
    # spec-pinned shape, then fail on the missing block (strict xfail on all transports).
    "T-UC-010-conversion-caps": "media_buy.conversion_tracking not emitted by the capabilities builder — #1855",
    "T-UC-010-creative-caps": "creative section not emitted — production advertises only the media_buy protocol — #1724",
    # Graduated (salesagent-y9ld R2): CHANNEL_MAPPING now includes sponsored_intelligence,
    # the 20th canonical channel. T-UC-010-channel-all-canonical removed.
    # Wired non-dormant + strengthened (salesagent-tmpd): each scenario executes and grades the
    # spec-pinned shapes, then fails on a block the capabilities builder never emits (strict
    # xfail on all transports).
    "T-UC-010-features": "media_buy.content_standards / conversion_tracking / audience_targeting presence-objects not emitted (#1855) and the account block (account.sandbox) not emitted (#1856) by the capabilities builder",
    # _build_geo_postal_areas builds the native country-keyed map correctly --
    # geo_postal_areas is not part of the gap. The remaining gap is the non-geo
    # targeting dimensions never being built.
    "T-UC-010-targeting": "targeting emits only geo_countries/geo_regions/geo_metros/geo_postal_areas — "
    "age_restriction, language, keyword_targets, negative_keywords, geo_proximity not built "
    "— #1857 non-geo targeting capability dimensions",
    # Wired non-dormant + strengthened (salesagent-scgh): each scenario executes and grades the
    # spec-pinned v3.1.1 shape, then fails on a block the capabilities builder never emits
    # (brand is not in supported_protocols; measurement block never built). Strict xfail, all
    # transports.
    # Re-cited #1592 -> #1724 (salesagent-3xmz, owner decision 2026-07-27). The brand family
    # was re-homed ENTIRELY rather than partially delivered: `brand` in supported_protocols
    # commits the seller to `get_brand_identity` (protocols/brand/index.yaml#required_tools),
    # which has zero implementations here, and the schema forbids emitting the block without
    # that protocol claim ("Only present if brand is in supported_protocols"). Emitting roster
    # facts either way would be the over-advertising STRICT exists to prevent.
    "T-UC-010-v31-brand-block": "scenario requires the brand protocol claim, which commits to get_brand_identity (unimplemented), and brand.rights=true, an unbacked tool commitment — #1724",
    # Ledger SHRINK (salesagent-3xmz batch B1): T-UC-010-v31-measurement-catalog removed.
    # The tenant's measurement catalog is a declarable business fact, so the scenario is
    # graded by the capability-declaration store (measurement block + supported_protocols
    # union + the measurement.core experimental-feature implication) rather than ledgered
    # as a permanent production gap.
    # Wired non-dormant + strengthened (salesagent-jd6a): each row executes and grades the
    # spec-pinned bound/relation, then fails on all transports because the capabilities builder
    # never derives idempotency from tenant config and runs no version negotiation (#1592).
    # Strict tag-level xfail — every parametrized row fails.
    # T-UC-010-v31-request-signing-monotonicity / T-UC-010-v31-webhook-signing-bounds moved to
    # _SELECTIVE_XFAIL (salesagent-3s5a): request_signing/webhook_signing={supported:false} now
    # emitted, so the "valid" rows (which only assert schema-valid subset/disjoint relations or
    # must_equal_when bounds against an unsupported posture) pass; the "invalid" rows (which
    # require the builder to REJECT a relation-violating/out-of-bounds posture with
    # CONFIGURATION_ERROR) still fail. NOTE (salesagent-z2cw): a per-tenant config surface DOES now
    # exist (tenants.capability_declarations, #1592 T1a) — what it deliberately lacks is any
    # signing field, under the STRICT capability policy. Re-cited #1592 -> #1291.
    # Graduated (salesagent-rldj): get_idempotency_posture() now returns a
    # typed IdempotencyPosture whose check_bounds() enforces the
    # replay_ttl_seconds/in_flight_max_seconds schema bounds, raising
    # CONFIGURATION_ERROR (terminal) on the invalid rows; the harness
    # CapabilitiesEnv.set_idempotency_posture override lets the boundary rows
    # drive it. T-UC-010-v31-idempotency-ttl-bounds removed from this dict.
    # Graduated (salesagent-rldj): version negotiation now emits a non-empty,
    # release-precision supported_versions in VERSION_UNSUPPORTED details on
    # every row. T-UC-010-v31-version-unsupported-details-bounds removed.
    # ── UC-011 list wiring — graduated; provenance below ───────────────────
    # Graduated (salesagent-tm97): _apply_list_account_filters honors req.account
    # (AccountReference oneOf, both account_id and natural-key arms), forwarded by
    # all 3 transports. T-UC-011-list-account-filter removed.
    # T-UC-011-list-authorization: the Account schema carries no authorization
    # object (account-with-authorization item shape is new in 3.1.1), so the
    # wire items never expose allowed_tasks. Out of scope (GH #1615).
    "T-UC-011-list-authorization": "per-account authorization block (account-with-authorization / allowed_tasks) not "
    "emitted — production Account schema has no authorization field, list items are bare — tracked as GH #1615, "
    "out of #1592 A3 core scope",
    # Graduated (salesagent-tm97): ListAccountsRequest.idempotency_key added --
    # the read wrapper now tolerates the 3.1 idempotency envelope instead of
    # rejecting it under extra=forbid. T-UC-011-list-read-idempotency-tolerance removed.
    # Graduated (salesagent-5g8e): settings-update (AccountReference) mode implemented
    # via _process_settings_update_entry (both AccountReference1/account_id and
    # AccountReference2/natural-key arms), mode-exclusivity enforced in _impl before
    # dispatch (VALIDATION_ERROR naming accounts[i]), unmatched references rejected
    # with UNSUPPORTED_PROVISIONING. T-UC-011-sync-settings-update,
    # T-UC-011-sync-settings-update-no-provision, T-UC-011-sync-mode-exclusive removed.
    # Graduated (salesagent-hh1f): _check_billing_policy now emits recovery="correctable"
    # + details={scope, supported_billing} (conditionally, honest-absence on an empty
    # policy) on the per-account BILLING_NOT_SUPPORTED error. T-UC-011-ext-c-rejected removed.
    # ── UC-011 per-buyer-agent commercial gate wiring (FIXME(#1772)) ──
    # Steps now execute non-dormant on a2a/mcp/rest and grade the spec-pinned
    # v3.1.1 shape (error-details/billing-not-permitted-for-agent.json); each
    # fails because production (src/core/tools/accounts.py) has NO per-buyer-agent
    # commercial gate. The passthrough-only Given declares agent as
    # capability-supported (supported_billing), so _check_billing_policy accepts
    # the value and production PROVISIONS the account (action "created") instead
    # of rejecting it with BILLING_NOT_PERMITTED_FOR_AGENT — the code is never
    # emitted anywhere in production.
    "T-UC-011-billing-agent-gate-reject": "no per-buyer-agent commercial gate exists in production — agent billing is "
    "capability-supported so _check_billing_policy accepts it and the account is provisioned (action 'created') "
    "instead of rejected with BILLING_NOT_PERMITTED_FOR_AGENT + clamped rejected_billing/suggested_billing details — "
    "#1772",
    "T-UC-011-billing-agent-gate-recover": "no per-buyer-agent commercial gate exists in production — the first leg "
    "never emits BILLING_NOT_PERMITTED_FOR_AGENT (capability-supported agent billing is provisioned), so the "
    "autonomous suggested_billing recovery flow is unreachable — #1772",
    # ── UC-011 account-level notification_configs + sandbox capability gate — ALL GRADUATED ──
    # Graduated (T2 increment F4a): T-UC-011-notif-register-paused,
    # -notif-replace-clear and -notif-omit-preserves removed. accounts.notification_configs now
    # persists as a whole-array JSONType column with declarative-replace semantics (omit preserves,
    # [] clears, re-sent subscriber_id replaces in place) and is echoed on both sync_accounts and
    # list_accounts with authentication.credentials scrubbed. The three scenarios grade that surface
    # on a2a/mcp/rest.
    # Graduated: _check_sandbox_capability gate added -- rejects
    # sandbox provisioning with UNSUPPORTED_FEATURE (accounts[i].sandbox) when the
    # tenant's account_sandbox capability is not declared. T-UC-011-sandbox-capability-not-declared removed.
    # ── UC-011 notification_configs per-account rejections — ALL GRADUATED ──
    # Graduated (T2 increment F4b): T-UC-011-notif-event-scope-reject and
    # -notif-duplicate-subscriber removed. _check_notification_configs runs pre-persist in BOTH
    # entry handlers and emits a per-account failure inside a transport-level success, with the
    # exact error.field pointers the storyboards grade.
    # Graduated (T2 increment F4c): T-UC-011-notif-activation-proof-fail
    # removed. NotificationProofService performs a bounded proof-of-control challenge BEFORE the
    # write transaction opens (see .claude/notes/async-sync-architecture.md); a failed proof
    # rejects the entry with VALIDATION_ERROR at notification_configs[j].url and writes nothing,
    # so the prior array is untouched.
}

# FIXME(beads-dul): Selective xfail for parametrized scenarios where only
# some examples exercise unimplemented features. Each entry: (tag, node_id
# substrings that should xfail, reason).
_SELECTIVE_XFAIL: list[tuple[str, set[str], str]] = [
    # #1721 M4: @T-UC-010-v31-account-sandbox newly wired. The true/false rows
    # pass for real; the "absent" row expects the wire to OMIT account.sandbox
    # (buyer applies the schema default) but _build_account_block
    # (capabilities.py) always assigns an explicit tenant.get("account_sandbox",
    # True) value and never conditionally omits it — same root as the other
    # #1856 account-config-surface entries (require_operator_auth,
    # required_for_products, authorization_endpoint).
    (
        "T-UC-010-v31-account-sandbox",
        {"sandbox absent in response"},
        "account.sandbox is always assigned an explicit boolean by _build_account_block, "
        "never conditionally omitted — #1856 account-config surface",
    ),
    # #1417 wiring surfaced pre-existing UC-003 targeting-overlay gaps
    # (tracked separately). The geo include/exclude overlap partitions DO reach the
    # converged update.py:444 raise and PASS (proving da07); these other partitions
    # hit unrelated gaps: pydantic extra='forbid' on unknown/managed/device_platform
    # fields raising a raw ValidationError before dispatch, GeoProximity requiring
    # lat/lng (geometry/radius/travel_time-only modes + method-conflict unmodeled),
    # frequency_cap field-combo validation, keyword-duplicate detection, and
    # device_type include/exclude overlap validation.
    (
        "T-UC-003-partition-targeting-overlay",
        {
            "unknown_field",
            "managed_only_dimension",
            "multiple_dimensions",
            "device_type_overlap",
            "proximity_method_conflict",
            "proximity_geometry",
            "proximity_radius",
            "proximity_travel_time",
            "frequency_cap_missing_fields",
            "keyword_duplicate",
        },
        "Pre-existing UC-003 targeting-overlay validation gaps (not da07): pydantic "
        "extra='forbid' / GeoProximity coordinate modes / frequency_cap / keyword-dup / device_type overlap",
    ),
    (
        "T-UC-003-boundary-targeting-overlay",
        {
            "unknown field name",
            "managed-only dimension",
            "device_type include/exclude overlap",
            "with travel_time only",
            "with radius only",
            "with geometry only",
            "with travel_time AND radius",
            "frequency_cap max_impressions without per",
            "keyword_targets with duplicate",
        },
        "Pre-existing UC-003 targeting-overlay validation gaps (not da07): pydantic "
        "extra='forbid' / GeoProximity coordinate modes / frequency_cap / keyword-dup / device_type overlap",
    ),
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
    # ── UC-010 batch-1 per-row gaps — re-cited to their GH homes ───────────
    # The 'omitted' / absence rows of these outlines pass vacuously (the field
    # is absent because the whole block is missing), so only the value rows xfail.
    # Graduated (salesagent-7moz): invalid_token_a2a row — A2A now always
    # validates a presented token, rejecting invalid ones with AUTH_INVALID.
    # operator_auth_not_required GRADUATED (salesagent-3s5a): require_operator_auth is now
    # emitted as the true constant False. operator_auth_required (expects True) can never
    # pass with this plan — no per-tenant operator-auth config surface exists.
    (
        "T-UC-010-account-require-operator-auth",
        {"operator_auth_required"},
        "account.require_operator_auth is a hardcoded False constant — no config surface to "
        "make it True exists — #1856",
    ),
    (
        "T-UC-010-account-required-for-products",
        {"products_gated", "products_open"},
        "account.required_for_products not emitted — #1856",
    ),
    (
        "T-UC-010-account-authorization-endpoint",
        {"oauth_supported"},
        "account.authorization_endpoint not emitted — #1856",
    ),
    # Graduated (#1721 M4): given_capability_config now writes account_sandbox
    # through configure_tenant_field when the row spells sandbox={true,false} --
    # sandbox_disabled passes for real on all 3 transports.
    (
        "T-UC-010-degradation-account",
        {"account_degraded"},
        # _build_account_block (src/core/tools/capabilities.py) always emits
        # require_operator_auth (a constant) and sandbox (tenant.account_sandbox,
        # default True) as real, non-null values -- only authorization_endpoint/
        # required_for_products/account_financials are honestly omitted. The
        # scenario expects a supported_billing-only shape, which this design
        # cannot produce.
        "account_degraded expects a supported_billing-only account block, but "
        "_build_account_block always emits require_operator_auth and sandbox as real "
        "constant/config values, not honestly omitted — #1856 account-config surface",
    ),
    # Wired non-dormant + strengthened (salesagent-chbi): the 'absent' rows (adapter
    # fails / capability disabled) pass — the block is genuinely off the wire; only the
    # 'present' rows (full_response: adapter succeeds AND capability enabled) fail,
    # because production never emits the media_buy.audience_targeting /
    # conversion_tracking blocks yet. Strict on the present rows only.
    (
        "T-UC-010-degradation-sections",
        {"full_response"},
        "media_buy.audience_targeting / conversion_tracking sections not emitted by the capabilities builder — #1855",
    ),
    # Wired non-dormant + strengthened (salesagent-tmpd): targeting-partitions rows that
    # production satisfies (adapter_unavailable_defaults, nested_absent) pass; the rest execute
    # the real assertion and fail because the capabilities builder never emitted the richer
    # non-geo dimensions (age_restriction/language/keyword_targets/negative_keywords/geo_proximity
    # -- R8 follow-up, out of core scope). Graduated (salesagent-y9ld R4): nested_populated /
    # postal_areas_native / postal_areas_legacy_alias now pass -- the native country-keyed
    # geo_postal_areas map is built (_build_geo_postal_areas, capabilities.py), no longer the
    # deprecated boolean-alias shape.
    (
        "T-UC-010-targeting-partitions",
        {
            "full_adapter",
            "partial_dimensions",
            "age_restriction_supported",
            "keyword_targeting",
            "geo_proximity_supported",
        },
        "targeting builder never emits the non-geo dimensions (age_restriction/language/"
        "keyword_targets/negative_keywords/geo_proximity) — #1857",
    ),
    # Wired non-dormant + strengthened (salesagent-tmpd): degradation-partitions rows that
    # production satisfies (adapter_fail, db_fail, adapter_and_db_fail, *_absent) pass; the
    # gap rows fail — no_tenant needs adcp.supported_versions (not emitted), and no_principal
    # expects [display] but INV-4 keeps the adapter principal-free so channels are NOT degraded
    # by a missing principal. full_response GRADUATED (salesagent-3s5a): the account block is
    # now emitted with non-empty supported_billing and adcp.idempotency is already present.
    # account_degraded stays xfailed — a separate, still-ungraded gap (needs investigation).
    (
        "T-UC-010-degradation-partitions",
        {"no_tenant", "no_principal", "account_degraded"},
        # _build_adcp_block(None) always emits supported_versions, so that is not
        # the no_tenant gap. The real no_tenant gap is extra top-level keys:
        # _deg_no_tenant asserts wire keys are a SUBSET of {adcp,
        # supported_protocols}, but the no-tenant response also includes
        # specialisms/webhook_signing/request_signing, which are non-null and
        # therefore present on the wire.
        "no_tenant top-level response carries extra keys (specialisms, webhook_signing, "
        "request_signing) beyond the minimal {adcp, supported_protocols} contract; "
        "INV-4 keeps adapter channels principal-free so no_principal does not degrade to "
        "[display]; account_degraded expects a supported_billing-only account block but "
        "_build_account_block always emits require_operator_auth/sandbox as real values "
        "— #1856 account-config surface",
    ),
    # Wired (salesagent-y9ld R7): approval_unspecified (creative_approval_mode omitted
    # by default -- TenantFactory.human_review_required=False and no
    # gam/kevel/mock_manual_approval_required column set) passes today with zero
    # production change -- honest-absence regression armor. Graduated: approval_human
    # now passes -- resolve_manual_approval_signal() derives require_human from
    # tenant.human_review_required (adapter_helpers.py), wired into the MediaBuy build.
    # approval_auto stays xfailed -- no config surface exists to affirmatively claim
    # auto_approve (Q2, deferred; declaring it without certainty would be a false
    # conformance claim).
    (
        "T-UC-010-v31-creative-approval-mode",
        {"approval_auto"},
        "media_buy.creative_approval_mode=auto_approve has no backing config surface (Q2 deferred) — #1724",
    ),
    # Moved from _XFAIL_TAGS (salesagent-3s5a): request_signing/webhook_signing={supported:false}
    # now emitted, so "valid" rows (asserting schema-valid relations/bounds against an
    # unsupported posture) pass; "invalid" rows (requiring the builder to REJECT a
    # relation-violating/out-of-bounds posture with CONFIGURATION_ERROR) still fail — no
    # per-tenant signing-posture config surface exists to reject against.
    # Graduated 2026-08-12 (#1291 D2): T-UC-010-v31-request-signing-monotonicity removed ENTIRELY
    # (rows: required_for adds one operation not in supported_for; warn_for and required_for share
    # exactly one operation; protocol_methods_required_for adds one method not in
    # protocol_methods_supported_for). request_signing became declarable with the signing family,
    # so CapabilityDeclarations._validate_bucket_monotonicity now has a posture to reject: each row
    # raises AdCPConfigurationError naming capability_declarations.request_signing.<bucket>, graded
    # on the wire as CONFIGURATION_ERROR / recovery terminal / message naming request_signing. The
    # Given declares the concrete posture instead of recording the label, writing the narrowing
    # bucket EXPLICITLY (the rule keys on model_fields_set, so an omitted superset would skip the
    # check and the row would grade nothing). Inspected per xpass-graduation.md against feature
    # :1438-1471; the three rejections were measured directly against production before the
    # conversion. No assertion weakened. Serial in-process run 2026-08-12: uc010 slice 329 -> 338
    # passed (+3 rows x 3 transports), 267 -> 258 xfailed, 0 failed, 0 xpassed. e2e_rest (not
    # gated for these entries, so un-xfailed too) verified in-network: bdd_e2e run
    # test-results/innet_120826_1403 has all SIX rows of the outline passing on e2e_rest,
    # 534 passed / 0 failed.
    # Graduated 2026-08-12 (#1291 D2), PARTIALLY: reporting_delivery_methods=['webhook'],
    # supported=false removed — a KEYLESS tenant declaring webhook report delivery is the one
    # reachable violation of must_equal_when, and validate_signing_platform_backing rule (d)
    # rejects it naming capability_declarations.webhook_signing.supported. The outline's three
    # realizable VALID rows (the keyed [webhook] trigger and the two in-profile algorithm sets)
    # now derive their posture from real key material too, instead of passing against an absent
    # block. The four rows below cannot be realized here, for two different reasons, and each
    # carries its own — none of them is signing's to fix.
    #
    # Two MORE rows of this outline left the passing count in the same change, and did not come
    # here: supports_webhook_delivery=true / wholesale_feed_webhooks.supported=true (both "valid")
    # passed only because the Given recorded intent, so their trigger never reached the wire and
    # the must_equal_when assertion short-circuited on a block that graded nothing. A test that
    # cannot fail is not coverage, so they are parked (owner decision, 2026-07-30) — in
    # _UC010_PARKED_ROWS, not here, because a STRICT xfail cannot hold a row that passes: it
    # converts the vacuous pass into an XPASS build failure (measured: 6 failures, 2 rows x 3
    # transports, before the park moved).
    #
    # Net effect on this tag, and it is DOWN on purpose: +1 graduated row and -2 parked rows, so
    # the uc010 slice went 344 -> 341 passed with 252 -> 255 xfailed, 0 failed, 0 xpassed. In
    # network, bdd_e2e test-results/innet_120826_1507: on e2e_rest the graduated
    # supported=false row PASSES, both algorithm rows and the keyed trigger row pass, and all four
    # unreachable rows xfail; 535 passed / 0 failed.
    (
        "T-UC-010-v31-webhook-signing-bounds",
        {
            "supports_webhook_delivery=true, supported absent",
            "algorithms=['rsa-pss-sha512']",
        },
        "these two boundaries have no reachable state in this deployment, and both FAIL rather "
        "than pass: the supports_webhook_delivery row names a must_equal_when trigger whose block "
        "is unbacked — media_buy.content_standards has no surface (#1855) — and declaring it is "
        "refused NAMING THAT BLOCK, so realizing it would grade the wrong refusal. The "
        "rsa-pss-sha512 row asks for an algorithm outside the AdCP profile, but "
        "webhook_signing.algorithms is DERIVED from the ACTIVE key row and narrow_alg refuses an "
        "off-profile algorithm at MINT time, so the value can never exist in the store to be "
        "rejected on the read path: the obligation is met by construction rather than by a "
        "rejection, and the row becomes gradable only if the profile itself widens. The two "
        "same-outline rows that pass VACUOUSLY are parked in _UC010_PARKED_ROWS instead — a strict "
        "xfail cannot hold a row that passes",
    ),
    # Wired non-dormant + strengthened (salesagent-scgh): the baseline-absence row passes
    # (polling_only → reporting_delivery_methods/offline_delivery_protocols absent, webhook_signing
    # honest-tautology); the push-delivery rows fail because the capabilities builder never emits
    # media_buy.reporting_delivery_methods / offline_delivery_protocols / webhook_signing.
    # Graduated 2026-08-12 (#1291 D2), PARTIALLY: webhook_only removed — reporting_delivery_methods
    # is declarable and [webhook] is backed, so a keyed, publishable tenant now emits
    # media_buy.reporting_delivery_methods=[webhook] with webhook_signing.supported=true, which is
    # all three of that row's Thens. The remaining two rows never needed signing; their blocker is
    # offline report delivery, which this epic does not build. Serial in-process run 2026-08-12:
    # uc010 slice 341 -> 344 passed (+1 row x 3 transports), 255 -> 252 xfailed, 0 failed,
    # 0 xpassed. In-network bdd_e2e test-results/innet_120826_1434: webhook_only PASSES on
    # e2e_rest, offline_only and mixed_delivery still xfailed, 536 passed / 0 failed.
    (
        "T-UC-010-v31-reporting-delivery-methods",
        {"offline_only", "mixed_delivery"},
        "no bucket report delivery is implemented — production refuses a method list containing "
        "'offline' and carries no offline_delivery_protocols field at all, so neither row can be "
        "realized without grading the unbacked-block refusal instead of this outline's rule. "
        "mixed_delivery declares [webhook, offline] and is blocked by the offline member alone. "
        "Both graduate when bucket report delivery lands — #1729",
    ),
    # Wired non-dormant + strengthened (salesagent-scgh): the no-emission row passes (no
    # must_equal_when trigger fires → webhook_signing absent is schema-valid); the emission rows
    # grade the conditional invariant (supported MUST equal true) and fail because the
    # capabilities builder emits no webhook_signing block.
    # Graduated 2026-08-12 (#1291 D2), PARTIALLY: reporting_webhook_emission removed — a keyed,
    # publishable tenant declaring reporting_delivery_methods=[webhook] now derives
    # webhook_signing.supported=true, which is the must_equal_when invariant the row grades, and
    # its Given declares that state instead of recording it. The other two rows stay: their
    # triggers are not declarable HERE, and the blocker is not signing. Serial in-process run
    # 2026-08-12: uc010 slice 338 -> 341 passed (+1 row x 3 transports), 258 -> 255 xfailed,
    # 0 failed, 0 xpassed. In-network (the authority, and the leg where a runner-minted key must
    # be openable by the live server): bdd_e2e test-results/innet_120826_1420 has
    # reporting_webhook_emission PASSING on e2e_rest with the two rows below still xfailed,
    # 535 passed / 0 failed.
    (
        "T-UC-010-v31-webhook-signing-required-when",
        {"content_standards_webhook", "wholesale_feed_webhook"},
        "the other two must_equal_when triggers name blocks this deployment does not implement, so "
        "there is no honest way to make either fire: media_buy.content_standards has no surface at "
        "all (#1855) and wholesale_feed_webhooks has no model field (#1867). Declaring either is "
        "refused NAMING THAT BLOCK, so realizing them would grade these rows by the wrong refusal "
        "rather than by the webhook-signing invariant. They graduate when those surfaces land",
    ),
    # Graduated 2026-08-12 (#1291 D2): T-UC-010-v31-identity-required-when-signing (rows
    # posture_declared_identity_absent, posture_declared_identity_empty) and
    # T-UC-010-v31-identity-brand-json-url-bounds (rows posture_url_absent,
    # posture_identity_empty) removed ENTIRELY — both outlines grade one rule and it is now
    # real. The identity block became declarable with the signing family (IdentityDeclaration,
    # src/core/signing/posture.py), and CapabilityDeclarations._validate_identity_relations
    # rule (e) rejects a bucket-naming posture whose trust-root pointer is missing or empty
    # with AdCPConfigurationError naming capability_declarations.identity.brand_json_url —
    # which the Thens grade on the wire via assert_envelope_shape(CONFIGURATION_ERROR,
    # recovery='terminal', message_substr='brand_json_url'). The Givens now realize the
    # posture and the identity state (absent / {} / derived) as real tenant declarations
    # through CapabilitiesEnv.declare_signing instead of recording intent, so the four rows
    # fail for the reason they name. Inspected per .claude/rules/workflows/xpass-graduation.md
    # (feature :1215-1240 and :1551-1580 are the authority; obligation re-verified against the
    # pinned 3.1.1 required_when; no assertion weakened). Verified serially on a2a/mcp/rest,
    # 2026-08-12: tests/bdd/test_uc010_discover_seller_capabilities.py +
    # test_uc010_declaration_backing.py -rxX went 317 -> 329 passed (+4 rows x 3 transports),
    # 279 -> 267 xfailed, 0 failed, 0 xpassed. These entries are NOT gated for e2e_rest
    # (:1314-1322), so graduation was decided on the in-network leg, which is the authority:
    # bdd_e2e run sa-0a76f566 (test-results/innet_120826_1349) has all 7 rows of both outlines
    # PASSING on e2e_rest, 0 failed. tests/bdd/e2e_rest_known_failures.txt carries no UC-010
    # entry for either outline, so nothing to graduate there.
]


# UC-010 scenarios that are PARKED WITH A REASON rather than silently unwired.
#
# Before this table, a scenario missing from _UC010_WIRED_TAGS xfailed with the blanket
# "UC-010 wiring batch 2/3 pending" message whether it was merely un-got-to or genuinely
# blocked on unimplemented backing. That is indistinguishable from the dormant-scenario
# defect: nobody reading the ledger could tell "no one has written the steps" from "the
# steps cannot be written honestly yet".
#
# Every entry names WHAT is missing and WHERE it is tracked. An entry leaves this table
# only when production backs the block — never by weakening the scenario.
_UC010_PARKED_TAGS: dict[str, str] = {
    # #1291 D1 made the signing family declarable, and four of its main-flow scenarios are
    # wired as a result (see _UC010_WIRED_TAGS batch 14). These three CANNOT be wired
    # honestly, and each for a reason that has nothing to do with signing:
    "T-UC-010-v31-identity-brand-json-url": (
        "the scenario's second Given declares sponsored_intelligence.brand_url, so its "
        "distinct_from assertion is non-vacuous. Nothing in src/ implements the "
        "sponsored_intelligence surface, so under the STRICT capability policy the block is "
        "undeclarable and unemitted: the Given cannot be realized, and wiring it anyway "
        "would compare the emitted trust root against an ABSENT value and pass vacuously. "
        "Needs a backed sponsored_intelligence block. The brand_json_url half it shares "
        "with -identity-brand-json-url-bounds IS graded (#1291 D1)"
    ),
    "T-UC-010-v31-identity-key-origins": (
        "three of the four rows declare a key-origin purpose this deployment does not back: "
        "governance_signing and tmp_signing need a separate governance/TMP signing JWKS "
        "origin that nothing serves, and webhook_signing names a delivery-surface origin we "
        "do not publish separately. Emitting any of them would break the pin's "
        "purpose_anchoring constraint (x-adcp-validation.verifier_constraints), which "
        "requires a declared origin to have its posture. The request_signing row IS "
        "gradable today — key_origins.request_signing is emitted from jwks_origin() exactly "
        "when a bucket is declared — so this graduates row-by-row, not as a scenario"
    ),
    "T-UC-010-v31-identity-compromise-notification": (
        "asserts the seller declares identity.compromise_notification.emits=true, i.e. that "
        "it emits the compromise-notification webhook on revocation-due-to-compromise. "
        "Zero implementation exists (no hits for 'compromise' anywhere in src/), so "
        "declaring it would be exactly the over-promise the STRICT policy exists to "
        "prevent. Needs the compromise-notification webhook event itself"
    ),
    # content_standards is refused by _UNBACKED_BLOCKS for a reason that OUTLIVES #1291:
    # nothing implements local evaluation, artifacts, verdicts or artifact_webhook
    # delivery, so signing landing does not make the block declarable.
    "T-UC-010-v31-content-standards-block": (
        "media_buy.content_standards is undeclarable and unemitted — no content-standards "
        "surface exists in this deployment (no local evaluation, artifacts, verdicts or "
        "artifact_webhook delivery). Re-homed off #1291, which does not unblock it"
    ),
}


# The same park, one level finer: individual ROWS of a wired outline whose state cannot be
# realized here, in outlines whose other rows ARE graded. _SELECTIVE_XFAIL cannot express
# this, because it is strict — and a row parked HERE is one that would PASS, vacuously, on
# a Given that could not realize its trigger. Strict-xfailing it turns the vacuous pass into
# a build failure; leaving it alone counts a test that cannot fail as coverage.
#
# So the park is imperative (``pytest.xfail`` before the harness is built), exactly like
# _UC010_PARKED_TAGS: the row does not run, and the reason says what would make it runnable.
# An entry leaves this table when production backs the block — never by weakening the
# scenario. (tag, nodeid substrings, reason)
_UC010_PARKED_ROWS: list[tuple[str, set[str], str]] = [
    (
        "T-UC-010-v31-webhook-signing-bounds",
        {
            "supports_webhook_delivery=true, supported=true",
            "wholesale_feed_webhooks.supported=true, supported=true",
        },
        "the row's must_equal_when trigger is an unbacked block — media_buy.content_standards "
        "has no surface in this deployment (#1855) and wholesale_feed_webhooks has no model "
        "field at all (#1867) — so the trigger can never reach the wire and "
        "_assert_webhook_signing_must_equal_when short-circuits: the row would PASS while "
        "grading nothing but a generic schema-valid block. Parked rather than left green "
        "(#1291 D2, owner decision): a test that cannot fail is not coverage. It graduates with "
        "the surface that makes its trigger declarable",
    ),
]


# MCP selective xfails: previously the MCP wrapper did not accept the
# disclosure_positions keyword. #1417 added disclosure_positions +
# disclosure_persistence to the MCP list_creative_formats wrapper, so the param
# is now accepted on MCP exactly like A2A/REST. The disclosure *filter* gap
# (_impl does not filter by disclosure) is all-transport and handled by
# _UC005_PARTIAL_TAGS / _XFAIL_TAGS, so no UC-005 MCP-specific entries remain.
# (tag, example_substrings, reason, strict)
# strict=True  → must fail (genuine xfail)
# strict=False → may pass vacuously (MCP errors → empty list → exclusion assertions pass)
_MCP_SELECTIVE_XFAIL: list[tuple[str, set[str], str, bool]] = [
    # Graduated (salesagent-rrz8): MCP ToolResult now pre-serializes via
    # model_dump(mode="json") (src/core/tools/_mcp.py), so unset
    # fields are correctly omitted instead of serialized as JSON null.
    # Former entries: T-UC-010-ext-e-absent (context: null), T-UC-010-
    # degradation-account/no_tenant (account: null).
]

# NOTE: the former _REST_XFAIL_TAGS set was retired once the stale
# CreativeFormatsEnv.build_rest_body override (which returned {}) was removed.
# In-process REST now serializes the request body and filters for real, so these
# UC-005 filter scenarios pass on [rest] like every other transport. The only
# UC-005 filter tags that still cannot hold are not REST-specific: inv-031-1-holds
# / inv-031-1-violated stay xfailed via _XFAIL_TAGS because adcp 3.12 removed the
# `type` filter for ALL transports (not a REST body issue).


# Every transport a BDD nodeid can be parametrized over. `impl` is vestigial
# (sunsetted from BDD parametrization by #1417) but two predicates below still
# consume it, so it stays in the alternation.
#
# `e2e_rest` is listed before `rest` for readability only — it is NOT what
# disambiguates them, and a comment claiming otherwise was wrong. A regex matches
# at the earliest POSITION before it consults alternation order, and the "rest"
# inside "e2e_rest" always sits four characters later, so neither reordering this
# tuple nor dropping the `\[` anchor can make an `[e2e_rest-row]` nodeid report as
# `rest` (all three mutations verified to leave the guard green). What the
# bracket discipline DOES buy is refusing a row id that merely contains a
# transport name.
_NODEID_TRANSPORTS = ("e2e_rest", "a2a", "mcp", "rest", "impl")
_TRANSPORT_IN_NODEID = re.compile(r"\[(" + "|".join(_NODEID_TRANSPORTS) + r")[-\]]")


def _transport_of(nodeid: str) -> str | None:
    """The transport a parametrized BDD nodeid dispatches through, else None.

    One derivation for the whole file. Previously this was spelled out as
    `"[X]" in nodeid or "[X-" in nodeid` pairs in several places, which is how the
    `e2e_rest`/`rest` overlap becomes a silent mis-route.
    """
    match = _TRANSPORT_IN_NODEID.search(nodeid)
    return match.group(1) if match else None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply xfail markers to scenarios with unimplemented production features."""
    for item in items:
        marker_names = {m.name for m in item.iter_markers()}
        nodeid = item.nodeid

        transport = _transport_of(nodeid)
        is_mcp = transport == "mcp"
        is_a2a = transport == "a2a"
        is_rest = transport == "rest"
        is_impl = transport == "impl"
        is_e2e_rest = transport == "e2e_rest"

        # uc005 type-filter / disclosure-validation scenarios cannot hold as strict
        # xfails over e2e_rest — but NOT because the body is dropped (build_rest_body
        # now serializes the request and the live server observes the filters). The
        # remaining gaps are transport-independent production gaps that the in-process
        # transports xfail strict=True: the `type` filter was removed from the SDK 5.7
        # request model (adcp 3.12 — the pin 3.1.0-beta.3 still lists it, but the
        # generated request cannot carry it), and disclosure_positions lacks the pin's
        # uniqueItems validation. Against the live Docker server these scenarios pass
        # vacuously (valid rows dispatch unfiltered) rather than failing deterministically,
        # so strict=True would XPASS — weaken to strict=False to tolerate either outcome.
        uc005_filter_e2e_untestable = {
            # type filter — removed from the SDK 5.7 request model (pin still lists it)
            "T-UC-005-inv-031-1-violated",
            "T-UC-005-partition-type-filter",
            "T-UC-005-boundary-type-filter",
            # disclosure_positions duplicate — prod lacks the pin's uniqueItems validation
            "T-UC-005-partition-disclosure",
            "T-UC-005-boundary-disclosure",
        }
        uc005_filter_e2e_reason = (
            "e2e_rest: type filter removed from SDK 5.7 request model / disclosure_positions "
            "uniqueItems not validated in production — transport-independent gaps that pass "
            "vacuously over the live server, so the strict in-process xfail cannot hold here"
        )

        # Graduated: UC-005 creative agent type/asset_type filter tests now pass —
        # When steps dispatch through harness (blanket xfail removed).

        # NOTE (#1417/S5 reconciliation): the UC-002 @account error
        # scenarios (ext-r / ext-r-nk / ext-s / ext-t) are NOT impl-exclusive and
        # are NOT a wire-only gap — they failed on ALL four transports (impl + wire)
        # in the pre-drop baseline. They are the pre-existing budget-branch When-step
        # routing bug (create_media_buy account-resolution scenarios build a request
        # with `account_ref`, which CreateMediaBuyRequest rejects). That is a step
        # bug fixable in the When step, not a production wire gap, so it is left as a
        # genuine (pre-existing) failure rather than masked with an xfail. The
        # drop-impl change introduces 0 new failures; this debt is out of scope.

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

        # Graduated: UC-006 REST account resolution (success AND error paths).
        # SyncCreativesBody now forwards `account`, so the sync_creatives REST route
        # resolves accounts and raises ACCOUNT_* errors instead of returning 200.
        # The former xfail block for T-UC-006-partition-account/boundary-account error
        # rows on rest is removed — those scenarios pass.

        # RESOLVED: MCP transport suggestion field now correctly unpacked by
        # _unwrap_mcp_tool_error (was double-nesting the extra JSON blob).

        # RESOLVED: in-process REST no longer drops UC-005 filter params. The
        # CreativeFormatsEnv.build_rest_body override that returned {} was removed,
        # so [rest] serializes the request body and filters for real — the former
        # _REST_XFAIL_TAGS block is gone (see note above the function).

        # E2E_REST: Docker always has the creative agent — can't test empty catalog
        if is_e2e_rest and "T-UC-005-empty-catalog" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="E2E Docker always has creative agents — cannot test empty catalog",
                    strict=True,
                )
            )

        # E2E_REST: the 3 UC-003 manual-approval scenarios (T-UC-003-alt-manual,
        # T-UC-003-approval-tenant, T-UC-003-approval-adapter) GRADUATED — the old
        # strict xfail ("RestE2EDispatcher lacks update-endpoint support") became
        # stale when MediaBuyDualEnv gained dynamic REST_ENDPOINT/REST_METHOD update
        # dispatch (_active_update, PR #1567 lineage) and the trio XPASSed the
        # in-network run. They now grade on all four transports.
        # Per-scenario graduation inspection (scenario → BR → siblings → production):
        # - T-UC-003-alt-manual → GRADUATE — POST-S7/S8: MediaBuyDualEnv.build_rest_body
        #   sets _active_update + _update_target_id, so RestE2EDispatcher PUTs the real
        #   /api/v1/media-buys/{id} route (src/routes/api_v1.py:345) and stashes the raw
        #   HTTP JSON as wire_response; task_id/NOT-contain steps grade that wire via
        #   _submitted_wire_dict (loud failure if wire_response missing on non-IMPL).
        # - T-UC-003-approval-tenant → GRADUATE — BR-RULE-017 INV-2: same real-wire path;
        #   status "submitted" asserted on the typed payload parsed from the live wire.
        # - T-UC-003-approval-adapter → GRADUATE — BR-RULE-017 INV-3: same real-wire path;
        #   the last_a2a_task guard is Transport.A2A-gated and inert on e2e_rest.
        # No UC-003 entries remain in e2e_rest_known_failures.txt — no sibling conflict.

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

        # FIXME(#1291): E2E_REST — these Thens observe env.mock['post'] or
        # CircuitBreaker state, neither of which is visible through the Docker HTTP
        # path.
        #
        # The cited id used to be a beads id that DOES NOT RESOLVE
        # ('bd show' returns no issue found). This project's rule is that code
        # comments cite a GitHub issue number precisely so they resolve for outside
        # contributors — a dangling internal id is worse than no reference, because
        # it reads as tracked.
        #
        # The stated unblock condition is now MET: a TLS-fronted receiver exists
        # (tests/e2e/webhook_capture_service.py) and the e2e webhook suites deliver
        # to it. What remains is per-scenario work, NOT a bulk removal: each Then
        # must be rewritten to read that receiver's captures instead of the
        # in-process mock, and each scenario re-checked for vacuity under
        # .claude/rules/workflows/xpass-graduation.md. Removing the tags without
        # that would turn the remaining 11 scenarios green against assertions that
        # can no longer observe anything.
        #
        # The receiver is no longer the only prerequisite: a delivery that happens
        # IN the test process is one the live server never made. `env.deliver_webhook()`
        # / `env.last_delivery()` (tests/harness/_mixins.py) are the seam that fixes
        # that; a tag graduates when its Thens read through them AND the behaviour it
        # asserts is one the live delivery path actually has.
        _UC004_E2E_WEBHOOK_INTERNAL_TAGS: set[str] = {
            # Graduated e2e_rest (salesagent-n78j0.13): T-UC-004-webhook-bearer. Traced
            # independently of the hmac row graduated below it — the two share a tag set, a
            # step layer and a harness, and this epic has twice had such neighbours be wrong
            # about each other, so "the sibling graduated" was treated as a hypothesis to
            # test rather than a reason.
            #
            # This row is STRUCTURALLY WEAKER than its hmac sibling and the inspection was
            # aimed at that: hmac has three Thens, one of which RECOMPUTES the digest over
            # the received bytes; bearer has ONE Then and no recompute of any kind. So the
            # question that decided it was not "does the delivery happen" but "does the lone
            # Then grade the token's VALUE, or merely the header's PRESENCE?" A presence-only
            # assertion is the vacuity signature: it passes for any Authorization header
            # anything happens to attach.
            #
            # It grades the VALUE. The chain, verified end to end rather than assumed:
            # `given_bearer_token_valid` sets ctx['webhook_bearer_token'] = 'b'*32;
            # `_auth_scheme_to_db_fields` maps scheme 'bearer' onto authentication_type +
            # authentication_token; `_persist_webhook_config_if_needed` writes that row; the
            # live server's `_send_report_for_media_buy` finds it by
            # (principal, tenant, url, is_active) — NOT the auth-less
            # `raw_request["reporting_webhook"]` — so `legacy_auth_mode` returns LEGACY_BEARER
            # (scheme not in _HMAC_SCHEMES, token non-empty) and `build_webhook_sender` takes
            # the `from_bearer_token` arm. `then_bearer_header` reads
            # `env.last_delivery()` — the TLS capture receiver, never `env.mock["post"]` —
            # and asserts token == ctx's token, so expected is TEST-owned and actual is
            # off the wire. Not circular.
            #
            # Verified by mutation, and the mutation shape was chosen to separate the two
            # questions: a WRONG-BUT-PRESENT token (sign with a different 32-char value),
            # NOT a removed header. Removing the header would only re-prove presence, the
            # half that was never in doubt. The leg goes RED on the wrong value, which is
            # what "grades the VALUE" means. Run ids for the pair are in the COMMIT BODY,
            # for the reason the sibling note below records.
            #
            # ONE LATENT WEAKNESS, RECORDED NOT FIXED (it does not affect this graduation,
            # and widening scope mid-graduation is how a row gets strengthened into passing):
            # the value assertion is CONDITIONAL — `if expected_token:` — so it silently
            # degrades to presence-only if a future Given ever stops setting
            # ctx['webhook_bearer_token']. Today the Given sets it unconditionally and the
            # only step that pops it (`given_webhook_no_authentication`, :569) belongs to
            # the 9421 scenario, so the branch is live here. Also, unlike its 9421 twin this
            # scenario asserts no NEGATIVE: :1425 forbids signing the same webhook both
            # ways, and nothing here would catch a delivery that carried Authorization AND
            # a 9421 Signature.
            #
            # Graduated e2e_rest (salesagent-n78j0.13): T-UC-004-webhook-hmac. Traced
            # independently of its 9421 sibling rather than carried by it, because "the
            # neighbour was fixed" is inference and not evidence. What the inspection
            # found, end to end: the When routes through `env.deliver_webhook()`, whose
            # e2e realization drives the live server's own
            # /admin/.../trigger-delivery-webhook route; the server's
            # `_send_report_for_media_buy` looks up DBPushNotificationConfig by
            # (principal, tenant, url, is_active) and that row — NOT the auth-less
            # `raw_request["reporting_webhook"]` that `_attach_reporting_webhook` writes —
            # is what carries the HMAC registration, so `build_webhook_sender` takes the
            # LEGACY_HMAC arm (`legacy_auth_mode` :463) and signs with
            # `from_adcp_legacy_hmac`. All three Thens read `env.last_delivery()`, i.e.
            # the TLS capture receiver, and the last one RECOMPUTES the digest over
            # `captured.content` — the bytes the receiver actually got — so it fails when
            # the bytes signed are not the bytes sent. None of them touches
            # `env.mock["post"]`, which is what made the rest of this set unobservable
            # over Docker HTTP.
            #
            # STALE COMMENT CORRECTED: this file's :412 still says of this same tag
            # "DB setup fix exposed that Then steps are pending (no-op) — test passes
            # trivially". That has not been true since #1291 C1 / salesagent-n78j0.1.4
            # routed the Thens; it described the tree at the time it was written and was
            # never revisited. It is the exact claim this graduation had to disprove, and
            # a reader who trusted it would have concluded the opposite of the truth.
            #
            # Verified by mutation, not by the green mark: blanking the legacy-HMAC arm in
            # `build_webhook_sender` turns this e2e_rest leg RED. Run ids for the pair are
            # in the COMMIT BODY, for the reason the sibling note below records — any edit
            # to this file voids the pair, so a citation kept here could never stay valid.
            #
            # Graduated e2e_rest (salesagent-n78j0.1.4): T-UC-004-webhook-9421. The
            # bypass this set records is now GONE for that scenario — the delivery
            # ACTION moved out of the step layer into `env.deliver_webhook()`, which
            # over e2e drives the live server's own
            # /admin/.../trigger-delivery-webhook route, its key is minted INSIDE the
            # container (the feature file's :301 comment recorded that no key was ever
            # provisioned for this leg), and its Thens read the TLS capture receiver
            # through `env.last_delivery()`. Verified by mutation: deleting
            # `_rfc9421_sender`'s signing arm turns the e2e_rest leg red. The two run ids
            # behind that sentence are cited in the COMMIT BODY rather than here, and
            # deliberately: any edit to this file voids the pair (tox.ini :181 collects
            # `pytest tests/bdd/`), so a comment that must be rewritten with each new pair
            # can never hold a valid citation. Whichever pair the commit cites, this
            # sentence is only true of a run on the committed tree.
            #
            # ---- the note below governs the NEXT tag, not the graduated one ----
            #
            # STAYS PARKED, and the transport bypass is no longer the reason — that
            # one is fixed (above). What remains is a real SERVER-SIDE gap: the live
            # delivery path emits NotificationType.scheduled unconditionally
            # (delivery_webhook_scheduler.py :267), so the `final` / `delayed` /
            # `adjusted` Examples rows cannot pass over e2e_rest whatever the harness
            # does. Unparks when production selects the notification type; grading that
            # is not this atom's scope (salesagent-n78j0.1.4).
            "T-UC-004-webhook-notification-type",
            # STAYS (salesagent-n78j0.13) — FAILED INSPECTION STEP 5 (Then assertions), and
            # it fails it in a shape neither graduated neighbour had, which is why "hmac and
            # bearer graduated" was not allowed to carry it.
            #
            # The seam is RIGHT here: the Then reads `env.last_delivery()` (the TLS capture
            # receiver), parses `captured.content`, and both `_get_last_webhook_payload` :77
            # and `last_delivery()` (_mixins.py :859) fail loudly on an empty payload / absent
            # delivery. So the usual vacuity routes — empty box, failed delivery, never read —
            # are all closed. It is still vacuous, structurally:
            #
            # `assert "aggregated_totals" not in payload` inspects ONLY TOP-LEVEL keys, and the
            # two production webhook builders emit different body SHAPES:
            #   in-process (WebhookDeliveryService.send_delivery_webhook :302) -> FLAT body, so
            #     top level is where the field would sit and the assertion is meaningful;
            #   live server / e2e_rest (admin trigger -> _send_report_for_media_buy ->
            #     create_mcp_webhook_payload :332) -> McpWebhookPayload ENVELOPE, whose
            #     top-level field set is FIXED. `aggregated_totals` is structurally impossible
            #     there, so this leg cannot fail for any behaviour of the delivery path.
            #
            # And the obligation is not merely ungraded, it is VIOLATED. Pinned 3.1.1,
            # building/by-layer/L3/webhooks.mdx :253 — "aggregated_totals ... must not be
            # emitted in reporting webhook RESULT PAYLOADS" — names `result`, which is exactly
            # where production puts it. Observed on the wire, run innet_220826_0718 (probe over
            # a strict e2e_rest leg, tag temporarily lifted, then restored by content copy):
            #   top_level=[idempotency_key, protocol, result, status, task_id, task_type,
            #              timestamp]
            #   result.aggregated_totals={'impressions': 11111.0, 'spend': 111.11,
            #                             'media_buy_count': 1}
            # with result also carrying currency / media_buy_deliveries / reporting_period /
            # sequence_number — i.e. the delivery ARRIVED intact, so the top-level absence was
            # observed against a populated payload, not an empty one. The real Then was GREEN
            # in that same run. No mutation was needed to "add aggregated totals": production
            # already ships them and this assertion does not see them. GH #2058.
            #
            # Unparks when the Then grades the RESULT payload (and production stops emitting
            # the field). Strengthening it now would strengthen a row into passing, which
            # proves nothing about production — so it is recorded, not patched, here.
            "T-UC-004-webhook-no-aggregated",
            "T-UC-004-webhook-circuit-open",
            # PARKED, NOT GRADUABLE (salesagent-n78j0.13): T-UC-004-webhook-circuit-recovery.
            # Inspected in full against .claude/rules/workflows/xpass-graduation.md. The
            # reason recorded in the xfail above — "CircuitBreaker state not observable
            # through Docker HTTP" — is TRUE of this scenario but FALSE of the behaviour,
            # and the difference is the whole finding. Do not un-route on the strength of
            # the second half.
            #
            # (a) THE SCENARIO NEVER DELIVERS ANYTHING. Its When —
            # `when_deliver_probe_reports` (steps/domain/uc004_delivery.py:1063) — does not
            # send a webhook. It reaches into the breaker and increments the counter:
            #     cb = service._circuit_breakers.get(endpoint_key)
            #     for _ in range(n): cb.record_success()
            # The Given `the webhook endpoint has recovered and returns 200` sets a mock
            # HTTP status that is never read, because no HTTP call is made. So "the system
            # delivers 2 successful probe reports" is not what is executed.
            #
            # (b) THE BREAKER IT POKES IS NOT PRODUCTION'S. `CircuitBreakerMixin.get_service`
            # (tests/harness/_mixins.py:1005) constructs a FRESH WebhookDeliveryService().
            # Production's consumer reads the module singleton (webhook_delivery_service,
            # src/services/webhook_delivery_service.py:647) via `_is_circuit_breaker_open`.
            # Different objects — so even in-process, the state this scenario sets is state
            # production would never consult. `e2e_config` does not change this; it only
            # scopes the DB.
            #
            # MEASURED, NOT INFERRED. Two mutations, restored by content copy (md5
            # cbbda5965914e9d163e70faa17bfd6e9, empty diff):
            #   M1  break the HALF_OPEN->CLOSED transition inside record_success  -> RED (3/3)
            #   M2  break the DELIVERY path's record_success() call (:558, the 2xx
            #       branch of _deliver_with_backoff)                              -> GREEN
            # M1 going red proves only that the Then reads a real CircuitBreaker object by
            # direct in-process attribute access — NOT that any transport observed it. M2 is
            # the one that decides the row: production can stop recording delivery success
            # entirely and this scenario, the whole UC-004 module (516 passed) and the
            # unit/harness circuit tests (141 passed) all stay green.
            #
            # THE BEHAVIOUR IS OBSERVABLE OVER THE WIRE — the parked reason is too strong.
            # An open breaker is buyer-visible: _get_media_buy_delivery_impl consults it per
            # request (src/core/tools/media_buy_delivery.py:257) and rewrites the reported
            # status (:297) `if status == "active" and reporting_circuit_open: status =
            # "reporting_delayed"`. A scenario that drives REAL failing deliveries until the
            # status flips to reporting_delayed, then REAL succeeding ones until it returns
            # to active, grades open->recover->close through production's own singleton on
            # every transport including e2e_rest.
            #
            # Un-routing now would be a false green precisely because the scenario passes on
            # e2e_rest WITHOUT contacting the live server. Stays until rewritten against the
            # reporting_delayed seam, and any rewrite must show M2 RED before graduating.
            # Production coverage gap tracked as GH #2060.
            "T-UC-004-webhook-circuit-recovery",
            "T-UC-004-webhook-retry-success",
            # jdy1-M4: retry/sequence observability — assert on env.mock['post']
            # call counts / args, not visible over the Docker HTTP path.
            "T-UC-004-webhook-retry-5xx",
            "T-UC-004-webhook-retry-network",
            "T-UC-004-webhook-no-retry-4xx",
            "T-UC-004-webhook-sequence",
        }
        if is_e2e_rest and (marker_names & _UC004_E2E_WEBHOOK_INTERNAL_TAGS):
            item.add_marker(
                pytest.mark.xfail(
                    reason="E2E: webhook POST mock + CircuitBreaker state not observable through Docker HTTP",
                    strict=False,
                )
            )

        # GRADUATED (#1417/nzjx): UC-003 empty update now rejected. Production raises
        # AdCPInvalidRequestError (INVALID_REQUEST + buyer suggestion) per BR-RULE-022
        # INV-3. Grounded against AdCP 3.1 GA: update fields are all optional in
        # update-media-buy-request.json, so an empty update passes schema validation and
        # is a SEMANTIC rejection → INVALID_REQUEST, not the schema-level VALIDATION_ERROR
        # (GA L3 error-handling). The two Scenario-Outline rows that asserted
        # VALIDATION_ERROR were corrected to INVALID_REQUEST in the same change.

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
            # Graduated (#1417/gh8p.10): both auth error paths now carry a buyer-facing
            # suggestion. The REST auth boundary (_require_auth_dep) raises
            # AdCPAuthRequiredError with AUTH_REQUIRED_SUGGESTION (REST no-identity envelope
            # no longer drops it), and the unknown-principal ownership check
            # (AdCPAuthorizationError) carries a "verify your x-adcp-auth token" suggestion.
            # T-UC-003-ext-a / -ext-a-unknown pass on a2a/mcp/rest.
            "T-UC-003-ext-c": "production returns PERMISSION_DENIED (AdCPAuthorizationError), spec expects ACCOUNT_NOT_FOUND",
            # Graduated: T-UC-003-ext-d, T-UC-003-ext-d-negative (production now returns BUDGET_TOO_LOW)
            # Production doesn't validate these cases at all
            "T-UC-003-ext-e": "production doesn't validate end_time < start_time on update",
            "T-UC-003-ext-e-equal": "production doesn't validate end_time == start_time on update",
            # FIXME(salesagent-lp0x): stale .feature expectation, NOT a production gap.
            # Production validates currency on update and correctly emits
            # UNSUPPORTED_FEATURE (AdCPCapabilityNotSupportedError, media_buy_update.py:441;
            # verified at wire on mcp/rest/a2a). The generated .feature asserts INVALID_REQUEST.
            # UNSUPPORTED_FEATURE is the authoritative code (adcp-req BR-UC-002 impl-coverage;
            # matches UC-002 ext-d). Graduates after upstream regen.
            "T-UC-003-ext-f": "generated .feature asserts INVALID_REQUEST; production correctly emits UNSUPPORTED_FEATURE for unsupported currency on update — stale spec, pending upstream regen (salesagent-lp0x)",
            # FIXME(salesagent-lp0x): stale .feature expectation, NOT a production gap.
            # Production validates the daily spend cap on update and correctly emits
            # BUDGET_EXCEEDED (AdCPBudgetExceededError, media_buy_update.py:484;
            # verified at wire on mcp/rest/a2a). The generated .feature asserts the
            # pre-v3.1 BUDGET_TOO_LOW (see UC-002 ext-k). Graduates after upstream regen.
            "T-UC-003-ext-g": "generated .feature asserts pre-v3.1 BUDGET_TOO_LOW; production validates and correctly emits BUDGET_EXCEEDED — stale spec, pending upstream regen (salesagent-lp0x)",
            # Graduated (#1417/gh8p.10): a failed creative sync no longer crashes with an
            # FK violation. _process_assignments skips assignment for un-synced creatives,
            # and update_media_buy raises a clean retryable AdCPAdapterError carrying a
            # buyer-facing retry suggestion. T-UC-003-ext-k passes on a2a/mcp/rest.
            # Graduated (#1417): the .feature now asserts standard codes
            # (VALIDATION_ERROR for an invalid placement id, UNSUPPORTED_FEATURE when the
            # product defines no placements; invalid_placement_ids is not in the AdCP
            # vocabulary @04f59d2d5) and production emits them with a recovery suggestion.
            # The placement fixture gap (placement_configs -> placements) is fixed.
            # T-UC-003-ext-m / -ext-m-unsupported pass; xfails removed.
            # T-UC-003-ext-n moved to a dedicated STRICT xfail below (production gap).
            # Graduated: T-UC-003-ext-o (rczc: adapter failure returns correct shape on all 4 transports)
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

        # FIXME(salesagent-gh8p.11 / production-gap bead): UC-003 ext-n insufficient
        # privileges. Storyboard BR-UC-003-ext-n grounds an ADMIN-only adapter gate
        # (e.g. GAM guaranteed-item activation) that emits the canonical
        # PERMISSION_DENIED (pinned enum @04f59d2d5; reconciled from the prose's
        # non-canonical "insufficient_privileges" in adcp-req BR-UC-003 impl-coverage).
        # Production has NO privilege gate on update, and the AdCP buyer protocol has
        # no principal-role concept (roles live on the admin-UI User model, not
        # Principal). The fields-less ext-n request also short-circuits through the
        # empty-update INVALID_REQUEST path before any adapter call. The step now
        # arms the real update adapter with a canonical PERMISSION_DENIED rejection,
        # so this strict xfail flips to a wire-asserted pass the moment production
        # gates admin-only update actions. Strict: fails loudly when that lands.
        if "T-UC-003-ext-n" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="production gap: no admin-only privilege gate on update_media_buy; "
                    "AdCP buyers have no principal-role concept and the fields-less request "
                    "short-circuits via empty-update INVALID_REQUEST before any adapter call "
                    "(canonical target: PERMISSION_DENIED) — salesagent-gh8p.11",
                    strict=True,
                )
            )

        # FIXME(salesagent-gh8p.13 / production-gap bead): UC-003 ext-v cancellation
        # refused. canceled IS a valid UpdateMediaBuyRequest field but production
        # never reads it, has no state-based NOT_CANCELLABLE check, and
        # has_updatable_fields() omits canceled — so a media_buy_id+canceled
        # request trips the empty-update INVALID_REQUEST path instead of
        # NOT_CANCELLABLE. The step arms the update adapter with the canonical
        # NOT_CANCELLABLE refusal and dispatches the real cancel on the wire, so
        # this strict xfail flips to a pass when production wires the cancel path.
        if "T-UC-003-ext-v" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="production gap: update_media_buy never reads canceled and has no state-based "
                    "cancellation gate; has_updatable_fields() omits canceled so the request short-circuits "
                    "via empty-update INVALID_REQUEST (canonical target: NOT_CANCELLABLE) — salesagent-gh8p.13",
                    strict=True,
                )
            )

        # Retired (both sides, 20e5b60d8 / PR #1567 round-2 item 2): the former
        # T-UC-002-alt-manual workflow_step_id xfail targeted the pre-3.1.1
        # scenario assertion. The scenario was reconciled to the 3.1.1
        # CreateMediaBuySubmitted contract (task_id, no media_buy_id/
        # workflow_step_id) and passes on all 4 transports — a strict xfail here
        # would XPASS-fail.

        # --- UC-005: disclosure/asset scenarios with partial impl ---
        # FIXME(beads-dul): disclosure_positions and brief/catalog asset types
        # partially implemented — some transport variants pass, others fail.
        # Must run BEFORE selective xfails (which use strict=True) to avoid
        # XPASS failures on transport variants that now pass.
        _UC005_PARTIAL_TAGS = {
            # disclosure_positions filter is not implemented in _impl (all transports).
            # #1417 added the param to the MCP wrapper, so MCP now sends it
            # and fails the exclusion assertion exactly like impl/a2a/rest — hence the
            # former `not is_mcp` exclusion is removed (MCP no longer passes vacuously).
            "T-UC-005-inv-049-8-violated",
            "T-UC-005-inv-049-8-nofield",
        }
        if marker_names & _UC005_PARTIAL_TAGS and not is_e2e_rest:
            item.add_marker(pytest.mark.xfail(reason="disclosure/asset partial impl", strict=False))
            # Skip selective xfails for these — the strict=False above covers them
        else:
            # Graduated (#1417): the partition/boundary-disclosure "valid"
            # examples (all_positions / no_matching_formats / all 8 positions /
            # "format has no") return unfiltered results that satisfy the assertion,
            # so they now PASS on every wire transport (a2a/mcp/rest) — no marker.
            # NOTE: main's MCP-specific strict xfails ("MCP wrapper does not accept
            # the disclosure_positions keyword") are intentionally dropped here —
            # #1417 added disclosure_positions to the MCP list_creative_formats
            # wrapper (src/core/tools/creative_formats.py:519), so MCP now accepts the
            # keyword exactly like a2a/rest and the valid examples pass on MCP too.

            # Selective xfail for parametrized scenarios
            for tag, substrings, reason in _SELECTIVE_XFAIL:
                if tag in marker_names:
                    if is_e2e_rest and tag in uc005_filter_e2e_untestable:
                        # tolerate either outcome — see uc005_filter_e2e_reason (salesagent-7fye)
                        item.add_marker(pytest.mark.xfail(reason=uc005_filter_e2e_reason, strict=False))
                        break
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
                if is_e2e_rest and tag == "T-UC-005-main":
                    # E2E_REST-only harness gap (#1721 M4 dormancy tripwire caught
                    # this): given_registry_multi_categories builds synthetic
                    # FormatFactory formats (audio-spot/banner/pre-roll) whose ids
                    # aren't in the live reference catalog. In-process this is a
                    # no-op (the registry is mocked directly) and the scenario
                    # genuinely reaches and fails on the real production gap this
                    # tag's blanket reason names (audio-spot lacks
                    # asset_requirements/render_capabilities). Over e2e_rest the
                    # Given never gets that far -- CreativeFormatsEnv's
                    # _validate_registry_formats (already a declared/pinned
                    # E2EUnsupportedSetup escape hatch) raises first, since the
                    # live stack can't be told to serve arbitrary synthetic
                    # format ids. Test-wiring, not the graded production gap.
                    item.add_marker(
                        pytest.mark.xfail(
                            reason="E2E_REST harness gap: given_registry_multi_categories' synthetic "
                            "format ids aren't in the live reference catalog, so "
                            "CreativeFormatsEnv._validate_registry_formats rejects the Given before "
                            "reaching the graded audio-spot asset_requirements gap — FIXME(salesagent-hzlp)",
                            strict=False,
                        )
                    )
                    break
                if is_e2e_rest and tag == "T-UC-005-main-referrals":
                    # GRADUATED for e2e_rest (#1417): with a seeded tenant the
                    # live server populates creative_agents (>=DEFAULT_AGENT), so referrals
                    # are present on the wire and the (wire-asserting) Then passes. The marker
                    # stays strict for in-process transports where the registry mock is empty.
                    break
                if is_e2e_rest and tag in uc005_filter_e2e_untestable:
                    # tolerate either outcome — see uc005_filter_e2e_reason (salesagent-7fye)
                    item.add_marker(pytest.mark.xfail(reason=uc005_filter_e2e_reason, strict=False))
                    break
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break

        # --- UC-002: validation xfails (production not implemented) ---
        # NOTE: the former account-ref entries (missing_account / invalid_oneOf_both /
        # "account field absent" / "both account_id and brand") were REMOVED by
        # #1417: those scenarios now dispatch a full create_media_buy on
        # the wire. account is OPTIONAL, so an absent account SUCCEEDS (not
        # INVALID_REQUEST); the oneOf-both shape is rejected by Pydantic at the
        # boundary as VALIDATION_ERROR. The feature outcomes were reconciled to
        # match production and the scenarios now pass on a2a/mcp/rest.
        _UC002_VALIDATION_XFAIL: list[tuple[str, set[str], str]] = [
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

        # GRADUATED (#1534 merge): the former UC-002 oneOf-both account and
        # UC-004 webhook short-credential MCP routes are retired. The documented
        # "MCP TypeAdapter forward-compat gap" (FastMCP's TypeAdapter rejected
        # the request as a bare ToolError before the AdCP boundary translator
        # ran) is closed by RequestCompatMiddleware (#1534,
        # src/core/mcp_compat_middleware.py): TypeAdapter ValidationErrors are
        # now normalized to the AdCP two-layer VALIDATION_ERROR envelope on the
        # MCP wire (spec 3.1.1 enums/error-code.json names VALIDATION_ERROR for
        # schema-level rejections), matching what a2a/rest already emitted. The
        # strict=True markers fired as designed — deterministic XPASS on the
        # merged in-network run for both the oneOf-both rows (partition +
        # boundary) and webhook-creds-short — so the routes are removed and the
        # scenarios grade live on all transports.

        # GRADUATED (#1508 merge): T-UC-002-ext-g on MCP. The recorded gap was real
        # and is now closed at its root. The MCP boundary stashes the idempotency
        # payload-hash input as a SHALLOW copy — `dict(context.message.arguments)`
        # (src/core/mcp_auth_middleware.py) — so the nested packages[].creatives[]
        # dicts stay shared with what FastMCP hands the tool. `Creative.
        # validate_format_id` was a mutate-in-place mode="before" validator, so it
        # wrote a live FormatId object straight into that shared dict; by the time
        # _create_media_buy_impl called canonical_payload_hash(raw_wire_payload) the
        # payload was no longer JSON, and rfc8785 raised CanonicalizationError BEFORE
        # the AdCP boundary translator ran. A2A (copy.deepcopy of parameters,
        # adcp_a2a_server.py) and REST (raw immutable bytes, rest_compat_middleware.py)
        # never shared the object graph — which is exactly why the xfail was MCP-only.
        # `copy_before_mutating()` (src/core/schemas/_base.py, applied in
        # Creative.validate_format_id) makes the validator non-mutating, so the raw
        # wire payload stays pure JSON, the idempotency hash succeeds, and MCP now
        # emits the same two-layer CREATIVE_REJECTED envelope a2a/rest already did.
        # The strict=True marker fired as designed — deterministic XPASS on the merged
        # in-network run — so the route is removed and the scenario grades live on all
        # transports.

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
                "OVER-SPECIFIED OBLIGATION (#1417): scenario asserts the non-canonical "
                "FORMAT_MISMATCH. Production now emits CREATIVE_REJECTED WITH a suggestion + "
                "details (#1417), so the suggestion gap is closed; the code "
                "assertion awaits upstream reconciliation (FORMAT_MISMATCH -> CREATIVE_REJECTED)."
            ),
            # FIXME(#1417): ext-k asserts FORMAT_MISMATCH, which is NOT in the pinned
            # error-code enum (non-canonical). Production now emits CREATIVE_REJECTED
            # (_assignments.py), converged with the update path for the identical
            # condition. Reconcile upstream (adcp-req: FORMAT_MISMATCH -> CREATIVE_REJECTED),
            # then remove this xfail.
            "T-UC-006-ext-k": (
                "OVER-SPECIFIED OBLIGATION (#1417): scenario asserts the non-canonical "
                "FORMAT_MISMATCH (absent from the pinned error-code enum). Production emits "
                "the canonical CREATIVE_REJECTED, converged with the update path. Awaiting "
                "upstream reconciliation of the generated feature."
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

        # Graduated (salesagent-jr5b): UC-004 boundary-account a2a valid rows
        # ("account exists" / "single match" / "sandbox account exists") now resolve
        # once their accounts are seeded — the former "dict lacks .root serialization
        # gap" xfail was masking the missing seed, not a real transport gap.

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
            # Invalid status filter: NOT a production gap — the generic
            # 'with {request_params}' When step shadows the specific
            # status_filter step and parses 'status_filter "X"' (no '=') to {},
            # so the request dispatches with NO params and succeeds (ah98
            # red-step inspection, 2026-07-06). GetMediaBuyDeliveryRequest DOES
            # reject invalid values; the REST wire already returns 400.
            # Suggestion parity for this path is pinned by
            # tests/integration/test_request_validation_suggestion_parity.py.
            "T-UC-004-filter-invalid": ("step shadowing: generic request_params step drops status_filter", True),
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
            # Graduated (#1721 M4 dormancy tripwire): T-UC-004-status-pending-legacy-alias
            # was masked by a missing second Then step (never actually reached the
            # assertion this xfail claimed was failing) -- production DOES correctly
            # surface the persisted pending_start status (XPASS(strict) once the
            # missing step was bound). Removed.
            # Graduated: T-UC-004-aggregated-roas-and-cpa (production now computes
            # conversions/conversion_value/roas/cost_per_acquisition in
            # aggregated_totals — DeliveryTotals.conversion_value + aggregation
            # quotients with omit-on-zero semantics).
            # T-UC-004-attr-supported: resolved — steps now assert attribution_window model and echo
            # T-UC-004-attr-unsupported: resolved — xfail now in step function for specific production gap
            # T-UC-004-attr-echo: resolved — vvx9 + ral2 fixed enum→str handling
            # T-UC-004-attr-omitted: resolved — vvx9 + ral2 fixed enum→str handling
            # T-UC-004-attr-campaign-valid: resolved — _impl now resolves campaign unit to days
            # T-UC-004-attr-campaign-invalid: GRADUATED (#1417). The standalone
            # "Campaign unit with interval != 1 - rejected" scenario now asserts on the wire
            # envelope (its When uses the non-shadowed 'for "mb-001" with attribution_window'
            # regex step, so the window reaches production and INV-5 fires VALIDATION_ERROR
            # with a suggestion on a2a and e2e_rest). The old transport-blind strict marker
            # was stale — removed rather than re-scoped (BDD has no in-process/_impl variant).
            # FIXME(salesagent-7ag5): _impl uses str(enum) instead of enum.value for sort_by metric
            # T-UC-004-dim-sortby-valid: resolved — sort_by now works in _impl
            # Graduated: T-UC-004-dim-sortby-fallback (impl, mcp, rest pass — only a2a still fails)
            # T-UC-004-dim-supported: resolved — by_device_type now populated by _impl (#1376)
            # T-UC-004-dim-truncated: resolved — truncation flags (by_*_truncated) now implemented (#1376)
            # T-UC-004-dim-complete: resolved — by_device_type_truncated flag now implemented (#1376)
            # T-UC-004-dim-geo-system: resolved — by_geo now populated by _impl
            # T-UC-004-dim-geo-postal: resolved — by_geo now populated by _impl
            # T-UC-004-dim-multi: resolved — by_device_type now on PackageDelivery (#1376)
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

        # Graduated: T-UC-004-dim-sortby-fallback — all transports pass.
        # A2A previously dropped by_placement; that serialization gap is fixed.
        #
        # CORRECTION (salesagent-n78j0.13). TWO SEPARATE FAILURES HERE — the second is
        # the serious one.
        #
        # (a) The assertion is vacuous. Descending-ness is not the obligation; INV-6 is
        #     about WHICH metric is sorted on. Production synthesizes every placement
        #     metric from one weight vector, so the rows are descending by all metrics
        #     at once and `values == sorted(values, reverse=True)` holds for any sort
        #     key — including none. Mutation-proved: deleting the spend-fallback (M1)
        #     and falling back to the wrong metric (M2) each left all 6 tests GREEN.
        #
        # (b) THE CERTIFICATE THAT STOOD HERE WAS WRONG. It read: "Verified: ... so the
        #     pass is real, not a weakened assertion" — and it cited, AS ITS EVIDENCE OF
        #     RIGOUR, the two things that hid the vacuity: the sorted(...) assert (which
        #     cannot fail on this data) and the inline pytest.xfail (called a "guard",
        #     but it is a SILENT ESCAPE — it converts "no data to grade" into xfail
        #     rather than failure). A graduation therefore shipped on a pass that could
        #     not fail, carrying a note telling the next reader it had been checked.
        #     A weak assertion is a gap; a false certificate PROPAGATES, because it is
        #     precisely what stops the next reader from looking. Do not restore any
        #     "verified" claim here without a mutation that goes RED.
        #
        # NOT re-routed: the rows do pass, so a strict xfail would fail the suite, and
        # xfail sets only ever shrink. Correcting the claim in place is the only move
        # that does not trade one false state for another. The fix is to make the
        # scenario discriminating (see the sortby-fallback e2e_rest note below) —
        # tracked in GH #2059.

        # UC-004 status filter: "active" works, other values may not
        # NOTE: the T-UC-004-filter / -empty / -array shadow entries were removed
        # once the generic `{request_params}` step was restricted to key=value
        # form (#1545): the specific status_filter step is no longer shadowed, so
        # values (single, empty-result, array) are all honored and pass.
        _UC004_FILTER_SELECTIVE: list[tuple[str, set[str], str]] = [
            (
                "T-UC-004-filter-default",
                set(),  # all examples
                "default status_filter=active not applied when no explicit IDs",
            ),
        ]
        if any(t.startswith("T-UC-004-filter") for t in marker_names):
            for tag, substrings, reason in _UC004_FILTER_SELECTIVE:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # Graduated: T-UC-004-daterange. When both start_date and end_date are
        # supplied, src/core/tools/media_buy_delivery.py uses them verbatim on
        # all transports (only the single-sided start-only/end-only defaulting
        # paths have a real gap, tracked separately as T-UC-004-daterange-end-only
        # / debt C7 below).

        # Per-row strict=True xfails for partition/boundary scenarios where
        # blanket markers were removed and production gaps are real and named
        # (see docs/test-debt-bdd-strict-markers.md). strict=True forces marker
        # removal the moment the underlying gap closes.
        _UC004_GENUINE_XFAIL_ROWS: list[tuple[str, set[str], str]] = [
            # GRADUATED on rest (2026-07-30, GH #1291 work): geo_missing_geo_level,
            # limit_zero and limit_negative fired as deterministic strict XPASSES on
            # [rest] the first time that leg ran — it had been the deselected
            # "redundant transport" for this scenario, so the tripwire could never fire.
            # Production now satisfies the Example as written on REST: the boundary
            # translates AdCPInvalidRequestError to the INVALID_REQUEST envelope and
            # returns HTTP 400 (captured in the graduating run, slice
            # innet-uc002..uc011 2026-07-30: "REST boundary translating
            # AdCPInvalidRequestError to envelope: INVALID_REQUEST").
            # NOT graduated on a2a/mcp, whose legs ran in the same slice and genuinely
            # xfailed: mcp emits VALIDATION_ERROR and a2a an "Invalid parameters" shape
            # error for identical input, so the C4 gap there is now an error-CODE
            # divergence rather than absent validation (filed separately). Those two
            # keep xfailing through the non-strict _UC004_PARTITION_SELECTIVE entry
            # below, matching the T-UC-004-partition-attribution precedent.
            # geo_metro_missing_system stays STRICT here: it is the C10 gap (the spec
            # states the metro/postal_area system requirement in a field description
            # only, so nothing validates it) and it xfailed on every transport.
            (
                "T-UC-004-partition-reporting-dims",
                {"geo_metro_missing_system"},
                "Pydantic raises ValidationError, not AdCPError(INVALID_REQUEST, suggestion). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # GRADUATED (removed): T-UC-004-partition-attribution interval_zero /
            # interval_negative / invalid_unit / invalid_model — the attribution_window
            # reference now asserts the wire envelope (error "INVALID_REQUEST" with
            # suggestion), which a2a/mcp/rest emit, closing the old reconstructed-path
            # C4 gap. campaign_interval_not_one is xfailed separately below — its window
            # never reaches production due to generic-step shadowing (#1417),
            # not the #1462 in-process drop.
            (
                "T-UC-004-boundary-reporting-dims",
                {"geo with geo_level=metro but no system"},
                "AdCP spec defines metro/postal_area system requirement only in field description; no validator. See docs/test-debt-bdd-strict-markers.md item C10.",
            ),
            # GRADUATED (removed): T-UC-004-boundary-attribution "unit=campaign with
            # interval=2" — BR-RULE-092 INV-5 is now enforced by the _validate_attribution_window
            # check in _get_media_buy_delivery_impl (returns INVALID_REQUEST on all
            # transports), so the description-only C10 gap is closed.
            # GRADUATED (#1534 merge): the boundary-reporting-dims and
            # boundary-attribution mcp/rest invalid-row entries (the C4
            # transport-boundary error-normalization gap: Pydantic rejected but
            # the wire got a bare ToolError / 422 detail instead of the AdCP
            # envelope) are retired. RequestCompatMiddleware (#1534) normalizes
            # MCP TypeAdapter ValidationErrors to the two-layer VALIDATION_ERROR
            # envelope, and the merged REST boundary emits the same envelope for
            # these schema rejections — the strict=True rows fired as designed
            # (deterministic XPASS on the merged in-network run for
            # mcp-geo-without-geo_level / mcp-limit=0 / mcp-limit-negative and
            # mcp-unit=weeks / rest-interval=0 / rest-model=last_click; the
            # remaining siblings are the same rejection class on the same
            # boundary). a2a graduated earlier (#1417). Rows removed so the
            # scenarios grade live on all transports.
            # C11 retired (salesagent-18h.1): the "production ignores buyer
            # start_date" failure was an artefact of the greedy with-params
            # step shadowing when_request_date_range and mis-parsing the
            # request. With correct step routing, production echoes the
            # buyer-supplied start_date/end_date in response.reporting_period,
            # so T-UC-004-daterange now genuinely passes (no strict xfail).
            #
            # date-range partition (salesagent-x18x, #1545): the a2a rows GRADUATED —
            # the Examples now name the wire code (error "VALIDATION_ERROR" with
            # suggestion) and production emits exactly that on the a2a wire ("Start date
            # must be before end date", media_buy_delivery.py:209-218 via
            # AdCPValidationError). Under the transport-aware harness (e2e-harness-wiring)
            # mcp/rest ARE parametrized for this partition and still gap, so they retain a
            # marker below.
            # date-range partition: fully GRADUATED. a2a first (salesagent-x18x,
            # #1545: "Start date must be before end date",
            # media_buy_delivery.py via AdCPValidationError), then mcp/rest
            # (2026-07-25, below). The mcp/rest partition entry the merge
            # temporarily re-added from main's e2e-harness-wiring lineage was
            # STALE — the pre-merge feature run already had all four mcp/rest
            # invalid rows passing, and on the merged in-network run the
            # re-added rows fired as deterministic strict XPASS — so it is
            # removed again (no partition marker remains).
            # Transport-scoped: impl genuinely PASSES start>=end on the _impl
            # path now.
            # GRADUATED (2026-07-25): mcp/rest now also validate
            # start_date>=end_date (confirmed XPASS on both once the single-transport
            # dedup fix stopped hiding them) — entry removed. The stricter standalone
            # T-UC-004-daterange-invalid/-equal scenarios (exact error_code/message/
            # suggestion pin) are unaffected and still genuinely xfail — this boundary
            # outline only asserts the looser "date handling should be invalid".
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
            # VERIFIED 2026-08-24 (xpass-graduation walk of e2e_rest ledger line
            # :55). This entry is CORRECT and stays. Three independent checks:
            #  1. The mechanism in the reason above is real, not inferred. Direct
            #     probe of the request model:
            #       GetMediaBuyDeliveryRequest.model_validate(
            #           {"include_package_daily_breakdown": "true"})
            #     -> ACCEPTED, field == True. Same for "TRUE"/"yes"/"1". The value
            #     is a DECLARED bool|None field, so it never reaches extra="forbid";
            #     Pydantic v2 lax mode coerces it. Production raises nothing at all.
            #  2. The obligation is spec-grounded, not over-specified. The pinned
            #     adcp 3.1 schema (media-buy/get-media-buy-delivery-request.json)
            #     declares include_package_daily_breakdown as {"type": "boolean"};
            #     JSON Schema type:boolean does not admit the string "true". The
            #     scenario is right and production is lax — a real gap.
            #  3. The row is NOT an xpass and never was. Full slice, all three
            #     in-process wire transports:
            #       saci test bdd tests/bdd/test_uc004_deliver_media_buy_metrics.py \
            #         -k daily_breakdown -- -rxX
            #     -> 18 passed, 12 xfailed, 0 XPASSED. a2a/mcp/rest all XFAIL on
            #     this reason. (Local slices persist no test-results/ report, so
            #     there is no run id to cite; the command above reproduces it.)
            # e2e_rest ledger line :55 therefore STAYS. No bdd-in-network run was
            # performed, and none is required: this change removes no routing, and
            # e2e_rest exercises the same app/request model as the in-process rest
            # transport, which XFAILs here for the reason above.
            # CAVEAT on the reason text: the "item C4" pointer is WRONG for this
            # row. C4 is "Pydantic ValidationError not translated to AdCPError";
            # here no ValidationError is ever raised (the value is coerced and
            # accepted), so C4's remedy — a boundary translator wrapping
            # ValidationError — would not move these rows. C4's "one change clears
            # ~32 rows" estimate over-counts by however many of them are coercion,
            # not translation. Fixing this needs strict-bool validation, not C4.
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
                    # valid rows (explicit_account_id / natural_key) now resolve the
                    # account on a2a/mcp/rest — the delivery When seeds the named valid
                    # accounts via _seed_valid_account_if_named / seed_account_with_access
                    # (salesagent-jr5b, #1545), which is exactly the "seed the account in the
                    # delivery Given" follow-up the e2e-harness-wiring branch flagged as the
                    # condition for graduation. That seeding is present in the merged tree,
                    # so the earlier REVERT no longer applies — the valid rows are removed.
                    # account_not_found now correctly raises ACCOUNT_NOT_FOUND on
                    # a2a/mcp/rest once resolution runs (seeded siblings exist, the unseeded
                    # id 404s) — removed. Only invalid_oneOf_both / empty_object still raise
                    # ValidationError-not-AdCPError on the wire, kept (impl path also fails).
                    "a2a-invalid_oneOf_both",
                    "a2a-empty_object",
                    "mcp-invalid_oneOf_both",
                    "mcp-empty_object",
                    "[rest-invalid_oneOf_both",
                    "[rest-empty_object",
                },
                "a2a/mcp/rest do not parse/resolve the invalid oneOf/empty account "
                "reference into an AdCPError(INVALID_REQUEST) at the transport boundary; "
                "these rows raise ValidationError instead. "
                "See docs/test-debt-bdd-strict-markers.md items C1/C2/C4.",
            ),
            (
                "T-UC-004-boundary-account",
                {
                    "impl-account_id present + not found",
                    # Valid rows (account exists / single match = "brand + operator
                    # present", incl. the sandbox:true variant) now resolve on a2a/mcp/rest
                    # once their accounts are seeded (salesagent-jr5b, present in the merged
                    # tree) — removed. a2a invalid rows (both / not found / empty) already
                    # raise AdCPError (wire-drop XPASS, #1417) — removed.
                    # GRADUATED (#1534 merge): mcp-both / mcp-empty-object —
                    # RequestCompatMiddleware normalizes the MCP TypeAdapter oneOf
                    # rejection to the VALIDATION_ERROR envelope; both rows fired
                    # as deterministic strict XPASS on the merged in-network run
                    # — removed.
                    # mcp-account_id present + not found genuinely passes
                    # (ValidationError satisfies 'invalid') — NOT marked.
                },
                "impl does not resolve the account_id-not-found reference into an "
                "AdCPError at the _impl boundary for this row. "
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
                    "[rest-unknown_value-systematic",
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
                    # a2a now rejects the unknown sampling_method value via extra=forbid
                    # -> AdCPError (wire-drop confirmed XPASS, #1417) — removed.
                    "mcp-random (first enum value)",
                    "mcp-failures_only (last enum value)",
                    # GRADUATED (#1534 merge): mcp-Unknown-string — the unknown
                    # sampling_method now rejects on the MCP wire with the AdCP
                    # envelope (extra=forbid rejection normalized by
                    # RequestCompatMiddleware, same class as the a2a graduation
                    # above); deterministic strict XPASS on the box slice —
                    # removed. rest still silently drops the unknown param
                    # (row kept).
                    "[rest-Unknown string not in enum",
                },
                "sampling_method is unimplemented in get_media_buy_delivery (no schema "
                "field); ValidationError not AdCPError (rest silently drops it). "
                "See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # resolution (salesagent-x18x, #1545): GRADUATED on all transports. The
            # Examples now name error "VALIDATION_ERROR" with suggestion, and the empty
            # media_buy_ids=[] hits the SDK min_length=1 constraint, surfacing as
            # AdCPValidationError(VALIDATION_ERROR)+suggestion on the a2a/mcp/rest wire
            # (empirically verified: a2a/mcp/rest all PASS the named code). The earlier
            # INVALID_REQUEST framing (and the "A2A wraps in RuntimeError" note) were both
            # stale — production emits VALIDATION_ERROR here, not INVALID_REQUEST — so no
            # partition marker remains. (e2e-harness-wiring corroborates: strict XPASS
            # observed on the merged tree 2026-07-09, the merged A2A boundary raises
            # AdCPError on the empty-array reject — adcp_validation_boundary from the
            # #1417 embed — matching the boundary-resolution graduation below. Entry removed.)
            # T-UC-004-boundary-resolution: a2a now raises AdCPError on the empty-array
            # reject (wire-drop confirmed XPASS, #1417); the only remaining
            # transport-aware failure (a2a empty array) is handled below — entry removed
            # here so it does not blanket-xfail every boundary-resolution row.
            # ownership (salesagent-lzf3): owner-matches rows pass on all
            # transports. owner-mismatch is the C3 security gap — cross-
            # principal access returns 200+empty instead of MEDIA_BUY_NOT_FOUND.
            (
                "T-UC-004-partition-ownership",
                {"owner_mismatch"},
                "cross-principal access returns 200+empty instead of "
                "AdCPError(MEDIA_BUY_NOT_FOUND). See docs/test-debt-bdd-strict-markers.md item C3.",
            ),
            # boundary-ownership: fully GRADUATED. a2a first (wire-drop XPASS,
            # #1417), then mcp/rest at the #1534 merge — production reports the
            # cross-principal buy as MEDIA_BUY_NOT_FOUND (spec 3.1.1
            # enums/error-code.json; the tenant-scoped repository excludes
            # foreign buys, media_buy_delivery.py not_found_errors) on every
            # wire transport, not the old 200+empty. The mcp row fired as a
            # deterministic strict XPASS on the merged in-network run; entry
            # removed so the boundary grades live. (The stricter
            # PERMISSION_DENIED partition/boundary Examples remain genuinely
            # xfailed via _UC004_PARTITION_SELECTIVE — that expectation gap is
            # separate and still open.)
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
                    # single_pending now normalizes on all wire transports (wire-drop
                    # confirmed XPASS, #1417) — removed. empty_array/unknown_value
                    # still raise ValidationError-not-AdCPError on a2a/mcp/rest, kept.
                    "a2a-empty_array",
                    "mcp-empty_array",
                    "[rest-empty_array",
                    "a2a-unknown_value",
                    "mcp-unknown_value",
                    "[rest-unknown_value",
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
                    # a2a now raises AdCPError on failed/[] (wire-drop confirmed XPASS,
                    # #1417) — removed.
                    # GRADUATED (#1534 merge): mcp-failed — RequestCompatMiddleware
                    # normalizes the MCP TypeAdapter enum rejection to the
                    # VALIDATION_ERROR envelope; the row fired as a deterministic
                    # strict XPASS on the merged in-network run — removed.
                    "mcp-pending_activation (first enum value)",
                    "[rest-pending_activation (first enum value)",
                },
                "pending_activation: Gherkin value not a valid AdCP MediaBuyStatus "
                "(item B1). See docs/test-debt-bdd-strict-markers.md.",
            ),
            # credentials (salesagent-f8u4): FULLY reconciled — the When step
            # now validates the real AdCP reporting_webhook Authentication
            # model (scheme enum + credentials min_length=32). All 40 rows
            # genuinely PASS on all transports; NO strict=True entry needed
            # (same shape as the reconciled date-range valid rows).
        ]
        # e2e_rest items must NOT be marked by this loop: it would stamp a strict=True
        # in-process reason onto e2e_rest items, contradicting the ledger's non-strict
        # policy and, once e2e_rest reaches the real boundary and passes (e.g.
        # INVALID_REQUEST now emitted), turning that pass into a spurious strict-XPASS
        # failure. e2e_rest xfails are owned by the dedicated tripwire blocks and the
        # ledger collapse. (PR #1420)
        #
        # The gate is needed because the entries match by TAG plus a row substring, and
        # an e2e_rest item carries the same scenario tags as its in-process siblings —
        # the selector shape is irrelevant to that. An earlier version of this comment
        # justified the gate by claiming the row substrings are bare prefixes that let
        # a `"rest-…"` selector match an `[e2e_rest-…]` nodeid; that was wrong twice
        # over (measured 2026-07-30): there are ZERO bare `"rest-` selectors in this
        # file — the 67 bare ones are impl (23), a2a (22) and mcp (22), none of which
        # can appear inside `e2e_rest` — and all 100 bracketed selectors are
        # `"[<transport>-` guarded. Do not build a guard on the old mechanism.
        if not is_e2e_rest:
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

        # Graduated: T-UC-004-boundary-credentials — the When now validates the real
        # AdCP reporting_webhook Authentication at the create_media_buy boundary
        # (scheme enum + credentials min_length=32), so all rows pass on all transports.

        # Graduated: T-UC-004-boundary-ownership — impl-"differs", a2a-"differs" and
        # rest-"matches" pass. Remaining failures: impl-matches, mcp-differs, rest-differs.
        if "T-UC-004-boundary-ownership" in marker_names:
            _ownership_passes = (
                (not is_a2a and not is_mcp)
                and (
                    (not is_rest and not is_e2e_rest and "differs from owner" in nodeid)
                    or (is_rest and "matches owner" in nodeid)
                    or (is_e2e_rest and "matches owner" in nodeid)
                )
            ) or (
                # a2a now raises AdCPError(MEDIA_BUY_NOT_FOUND) on cross-principal access
                # (wire-drop confirmed XPASS, #1417).
                is_a2a and "differs from owner" in nodeid
            )
            if not _ownership_passes:
                # mcp's xpass here is VACUOUS, not a production graduation.
                # when_boundary_ownership (uc004_delivery.py) sends the Gherkin
                # label text as a literal `ownership=` kwarg, which is not a real
                # request field -- FastMCP's TypeAdapter rejects it as an
                # unrecognized argument before _get_media_buy_delivery_impl ever
                # runs, coincidentally matching `invalid`. It does not test whether
                # production enforces cross-principal ownership. See
                # docs/test-debt-bdd-strict-markers.md item B3 (RECONCILED for the
                # partition variant only, via _dispatch_ownership_partition; the
                # boundary variant used here still has the bug). Do NOT graduate
                # until when_boundary_ownership is fixed to route through a real
                # identity swap.
                #
                # VERIFIED AND EXTENDED (salesagent-n78j0.13, e2e_rest ledger walk).
                # The claim above is accurate and still holds: when_boundary_ownership
                # (uc004_delivery.py:1293) calls _dispatch_partition(ctx, "ownership", ...),
                # which falls through to dispatch_request(ctx, ownership=<label text>).
                # `ownership` is not a field of GetMediaBuyDeliveryRequest (14 fields,
                # extra="forbid"), so the request dies at the model boundary. The correct
                # helper already exists 2200 lines away — _dispatch_ownership_partition
                # (:3566) "queries the same buy id as a foreign principal" — and the
                # sibling When one function above (:1289) already calls it.
                #
                # WHAT IS NEW: the vacuity is not mcp-only. Mutation M1 deleted
                # `MediaBuy.principal_id == principal_id` from
                # MediaBuyRepository.get_by_principal (repositories/media_buy.py:140) —
                # i.e. removed cross-principal ownership enforcement outright — and NOT ONE
                # of the 12 ownership rows moved: boundary[a2a-differs] PASSED,
                # boundary[mcp-differs] XPASS, boundary[rest-differs] XFAIL,
                # partition[*-owner_mismatch] XFAIL, before and after, identically. The
                # whole UC-004 module (516 passed) and `make quality` (6751 passed) were
                # green with the control deleted.
                #
                # THE OBLIGATION IS NOT UNGRADED, THOUGH — and that distinction matters
                # before anyone reads the above as a security hole. M1 turns
                # tests/integration/test_cross_principal_security.py::TestCrossPrincipalSecurity
                # ::test_get_media_buy_delivery_cannot_see_other_principals_data RED. Real
                # coverage exists, in an integration test that performs the identity swap
                # these BDD rows only describe. What the ledger row would "graduate" is
                # therefore redundant theatre on top of a working test, not the coverage
                # itself — which is exactly why un-routing it would be a false green.
                #
                # Scenario quality, upstream of all of this (protocol step 2): the Examples
                # say only `invalid`, so _assert_partition_or_boundary takes the
                # _assert_wire_rejection path (uc004_delivery.py:3137), which pins NO code —
                # it only excludes server faults and auth codes. Any client rejection
                # satisfies it, including the VALIDATION_ERROR the bogus kwarg produces.
                # The sibling sampling scenario 8 lines below in the feature file shows the
                # corrected form: `error "INVALID_REQUEST" with suggestion`.
                item.add_marker(
                    pytest.mark.xfail(reason="ownership boundary: validation gaps on some transports", strict=False)
                )

        # Graduated: T-UC-004-boundary-reporting-dims — "metro but no system" is the
        # only row still genuinely gapped (prose-only spec constraint, no formal
        # validator; separately tracked as C10 in _UC004_GENUINE_XFAIL_ROWS above).
        # "geo without geo_level", "limit=0", "limit negative" also now genuinely
        # reject on mcp/rest (a2a already passed, #1417) — required geo_level /
        # limit>=1 per the pinned v3.1.1 get-media-buy-delivery-request.json, and
        # RequestCompatMiddleware normalizes the ToolError to a two-layer envelope
        # on mcp/rest.
        if "T-UC-004-boundary-reporting-dims" in marker_names:
            _rdim_all_transport_fail = "geo_level=metro but no system" in nodeid
            if _rdim_all_transport_fail:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="reporting_dimensions boundary: validation gaps on some transports", strict=False
                    )
                )
            # Graduated: e2e_rest invalid reporting_dimensions schema violations now
            # return 400 INVALID_REQUEST (the RequestValidationError handler in
            # src/app.py; not a raw 500/empty body), so the wire-envelope assertion
            # handles them.

        # Graduated: T-UC-004-boundary-sampling — "Not provided" passes everywhere;
        # "random"/"failures_only" pass on rest only; "Unknown string" passes on impl only.
        if "T-UC-004-boundary-sampling" in marker_names:
            _samp_not_rest_fail = (
                not is_rest
                and not is_e2e_rest
                and any(s in nodeid for s in ("random (first enum", "failures_only (last enum"))
            )
            # a2a now rejects the unknown value via extra=forbid -> AdCPError (wire-drop
            # confirmed XPASS, #1417); mcp still fails the type check.
            _samp_not_impl_fail = (
                not is_impl and not is_a2a and not is_e2e_rest and "Unknown string not in enum" in nodeid
            )
            if _samp_not_rest_fail or _samp_not_impl_fail:
                # mcp's xpass here is VACUOUS. `sampling_method` is not a real
                # get_media_buy_delivery request field (does not exist in the
                # pinned v3.1.1 schema at all -- it belongs to content-standards
                # native-creative sampling, a different domain).
                # when_boundary_sampling sends it as a raw kwarg, which FastMCP's
                # TypeAdapter rejects as unrecognized before
                # _get_media_buy_delivery_impl runs -- coincidentally matching
                # `invalid` for ANY value, valid or not, so this scenario cannot
                # distinguish "enum rejected" from "field doesn't exist". See
                # docs/test-debt-bdd-strict-markers.md item B4 -- the documented fix
                # is to relocate/delete this scenario family, not graduate rows.
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

        # Graduated: T-UC-004-boundary-date-range. a2a/mcp/rest all accept a valid
        # start_date<end_date pair and omitted dates without error — the shared
        # _get_media_buy_delivery_impl (src/core/tools/media_buy_delivery.py) has
        # no transport-specific date-range branch. Production also validates date
        # range over e2e_rest, rejecting the invalid cases (equals, after).

        # T-UC-004-daterange-end-only over e2e_rest: same Gap G40 (debt C7) as
        # in-process — when only end_date is given, production defaults start to
        # today-30d, not the media buy creation date the Then-step asserts. The
        # _UC004_GENUINE_XFAIL_ROWS loop is gated to in-process only (see :1422),
        # so e2e_rest needs its own strict tripwire. Deterministic: the live
        # server reliably returns today-30d. Retire when Gap G40 is closed.
        if is_e2e_rest and "T-UC-004-daterange-end-only" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="e2e_rest: Gap G40 — start defaults to today-30d, not media buy creation date",
                    strict=True,
                )
            )

        # attribution_window REFERENCE (clean scenario->step->harness path): the Examples
        # name the exact error code (error "VALIDATION_ERROR" — the schema-canonical code
        # for value/enum/range/business-rule violations; reconciled from the earlier
        # INVALID_REQUEST mis-pin per the AdCP graded error-compliance storyboard), the
        # step asserts it on the harness wire envelope. interval=0 / unit=weeks /
        # model=last_click PASS on a2a/mcp/rest (VALIDATION_ERROR).
        # GRADUATED (salesagent-x18x, #1545): the partition "campaign with interval=2"
        # (campaign_interval_not_one) now passes on a2a — the only transport parametrized
        # for that row — because the attribution_window.post_click reaches production and
        # INV-5 fires (VALIDATION_ERROR "interval must be 1 when unit is 'campaign'"), which
        # the Examples now name and the step asserts on the wire. So the former strict=True
        # _aw_partition_campaign leg is dropped; the row passes unmasked. (The old #1462
        # "request path drops post_click" framing was wrong for the wire transports; #1462 is
        # the in-process _impl path, which BDD does not parametrize.)
        # The partition shape's error "INVALID_REQUEST" rows STILL fail on e2e_rest: the
        # generic "with {request_params}" step shadows the specific "with attribution_window
        # {value}" step and _parse_request_params drops the space-form window, so the window
        # never reaches the live server (#1417). Marker kept for e2e_rest until the step-
        # binding bug is fixed.
        _aw_partition_error = "T-UC-004-partition-attribution" in marker_names and 'error "INVALID_REQUEST"' in nodeid
        # #1545/x18x: the campaign partition row GRADUATED on a2a (the only transport
        # parametrized for it) — INV-5 fires VALIDATION_ERROR with suggestion — so the
        # former strict=True _aw_partition_campaign leg is dropped (no _aw_partition_campaign
        # var remains). Only the error "INVALID_REQUEST" rows still fail on e2e_rest, where
        # the generic "with {request_params}" step still shadows the specific partition step.
        _partition_window_dropped = _aw_partition_error and is_e2e_rest
        if _partition_window_dropped:
            item.add_marker(
                pytest.mark.xfail(
                    reason="attribution_window partition: the generic 'with {request_params}' step "
                    "shadows the specific partition step and drops the window (salesagent-50hl); "
                    "validation never fires so the rejection assertion can't pass",
                    strict=True,
                )
            )

        # Graduated: T-UC-004-boundary-account — transport-aware.
        # "account_id present"/"brand + operator" (valid): fail on mcp/rest only.
        # "both account_id"/"empty object" (invalid): fail on a2a only.
        # "account_id not found" (invalid): fail on impl/a2a only.
        # "omitted": already PASS everywhere.
        if "T-UC-004-boundary-account" in marker_names:
            # a2a now raises AdCPError on invalid-account rows (both / empty / not found)
            # (wire-drop confirmed XPASS, #1417). Valid rows (account exists / single
            # match / sandbox account exists) now pass on mcp/rest once their accounts
            # are seeded (salesagent-jr5b) — the former "production gaps" mask hid the
            # missing seed. impl still gaps on not-found (impl is not in the default
            # BDD parametrization).
            # mcp's "both account_id"/"empty object" invalid rows also now reject
            # correctly — FastMCP's TypeAdapter validates the account param against
            # the adcp library's AccountReference oneOf (RootModel,
            # additionalProperties:false per branch) BEFORE the tool body runs,
            # normalized to VALIDATION_ERROR via the shared normalize_to_adcp_error().
            _acc_notfound_fail = is_impl and "not found" in nodeid
            if _acc_notfound_fail:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="delivery account boundary: production gaps on this transport", strict=False
                    )
                )
            # e2e_rest fully graduated: invalid rows ("not found", "both
            # account_id", "empty object") passed first; the valid rows
            # ("account exists", "single match") followed at the #1417 merge —
            # the jr5b seeded-account Given is realized against the server DB,
            # so the account fixture IS visible now (XPASS innet_140726_1516).

        # --- UC-004 boundary: selective xfail for graduated strong groups ---
        # Only the failing subset gets xfailed; clean-pass examples graduate to PASS.
        _UC004_BOUNDARY_SELECTIVE: list[tuple[str, set[str], str]] = [
            # include_package_daily_breakdown: only non_boolean fails (all transports)
            #
            # SHADOW — DO NOT REMOVE THE strict=True ENTRY WITHOUT REMOVING THIS ONE.
            # This entry duplicates the routing of the SAME tag + the SAME row that
            # the strict=True Phase-2 entry above (search: "lax-coerces non-boolean")
            # already covers, but with strict=False. Measured 2026-08-24:
            #   * With both present, the strict=True entry governs — the reported
            #     reason is the Phase-2 one, so the ratchet works TODAY.
            #   * Mutation M1 (this entry left in place, the strict=True entry
            #     temporarily deleted) -> the row still XFAILs, now reporting THIS
            #     reason. So this entry is live and reachable, not dead code.
            # Consequence: the moment production grows strict-bool validation, the
            # strict=True entry fires (XPASS -> failure) and forces its own removal —
            # which is the intended ratchet. But removing it hands the row straight
            # to this strict=False entry, under which the now-passing row reports a
            # silent XPASS forever instead of graduating. That is a mechanism for
            # MANUFACTURING xpass residue out of a completed fix, and the route pin
            # (EXPECTED_XFAIL_ROUTES in tests/unit/test_architecture_e2e_rest_escape_
            # hatches.py) cannot catch it: it records conditions only, never `strict`,
            # so a strict=False shadow behind a strict=True route is invisible to it.
            # 62 tags in this file are routed more than once; only this one has been
            # checked. Deliberately NOT deleted here — it is behaviour-neutral today
            # and removing a pinned route is its own change, not part of walking :55.
            (
                "T-UC-004-boundary-daily-breakdown",
                {"non-boolean", "non_boolean", "string 'true'"},
                "include_package_daily_breakdown boundary: non-boolean validation not implemented",
            ),
            # Graduated: "buyer_refs only" and "zero resolution" (all 4 transports pass)
            # Graduated: "empty array" passes on impl/mcp/rest (only a2a fails)
            # Graduated: "partial resolution" -- the transport-agnostic _impl
            # (src/core/tools/media_buy_delivery.py) diffs requested media_buy_ids
            # vs. resolved buys and appends an advisory MEDIA_BUY_NOT_FOUND to
            # response.errors[] instead of hard-failing, which is exactly the shape
            # get-media-buy-delivery-response.json#/properties/errors documents
            # (v3.1.1), on all 3 transports.
            # Clean-pass: media_buy_ids only, both provided, neither provided
            # Graduated: status_filter "not in AdCP enum" passes on impl+rest,
            # "empty array, violates" passes on impl+mcp+rest (transport-aware below)
        ]
        for tag, substrings, reason in _UC004_BOUNDARY_SELECTIVE:
            if tag in marker_names:
                if any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # T-UC-004-boundary-resolution "empty array": a2a now raises AdCPError
        # (wire-drop confirmed XPASS, #1417) — no transport still fails here.
        # T-UC-004-boundary-status-filter: graduated per-transport
        # "not in AdCP enum" (failed): all transports now pass.
        # "empty array, violates" ([]): a2a now passes — no transport still fails
        if "T-UC-004-boundary-status-filter" in marker_names:
            # mcp's "not in AdCP enum" (status_filter="failed") row also now
            # rejects correctly — FastMCP's TypeAdapter validates status_filter
            # against the adcp library's MediaBuyStatus enum before the tool body
            # runs, same mechanism/normalize_to_adcp_error() path as the account
            # boundary graduation above.
            # Graduated: e2e_rest invalid status_filter (unknown enum value) now
            # returns 400 INVALID_REQUEST (the RequestValidationError handler in
            # src/app.py; not a raw 500/empty body), so the wire-envelope assertion
            # handles it.
            # adcp 3.12: pending_activation renamed to pending_start — feature file
            # still uses old name, schema rejects it as unknown enum value.
            if "pending_activation" in nodeid or "all 6 statuses" in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="adcp 3.12: pending_activation renamed to pending_start — feature file needs update",
                        strict=True,
                    )
                )

        # Graduated: "both provided (priority rule)". #1417 already retired
        # buyer_refs and rewrote _dispatch_resolution
        # (tests/bdd/steps/domain/uc004_delivery.py) to send media_buy_ids +
        # status_filter instead, so the row tests a real, spec-permitted
        # combination, not obsolete content.

        # Graduated: e2e_rest media_buy_resolution "empty array" now returns a
        # structured AdCP error envelope (not a raw 500/empty body), so the
        # wire-envelope assertion handles it.

        # e2e_rest: principal_ownership "differs from owner" — ownership check not enforced
        # through REST layer; test succeeds when it should fail (strict=True xfail).
        if "T-UC-004-boundary-ownership" in marker_names and is_e2e_rest and "differs from owner" in nodeid:
            item.add_marker(
                pytest.mark.xfail(
                    reason="e2e_rest: ownership boundary not enforced through REST — test succeeds unexpectedly",
                    strict=True,
                )
            )

        # STAYS — inspected salesagent-n78j0.13, and the previous reason here was WRONG
        # in every particular. It claimed the spend-fallback needs injected by_placement
        # data that is "in-process mock state invisible to the live server", with
        # salesagent-04im as the follow-up. In fact:
        #   • _inject_placement_data is DEAD CODE — zero callers anywhere in tests/
        #     (its only other mention was that comment). It never runs on ANY transport,
        #     so it cannot be the reason e2e_rest differs.
        #   • salesagent-04im does not exist (`bd show` finds no such issue).
        #   • by_placement is NOT injected at all — production SYNTHESIZES it server-side
        #     (media_buy_delivery.py:1040-1058) whenever the adapter reports no
        #     per-placement data, so e2e_rest receives the same rows as in-process.
        #
        # The real defect is that the scenario cannot grade its own obligation, on any
        # transport. _build_placement_breakdown derives every metric from ONE weight
        # vector (0.5, 0.3, 0.2): impressions=imp*w, spend=spd*w, clicks=imp*w*0.01.
        # All metrics are therefore rank-identical by construction AND already emitted
        # in descending order, so `values == sorted(values, reverse=True)` holds for
        # every sort key regardless of what production does. Probe (in-process, all 3
        # transports): n_placements=3, spend=[125.0, 75.0, 50.0], clicks=[25.0, 15.0,
        # 10.0] — plc_a > plc_b > plc_c on every metric.
        #
        # Mutation-proved (salesagent-n78j0.13, local slice, 6 tests = 3 transports x
        # {fallback, counter-example}):
        #   M1 delete the spend-fallback branch entirely  -> 6 passed (GREEN)
        #   M2 fall back to "clicks" instead of "spend"   -> 6 passed (GREEN)
        # Deleting the exact behaviour the scenario exists to grade does not turn it
        # red. The obligation is real and correctly stated — AdCP 3.1.1
        # media-buy/task-reference/get_media_buy_delivery.mdx:869 "falls back to `spend`
        # if the seller does not report the requested metric" — it is simply ungraded.
        #
        # NOTE the discriminating fixture already exists and is the dead one:
        # _DEFAULT_PLACEMENT_DATA (spend 150/200/50, clicks 30/10/50) is NOT
        # rank-correlated and WOULD separate the orderings. Fixing this row means
        # wiring that data in (and asserting the ORDER of placement_ids, not just
        # descending-ness), then re-checking whether it still passes. GH #2059.
        # Do not graduate this route until a replacement assertion is mutation-proved.
        if "T-UC-004-dim-sortby-fallback" in marker_names and is_e2e_rest:
            item.add_marker(
                pytest.mark.xfail(
                    reason="e2e_rest: by_placement injection is in-process-only (invisible to live server) — "
                    "sort_by spend-fallback untestable over e2e_rest",
                    strict=False,
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
            # reporting_dimensions: validation IS implemented now, and the reason above
            # ("production accepts invalid configs") was stale — every transport rejects
            # these inputs. What differs is the CODE: rest emits INVALID_REQUEST (which
            # the Example names, so those rows XPASS there and graduated out of the
            # strict table above), mcp emits VALIDATION_ERROR and a2a an "Invalid
            # parameters" shape error. Non-strict, so the rest XPASS stays visible
            # without failing CI — same shape as T-UC-004-partition-attribution below.
            # geo_metro_missing_system is the separate C10 description-only gap.
            (
                "T-UC-004-partition-reporting-dims",
                {"geo_missing_geo_level", "geo_metro_missing_system", "limit_zero", "limit_negative"},
                "reporting_dimensions rejection code diverges by transport — rest emits INVALID_REQUEST "
                "(the named Example), mcp VALIDATION_ERROR, a2a an invalid-parameters shape error",
            ),
            # Graduated: T-UC-004-partition-attribution
            # interval_zero/interval_negative/invalid_unit/invalid_model. The
            # generic "with {request_params}" step no longer shadows the specific
            # "with attribution_window {value}" step (the generic step now
            # requires \w+=... key=value form, mutually exclusive with the
            # space-form "attribution_window {json}" step). attribution_window is
            # a real-wire-asserted field (_WIRE_ASSERTED_FIELDS), and all 4 rows
            # pass with the correct VALIDATION_ERROR+suggestion on all 3
            # transports.
            # daily breakdown: production doesn't validate non-boolean values
            (
                "T-UC-004-partition-daily-breakdown",
                {"non_boolean"},
                "include_package_daily_breakdown validation not implemented — production accepts non-boolean",
            ),
            # account: production doesn't validate the oneOf constraint / empty object
            # on the wire (raises ValidationError, not AdCPError(INVALID_REQUEST)).
            # account_not_found is NOT here: with the valid siblings seeded
            # (salesagent-jr5b), resolution runs and the unseeded id correctly
            # raises ACCOUNT_NOT_FOUND on every transport.
            (
                "T-UC-004-partition-account",
                {"invalid_oneOf_both", "empty_object"},
                "delivery account oneOf/empty-object validation not implemented — "
                "production raises ValidationError not AdCPError(INVALID_REQUEST)",
            ),
            # Graduated: T-UC-004-partition-sampling (transport-aware block below)
            # "not_provided" passes all transports; valid named methods pass on REST only.
            # status_filter: production doesn't validate unknown values or empty arrays
            (
                "T-UC-004-partition-status-filter",
                {"unknown_value", "empty_array"},
                "status_filter validation not implemented — production accepts invalid values",
            ),
            # date range partition GRADUATED (salesagent-x18x, #1545): only [a2a-…] is
            # parametrized for start_equals_end/start_after_end, and a2a now emits
            # VALIDATION_ERROR+suggestion ("Start date must be before end date",
            # media_buy_delivery.py:209-218) for the named Examples — passes unmasked. Entry
            # removed. (mcp/rest are only parametrized on the BOUNDARY counterpart, which
            # stays masked in _UC004_GENUINE_XFAIL_ROWS above.)
            # resolution partition GRADUATED (salesagent-x18x, #1545): empty media_buy_ids=[]
            # hits the SDK min_length=1 constraint -> VALIDATION_ERROR+suggestion on the
            # a2a/mcp/rest wire (all three empirically PASS the named Example). Entry removed.
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

        # (#1545 review) The generic `{request_params}` step was restricted to
        # key=value form, which un-shadowed the date-range / ownership / resolution
        # partition steps so their params are now genuinely applied. The latent
        # step-plumbing bugs that exposed (labels leaking as bogus request kwargs;
        # a partial-resolution assertion demanding the deliberately-absent id) are
        # fixed in uc004_delivery.py's _dispatch_date_range_partition /
        # _dispatch_ownership_partition / _dispatch_resolution, so these rows are
        # graded rather than deferred. The genuinely-unimplemented rows
        # (start>=end, owner_mismatch, empty_array) remain in _UC004_PARTITION_SELECTIVE.

        # Graduated: T-UC-004-partition-credentials — the When now validates the real
        # AdCP reporting_webhook Authentication at the create_media_buy boundary
        # (scheme enum + credentials min_length=32), so all rows pass on all transports.

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
            # Status filter invalid — all parametrizations still fail.
            # NOTE(ah98 red-step inspection, 2026-07-06): NOT graduatable —
            # with this entry removed the scenario still xfails at the fixture
            # ("No harness wired for None": not env-wired), and its examples
            # assert non-canonical codes (STATUS_FILTER_INVALID_VALUE /
            # STATUS_FILTER_EMPTY — absent from the pinned error-code enum),
            # which the shared-boundary fix will not emit. Reconcile upstream.
            # Suggestion parity for get_media_buys is pinned by
            # tests/integration/test_request_validation_suggestion_parity.py.
            "T-UC-019-partition-status-filter-invalid",
            # Creative approval mapping — not implemented
            "T-UC-019-partition-approval",
            "T-UC-019-partition-approval-invalid",
            "T-UC-019-boundary-approval",
            # Graduated (#1545 review), now wired + passing on the A2A/MCP wire:
            #   inv-150-1 (pre-flight active -> pending_start)
            #   inv-150-3 (post-flight active -> completed)
            # Graduated: T-UC-019-inv-150-5 (status filter no longer blocks by-ID queries)
            "T-UC-019-inv-151-4",
            # inv-153-3/4/5 moved to _UC019_SNAPSHOT_HARNESS_GAP_TAGS (#1721 M4):
            # they were mislabeled here as production gaps but actually fail on the
            # Given (no adapter mock in this harness), never reaching graded behavior.
            # Sandbox mode (response echo) — not implemented
            "T-UC-019-sandbox-happy",
            # Graduated (6szx): T-UC-019-sandbox-validation — BR-RULE-209 INV-7:
            # invalid status_filter on a sandbox account yields a REAL rejection
            # (_resolve_status_filter → AdCPValidationError → VALIDATION_ERROR wire
            # envelope). Given now seeds a real sandbox Account + AgentAccountAccess
            # (was an inert ctx flag); Then steps assert wire-first.
            # Graduated: T-UC-019-partition-principal-invalid identity_missing (impl/a2a/mcp pass)
            # — moved to _UC019_PARAM_XFAIL for selective identity_missing exclusion.
            # Graduated (salesagent-mkso): T-UC-019-ext-a (no-auth get_media_buys)
            # now correctly emits AUTH_MISSING per the v3.1.1 AUTH_MISSING/
            # AUTH_INVALID split — was previously stale on AUTH_TOKEN_INVALID/
            # AUTH_REQUIRED.
            # Extension errors — error code mismatches / not implemented.
            "T-UC-019-ext-b",
            "T-UC-019-ext-c",
            # Graduated (6szx): T-UC-019-ext-d — invalid parameter types are rejected
            # inside the shared adcp_validation_boundary (_build_get_media_buys_request)
            # with VALIDATION_ERROR, field-level details (field="media_buy_ids"),
            # recovery=correctable and a top-level suggestion, on the A2A wire and via
            # the typed exception on the legacy MCP wrapper. Then steps assert wire-first.
            "T-UC-019-ext-e",
            # Transport-agnostic main scenario
            "T-UC-019-main",
        }
        # Snapshot scenarios (main-snapshot, inv-153-3/4/5): given_adapter_supports_reporting /
        # given_adapter_no_reporting assert "adapter" in env.mock, but MediaBuyListEnv
        # (the UC-019 harness) deliberately runs get_media_buys against a real DB with
        # NO adapter mock at all ("list is a pure read" — see the UC-019 harness comment).
        # This is a TEST-HARNESS gap (the snapshot Given can never succeed), not a
        # production behavior gap -- was mislabeled "spec-production gap" (#1721 M4
        # dormancy tripwire caught it: the scenarios fail on the Given, before ever
        # reaching the production code the reason claimed was ungraded).
        _UC019_SNAPSHOT_HARNESS_GAP_TAGS: set[str] = {
            "T-UC-019-main-snapshot",
            "T-UC-019-inv-153-3",
            "T-UC-019-inv-153-4",
            "T-UC-019-inv-153-5",
        }
        if marker_names & _UC019_SNAPSHOT_HARNESS_GAP_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-019 test-harness gap: MediaBuyListEnv wires no adapter mock "
                    "(get_media_buys list is a pure DB read), so the snapshot Given steps "
                    "(given_adapter_supports_reporting / given_adapter_no_reporting) cannot "
                    "configure anything and fail before reaching the graded behavior — FIXME(salesagent-cyzy)",
                    strict=False,
                )
            )
        elif marker_names & _UC019_XFAIL_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-019 spec-production gap — feature not yet implemented",
                    strict=False,
                )
            )

        # --- UC-019: selective boundary xfails for un-implemented sub-features ---
        # These scenario outlines are mostly graduated; only the rows exercising a
        # not-yet-implemented sub-feature are xfailed. All are pre-existing gaps
        # unrelated to this PR's status-taxonomy work.
        _UC019_BOUNDARY_SELECTIVE: list[tuple[str, set[str], str]] = [
            # Invalid status_filter VALUES need a dedicated STATUS_FILTER_INVALID_VALUE
            # code; production raises the generic VALIDATION_ERROR instead.
            (
                "T-UC-019-boundary-status-filter",
                {"pending_activation", "expired"},
                "status_filter value validation emits VALIDATION_ERROR, not STATUS_FILTER_INVALID_VALUE (unimplemented)",
            ),
            # Sandbox echo (sandbox=true/false in the response) is not implemented;
            # only the production-absent row is graded.
            (
                "T-UC-019-boundary-sandbox",
                {"sandbox account", "explicit production"},
                "sandbox response echo not implemented (BR-RULE-209)",
            ),
        ]
        for tag, substrings, reason in _UC019_BOUNDARY_SELECTIVE:
            if tag in marker_names and any(s in nodeid for s in substrings):
                item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

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
            # Graduated (#1545 review): T-UC-019-partition-status-filter
            # multiple_statuses / all_statuses — multi-status filtering works on the
            # wire once status_filter is coerced to the MediaBuyStatus enum and the
            # scenario pins its clock. The remaining status-filter gaps are the
            # value/empty VALIDATION rows below, not the mapping.
            # Status filter boundary: STATUS_FILTER_EMPTY (empty array) is not a
            # dedicated code yet (the value-validation rows are handled by
            # _UC019_BOUNDARY_SELECTIVE above). "all seven" now grades and passes.
            (
                "T-UC-019-boundary-status-filter",
                {"empty array"},
                "STATUS_FILTER_EMPTY not implemented — empty array returns empty success, not an error",
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
                {"[rest-update_mutable_only", "[rest-no_immutable_fields_present"},
                "REST update dispatch not wired for partition immutable success tests",
            ),
            (
                "T-UC-026-boundary-immutable",
                {"[rest-update with only mutable"},
                "REST update dispatch not wired for boundary immutable success tests",
            ),
            # Keyword add partition: only REST success-path tests fail
            (
                "T-UC-026-partition-keyword-add",
                {
                    "[rest-new_keyword",
                    "[rest-existing_keyword_update_bid",
                    "[rest-mixed_new_and_update",
                    "[rest-same_keyword_different_match",
                },
                "REST update dispatch not wired for partition keyword-add success tests",
            ),
            # Keyword remove partition: only REST success-path tests fail
            (
                "T-UC-026-partition-keyword-remove",
                {
                    "[rest-remove_existing_pair",
                    "[rest-remove_nonexistent_pair",
                    "[rest-remove_all_keywords",
                    "[rest-mixed_existing_and_nonexistent",
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
                    "[rest-single new keyword target",
                    "[rest-existing (keyword, match_type) pair",
                    "[rest-same keyword with broad and exact",
                    "[rest-bid_price = 0",
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
                    "[rest-remove single existing",
                    "[rest-remove non-existent pair",
                    "[rest-remove all keyword targets",
                    "[rest-mix of existing and non-existent",
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
                    "[rest-typical_add",
                    "[rest-add_with_bid_price",
                    "[rest-add_without_bid_price",
                    "[rest-all_match_types",
                    "[rest-boundary_min_array",
                    "[rest-boundary_min_keyword",
                    "[rest-cross_dimension_valid",
                    "[rest-upsert_existing",
                    "[rest-zero_bid_price",
                },
                "conflict_with_overlay not implemented / REST update not wired",
            ),
            (
                "T-UC-026-partition-kw-remove-shared",
                {
                    "impl-conflict_with_overlay",
                    "a2a-conflict_with_overlay",
                    "mcp-conflict_with_overlay",
                    "[rest-typical_remove",
                    "[rest-all_match_types",
                    "[rest-boundary_min_array",
                    "[rest-boundary_min_keyword",
                    "[rest-cross_dimension_valid",
                    "[rest-remove_nonexistent",
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
                    "[rest-array length 1",
                    "[rest-keyword length 1",
                    "[rest-keyword_targets_add WITH targeting_overlay.negative_keywords",
                    "[rest-keyword_targets_add WITHOUT",
                    "[rest-match_type = 'broad'",
                    "[rest-match_type = 'exact'",
                    "[rest-match_type = 'phrase'",
                },
                "overlay conflict validation not implemented / REST update not wired",
            ),
            (
                "T-UC-026-boundary-kw-remove-shared",
                {
                    "impl-keyword_targets_remove WITH targeting_overlay.keyword_targets-error",
                    "a2a-keyword_targets_remove WITH targeting_overlay.keyword_targets-error",
                    "mcp-keyword_targets_remove WITH targeting_overlay.keyword_targets-error",
                    "[rest-array length 1",
                    "[rest-keyword length 1",
                    "[rest-keyword_targets_remove WITHOUT",
                    "[rest-match_type = 'broad'",
                    "[rest-match_type = 'exact'",
                    "[rest-match_type = 'phrase'",
                    "[rest-remove pair that does NOT exist",
                    "[rest-remove pair that exists",
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
                    "[rest-typical_add",
                    "[rest-add_duplicate",
                    "[rest-all_match_types",
                    "[rest-boundary_min_array",
                    "[rest-boundary_min_keyword",
                    "[rest-cross_dimension_valid",
                },
                "conflict_with_overlay not implemented / REST update not wired",
            ),
            (
                "T-UC-026-partition-neg-kw-remove",
                {
                    "impl-conflict_with_overlay",
                    "a2a-conflict_with_overlay",
                    "mcp-conflict_with_overlay",
                    "[rest-typical_remove",
                    "[rest-all_match_types",
                    "[rest-boundary_min_array",
                    "[rest-boundary_min_keyword",
                    "[rest-cross_dimension_valid",
                    "[rest-remove_nonexistent",
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
                    "[rest-negative_keywords_add WITHOUT",
                    "[rest-negative_keywords_add WITH targeting_overlay.keyword_targets",
                    "[rest-add pair that already exists",
                    "[rest-array length 1",
                    "[rest-keyword length 1",
                    "[rest-match_type = 'broad'",
                    "[rest-match_type = 'exact'",
                    "[rest-match_type = 'phrase'",
                },
                "overlay conflict validation not implemented / REST update not wired",
            ),
            (
                "T-UC-026-boundary-neg-kw-remove",
                {
                    "impl-negative_keywords_remove WITH targeting_overlay.negative_keywords-error",
                    "a2a-negative_keywords_remove WITH targeting_overlay.negative_keywords-error",
                    "mcp-negative_keywords_remove WITH targeting_overlay.negative_keywords-error",
                    "[rest-negative_keywords_remove WITHOUT",
                    "[rest-array length 1",
                    "[rest-keyword length 1",
                    "[rest-match_type = 'broad'",
                    "[rest-match_type = 'exact'",
                    "[rest-match_type = 'phrase'",
                    "[rest-remove pair that does NOT exist",
                    "[rest-remove pair that exists",
                },
                "overlay conflict validation not implemented / REST update not wired",
            ),
            # Paused: only REST update-path tests fail (create-path passes)
            (
                "T-UC-026-partition-paused",
                {"[rest-pause_on_update", "[rest-resume_on_update"},
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
                    "[rest-paused=false on update",
                    "[rest-paused=true on update",
                    "[rest-paused=true on already-paused",
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
                    "[rest-omit_array_fields",
                    "[rest-replace_catalogs",
                    "[rest-replace_targeting_overlay",
                },
                "creative_assignments/optimization_goals replacement not implemented / REST update not wired",
            ),
            (
                "T-UC-026-boundary-replacement",
                {
                    "creative_assignments",
                    "optimization_goals",
                    "[rest-all array fields omitted",
                    "[rest-catalogs provided",
                    "[rest-only scalar fields updated",
                    "[rest-targeting_overlay replacement",
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
        # Graduated: expired-token also passes — AUTH_REQUIRED matches.

        # T-UC-011-ext-g-echo-error: impl passes (AdCPError carries context=req.context);
        # a2a/mcp/rest xfail+note via the context-echo Then step (pytest.xfail) because the
        # wire error envelope does not echo context — #1417 / D2. No marker here.
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

        # ── E2E_REST ledger + non-strict policy ──────────────────────
        # The e2e_rest transport dispatches over real HTTP to a separate server,
        # so scenarios relying on in-process mock injection can't pass. xfail the
        # known ones (ledger) as non-strict — e2e is environment-dependent, so a
        # ledger xpass must not fail CI. Authored strict=True markers (the #1270
        # validation tripwires at ~1475/~1502) are PRESERVED by the collapse
        # below, so a real production fix still surfaces as a strict xpass.
        if is_e2e_rest:
            if nodeid in _E2E_REST_KNOWN_FAILURES:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: mock-incompatible scenario (tests/bdd/e2e_rest_known_failures.txt)",
                        strict=False,
                    )
                )
            # Collapse the e2e_rest xfail markers into ONE, but PRESERVE authored
            # strictness: if any source marker is strict=True (the #1270 validation
            # tripwires at ~1475/~1502), the collapsed marker stays strict so a
            # production fix surfaces as a strict xpass instead of being silently
            # swallowed. Ledger-only items carry only non-strict markers, so they
            # stay non-strict — an environment-dependent xpass must not fail CI.
            xfails = [m for m in item.own_markers if m.name == "xfail"]
            if xfails:
                strict = next((m for m in xfails if m.kwargs.get("strict", False)), None)
                chosen = strict or xfails[0]
                item.own_markers = [m for m in item.own_markers if m.name != "xfail"]
                item.add_marker(
                    pytest.mark.xfail(
                        reason=chosen.kwargs.get("reason", "e2e_rest xfail"),
                        strict=strict is not None,
                    )
                )

    # ── Every strict-xfail leg runs on every wire transport ──────────
    # There is deliberately NO single-transport optimization here. Until
    # 2026-07-30 this block kept ONE mcp/rest "representative" per strict-xfail
    # scenario and deselected the sibling, which had two consequences:
    #
    #   * the representative was whichever variant appeared FIRST in `items`,
    #     and pytest-randomly (active for the bdd env — only `integration`
    #     passes `-p no:randomly`) reshuffles `items` per run, so the surviving
    #     transport was a per-run coin flip (GH #1291 work, 22 UC-010 nodeids
    #     traded mcp<->rest between full runs with the totals conserved);
    #   * transports diverge one at a time in this repo, so a single
    #     representative structurally cannot see a transport-specific
    #     production fix: the XPASS(strict) tripwire simply is not on the
    #     transport that got fixed.
    #
    # The fix is completeness, not a deterministic tie-break: a deterministic
    # representative would have turned an intermittent blind spot into a
    # permanent one. Every strict-xfail scenario now runs on a2a AND mcp AND
    # rest, each with strict=True, so an xpass surfaces on whichever transport
    # production actually fixed. The price is ~341 extra items (4.2% of the BDD
    # suite), all of them strict xfails.
    #
    # Do not reintroduce a keep-one optimization. If runtime ever forces one, it
    # must be expressed as an explicit per-scenario decision, not as an
    # order-dependent accumulator — see
    # tests/unit/test_guards_bdd_strict_xfail_representative.py, which fails on
    # any deselection of a strict-xfail transport leg.


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
# (UC-002 @account used to live here when it ran resolve_account() via IMPL on
# MediaBuyAccountEnv; #1417 routed those scenarios through a full
# create_media_buy on the wire, so they now parametrize across a2a/mcp/rest.)
_IMPL_ONLY: set[tuple[str, str]] = set()

# UC-002 idempotency scenarios wired to MediaBuyCreateEnv (run a real
# create_media_buy across all 4 transports). Only these two @idempotency-key
# tags are live; the rest stay blanket-xfailed in _harness_env until their
# production gaps + steps are wired.
_UC002_IDEMPOTENCY_WIRED: set[str] = {
    "T-UC-002-v31-idempotency-replay",
    "T-UC-002-v31-idempotency-missing",
}

# UC-002 manual-approval scenario wired to MediaBuyCreateEnv (PR #1567 round-2 item 2):
# grades the spec-3.1.1 CreateMediaBuySubmitted envelope (status="submitted" +
# task_id, no media_buy_id/confirmed_at/revision) across all 4 transports —
# the create mirror of the BR-UC-003 wiring (1b2f03bc9). Other @alt-manual
# scenarios (reject/approve flows) stay dormant until their steps are wired.
_UC002_MANUAL_APPROVAL_WIRED: set[str] = {
    "T-UC-002-alt-manual",
}


def _is_brand_shorthand_media_buy(marker_names: set[str]) -> bool:
    """True when a brand_shorthand scenario targets create_media_buy (UC-002 harness)."""
    return "brand_shorthand" in marker_names and "create_media_buy" in marker_names


# Admin scenarios have their own transport (Flask test_client / requests.Session).
# They must NOT be parametrized across MCP/A2A/REST/IMPL API transports.
_ADMIN_TAG_PREFIX = "T-ADMIN-"

# Scenario outlines whose <channel> column IS the transport: each Examples row
# dispatches through its own channel inside the When step, so pytest-level
# transport multiplication adds zero coverage (×3 identical in-process runs,
# and an e2e_rest variant that never touches the live server — the channel
# map has no e2e leg). Run once, like the @mcp/@a2a-tagged scenarios. The
# UC-010 feature header declares the auth-policy rows deliberately
# transport-specific (#1592).
_CHANNEL_COLUMN_TAGS = {"T-UC-010-auth"}

# UCs whose tool has no REST route — parametrize across A2A + MCP only (a REST
# variant would 404). get_media_buys (UC-019) is A2A/MCP-only.
_NO_REST_UC_TAG_PREFIXES = ("T-UC-019-",)

# Send-time webhook scenarios that assert in-process mock/circuit-breaker state.
# Do NOT append e2e_rest (false-green) and do NOT grow _UC004_E2E_WEBHOOK_INTERNAL_TAGS.
_NO_E2E_REST_TAGS: frozenset[str] = frozenset(
    {
        "T-UC-004-webhook-ssrf-blocked",
    }
)


def _parametrize_ctx(
    metafunc: pytest.Metafunc,
    base_transports: list[Any],
    base_ids: list[str],
    e2e_member: Any | None,
    e2e_id: str | None,
) -> None:
    """Parametrize ``ctx`` over the in-process transports, plus the e2e one when enabled.

    Extracted so the AdCP arm and the admin arm share ONE copy of the
    append-e2e-when-enabled tail. Duplicating it would be the
    same logical operation with substituted enum members — the R0801 shape the
    DRY invariant treats as a defect, against a duplication baseline that may
    only shrink.
    """
    transports = list(base_transports)
    ids = list(base_ids)
    if e2e_member is not None and os.environ.get("BDD_E2E_ENABLED") == "true":
        transports.append(e2e_member)
        ids.append(e2e_id)
    metafunc.parametrize("ctx", transports, ids=ids, indirect=True)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize BDD scenarios across the wire transports (a2a/mcp/rest).

    The IMPL transport was dropped from the BDD default parametrization
    (#1417): BDD asserts AdCP *wire* conformance only. IMPL/call_impl
    remain available for unit/integration tests via the harness; they are simply
    no longer auto-parametrized here.

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

    if marker_names & _CHANNEL_COLUMN_TAGS:
        # Channel-column outline — each row dispatches via its own channel
        return

    # Admin scenarios are not AdCP tool surfaces (no a2a/mcp/rest/e2e_rest), but
    # they DO have two transports of their own, both declared in
    # BR-ADMIN-ACCOUNTS.feature's header and both implemented by AdminAccountEnv.
    # Parametrize over them here so the transport is chosen at collection time
    # rather than pinned inside the harness.
    if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
        from tests.harness.admin_accounts import AdminTransport

        _parametrize_ctx(
            metafunc,
            [AdminTransport.INTEGRATION],
            [AdminTransport.INTEGRATION.value],
            AdminTransport.E2E,
            AdminTransport.E2E.value,
        )
        return

    # IMPL-only scenarios: harness has no transport wrappers for this path
    for uc_prefix, required_tag in _IMPL_ONLY:
        tag_prefix = f"T-{uc_prefix}-"
        if any(t.startswith(tag_prefix) for t in marker_names) and required_tag in marker_names:
            return

    # IMPL sunsetted: it adds no coverage the wire transports don't, and it has no
    # wire envelope (so it can't participate in error-envelope assertions). The four
    # truthful transports are a2a/mcp/rest + e2e_rest (added below when enabled).
    transports = [Transport.A2A, Transport.MCP, Transport.REST]

    # UCs without a REST endpoint (get_media_buys has no REST route) are graded on
    # the A2A + MCP wire transports only — including a REST variant would 404.
    # This applies to e2e_rest too: it dispatches real HTTP REST to the live
    # server, so a tool with no REST route 404s there identically (confirmed by
    # the first in-network CI run: every UC-019 e2e_rest param died on a live
    # 404). Skip the e2e append for these UCs instead of parking ~40 ledger
    # entries for a definitionally-unsupported transport.
    no_rest_uc = any(t.startswith(_uc_prefix) for _uc_prefix in _NO_REST_UC_TAG_PREFIXES for t in marker_names)
    if no_rest_uc:
        transports = [Transport.A2A, Transport.MCP]

    # In-process-only webhook scenarios (PR #1697) have no e2e-observable
    # surface — skip e2e_rest rather than xfail (shrink-only ratchet /
    # false-green).
    skip_e2e_rest = no_rest_uc or bool(marker_names & _NO_E2E_REST_TAGS)

    # `Transport` is a StrEnum whose values ARE the parametrize ids ("a2a",
    # "mcp", "rest", "e2e_rest"), so deriving them keeps ONE source for both the
    # transport set and its spelling. A second literal list is how the ids and
    # the transports drift apart — and the ids are what every xfail route,
    # ledger entry and `_transport_of` call matches on.
    _parametrize_ctx(
        metafunc,
        transports,
        [t.value for t in transports],
        None if skip_e2e_rest else Transport.E2E_REST,
        None if skip_e2e_rest else Transport.E2E_REST.value,
    )


def _ssl_failure(exc: BaseException | None, depth: int = 0) -> ssl.SSLError | None:
    """The ``ssl.SSLError`` reachable from *exc*, walking the exception chain.

    httpx does not surface a certificate failure as an ``ssl`` exception: it
    raises ``httpx.ConnectError`` **wrapping** one, which is indistinguishable
    from "connection refused" by type alone. The chain is where the difference
    lives, so that is where the probe looks. Depth-bounded — a malformed chain
    must not hang the probe.
    """
    if exc is None or depth > 20:
        return None
    if isinstance(exc, ssl.SSLError):
        return exc
    return _ssl_failure(exc.__cause__ or exc.__context__, depth + 1)


def _probe_verify(base_url: str, ca_bundle: str | None) -> dict[str, object]:
    """``verify=`` kwargs for the health probe: the generated CA, when there is one.

    Only for an https base URL, and only when the bundle is really on disk — a
    missing file must reach the handshake and be reported as the TLS failure it
    is, not raise a ``FileNotFoundError`` from context construction that would
    read as a probe bug.
    """
    if not base_url.startswith("https://") or not ca_bundle or not Path(ca_bundle).is_file():
        return {}
    return {"verify": ssl.create_default_context(cafile=ca_bundle)}


@pytest.fixture(scope="session")
def e2e_stack():
    """Detect the live E2E stack; return an E2EConfig or None (never skips here).

    Reads E2E_BASE_URL / E2E_POSTGRES_URL (set by the in-network runner /
    run_all_tests via tox pass_env). Health-checks base_url so non-e2e transports
    still run when the stack is absent (returns None). For an e2e_* transport a
    None here is a hard ERROR (the ctx fixture raises) — never a skip, because
    e2e_* is only parametrized when BDD_E2E_ENABLED=true, so a missing stack means
    an explicitly-requested transport could not run. The RestE2EDispatcher reads
    config off the env, never the environment.
    """
    import httpx

    from tests.harness.transport import E2EConfig

    base_url = os.environ.get("E2E_BASE_URL")
    postgres_url = os.environ.get("E2E_POSTGRES_URL")

    # Phase B: per-worker e2e stacks. With E2E_PER_WORKER=1 under xdist, each
    # worker (PYTEST_XDIST_WORKER="gwN") targets its OWN server container
    # (network alias "server-gwN", port 8080) and its OWN database (adcp_gwN),
    # provisioned by run_all_tests.sh — so e2e_rest runs in parallel with no
    # shared-server/shared-DB contention. Falls back to the shared stack when off.
    ca_bundle = os.environ.get("E2E_CA_BUNDLE")
    tls_base_url = os.environ.get("E2E_TLS_BASE_URL")
    worker = os.environ.get("PYTEST_XDIST_WORKER")  # e.g. "gw3"
    if os.environ.get("E2E_PER_WORKER") == "1" and worker and worker.startswith("gw"):
        import re

        # Server containers are named "<project>-server-gwN" (globally-unique so
        # parallel worktrees don't collide) and reachable by that name on the
        # compose network. Hit the server directly on :8080 (SKIP_NGINX).
        proj = os.environ.get("COMPOSE_PROJECT_NAME", "")
        prefix = f"{proj}-" if proj else ""
        base_url = f"http://{prefix}server-{worker}:8080"
        # Each worker's TLS sidecar carries its own DOTTED CONTAINER NAME for the
        # same reason — `docker compose run` cannot give it a network alias.
        if tls_base_url:
            tls_base_url = f"https://{prefix}tls-{worker}.adcp.test:8443"
        if postgres_url:
            # swap the database name in the URL path -> adcp_<worker>
            postgres_url = re.sub(r"/[^/?]+(\?|$)", rf"/adcp_{worker}\1", postgres_url, count=1)

    if not base_url:
        return None

    probe_url = f"{base_url}/health"
    try:
        resp = httpx.get(probe_url, timeout=5, **_probe_verify(base_url, ca_bundle))
        resp.raise_for_status()
    except Exception as exc:
        # THREE outcomes, and collapsing any two of them is a defect:
        #   * a TLS/certificate failure is a BROKEN RIG -> raise. Reporting it as
        #     "absent" would hand back the plaintext config below and let an https
        #     scenario grade the http branch while reporting green — the exact
        #     vacuity salesagent-tgzb exists to remove.
        #   * a transport/HTTP failure means nothing is listening -> None, so the
        #     in-process transports still run on a machine with no Docker stack.
        #   * anything else is a bug in this probe or in httpx -> propagate. A
        #     bare `except Exception: return None` classified those as "no stack".
        if _ssl_failure(exc) is not None:
            raise RuntimeError(
                f"TLS verification FAILED probing the e2e stack at {probe_url} "
                f"(E2E_CA_BUNDLE={ca_bundle!r}). A certificate failure is a broken test rig, not an "
                f"absent stack: reporting it as absent would silently fall back to the plaintext "
                f"config and grade an https scenario on the http branch."
            ) from exc
        if isinstance(exc, httpx.TransportError | httpx.HTTPStatusError):
            return None
        raise

    if not postgres_url:
        postgres_url = (
            f"postgresql://adcp_user:secure_password_change_me@localhost:{os.environ.get('POSTGRES_PORT', '5435')}/adcp"
        )
    return E2EConfig(
        base_url=base_url,
        postgres_url=postgres_url,
        tls_base_url=tls_base_url,
        ca_bundle=ca_bundle,
    )


def _reset_e2e_db(e2e_config) -> None:
    """Flush the live server DB to a clean baseline before an e2e scenario.

    Live-server e2e shares ONE database and the server process commits
    independently, so the transaction-rollback isolation the in-process
    transports get (via the per-test integration_db) is impossible here. Instead
    TRUNCATE every data table CASCADE so each scenario's harness setup recreates
    exactly the rows it needs into a clean DB. The server reads the DB live, so it
    observes the reset immediately. alembic_version is preserved (schema stays).
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(e2e_config.postgres_url)
    try:
        with engine.begin() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            ]
            if tables:
                joined = ", ".join(f'"{t}"' for t in tables)
                conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()


@pytest.fixture()
def ctx(request: pytest.FixtureRequest, e2e_stack) -> Generator[dict, None, None]:
    """Per-scenario mutable context shared across Given/When/Then steps.

    When parametrized by pytest_generate_tests, ``request.param`` is a
    Transport enum injected as ctx["transport"]. Transport-specific
    scenarios (tagged @rest/@mcp/@a2a) are NOT parametrized and get
    an empty ctx (When steps handle dispatch explicitly).

    For an e2e_* transport, stash the live-stack E2EConfig in ctx so
    ``_harness_env`` passes it to the harness env (which then binds factories to
    the server's DB and dispatches over real HTTP). Skip if the stack is absent.
    """
    d: dict = {}
    if hasattr(request, "param"):
        d["transport"] = request.param
        t = request.param
        if hasattr(t, "value") and str(t.value).startswith("e2e_"):
            if e2e_stack is None:
                # e2e_* transports are only parametrized when BDD_E2E_ENABLED=true
                # (see pytest_generate_tests), so reaching here means e2e was
                # EXPLICITLY requested but the live stack could not be reached. That
                # is a hard ERROR, never a skip: a skipped e2e test masks the fact
                # that the transport never ran, turning a non-executed test into a
                # false green (No Quiet Failures / Test Integrity).
                base_url = os.environ.get("E2E_BASE_URL")
                cause = "E2E_BASE_URL is unset" if not base_url else f"{base_url}/health failed"
                raise RuntimeError(
                    f"BDD_E2E_ENABLED=true but the live E2E stack is unreachable ({cause}). "
                    "The e2e_rest transport cannot run. Start the in-network stack "
                    "(run_all_tests.sh) or unset BDD_E2E_ENABLED to run the in-process "
                    "transports only. Refusing to skip — a skipped e2e test is a false green."
                )
            d["e2e_config"] = e2e_stack
    try:
        yield d
    finally:
        # Stop any step-level patchers stashed on ctx (e.g. given_today_is patches
        # src.core.tools.media_buy_list.datetime; snapshot/adapter steps patch
        # get_adapter). These use patch().start() and are NOT tracked by the
        # harness's context-managed EXTERNAL_PATCHES, so without this teardown they
        # leak the module patch into later scenarios in the same worker — an
        # order-dependent contamination (masked today only by the wide factory
        # flight window). Stop in reverse (LIFO) and ignore already-stopped.
        for patcher in reversed(d.get("_patchers", [])):
            try:
                patcher.stop()
            except RuntimeError:
                pass  # already stopped


def _setup_existing_media_buy(ctx: dict, env: object, tenant: object, principal: object, product: object) -> None:
    """Create an existing media buy + package for UC-003 update scenarios.

    Seeds the database with a committed media buy and one package, then
    stores references in ctx so Given/When/Then steps can find them.
    Also registers the package label mapping for Gherkin "pkg_001".
    """
    from datetime import UTC, datetime, timedelta

    from tests.factories import MediaBuyFactory, MediaPackageFactory

    mb = MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        status="pending_approval",
        currency="USD",
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(days=30),
    )
    pkg = MediaPackageFactory(
        media_buy=mb,
        package_config={
            "package_id": "pkg_001",
            "product_id": product.product_id,
            "budget": 5000.0,
        },
    )
    env._commit_factory_data()
    ctx["existing_media_buy"] = mb
    ctx["existing_package"] = pkg
    # Register Gherkin label → real package_id mapping (see uc003 _register_package)
    from tests.bdd.steps.domain.uc003_update_media_buy import _register_package

    _register_package(ctx, "pkg_001", pkg)


def _detect_uc(request: pytest.FixtureRequest) -> str | None:
    """Detect which use case a BDD scenario belongs to via its tags."""
    marker_names = {m.name for m in request.node.iter_markers()}
    if any(t.startswith("T-UC-002") for t in marker_names):
        return "UC-002"
    if any(t.startswith("T-UC-003") for t in marker_names):
        return "UC-003"
    if any(t.startswith("T-UC-006") for t in marker_names):
        return "UC-006"
    if any(t.startswith("T-UC-005") for t in marker_names):
        return "UC-005"
    if any(t.startswith("T-UC-004") for t in marker_names):
        return "UC-004"
    if any(t.startswith("T-UC-010-") for t in marker_names):
        # Trailing dash matters: "T-UC-010" without it would prefix-capture
        # nothing today, but the dash pins the boundary against future
        # T-UC-0100-style tags (and mirrors the UC-011/018/019 hazard note).
        return "UC-010"
    if any(t.startswith("T-UC-011") for t in marker_names):
        return "UC-011"
    if any(t.startswith("T-UC-018") for t in marker_names):
        return "UC-018"
    if any(t.startswith("T-UC-019") for t in marker_names):
        return "UC-019"
    if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
        return "ADMIN"
    if "inventory_profile" in marker_names or (
        "brand_shorthand" in marker_names and not _is_brand_shorthand_media_buy(marker_names)
    ):
        return "UC-GET-PRODUCTS"
    if _is_brand_shorthand_media_buy(marker_names):
        return "UC-002"
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
    # Webhook-credential-length scenarios assert that a too-short reporting_webhook
    # credential is rejected at the create_media_buy boundary (the SDK
    # Authentication.credentials MinLen=32 fires on the wire). They need the
    # create transport wrappers, not the delivery/circuit-breaker harness — route
    # them to MediaBuyCreateEnv so production Pydantic does the rejecting.
    if {"T-UC-004-webhook-creds-short", "T-UC-004-webhook-creds-valid"} & marker_names:
        return "create"
    if "webhook-reliability" in marker_names:
        return "circuit-breaker"
    if "webhook" in marker_names:
        # Webhook scenarios (HMAC, bearer, sequence, notification_type) use
        # WebhookDeliveryService which lives in CircuitBreakerEnv, not the
        # older deliver_webhook_with_retry from WebhookEnv.
        return "circuit-breaker"
    return "poll"


@contextmanager
def _production_db_pointed_at(url: str) -> Generator[None, None, None]:
    """Point production's cached DB engine at ``url`` for the scenario duration.

    The e2e counterpart of ``integration_db``'s engine repoint: over e2e_rest
    the env's factories write to the live server DB (``e2e_config.postgres_url``),
    but the runner's ``DATABASE_URL`` targets the in-process test base (in-network:
    ``.../adcp_test``), so any in-process production call inside an e2e scenario
    (e.g. a TRANSPORT-BYPASS Given calling an ``_impl``) would read a different
    database than the one being seeded. Repoint DATABASE_URL + reset the cached
    engine on entry, restore both on exit (mirrors tests/conftest_db.py).
    """
    import src.core.context_manager as _context_manager_module
    from src.core.database.database_session import reset_engine

    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    reset_engine()
    _context_manager_module._context_manager_instance = None
    try:
        yield
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url
        reset_engine()
        _context_manager_module._context_manager_instance = None


def _db_scope_for(request: pytest.FixtureRequest, e2e_config: object | None) -> AbstractContextManager[None]:
    """Select the production-DB scope for an e2e-capable harness branch.

    In-process transports need the per-test database (``integration_db``).
    Over e2e_rest, ``integration_db`` would repoint production's cached engine
    at an empty per-test DB while the env's factories write to the live server
    DB — so any in-process production call inside an e2e scenario (raw
    ``get_db_session()`` read-backs in Then steps, TRANSPORT-BYPASS Givens
    calling an ``_impl``) would read the wrong database. Point production at
    the server DB instead for the scenario duration.
    """
    if e2e_config is None:
        request.getfixturevalue("integration_db")
        return nullcontext()
    return _production_db_pointed_at(e2e_config.postgres_url)  # type: ignore[attr-defined]


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
    e2e_config = ctx.get("e2e_config")

    # E2E shares one live DB across all scenarios; flush it to a clean baseline so
    # this scenario's harness setup starts fresh (no cross-scenario tenant_id
    # collisions). No-op for the in-process transports (they use per-test DBs).
    if e2e_config is not None:
        _reset_e2e_db(e2e_config)

    if uc == "UC-002":
        marker_names = {m.name for m in request.node.iter_markers()}
        # Tags that need the full create_media_buy flow (MediaBuyCreateEnv)
        # rather than account resolution only (MediaBuyAccountEnv).
        if "account" in marker_names:
            # Account-resolution scenarios run a full create_media_buy on the wire
            # (#1417): production resolves the account at the transport
            # boundary (enrich_identity_with_account → resolve_account) and emits
            # ACCOUNT_NOT_FOUND/AMBIGUOUS/SETUP_REQUIRED/PAYMENT_REQUIRED/SUSPENDED
            # — or succeeds — on the wire. MediaBuyCreateEnv gives the create
            # transport wrappers + the full product/pricing dependency chain; the
            # account Given steps seed the account rows on top.
            from tests.harness.media_buy_create import MediaBuyCreateEnv

            with _db_scope_for(request, e2e_config), MediaBuyCreateEnv(e2e_config=e2e_config) as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                yield
        elif (
            any(t.startswith("T-UC-002-ext-") for t in marker_names)
            or "nfr-highvalue" in marker_names
            or "T-UC-002-nfr-001-enforcement" in marker_names
        ):
            # Extension/error scenarios: budget validation, pricing errors, etc.
            # Plus the nfr-highvalue >$10k Seller-alert scenario (#1417),
            # and the nfr-001 no-auth rejection scenario (#1417), which
            # needs the same full create dispatch so each transport's REAL auth
            # gate (A2A on_message_send no-token gate, REST _require_auth_dep,
            # MCP boundary) produces the wire rejection.
            # which needs the same full create_media_buy flow to reach the
            # pending-approval audit feed.
            # Use MediaBuyCreateEnv which calls _create_media_buy_impl with real DB.
            from tests.harness.media_buy_create import MediaBuyCreateEnv

            with _db_scope_for(request, e2e_config), MediaBuyCreateEnv(e2e_config=e2e_config) as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                ctx["dispatch_mode"] = "create"
                yield
        elif marker_names & (_UC002_IDEMPOTENCY_WIRED | _UC002_MANUAL_APPROVAL_WIRED) or _is_brand_shorthand_media_buy(
            marker_names
        ):
            if marker_names & _UC002_MANUAL_APPROVAL_WIRED:
                # Tells the shared When step to dispatch a FULL create through
                # the parametrized transport (not account resolution). (PR #1567)
                ctx["uc002_full_create"] = True
            # v3.1 idempotency replay/missing scenarios — MediaBuyCreateEnv runs a
            # real create_media_buy through every transport (the replay scenario
            # creates once, then sends the same key again to exercise the
            # production replay path). Only the two wired tags go live here; the
            # remaining @idempotency-key scenarios (in-flight, expired, conflict,
            # pattern, canonical) stay blanket-xfailed below until their
            # production gaps + steps are wired.
            from tests.harness.media_buy_create import MediaBuyCreateEnv

            with _db_scope_for(request, e2e_config), MediaBuyCreateEnv(e2e_config=e2e_config) as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                yield
        elif "T-UC-002-inv-015-6" in marker_names:
            pytest.xfail("T-UC-002-inv-015-6 create_media_buy harness wiring is tracked in #1652")
        else:
            # Restore the xfail guard every other use case keeps on its catch-all:
            # non-account / non-extension UC-002 scenarios are NOT yet wired (no
            # dispatch_mode -> they route to resolve_account_or_error and fail with
            # "Account reference is required"). Mirror UC-003/004/006/011: xfail them
            # until each is explicitly wired into a run branch above. Dropping this
            # line is what flipped ~800 dormant scenarios from xfail to fail.
            pytest.xfail("UC-002 harness not yet wired for non-extension scenarios")

    elif uc == "UC-003":
        marker_names = {m.name for m in request.node.iter_markers()}
        # The targeting-overlay partition/boundary outlines (#1417) need the
        # same full update flow as ext- scenarios to reach the overlay-validation raise
        # at media_buy_update.py:444; wire them through MediaBuyDualEnv too.
        _UC003_TARGETING_OVERLAY = {
            "T-UC-003-partition-targeting-overlay",
            "T-UC-003-boundary-targeting-overlay",
        }
        # The 3 manual-approval submitted-envelope scenarios (PR #1567) — see the
        # BOUNDED branch below.
        _UC003_WIRED_TAGS = {
            "T-UC-003-alt-manual",
            "T-UC-003-approval-tenant",
            "T-UC-003-approval-adapter",
        }
        if any(t.startswith("T-UC-003-ext-") for t in marker_names) or (marker_names & _UC003_TARGETING_OVERLAY):
            # Extension/error scenarios: budget, currency, auth, creative,
            # placement, keyword, and immutable-field validation on the update
            # path. MediaBuyDualEnv extends MediaBuyCreateEnv with update-module
            # patches and dispatches UpdateMediaBuyRequest through the update
            # transport wrappers (_update_media_buy_impl / update_media_buy_raw /
            # MCP / REST), so update scenarios actually exercise the update flow
            # against the real DB instead of falling through to _create_media_buy_impl.
            from tests.harness.media_buy_dual import MediaBuyDualEnv

            with _db_scope_for(request, e2e_config), MediaBuyDualEnv(e2e_config=e2e_config) as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                # Seed an existing media buy + package for update scenarios and
                # tell the env which media_buy_id the REST update endpoint targets.
                _setup_existing_media_buy(ctx, env, tenant, principal, product)
                env._seeded_media_buy_id = ctx["existing_media_buy"].media_buy_id
                yield
        elif marker_names & _UC003_WIRED_TAGS:
            # BOUNDED (PR #1567): the 3 manual-approval submitted-envelope
            # scenarios are graded here (they exercise UpdateMediaBuySubmitted
            # cross-transport). Every other non-extension UC-003 scenario stays
            # dormant via the else below — graduating the full UC-003 file is a
            # tracked PR #1567 follow-up. This guard is what keeps un-dormanting
            # UC-003 from turning the suite red.
            #
            # UpdateMediaBuy manual-approval scenarios. MediaBuyDualEnv (an IntegrationEnv)
            # routes an UpdateMediaBuyRequest through IMPL/A2A/MCP/REST. Seed the full create
            # dependency chain plus a standalone MediaBuy with the literal id the
            # Background references ("mb_existing") so the update path has a target.
            from tests.factories import MediaBuyFactory
            from tests.harness.media_buy_dual import MediaBuyDualEnv

            with _db_scope_for(request, e2e_config), MediaBuyDualEnv(e2e_config=e2e_config) as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                existing_media_buy = MediaBuyFactory(
                    tenant=tenant,
                    principal=principal,
                    media_buy_id="mb_existing",
                    status="active",
                )
                env._commit_factory_data()
                env._seeded_media_buy_id = "mb_existing"
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                ctx["existing_media_buy"] = existing_media_buy
                yield
        else:
            pytest.xfail(
                "UC-003 harness not yet wired for non-extension scenarios (full graduation pending, PR #1567 follow-up)"
            )

    elif uc == "UC-006":
        marker_names = {m.name for m in request.node.iter_markers()}
        if marker_names & {"account", "creative-invariant", "BR-RULE-034", "webhook-ssrf", "request-signing"}:
            # CreativeSyncEnv exercises the full sync_creatives transport wrappers.
            # @account scenarios drive account resolution (enrich_identity_with_account());
            # @creative-invariant scenarios (#1399 R3-F2) drive the success-variant
            # response invariants (e.g. all-failed still returns the success variant);
            # @BR-RULE-034 scenarios drive cross-principal isolation (triple-key
            # creative lookup) — dormant until the cross-principal existence-gate
            # fix (PR #1430 review) made the surface safe to grade.
            # @webhook-ssrf scenarios grade registration SSRF on push_notification_config.url.
            # @request-signing scenarios (salesagent-n78j0.1.3) grade the INBOUND
            # RFC 9421 enforcement ladder — the composition rule and the
            # webhook-credential escalation — on the same sync_creatives dispatch. They
            # need nothing from this env beyond a real wire on every transport and a
            # push_notification_config it already forwards; the posture, the key and the
            # verification oracle are BaseTestEnv's (env.declare_request_signing /
            # enable_request_signing / signature_verifications).
            from tests.harness.creative_sync import CreativeSyncEnv

            with _db_scope_for(request, e2e_config), CreativeSyncEnv(e2e_config=e2e_config) as env:
                ctx["env"] = env
                yield
        else:
            pytest.xfail("UC-006 harness not yet wired for non-account scenarios")

    elif uc == "UC-005":
        from tests.harness.creative_formats import CreativeFormatsEnv

        with _db_scope_for(request, e2e_config), CreativeFormatsEnv(e2e_config=e2e_config) as env:
            # Seed a tenant ONLY in e2e mode: the live server authenticates the token
            # against the DB tenant, and UC-005 baseline scenarios carry no account/tenant
            # Given step to seed it (unlike UC-006/UC-011). In-process the registry is mocked
            # and the DB is per-test, so the in-process status quo must stay unseeded.
            # Mirrors the UC-004 poll branch (#1417).
            if env.e2e_config is not None:
                env.setup_default_data()
            ctx["env"] = env
            yield

    elif uc == "UC-018":
        # list_creatives — the wired scenarios are @list-after-sync (#1405),
        # @concept-id (#1407), and the @BR-RULE-034 cross-principal isolation
        # invariants (#1503). The remaining UC-018 scenarios (main/partition/
        # boundary/other filter siblings) have no step definitions yet, so xfail
        # fast at the fixture (mirrors UC-002/006/011) rather than spinning up a
        # DB per scenario only to auto-xfail at the first missing step.
        #
        # When the dormant all-fields boundary scenarios are wired, their Then must
        # assert value-when-present, not key-presence-of-13: list_creatives drops a
        # corrupt tags/assets blob to absent and collapses an empty stored tags list
        # to omission (both conformant at 3.1.1) — see the #1508 reconciliation note
        # in test_uc018_list_creatives.py's module docstring.
        #
        # BR-RULE-034 is unambiguous here: this branch only runs for T-UC-018-*
        # scenarios (see _detect_uc), so the tag never collides with the UC-006
        # BR-RULE-034 scenarios routed elsewhere.
        marker_names = {m.name for m in request.node.iter_markers()}
        if marker_names & {"list-after-sync", "concept-id", "BR-RULE-034"}:
            # CreativeListEnv mocks only the audit logger; DB, repository, and
            # query building are real. The Background auth step switches the env
            # principal; the seed step owns the creatives under it.
            from tests.harness.creative_list import CreativeListEnv

            with _db_scope_for(request, e2e_config), CreativeListEnv(e2e_config=e2e_config) as env:
                ctx["env"] = env
                yield
        elif "T-UC-018-ext-c" in marker_names:
            pytest.xfail("T-UC-018-ext-c list_creatives validation harness wiring is tracked in #1652")
        else:
            pytest.xfail(
                "UC-018 harness wired only for the @list-after-sync (#1405), @concept-id (#1407), "
                "and @BR-RULE-034 isolation (#1503) scenarios"
            )

    elif uc == "UC-010":
        # get_adcp_capabilities — CapabilitiesEnv mocks only the adapter factory
        # and audit logger; DB, TenantConfigUoW and all transport wrappers are
        # real. Wiring lands in batches (#1592 / salesagent-4sn7): only tag
        # families whose step batch has landed pay integration_db + env setup;
        # the rest xfail fast here (UC-018 pattern). The gate SHRINKS per batch
        # and disappears at batch 3.
        _UC010_WIRED_TAGS = {
            # Batch 1 — envelope + account families
            "T-UC-010-main",
            "T-UC-010-main-timestamp",
            "T-UC-010-main-readonly",
            "T-UC-010-pricing",
            "T-UC-010-audience-caps",
            "T-UC-010-conversion-caps",
            "T-UC-010-creative-caps",
            "T-UC-010-ext-b-schema-valid",
            "T-UC-010-ext-a",
            "T-UC-010-account-require-operator-auth",
            "T-UC-010-account-authorization-endpoint",
            "T-UC-010-account-required-for-products",
            "T-UC-010-account-supported-billing",
            "T-UC-010-account-financials-declaration",
            "T-UC-010-account-block-presence",
            "T-UC-010-degradation-account",
            "T-UC-010-features-partitions",
            "T-UC-010-auth",
            "T-UC-010-auth-data-identity",
            "T-UC-010-ext-c-a2a",
            "T-UC-010-ext-c-mcp",
            "T-UC-010-ext-e-echo",
            "T-UC-010-ext-e-absent",
            "T-UC-010-ext-e-nested",
            "T-UC-010-ext-e-empty",
            "T-UC-010-ext-d-filter",
            "T-UC-010-ext-d-all-protocols",
            "T-UC-010-ext-d-invalid-value",
            "T-UC-010-ext-d-empty",
            "T-UC-010-v31-supported-versions",
            "T-UC-010-v31-version-unsupported",
            "T-UC-010-v31-version-unsupported-major-fallback",
            "T-UC-010-v31-version-unsupported-build-version-advisory",
            # Batch 3 — degradation-sections + channel-all-canonical (salesagent-chbi)
            "T-UC-010-degradation-sections",
            "T-UC-010-channel-all-canonical",
            # Batch 4 — features / targeting / idempotency-required (salesagent-tmpd)
            "T-UC-010-features",
            "T-UC-010-targeting",
            "T-UC-010-targeting-partitions",
            "T-UC-010-degradation-partitions",
            "T-UC-010-v31-idempotency-required",
            # Batch 5 — v3.1 signing / brand / reporting / measurement (salesagent-scgh)
            "T-UC-010-v31-reporting-delivery-methods",
            "T-UC-010-v31-brand-block",
            "T-UC-010-v31-webhook-signing-required-when",
            "T-UC-010-v31-identity-required-when-signing",
            "T-UC-010-v31-measurement-catalog",
            # Batch 6 — compliance_testing / specialisms / advisory errors (salesagent-e4ad)
            "T-UC-010-v31-compliance-testing",
            "T-UC-010-v31-specialisms",
            "T-UC-010-v31-advisory-errors",
            # Batch 7 — bounds / monotonicity outlines (salesagent-jd6a)
            "T-UC-010-v31-request-signing-monotonicity",
            "T-UC-010-v31-idempotency-ttl-bounds",
            "T-UC-010-v31-version-unsupported-details-bounds",
            "T-UC-010-v31-identity-brand-json-url-bounds",
            # Batch 8 — webhook-signing bounds outline (salesagent-8wuu)
            "T-UC-010-v31-webhook-signing-bounds",
            # Batch 9 — version negotiation + idempotency posture (salesagent-rldj)
            "T-UC-010-v31-idempotency-supported",
            "T-UC-010-v31-idempotency-in-flight-bound",
            # Batch 10 — creative_approval_mode (salesagent-y9ld R7)
            "T-UC-010-v31-creative-approval-mode",
            # Batch 11 — trusted_match surfaces (salesagent-3xmz)
            "T-UC-010-v31-trusted-match-surfaces",
            # Batch 12 — measurement accreditations (salesagent-3xmz)
            "T-UC-010-v31-measurement-accreditations",
            # Batch 13 — locally-added declaration-backing graders (salesagent-3xmz).
            # These grade validate_backing()'s rejection rules, which the generated
            # specialisms scenario cannot: it declares creative-generative + the
            # creative protocol, both unbacked, so it stays xfailed against #1724.
            "T-UC-010-local-backed-specialism",
            "T-UC-010-local-unbacked-specialism",
            "T-UC-010-local-orphaned-specialism",
            "T-UC-010-local-unbacked-protocol",
            # Batch 14 — the signing family's MAIN-FLOW scenarios (#1291 D1). These four
            # were dormant TWICE over: their Givens had no step definition anywhere (which
            # pytest_runtest_makereport converts to xfail) AND their tags were absent from
            # this set (which xfails at fixture setup, before a single step runs). Both
            # halves are fixed; `request_signing` is now a real tenant declaration and
            # `webhook_signing` is realized as platform state.
            "T-UC-010-v31-request-signing-posture",
            "T-UC-010-v31-request-signing-namespace-split",
            "T-UC-010-v31-request-signing-subset",
            "T-UC-010-v31-webhook-signing",
            # Batch 15 — account.sandbox boundary outline (#1721 M4). Was dormant
            # (no bound Given for "the tenant account is configured for
            # {boundary_point}"), citing #1855 (generic wiring) instead of the
            # accurate #1856 (account-config surface) -- both fixed.
            "T-UC-010-v31-account-sandbox",
        }
        marker_names = {m.name for m in request.node.iter_markers()}
        parked = marker_names & _UC010_PARKED_TAGS.keys()
        if parked:
            tag = sorted(parked)[0]
            pytest.xfail(f"{tag}: {_UC010_PARKED_TAGS[tag]}")
        for tag, substrings, reason in _UC010_PARKED_ROWS:
            if tag in marker_names and any(s in request.node.nodeid for s in substrings):
                pytest.xfail(f"{tag}: {reason}")
        if not (marker_names & _UC010_WIRED_TAGS):
            pytest.xfail(
                "UC-010 harness wiring not extended to this tag (dormant, never graded) — steps tracked by #1855; presence-object production gap is #1855"
            )

        from tests.harness.capabilities import CapabilitiesEnv

        with (
            _db_scope_for(request, e2e_config),
            CapabilitiesEnv(principal_id="buyer-001", e2e_config=e2e_config) as env,
        ):
            tenant, principal = env.setup_default_data()
            ctx["env"] = env
            ctx["tenant"] = tenant
            ctx["principal"] = principal
            yield

    elif uc == "UC-011":
        marker_names = {m.name for m in request.node.iter_markers()}
        harness_type = _detect_uc011_harness(marker_names)

        if harness_type == "list":
            from tests.harness.account_list import AccountListEnv

            with _db_scope_for(request, e2e_config), AccountListEnv(e2e_config=e2e_config) as env:
                ctx["env"] = env
                yield
        elif harness_type == "sync":
            from tests.harness.account_sync import AccountSyncEnv

            with _db_scope_for(request, e2e_config), AccountSyncEnv(e2e_config=e2e_config) as env:
                ctx["env"] = env
                yield
        else:
            pytest.xfail(f"UC-011 harness not yet wired for markers: {marker_names}")

    elif uc == "ADMIN":
        from tests.harness.admin_accounts import AdminAccountEnv

        # Both transports the feature file declares, chosen by the collection-time
        # parametrization rather than pinned here. The env is
        # TOLD its transport and, over e2e, the per-worker address e2e_stack
        # synthesised — it discovers neither.
        #
        # This is the ONE branch that passes `base_url=` instead of `e2e_config=`,
        # and the asymmetry is deliberate rather than an oversight: the admin UI is
        # an HTML form surface, not an AdCP tool surface, so the env needs the
        # ADDRESS and nothing else from E2EConfig. Handing it the whole object
        # would pull an AdCP-shaped dependency into a surface that has no AdCP
        # protocol — the same reason AdminTransport is not a member of the
        # Transport enum (see its docstring). A census that asks "does every env
        # here receive e2e_config?" will flag this line; that flag is expected.
        # What actually must hold — no branch pins its own DB scope — is machine
        # -checked by tests/unit/test_bdd_admin_transport_parametrization.py
        # ::test_harness_env_never_pins_its_db_scope, not by that heuristic.
        mode = "e2e" if e2e_config is not None else "integration"
        base_url = e2e_config.base_url if e2e_config is not None else None
        with _db_scope_for(request, e2e_config), AdminAccountEnv(mode=mode, base_url=base_url) as env:
            ctx["env"] = env
            yield

    elif uc == "COMPAT":
        from tests.harness.product import ProductEnv

        with _db_scope_for(request, e2e_config), ProductEnv(e2e_config=e2e_config) as env:
            ctx["env"] = env
            yield

    elif uc == "UC-004":
        harness_type = _detect_delivery_harness(request)

        if harness_type == "poll":
            from tests.harness.delivery_poll import DeliveryPollEnv

            # Use "buyer-001" as principal — matches most UC-004 scenarios.
            # _ensure_media_buy_in_db creates media buys owned by the
            # scenario's "owner" (usually "buyer-001"), and _impl filters
            # by the identity's principal. They must match.
            with (
                _db_scope_for(request, e2e_config),
                DeliveryPollEnv(principal_id="buyer-001", e2e_config=e2e_config) as env,
            ):
                tenant, principal = env.setup_default_data()
                ctx["env"] = env
                ctx["db_tenant"] = tenant
                ctx[f"db_principal_{env._principal_id}"] = principal
                yield
        elif harness_type == "webhook":
            from tests.harness.delivery_webhook import WebhookEnv

            with _db_scope_for(request, e2e_config), WebhookEnv(e2e_config=e2e_config) as env:
                env.setup_default_data()
                ctx["env"] = env
                yield
        elif harness_type == "circuit-breaker":
            from tests.harness.delivery_circuit_breaker import CircuitBreakerEnv

            with _db_scope_for(request, e2e_config), CircuitBreakerEnv(e2e_config=e2e_config) as env:
                env.setup_default_data()
                ctx["env"] = env
                yield
        elif harness_type == "create":
            # Webhook-credential-length scenarios dispatch a real create_media_buy
            # carrying a reporting_webhook so production's Pydantic boundary
            # (Authentication.credentials MinLen=32) accepts/rejects on the wire.
            from tests.harness.media_buy_create import MediaBuyCreateEnv

            with _db_scope_for(request, e2e_config), MediaBuyCreateEnv(e2e_config=e2e_config) as env:
                tenant, principal, product, pricing_option = env.setup_media_buy_data()
                ctx["env"] = env
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                ctx["default_product"] = product
                ctx["default_pricing_option"] = pricing_option
                yield
        else:
            pytest.xfail(f"UC-004 harness not yet wired for type: {harness_type}")
    elif uc == "UC-GET-PRODUCTS":
        from tests.harness.product import ProductEnv

        with _db_scope_for(request, e2e_config), ProductEnv(e2e_config=e2e_config) as env:
            ctx["env"] = env
            yield
    elif uc == "UC-019":
        # get_media_buys — MediaBuyListEnv runs the real _get_media_buys_impl and
        # its A2A/MCP wrappers against a real DB (no adapter mock; list is a pure
        # read). Scenarios seed buys via factories under ctx["tenant"]/["principal"]
        # (principal "buyer-001" matches the feature files). Genuine spec-production
        # gaps stay xfailed via _UC019_XFAIL_TAGS / the selective blocks above.
        from tests.harness.media_buy_list import MediaBuyListEnv

        with (
            _db_scope_for(request, e2e_config),
            MediaBuyListEnv(principal_id="buyer-001", e2e_config=e2e_config) as env,
        ):
            tenant, principal = env.setup_default_data()
            ctx["env"] = env
            ctx["tenant"] = tenant
            ctx["principal"] = principal
            yield
    else:
        pytest.xfail(f"No harness wired for {uc}")
