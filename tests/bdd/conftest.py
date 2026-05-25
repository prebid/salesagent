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
    # FIXME(beads-dul): disclosure_positions filter not implemented in
    # _list_creative_formats_impl. The adcp library request carries a
    # disclosure_positions field but creative_formats.py applies no
    # disclosure filter, so "holds" (asserts a specific format is the only
    # match) genuinely fails on every transport. violated/nofield are
    # reconciled separately (salesagent-9z2t): they fail on impl/a2a/rest
    # for the same reason and pass vacuously on MCP (the MCP wrapper has no
    # disclosure_positions param → ToolError → empty result satisfies the
    # absence assertion).
    "T-UC-005-inv-049-8-holds": "disclosure_positions filter not implemented in _list_creative_formats_impl",
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

# Selective xfail for parametrized scenarios where only some examples
# exercise an unimplemented feature. Each entry: (tag, node_id substrings
# that should xfail, reason). strict=True — these MUST fail until the gap
# closes.
#
# salesagent-9z2t: the disclosure_positions When-step now builds a valid
# request (the earlier "all_positions"/"no_matching_formats" partitions
# used stale enum literals — corner/inline/before/after — which no longer
# exist in DisclosurePosition, so the request never built and the row
# passed vacuously under a blanket strict=False marker). With a real
# request the only genuine, transport-independent failure is the
# duplicate-positions example: production does not reject duplicate
# disclosure_positions values (no dedup/validation), so a scenario that
# expects rejection fails on every transport. All other partition/boundary
# disclosure rows now pass on impl/a2a/rest (a "valid" outcome only
# requires a schema-valid success; the missing filter is a no-op, not a
# rejection) and are reconciled per-transport for MCP via
# _MCP_SELECTIVE_XFAIL.
_SELECTIVE_XFAIL: list[tuple[str, set[str], str]] = [
    (
        "T-UC-005-partition-disclosure",
        {"duplicate_positions"},
        "production does not reject duplicate disclosure_positions values "
        "(no dedup/validation in _list_creative_formats_impl)",
    ),
    (
        "T-UC-005-boundary-disclosure",
        {"duplicate positions"},
        "production does not reject duplicate disclosure_positions values "
        "(no dedup/validation in _list_creative_formats_impl)",
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
    # disclosure_positions (salesagent-9z2t): the MCP list_creative_formats
    # wrapper signature has no disclosure_positions parameter, so any MCP
    # call that sends one is rejected by FastMCP tool validation
    # (ToolError "unexpected keyword argument"). The When-step records that
    # as ctx["error"], so every example that actually SENDS the param
    # genuinely fails the strengthened assertion (a "valid" outcome must
    # have no error; an "invalid" outcome must be a real AdCP/validation
    # rejection, not a transport ToolError). Examples that send no param —
    # "omitted" — and the schema-invalid examples (unknown_position /
    # empty_array, rejected by Pydantic before the MCP boundary) still pass.
    (
        "T-UC-005-partition-disclosure",
        {
            "single_position",
            "multiple_positions_all_match",
            "all_positions",
            "no_matching_formats",
            "duplicate_positions",
        },
        "MCP wrapper does not accept disclosure_positions",
        True,
    ),
    (
        "T-UC-005-boundary-disclosure",
        {"single position", "all 8 positions", "format has no", "duplicate positions"},
        "MCP wrapper does not accept disclosure_positions",
        True,
    ),
    # Invariant scenarios — only "holds" carries a marker. "holds" asserts
    # a specific format is the unique match, so the MCP wrapper dropping the
    # filter param genuinely fails it (strict=True, tied to the named
    # wrapper gap). "violated"/"nofield" assert a format is *absent*; the
    # MCP wrapper rejecting the param yields an empty/errored result that
    # trivially satisfies absence, so they pass vacuously. A strict=False
    # marker there would only ever XPASS — it documents nothing the
    # strict=True "holds" markers don't already pin to the same gap — so it
    # is removed (salesagent-9z2t/7xc2). The genuine guardrail is the
    # "holds" rows: when the wrapper gains the param they flip XPASS and
    # force the marker out.
    ("T-UC-005-inv-049-9-holds", set(), "MCP wrapper does not accept output_format_ids", True),
    ("T-UC-005-inv-049-10-holds", set(), "MCP wrapper does not accept input_format_ids", True),
]

# REST xfails: REST endpoint drops all filter params (build_rest_body returns {}).
# Only xfail scenarios that genuinely fail — many invariant "holds" scenarios
# pass coincidentally because unfiltered results include the expected format.
_REST_XFAIL_TAGS: set[str] = {
    # Invariant filter scenarios where REST unfiltered results break assertions
    # disclosure_positions (salesagent-9z2t): REST drops all filter params,
    # so the seeded non-matching format leaks into the unfiltered result and
    # the "should not be returned" / edge-exclusion assertions fail. (holds
    # is covered transport-wide by _XFAIL_TAGS.)
    "T-UC-005-inv-049-8-violated",
    "T-UC-005-inv-049-8-nofield",
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
        # UC-011 MCP: billing policy and approval mode now populated from DB via
        # account_approval_mode column + proper harness writes (#1184 complete).

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

        # --- UC-005: disclosure_positions filter not implemented in _impl ---
        # salesagent-9z2t reconciliation. The earlier blanket
        # _UC005_PARTIAL_TAGS strict=False masked two distinct realities:
        #   1. a broken When-step (stale enum literals) that never built a
        #      valid request — now fixed in when_request.py, so the rows
        #      genuinely exercise the feature; and
        #   2. a genuine production gap — _list_creative_formats_impl
        #      applies no disclosure_positions filter.
        # inv-049-8 "violated"/"nofield" assert a seeded format is excluded.
        # With the filter absent it leaks into the result, so they fail on
        # impl and a2a (full filter pipeline). REST is handled by
        # _REST_XFAIL_TAGS (drops all params) and MCP by
        # _MCP_SELECTIVE_XFAIL (wrapper rejects the param → vacuous pass),
        # so this strict=True only targets impl/a2a.
        _UC005_DISCLOSURE_IMPL_GAP = {
            "T-UC-005-inv-049-8-violated",
            "T-UC-005-inv-049-8-nofield",
        }
        if (marker_names & _UC005_DISCLOSURE_IMPL_GAP) and not is_mcp and not is_rest:
            item.add_marker(
                pytest.mark.xfail(
                    reason="disclosure_positions filter not implemented in _list_creative_formats_impl",
                    strict=True,
                )
            )

        # Selective xfail for parametrized scenarios. MCP already handled
        # above (its disclosure rows live in _MCP_SELECTIVE_XFAIL); applying
        # the transport-wide entry again would double-mark, so skip MCP here.
        if not is_mcp:
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

        # --- UC-006: INVALID_REQUEST validation xfails ---
        # B7 reconciled (salesagent-miva, 18h.10 Phase-2): the step layer no
        # longer synthesizes an AdCPValidationError — when_sync_creative now
        # genuinely dispatches the absent/both-account payloads to production
        # (see tests/bdd/steps/domain/uc006_sync_creatives.py). With that, the
        # rows genuinely fail for a real, named production gap:
        #   - missing_account / account field absent: production performs no
        #     required-account schema validation. enrich_identity_with_account()
        #     returns identity unchanged, _sync_creatives_impl succeeds — no
        #     INVALID_REQUEST is ever raised.
        #   - invalid_oneOf_both / both account_id and brand: the adcp library
        #     AccountReference union raises a Pydantic ValidationError at parse
        #     time, which production does not translate into
        #     AdCPError(INVALID_REQUEST, suggestion) — same C4 gap as UC-004.
        # strict=True forces marker removal the moment a transport-boundary
        # translator + required-account validation lands. See
        # docs/test-debt-bdd-strict-markers.md items B7 and C4.
        _UC006_VALIDATION_XFAIL: list[tuple[str, set[str], str]] = [
            (
                "T-UC-006-partition-account",
                {"missing_account", "invalid_oneOf_both"},
                "production performs no required-account validation and does not "
                "translate Pydantic ValidationError into AdCPError(INVALID_REQUEST, "
                "suggestion). See docs/test-debt-bdd-strict-markers.md items B7 and C4.",
            ),
            (
                "T-UC-006-boundary-account",
                {"account field absent", "both account_id and brand"},
                "production performs no required-account validation and does not "
                "translate Pydantic ValidationError into AdCPError(INVALID_REQUEST, "
                "suggestion). See docs/test-debt-bdd-strict-markers.md items B7 and C4.",
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

        # UC-004 reporting_dimensions breakdowns (by_geo, by_device_type, by_audience):
        # PackageDelivery model only declares by_placement. The optional fields
        # by_geo, by_device_type, by_audience and their *_truncated siblings are not
        # on the model, and media_buy_delivery._impl only branches on req.reporting_dimensions.placement.
        # Tracked by salesagent-zk1: feat: implement reporting_dimensions breakdowns
        # (geo, device_type, audience) per BR-RULE-091.
        _UC004_DIM_XFAIL_TAGS: dict[str, tuple[str, bool]] = {
            "T-UC-004-dim-supported": (
                "by_device_type breakdown not implemented — see salesagent-zk1: "
                "feat: implement reporting_dimensions breakdowns (geo, device_type, audience) per BR-RULE-091",
                True,
            ),
            "T-UC-004-dim-truncated": (
                "by_geo breakdown + truncation flag not implemented — see salesagent-zk1: "
                "feat: implement reporting_dimensions breakdowns (geo, device_type, audience) per BR-RULE-091",
                True,
            ),
            "T-UC-004-dim-complete": (
                "by_device_type breakdown + truncation flag not implemented — see salesagent-zk1: "
                "feat: implement reporting_dimensions breakdowns (geo, device_type, audience) per BR-RULE-091",
                True,
            ),
            "T-UC-004-dim-multi": (
                "by_geo and by_device_type breakdowns not implemented — see salesagent-zk1: "
                "feat: implement reporting_dimensions breakdowns (geo, device_type, audience) per BR-RULE-091",
                True,
            ),
        }
        for tag, (reason, strict) in _UC004_DIM_XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                break

        # UC-004 status filter: selection by persisted status now works
        # (salesagent-18h.1). The remaining xfails are unrelated:
        #   - pending_activation: invalid MediaBuyStatus enum value in the
        #     Gherkin Examples table (debt-doc B1, separate test rewrite)
        #   - paused/completed: the *response* delivery status is still
        #     date-derived, so then_only_status sees "active" for a buy
        #     persisted as paused/completed even though it is selected
        #     correctly (response-status-display gap — see debt-doc B1).
        _UC004_FILTER_SELECTIVE: list[tuple[str, set[str], str]] = [
            (
                "T-UC-004-filter",
                {"pending_activation", "paused", "completed"},
                "response delivery status still date-derived (selection fixed in salesagent-18h.1)",
            ),
        ]
        if any(t.startswith("T-UC-004-filter") for t in marker_names):
            for tag, substrings, reason in _UC004_FILTER_SELECTIVE:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # UC-004 daterange-end-only: moved to _UC004_GENUINE_XFAIL_ROWS below
        # as strict=True (salesagent-losz / debt C7 — end-only date_range defaults
        # to today-30d instead of MediaBuy.created_at). The previous
        # _UC004_DATE_SELECTIVE block was the last vacuous-tolerance state on
        # daterange and has been retired.

        # Per-row strict xfails for the partition/boundary scenarios whose
        # blanket markers were removed. Each entry corresponds to a row in
        # the Examples table that genuinely xfails because of a real
        # production gap. strict=True ensures we are forced to remove the
        # marker the moment the gap is closed (e.g., when ValidationError
        # gets translated into AdCPError(INVALID_REQUEST) at the transport
        # boundary, the partition tests will start xpassing → fail-strict
        # → marker removal). Tracked centrally in
        # docs/test-debt-bdd-strict-markers.md.
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
            (
                "T-UC-004-boundary-date-range",
                {"start_date after end_date", "start_date equals end_date"},
                "production does not validate start_date>=end_date (same gap as "
                "T-UC-004-daterange-invalid/-equal). See docs/test-debt-bdd-strict-markers.md item C4.",
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
                    "mcp-account_not_found",
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
            (
                "T-UC-004-boundary-ownership",
                {"principal differs from owner"},
                "cross-principal access returns 200+empty instead of "
                "AdCPError(MEDIA_BUY_NOT_FOUND). See docs/test-debt-bdd-strict-markers.md item C3.",
            ),
            # status-filter (salesagent-6vu): all valid single statuses +
            # arrays + (field absent) pass. pending_activation rows fail
            # (Gherkin uses a non-spec MediaBuyStatus — item B1); empty-array /
            # unknown-value "failed" rows raise ValidationError not
            # AdCPError(INVALID_REQUEST) — item C4.
            # partition: single_pending / unknown_value(failed) / empty_array
            # fail on all 4 transports, so plain substrings are exact.
            (
                "T-UC-004-partition-status-filter",
                {"single_pending", "empty_array", "unknown_value"},
                "single_pending: Gherkin 'pending_activation' is not a valid AdCP "
                "MediaBuyStatus (item B1). empty_array/unknown_value: ValidationError "
                "not AdCPError(INVALID_REQUEST) (item C4). See docs/test-debt-bdd-strict-markers.md.",
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
        # 18h.10 Phase-2 (salesagent-6vu/1pl/8n9/03q/lghk/lzf3/f8u4): all
        # seven remaining UC-004 boundary fields reconciled. Their When steps
        # now translate the descriptor into real request kwargs/setup; valid
        # rows pass with no marker; genuinely-failing rows carry a strict=True
        # entry in _UC004_GENUINE_XFAIL_ROWS (credentials fully passes — no
        # entry). The blanket strict=False set is now empty for UC-004
        # boundary; kept as a documented anchor for future fields.
        _UC004_BOUNDARY_TAGS: set[str] = set()
        if marker_names & _UC004_BOUNDARY_TAGS:
            item.add_marker(pytest.mark.xfail(reason="boundary validation partially implemented", strict=False))

        # UC-004 partition scenarios: adcp 3.10 changed schema validation behavior.
        # Partition tests exercise valid/invalid value ranges per field.
        # strict=False: some partition values pass, others fail depending on schema version.
        # 18h.10 Phase-2 (salesagent-6vu/1pl/8n9/03q/lghk/lzf3/f8u4): all
        # seven remaining UC-004 partition fields reconciled (see the boundary
        # note above). The blanket strict=False set is now empty for UC-004
        # partition; kept as a documented anchor for future fields.
        _UC004_PARTITION_TAGS: set[str] = set()
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
    """Detect which delivery harness a UC-004 scenario needs.

    The ``circuit-breaker`` env wraps :class:`WebhookDeliveryService`, the
    real production code path, which emits ``X-ADCP-Signature`` /
    ``Authorization: Bearer`` headers and implements proper retry/backoff
    timing. Scenarios that exercise webhook authentication or retry/backoff
    MUST go through this env — ``WebhookEnv`` routes through the legacy
    ``deliver_webhook_with_retry`` function which emits the wrong header
    name (``X-Webhook-Signature``) and has different retry timing.
    """
    marker_names = {m.name for m in request.node.iter_markers()}
    if "webhook-reliability" in marker_names:
        return "circuit-breaker"
    # Auth-scheme scenarios (HMAC, Bearer) verify production-emitted headers
    # and must use the real WebhookDeliveryService path, not WebhookEnv.
    if "T-UC-004-webhook-hmac" in marker_names or "T-UC-004-webhook-bearer" in marker_names:
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
            request.getfixturevalue("integration_db")
            from tests.harness.delivery_webhook import WebhookEnv

            with WebhookEnv() as env:
                ctx["env"] = env
                yield
        elif harness_type == "circuit-breaker":
            request.getfixturevalue("integration_db")
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
