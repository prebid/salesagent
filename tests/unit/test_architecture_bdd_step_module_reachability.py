"""Guard: every BDD step module that defines steps must be reachable.

pytest-bdd only discovers step definitions from modules registered as plugins.
``tests/bdd/conftest.py`` lists them in ``pytest_plugins``. A module under
``tests/bdd/steps/`` that defines ``@given/@when/@then`` steps but is NOT in
``pytest_plugins`` is imported by nothing — every one of its step definitions is
dead, and any scenario that needs one of those steps fails with
``StepDefinitionNotFoundError`` (auto-xfailed by the conftest hook). The dead
steps also silently rot out of sync with the live generic steps.

Detection approach: behavioral, not source-text. We import each candidate module
and check for the ``pytestbdd_stepdef_*`` fixtures pytest-bdd creates at import
time. A module with zero step fixtures (e.g. an empty stub) is not flagged —
only modules that genuinely define steps must be registered.

beads: salesagent-mdhh
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist

_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"
_CONFTEST = _STEPS_DIR.parent / "conftest.py"
_STEPDEF_PREFIX = "pytestbdd_stepdef_"

# Step-defining modules not in conftest's pytest_plugins. RATCHETING baseline —
# may only shrink, never grow. Two DISTINCT reasons an entry is allowed:
#
# (A) Dead-pending-harness: unregistered AND no per-UC harness exists, so the
#     scenarios xfail at the harness gate (conftest.py) regardless of
#     registration. Wiring now yields zero behavioral benefit and would turn
#     step-text collisions into live shadows (test_architecture_bdd_no_shadowed_steps).
#     Resolution: add the per-UC harness, resolve the collisions, register it,
#     and REMOVE the entry.
#     FIXME(salesagent-mdhh): wire each module + harness, then delete its entry.
#
# (B) Intentionally-local: the module IS live, but registered LOCALLY in its test
#     module (``from … import *``) rather than globally via pytest_plugins — on
#     purpose, so its intentional generic-step overrides stay scoped to that UC
#     instead of shadowing every other UC. Global registration would be a
#     regression, so this kind of entry is a PERMANENT exception, not pending work.
_ALLOWED_UNREGISTERED: set[str] = {
    # (A) dead-pending-harness:
    "tests.bdd.steps.domain.uc002_nfr",
    "tests.bdd.steps.domain.uc002_task_query",
    "tests.bdd.steps.domain.uc003_ext_error_scenarios",
    "tests.bdd.steps.domain.uc026_package_media_buy",
    "tests.bdd.steps.generic.then_media_buy",
    # (B) intentionally-local (live via test_uc019_query_media_buys.py `import *`;
    # kept out of pytest_plugins so its 8 generic-step overrides stay UC-019-scoped):
    "tests.bdd.steps.domain.uc019_query_media_buys",
}


def _registered_plugins() -> set[str]:
    """Return the dotted module names listed in conftest's pytest_plugins."""
    tree = ast.parse(_CONFTEST.read_text(), filename=str(_CONFTEST))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytest_plugins":
                    return {
                        el.value for el in node.value.elts if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    }
    raise AssertionError("pytest_plugins not found in tests/bdd/conftest.py")


def _dotted_name(py_file: Path) -> str:
    rel = py_file.relative_to(_STEPS_DIR.parent.parent.parent)
    return ".".join(rel.with_suffix("").parts)


def _defines_steps(dotted: str) -> bool:
    """True if importing the module yields any pytest-bdd step fixture."""
    module = importlib.import_module(dotted)
    return any(attr.startswith(_STEPDEF_PREFIX) for attr in vars(module))


def _scan_unregistered_step_modules() -> list[str]:
    """Return dotted names of all step-defining modules absent from pytest_plugins.

    The allowlist is NOT subtracted here — callers classify the result into
    new violations vs. allowlisted (still-dead) modules so stale allowlist
    entries can be detected.
    """
    registered = _registered_plugins()
    unregistered: list[str] = []
    for py_file in sorted(_STEPS_DIR.rglob("*.py")):
        if py_file.name == "__init__.py" or py_file.name.startswith("_"):
            continue
        dotted = _dotted_name(py_file)
        if dotted in registered:
            continue
        if _defines_steps(dotted):
            unregistered.append(dotted)
    return unregistered


class TestBddStepModuleReachability:
    """Structural guard: every step-defining module is in pytest_plugins."""

    @pytest.mark.arch_guard
    def test_unregistered_step_modules_match_allowlist(self) -> None:
        """Every step-defining module must be in pytest_plugins or the ratcheting allowlist.

        An unregistered step module's definitions are dead — scenarios needing
        them auto-xfail and the steps drift out of sync with live generic steps.
        """
        found = set(_scan_unregistered_step_modules())
        assert_violations_match_allowlist(
            found,
            _ALLOWED_UNREGISTERED,
            fix_hint=(
                "Register the module in tests/bdd/conftest.py pytest_plugins, "
                "or remove from _ALLOWED_UNREGISTERED when fixed."
            ),
        )
