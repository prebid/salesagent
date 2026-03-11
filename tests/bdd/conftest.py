"""
BDD test configuration and fixtures.

Every scenario runs against real production code through the CreativeFormatsEnv
harness. There is no stub mode — steps call the harness directly and assert on
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

# FIXME(beads-dul): disclosure_positions not a field on ListCreativeFormatsRequest.
# Partition/boundary outlines have "omitted" examples that pass (no filter used).
# Only xfail examples that actually exercise the unimplemented field.
_DISCLOSURE_PARTITIONS_XFAIL = {
    "single_position",
    "multiple_positions_all_match",
    "all_positions",
    "no_matching_formats",
}
_DISCLOSURE_BOUNDARY_XFAIL = {
    "single position",
    "all 8 positions",
    "format has no",
}

# FIXME(beads-dul): brief/catalog asset types not yet in adcp enum
_ASSET_TYPES_BOUNDARY_XFAIL = {"brief", "catalog"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply xfail markers to scenarios with unimplemented production features."""
    for item in items:
        marker_names = {m.name for m in item.iter_markers()}

        # Parametrized disclosure scenarios — selective xfail
        if "T-UC-005-partition-disclosure" in marker_names:
            node_id = item.nodeid
            if any(p in node_id for p in _DISCLOSURE_PARTITIONS_XFAIL):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="disclosure_positions filter not implemented",
                        strict=True,
                    )
                )
            continue
        if "T-UC-005-boundary-disclosure" in marker_names:
            node_id = item.nodeid
            if any(p in node_id for p in _DISCLOSURE_BOUNDARY_XFAIL):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="disclosure_positions filter not implemented",
                        strict=True,
                    )
                )
            continue
        if "T-UC-005-boundary-asset-types" in marker_names:
            node_id = item.nodeid
            if any(p in node_id for p in _ASSET_TYPES_BOUNDARY_XFAIL):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="brief/catalog asset types not in adcp enum",
                        strict=True,
                    )
                )
            continue

        # Tag-based xfail for all other scenarios
        for tag, reason in _XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ctx() -> dict:
    """Per-scenario mutable context shared across Given/When/Then steps.

    Keys used:
        env: CreativeFormatsEnv harness instance
        response: ListCreativeFormatsResponse from production code
        error: Exception raised by production code
    """
    return {}


@pytest.fixture(autouse=True)
def _creative_formats_env(request: pytest.FixtureRequest, ctx: dict) -> Generator[None, None, None]:
    """Provide CreativeFormatsEnv for every BDD scenario.

    Every scenario gets a real harness backed by PostgreSQL.
    No stub mode — if the database isn't available, the test fails.
    """
    request.getfixturevalue("integration_db")

    from tests.harness.creative_formats import CreativeFormatsEnv

    with CreativeFormatsEnv() as env:
        ctx["env"] = env
        yield
