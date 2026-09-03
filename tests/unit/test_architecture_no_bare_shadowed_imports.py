"""Structural guard: no bare import shadowing a real src/-namespaced module (GH #1802).

``XandrAdapter._create_human_task`` (src/adapters/xandr.py) did
``from database_session import get_db_session`` and
``from slack_notifier import get_slack_notifier`` — both collide with real
``src/**/<name>.py`` module basenames (``src.core.database.database_session``,
``src.services.slack_notifier``), but no bare module exists at the repo root
under either name, so both raised ``ModuleNotFoundError`` on first real call.
CLAUDE.md mandates absolute ``src.*`` imports; this is the shape that
violation takes when it silently breaks rather than merely looking wrong.

A whole-``src/`` sweep (GH #1802's disease-scan atom) found 4 more
live instances in ``src/admin/sync_api.py`` (bare ``gam_orders_service``
imports), tracked and fixed separately as GH #1802. This guard was
originally scoped to ``src/adapters/`` only, with its own docstring
committing to widen to all of ``src/`` once vwbj's fix landed — done here:
vwbj's disease-scan and sweep-verify re-scans both found zero violations
repo-wide post-fix, so the widened scope needs no allowlist entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import repo_root

_SRC_DIR = repo_root() / "src"


def _real_src_basenames(repo: Path) -> set[str]:
    """Every real module basename under src/ (e.g. 'database_session', 'slack_notifier')."""
    basenames: set[str] = set()
    for path in (repo / "src").rglob("*.py"):
        if path.stem != "__init__":
            basenames.add(path.stem)
    return basenames


def find_bare_shadowed_imports(tree: ast.Module, basenames: set[str]) -> list[tuple[int, str]]:
    """Return (lineno, module) for every level-0 ImportFrom whose top module shadows *basenames*.

    A "shadow" is a bare import (``level == 0``, no leading dots) whose
    top-level module component matches a real ``src/**/<name>.py`` basename
    exactly — the only way this fires false-positive is a genuinely
    third-party package that happens to share a name with an internal module,
    which is what the allowlist below is for.
    """
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top = node.module.split(".")[0]
            if top != "src" and top in basenames:
                violations.append((node.lineno, node.module))
    return violations


# Allowlist can only shrink. Every entry needs a live FIXME at the source line
# (CLAUDE.md: reference a GitHub issue/PR number, never a local beads id).
ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


class TestNoBareShadowedImportsInSrc:
    def test_no_bare_shadowed_imports(self) -> None:
        basenames = _real_src_basenames(repo_root())
        violations: list[str] = []
        for path in sorted(_SRC_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = str(path.relative_to(repo_root()))
            for lineno, module in find_bare_shadowed_imports(tree, basenames):
                if (rel, lineno) in ALLOWLIST:
                    continue
                violations.append(
                    f"{rel}:{lineno}: bare 'from {module} import ...' shadows a real src/ module — "
                    f"use its absolute 'src.<package>.{module}' import path (CLAUDE.md import convention)"
                )
        assert not violations, "Bare imports shadowing real src/ modules found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Meta-tests: the live detector catches known-bad and ignores known-good code
# ---------------------------------------------------------------------------

_SYNTHETIC_BASENAMES = {"database_session", "slack_notifier", "gam_orders_service"}

_SYNTHETIC_BAD = """
def _create_human_task(self):
    from database_session import get_db_session
    from slack_notifier import get_slack_notifier
    from src.core.database.models import Tenant
"""

_SYNTHETIC_GOOD = """
def _create_human_task(self):
    from src.core.database.database_session import get_db_session
    from src.services.slack_notifier import get_slack_notifier
    from src.core.database.models import Tenant
    import os
    from unrelated_third_party_package import Something
"""


def test_detector_catches_bare_shadowed_imports() -> None:
    """The live detector reports exactly the two bare, shadowing imports."""
    violations = find_bare_shadowed_imports(ast.parse(_SYNTHETIC_BAD), _SYNTHETIC_BASENAMES)
    assert sorted(module for _, module in violations) == ["database_session", "slack_notifier"]


def test_detector_ignores_absolute_and_unrelated_imports() -> None:
    """Absolute src.* imports and non-shadowing third-party imports are not flagged."""
    violations = find_bare_shadowed_imports(ast.parse(_SYNTHETIC_GOOD), _SYNTHETIC_BASENAMES)
    assert violations == []
