"""The properties that ARE the test flag are enumerated, and the list only shrinks.

``.ast-grep/rules/test-flag-never-reaches-production.yml`` bans reading ``adcp_testing``
anywhere under ``src/`` and exempts one file, ``src/core/config.py``, because that file
declares the field. It also holds every property whose whole body is
``return self.testing.adcp_testing``, and those are not settled exceptions -- each one is a
production behavior that forks on whether a suite is running, which is the bug the rule
exists to stop. Exempting the file is what keeps the ban expressible as one rule; this test
is what keeps the exemption honest.

Two directions, both enforced:

* a NEW property reading the flag fails here, so the rule cannot be routed around by adding
  one more line to the exempt file; and
* removing one forces this list down, so the set cannot quietly stay at seven while looking
  tracked.

GH #2255 owns emptying it. Each entry needs a real input instead -- a tenant column, an
AdapterConfig row, a settings field a deployment can set -- and then the tests seed that
input like any other state.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _REPO_ROOT / "src" / "core" / "config.py"

#: Properties on ``Settings`` whose value IS ``testing.adcp_testing``. Frozen at the count
#: measured when the ban landed; can only shrink. ``relaxed_brand_validation`` is listed
#: with zero readers deliberately: a fork nothing reads is still a fork, and it is the
#: cheapest one to delete.
TEST_FLAG_PROPERTIES: frozenset[str] = frozenset(
    {
        "debug_routes_enabled",
        "loopback_webhooks_allowed",
        "mock_adapter_counts_as_configured",
        "mock_delivery_seed_enabled",
        "reference_formats_only",
        "relaxed_brand_validation",
        "unit_tests_may_not_open_a_database",
    }
)


def _properties_reading_the_flag() -> set[str]:
    """Every function in config.py whose body reads ``adcp_testing``.

    Reads the file rather than importing it: the question is which properties EXIST, and a
    walk of the source answers that without constructing a Settings or touching the
    environment.
    """
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"), filename=str(_CONFIG))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and inner.attr == "adcp_testing":
                found.add(node.name)
                break
    return found


def test_no_new_property_reads_the_test_flag() -> None:
    """A new fork on the flag is a new bug, and the exempt file is not a loophole."""
    added = _properties_reading_the_flag() - TEST_FLAG_PROPERTIES
    assert not added, (
        f"These properties in src/core/config.py newly read the test flag: {sorted(added)}.\n"
        "Production behavior must not branch on whether a suite is running. Give the behavior a "
        "real input a deployment can set -- a tenant column, an AdapterConfig row, a settings "
        "field -- and let the test seed it (CLAUDE.md pattern 11, GH #2255)."
    )


def test_the_list_only_shrinks() -> None:
    """When a fork is removed, this list comes down with it."""
    gone = TEST_FLAG_PROPERTIES - _properties_reading_the_flag()
    assert not gone, (
        f"These no longer read the test flag and must be dropped from TEST_FLAG_PROPERTIES: "
        f"{sorted(gone)}.\nThe list is the outstanding count; a stale entry makes seven look "
        "like the number when it is fewer."
    )
