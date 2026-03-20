"""
BDD test configuration and fixtures.

Every scenario runs against real production code through harness environments:
  - UC-005 (Creative Formats): CreativeFormatsEnv
  - UC-004 (Delivery Metrics): DeliveryPollEnv / WebhookEnv / CircuitBreakerEnv

There is no stub mode — steps call the harness directly and assert on
real response objects.

Scenarios for unimplemented production features are marked ``xfail``.
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
]

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

        # --- UC-004 xfail: @pending scenarios have systemic factory bug ---
        # FIXME(beads-tbd): UC-004 BDD _ensure_media_buy_in_db uses non-existent
        # TenantFactory.create_sync — all @pending delivery scenarios are broken.
        if any(t.startswith("T-UC-004") for t in marker_names) and "pending" in marker_names:
            item.add_marker(pytest.mark.xfail(reason="UC-004 BDD factory bug (create_sync)", strict=False))

        # --- UC-011 xfail: scenarios pending step definitions ---
        # Tags implemented: @list (slice 1), sync core scenarios (slice 2)
        _UC011_IMPLEMENTED_TAGS = {
            # Slice 2: sync core
            "T-UC-011-sync-create",
            "T-UC-011-sync-multi-brand",
            "T-UC-011-sync-brand-direct",
            "T-UC-011-sync-update",
            "T-UC-011-sync-unchanged",
            "T-UC-011-sync-billing-enum",
            "T-UC-011-sync-mixed",
            "T-UC-011-sync-brand-echo",
            "T-UC-011-sync-shortest-domain",
            # Slice 3: auth + atomic XOR
            "T-UC-011-ext-a-no-token",
            "T-UC-011-ext-a-expired",
            "T-UC-011-atomic-error",
            "T-UC-011-atomic-service-error",
            "T-UC-011-atomic-both",
            "T-UC-011-atomic-success",
            "T-UC-011-atomic-all-failed",
            # Slice 4: billing policy + partial failure
            "T-UC-011-ext-b-partial",
            "T-UC-011-ext-c-rejected",
            "T-UC-011-ext-c-mixed",
            "T-UC-011-ext-c-invalid-enum",
            # Slice 5: approval workflow + push
            "T-UC-011-ext-d-pending-url",
            "T-UC-011-ext-d-pending-message",
            "T-UC-011-ext-d-active",
            "T-UC-011-ext-d-push",
            # Slice 6: dry_run + delete_missing
            "T-UC-011-ext-e-preview",
            "T-UC-011-ext-e-normal",
            "T-UC-011-ext-e-omitted",
            "T-UC-011-ext-f-deactivate",
            "T-UC-011-ext-f-scoped",
            "T-UC-011-ext-f-false",
            "T-UC-011-ext-f-none-absent",
            "T-UC-011-ext-f-omitted",
            # Slice 7: context echo + validation + schema + sandbox
            "T-UC-011-ext-g-echo",
            "T-UC-011-ext-g-echo-error",
            "T-UC-011-ext-g-absent",
            "T-UC-011-ext-g-empty",
            "T-UC-011-ext-g-nested",
            "T-UC-011-sync-empty-accounts",
            "T-UC-011-sync-missing-brand",
            "T-UC-011-sync-missing-operator",
            "T-UC-011-sync-missing-billing",
            "T-UC-011-sync-invalid-patterns",
            "T-UC-011-sync-accounts-bva",
            "T-UC-011-sandbox-provision",
            "T-UC-011-sandbox-list-filter",
            "T-UC-011-sandbox-validation",
        }
        if any(t.startswith("T-UC-011") for t in marker_names):
            is_list = "list" in marker_names
            is_implemented_sync = bool(marker_names & _UC011_IMPLEMENTED_TAGS)
            # FIXME(salesagent-pnc): remaining sync scenarios not yet implemented
            if not is_list and not is_implemented_sync:
                item.add_marker(pytest.mark.xfail(reason="UC-011 steps not implemented", strict=True))

        # --- UC-002: @pending scenarios need step definitions ---
        # Account resolution scenarios are implemented (salesagent-2rq)
        _UC002_IMPLEMENTED_TAGS = {
            "T-UC-002-ext-r",
            "T-UC-002-ext-r-nk",
            "T-UC-002-ext-s",
            "T-UC-002-ext-t",
            # INV-080-1 excluded: tests INVALID_REQUEST (schema-level, not resolve_account)
            "T-UC-002-partition-account-ref",
            "T-UC-002-boundary-account-ref",
        }
        # FIXME(salesagent-2rq): INVALID_REQUEST validation (missing account / both fields)
        # is Pydantic-level oneOf validation, not resolve_account. INV-080-1 is same.
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
            # Selective xfail for INVALID_REQUEST examples within implemented tags
            for tag, substrings, reason in _UC002_VALIDATION_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break
            is_implemented = bool(marker_names & _UC002_IMPLEMENTED_TAGS)
            if "pending" in marker_names and not is_implemented:
                item.add_marker(pytest.mark.xfail(reason="UC-002 BDD steps not yet implemented", strict=True))

        # --- UC-006: @pending scenarios need step definitions ---
        # Account resolution scenarios are implemented (salesagent-71q)
        _UC006_IMPLEMENTED_TAGS = {
            "T-UC-006-partition-account",
            "T-UC-006-boundary-account",
        }
        # FIXME(salesagent-71q): INVALID_REQUEST validation (missing account / both fields)
        # is Pydantic-level oneOf validation, not resolve_account.
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
            # Selective xfail for INVALID_REQUEST examples within implemented tags
            for tag, substrings, reason in _UC006_VALIDATION_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break
            is_implemented = bool(marker_names & _UC006_IMPLEMENTED_TAGS)
            if "pending" in marker_names and not is_implemented:
                item.add_marker(pytest.mark.xfail(reason="UC-006 BDD steps not yet implemented", strict=True))

        # --- Admin BDD: @pending scenarios need step definitions ---
        if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
            if "pending" in marker_names:
                item.add_marker(pytest.mark.xfail(reason="Admin BDD steps not yet implemented", strict=True))

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
        request.getfixturevalue("integration_db")
        from tests.harness.media_buy_account import MediaBuyAccountEnv

        with MediaBuyAccountEnv() as env:
            ctx["env"] = env
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

            with DeliveryPollEnv() as env:
                ctx["env"] = env
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
            yield
    else:
        yield
