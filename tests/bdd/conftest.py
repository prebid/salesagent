"""
BDD test configuration and fixtures.

Harness Bridge Pattern
----------------------
Domain step definitions (tests/bdd/steps/domain/) import helper functions
and fixtures from the shared test harness (tests/harness/). This keeps
step definitions thin — they translate Gherkin phrases into harness calls —
while the harness owns the actual setup/teardown logic, factories, and
assertion helpers. Generic steps (tests/bdd/steps/generic/) are pure
pytest-bdd and have no domain or harness dependencies.

Harness Mode
------------
When the ``creative_formats_env`` fixture is requested (via ``integration_db``
marker), the BDD scenario runs against real production code through the
CreativeFormatsEnv harness. Generic steps detect ``ctx["env"]`` and delegate
to the harness instead of using stub logic.

When ``creative_formats_env`` is NOT requested (partition/boundary stubs),
the steps fall back to pure-dict manipulation in ctx — no database needed.
"""

from __future__ import annotations

import re
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
# Feature files use Gherkin @tags that pytest-bdd converts to pytest markers.
# With --strict-markers these must be declared. Since tags are auto-generated
# by compile_bdd.py, we register them dynamically by scanning .feature files
# rather than maintaining a manual list in pytest.ini.


def pytest_configure(config: pytest.Config) -> None:
    """Register BDD tag markers dynamically.

    Scans all .feature files under tests/bdd/features/ and registers
    every @tag as a pytest marker so --strict-markers is satisfied.
    """
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


@pytest.fixture()
def ctx():
    """Per-scenario mutable context shared across Given/When/Then steps.

    Each scenario gets a fresh dict. Steps store intermediate state here
    (e.g., API responses, created object IDs) so that later steps can
    make assertions against it.
    """
    return {}


@pytest.fixture(autouse=True)
def _maybe_creative_formats_env(request: pytest.FixtureRequest, ctx: dict) -> None:
    """Conditionally provide CreativeFormatsEnv for harness-backed BDD scenarios.

    Activates harness mode when a real PostgreSQL database is available
    (i.e., the ``integration_db`` fixture can be resolved) AND the scenario
    is tagged with a category that should use the harness.

    Phase 1 wires these scenario categories:
      - main-flow (3): full catalog REST/MCP, filtered (referrals excluded)
      - invariant (17): BR-RULE-031 (3) + BR-RULE-049 INV-1 through INV-7 (14)
      - edge-case (1): empty-catalog
      - boundary (1): dim-boundary
      - extension ext-b (2): invalid params REST/MCP

    Partition and sandbox scenarios stay as stubs (no database needed).

    When active, opens CreativeFormatsEnv, creates default data, and stores
    it in ``ctx["env"]``. Steps detect this and route through real code.

    When inactive (no DB, or stub scenarios), ctx["env"] remains unset.
    """
    # Scenario-level tags that should use the harness.
    # Phase 1 wires ~31 scenarios. Tags listed here correspond to
    # feature file @T-UC-005-* tags converted to pytest markers (with
    # hyphens converted to hyphens — pytest-bdd preserves them).
    #
    # Exclude: partition stubs, ext-a (no-tenant error path),
    # sandbox, new-filter invariants (inv-049-8/9/10), and
    # new-filter ext-b validation (disclosure/output/input).
    _HARNESS_SCENARIO_TAGS = {
        # Main flow (3 — referrals excluded: mock registry doesn't
        # configure _get_tenant_agents, so creative_agents would be
        # empty. Wire when harness gains agent-referral support.)
        "T-UC-005-main-rest",
        "T-UC-005-main-mcp",
        "T-UC-005-main-filtered",
        # Invariant: BR-RULE-031 (3)
        "T-UC-005-inv-031-1-holds",
        "T-UC-005-inv-031-1-violated",
        "T-UC-005-inv-031-2-holds",
        # Invariant: BR-RULE-049 INV-1 through INV-7 (14)
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
        # Edge cases (1)
        "T-UC-005-empty-catalog",
        # Boundary (1)
        "T-UC-005-dim-boundary",
        # Extension B basic (2)
        "T-UC-005-ext-b-rest",
        "T-UC-005-ext-b-mcp",
    }

    marker_names = {m.name for m in request.node.iter_markers()}
    should_use_harness = bool(marker_names & _HARNESS_SCENARIO_TAGS)

    if not should_use_harness:
        yield
        return

    # Try to resolve integration_db; if unavailable, fall back to stub mode
    try:
        request.getfixturevalue("integration_db")
    except pytest.FixtureLookupError:
        yield
        return

    from tests.harness.creative_formats import CreativeFormatsEnv

    with CreativeFormatsEnv() as env:
        env.setup_default_data()
        ctx["env"] = env
        yield
