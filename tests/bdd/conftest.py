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
"""

from __future__ import annotations

import re

import pytest

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
