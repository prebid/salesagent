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
}

# FIXME(beads-dul): Selective xfail for parametrized scenarios where only
# some examples exercise unimplemented features. Each entry: (tag, node_id
# substrings that should xfail, reason).
_SELECTIVE_XFAIL: list[tuple[str, set[str], str]] = [
    (
        "T-UC-005-partition-disclosure",
        {"single_position", "multiple_positions_all_match", "all_positions", "no_matching_formats"},
        "disclosure_positions filter not implemented",
    ),
    (
        "T-UC-005-boundary-disclosure",
        {"single position", "all 8 positions", "format has no"},
        "disclosure_positions filter not implemented",
    ),
    (
        "T-UC-005-boundary-asset-types",
        {"brief", "catalog"},
        "brief/catalog asset types not in adcp enum",
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

        # Detect transport from parametrized nodeid: [mcp], [mcp-...], [rest], [rest-...]
        is_mcp = "[mcp]" in nodeid or "[mcp-" in nodeid
        is_rest = "[rest]" in nodeid or "[rest-" in nodeid

        # Transport-specific xfails: MCP wrappers don't accept certain filter params
        if is_mcp:
            for tag, substrings, reason, strict in _MCP_SELECTIVE_XFAIL:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                    break

        # UC-011 REST: per-request auth implemented (salesagent-xms)

        # FIXME(#1184): UC-011 MCP — billing policy and approval mode not populated
        # from DB. The real MCP auth chain resolves identity from DB which doesn't
        # carry supported_billing or account_approval_mode. These are tenant-level
        # configs that need a DB migration to persist.
        _UC011_MCP_IDENTITY_XFAIL: set[str] = {
            "T-UC-011-ext-c-rejected",  # billing rejection
            "T-UC-011-ext-c-mixed",  # per-account billing rejection
            "T-UC-011-ext-d-pending-url",  # approval mode pending
            "T-UC-011-ext-d-pending-message",  # approval mode pending
            "T-UC-011-atomic-all-failed",  # all-failed (uses billing rejection)
        }
        if is_mcp and (marker_names & _UC011_MCP_IDENTITY_XFAIL):
            item.add_marker(
                pytest.mark.xfail(
                    reason="MCP: billing/approval config not in DB — needs #1184",
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

        # --- UC-005: disclosure/asset scenarios with partial impl ---
        # FIXME(beads-dul): disclosure_positions and brief/catalog asset types
        # partially implemented — some transport variants pass, others fail.
        # Must run BEFORE selective xfails (which use strict=True) to avoid
        # XPASS failures on transport variants that now pass.
        _UC005_PARTIAL_TAGS = {
            "T-UC-005-partition-disclosure",
            "T-UC-005-boundary-disclosure",
            "T-UC-005-boundary-asset-types",
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
            # campaign unit interval validation: _impl doesn't validate attribution_window
            "T-UC-004-attr-campaign-invalid": (
                "attribution_window campaign unit validation not implemented in _impl",
                True,
            ),
            # Partial-success Error model lacks suggestion field and rich messages
            "T-UC-004-ext-a": ("partial-success Error needs suggestion field + authentication in message", True),
            "T-UC-004-ext-b": ("partial-success Error model needs suggestion field — production enhancement", True),
            "T-UC-004-ext-c": ("partial-success Error model needs suggestion field — production enhancement", True),
            "T-UC-004-ext-d": ("partial-success Error model needs suggestion field — production enhancement", True),
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
            ("T-UC-004-daterange-start-only", set(), "start-only date range partially applied"),
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
            "T-UC-004-partition-reporting-dims",
            "T-UC-004-partition-attribution",
            "T-UC-004-partition-daily-breakdown",
            "T-UC-004-partition-account",
            "T-UC-004-partition-sampling",
            "T-UC-004-partition-status-filter",
            "T-UC-004-partition-date-range",
            "T-UC-004-partition-resolution",
            "T-UC-004-partition-ownership",
            "T-UC-004-partition-credentials",
        }
        if marker_names & _UC004_PARTITION_TAGS:
            item.add_marker(
                pytest.mark.xfail(reason="partition validation behavior varies with adcp schema version", strict=False)
            )

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
        return "webhook"
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
            from tests.harness.delivery_webhook import WebhookEnv

            with WebhookEnv() as env:
                ctx["env"] = env
                yield
        elif harness_type == "circuit-breaker":
            from tests.harness.delivery_circuit_breaker import CircuitBreakerEnv

            with CircuitBreakerEnv() as env:
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
