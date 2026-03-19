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
        for match in re.finditer(r"@([\w-]+)", text):
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


# ---------------------------------------------------------------------------
# @pending graduation: vacuous vs genuine
# ---------------------------------------------------------------------------
# Partition/boundary scenarios for fields NOT in GetMediaBuyDeliveryRequest.
# The DeliveryPollMixin.call_impl() drops these fields → test exercises the
# default code path → vacuous pass.  Keep xfailed with an honest reason.
_PENDING_VACUOUS_TAGS: set[str] = {
    "T-UC-004-partition-sampling",
    "T-UC-004-boundary-sampling",
    "T-UC-004-partition-credentials",
    "T-UC-004-boundary-credentials",
    # Boundary tags for fields NOT in schema — impl rejects with ValueError.
    # Partition tags for these are in _PENDING_GRADUATED_TAGS because "omitted"
    # rows don't send the field, so they genuinely exercise the default path.
    "T-UC-004-boundary-ownership",
    "T-UC-004-boundary-date-range",
    "T-UC-004-boundary-resolution",
}

# Partition/boundary tags for fields that ARE in the schema.
# "Valid" example rows for these scenarios genuinely test production code.
_PENDING_GRADUATED_TAGS: set[str] = {
    # Fields in schema — partition/boundary valid rows pass all 4 transports.
    # Graduated by scripts/graduate_pending.py from JSON test results.
    "T-UC-004-partition-reporting-dims",
    "T-UC-004-boundary-reporting-dims",
    "T-UC-004-partition-attribution",
    "T-UC-004-boundary-attribution",
    "T-UC-004-partition-daily-breakdown",
    "T-UC-004-boundary-daily-breakdown",
    "T-UC-004-partition-account",
    "T-UC-004-boundary-account",
    "T-UC-004-partition-status-filter",
    # Fields NOT in schema — partition valid rows pass because "omitted/not_provided"
    # rows don't actually send the field, so the mixin ValueError doesn't fire.
    # Boundary tags for these are UNSAFE (some rows fail on impl).
    "T-UC-004-partition-date-range",
    "T-UC-004-partition-ownership",
    "T-UC-004-partition-resolution",
}

# Tags where ALL rows pass all 4 transports — no valid/invalid split needed.
# These get NO xfail marker at all (fully graduated).
_PENDING_FULLY_GRADUATED_TAGS: set[str] = {
    "T-UC-004-webhook-retry-5xx",
}


def _is_graduated_valid_row(marker_names: set[str], nodeid: str) -> bool:
    """Check if a @pending scenario row is a genuinely passing "valid" test.

    For graduated partition/boundary scenarios: rows where the expected outcome
    is "valid" are genuinely tested (field is in schema, production accepts it).
    Rows where expected is "invalid" or "error" stay xfailed.
    """
    if not (marker_names & _PENDING_GRADUATED_TAGS):
        return False
    # Partition/boundary node_ids end with: -expected]
    # e.g. [impl-omitted-(field absent)-valid]
    # e.g. [impl-interval_zero-{...}-error "INVALID_REQUEST" with suggestion]
    # e.g. [impl-model=last_click (not in enum)-{...}-invalid]
    return nodeid.endswith("-valid]")


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

        # Transport-specific xfails: REST drops all filter params
        if is_rest:
            for tag in _REST_XFAIL_TAGS:
                if tag in marker_names:
                    item.add_marker(pytest.mark.xfail(reason="REST endpoint drops filter params", strict=True))
                    break

        # Selective xfail for parametrized scenarios
        for tag, substrings, reason in _SELECTIVE_XFAIL:
            if tag in marker_names:
                if any(s in item.nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break  # tag matched — skip remaining selective entries

        # @pending scenarios — graduated xfail handling.
        #
        # Three categories:
        # 1. Vacuous: field not in GetMediaBuyDeliveryRequest schema.
        #    The mixin drops the field → test exercises default code path.
        #    xfail(strict=False) with honest reason.
        #
        # 2. Genuinely testable partition/boundary "valid" rows:
        #    Field IS in schema, production accepts it, test passes for real.
        #    NO xfail → runs as normal PASS.
        #
        # 3. Everything else (invalid/error rows, non-partition scenarios
        #    for unimplemented features): xfail(strict=True).
        if "pending" in marker_names:
            if marker_names & _PENDING_VACUOUS_TAGS:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="field not in request schema — mixin drops it, vacuous pass",
                        strict=False,
                    )
                )
            elif marker_names & _PENDING_FULLY_GRADUATED_TAGS:
                pass  # No xfail — all rows pass all transports
            elif _is_graduated_valid_row(marker_names, nodeid):
                pass  # No xfail — valid rows pass all transports
            else:
                # Remaining @pending rows: validation not implemented, feature not
                # wired, or transport-specific gap.  strict=False keeps existing
                # behavior — these xpass on some transports which is acceptable.
                item.add_marker(
                    pytest.mark.xfail(
                        reason="pending: production feature or validation not implemented",
                        strict=False,
                    )
                )

        # Tag-based xfail for all other scenarios
        for tag, reason in _XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break

        # --- Entity marker auto-application based on BDD tags ---
        # BDD tests don't have entity keywords in filenames; instead they
        # use tags like T-UC-004-* (delivery) and T-UC-005-* (creative).
        if any(t.startswith("T-UC-004") for t in marker_names):
            item.add_marker(pytest.mark.delivery)
        if any(t.startswith("T-UC-005") for t in marker_names):
            item.add_marker(pytest.mark.creative)


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Multi-transport dispatch
# ---------------------------------------------------------------------------
# Tags that indicate a scenario already dispatches through a specific transport.
# These scenarios must NOT be multiplied — they have explicit When steps.
_TRANSPORT_SPECIFIC_TAGS = {"rest", "mcp", "a2a"}


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
    if any(t.startswith("T-UC-005") for t in marker_names):
        return "UC-005"
    if any(t.startswith("T-UC-004") for t in marker_names):
        return "UC-004"
    return None


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

    if uc == "UC-005":
        request.getfixturevalue("integration_db")
        from tests.harness.creative_formats import CreativeFormatsEnv

        with CreativeFormatsEnv() as env:
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
            from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

            with CircuitBreakerEnv() as env:
                ctx["env"] = env
                yield
        else:
            yield
    else:
        yield
