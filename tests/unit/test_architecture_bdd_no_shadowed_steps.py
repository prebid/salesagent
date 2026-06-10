"""Guard: BDD step text must not be registered by 2+ imported step modules.

pytest-bdd turns each ``@given/@when/@then`` into a module-level fixture named
``pytestbdd_stepdef_<type>_<parsed text>``. When two modules that are BOTH
listed in ``tests/bdd/conftest.py``'s ``pytest_plugins`` register the same step
text, the two fixtures collide by name. pytest resolves the collision by plugin
registration order, so ONE definition wins and the other — which usually carries
*different* verification logic — is silently dead. Scenarios then run against a
verification body the author never intended.

Unlike ``test_architecture_bdd_no_duplicate_steps`` (which flags identical
*bodies* regardless of registration), this guard flags identical *step text*
registered by 2+ *imported* modules — the live-shadowing failure mode.

Detection approach: behavioral, not source-text. We import every module listed
in ``pytest_plugins`` and read the real ``pytestbdd_stepdef_*`` fixtures
pytest-bdd created at import time — exactly the objects pytest-bdd uses for step
resolution. A collision in this set is a collision pytest-bdd will actually hit.

beads: salesagent-g4cm
"""

from __future__ import annotations

import ast
import importlib
from collections import defaultdict
from pathlib import Path

_CONFTEST = Path(__file__).resolve().parents[1] / "bdd" / "conftest.py"

# pytest-bdd names every step fixture with this prefix.
_STEPDEF_PREFIX = "pytestbdd_stepdef_"

# Step texts intentionally registered by 2+ imported modules. MUST stay empty —
# a shadowed step means one verification body is dead. Fix the collision (pick a
# winner, delete the redundant one, or give the domain step distinct text)
# rather than allowlisting it.
_ALLOWED_SHADOWS: set[str] = set()


def _registered_step_plugins() -> list[str]:
    """Return the ``pytest_plugins`` step modules declared in bdd/conftest.py."""
    tree = ast.parse(_CONFTEST.read_text(), filename=str(_CONFTEST))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytest_plugins":
                    return [
                        el.value for el in node.value.elts if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    ]
    raise AssertionError("pytest_plugins not found in tests/bdd/conftest.py")


def _scan_shadowed_steps() -> dict[str, list[str]]:
    """Map each step-fixture name to the imported modules that register it.

    Only names registered by 2+ modules (i.e. genuine shadows) are returned.
    """
    registry: dict[str, list[str]] = defaultdict(list)
    for dotted in _registered_step_plugins():
        module = importlib.import_module(dotted)
        short = dotted.split(".")[-1]
        for attr in vars(module):
            if attr.startswith(_STEPDEF_PREFIX):
                registry[attr].append(short)

    return {
        name[len(_STEPDEF_PREFIX) :]: mods
        for name, mods in registry.items()
        if len(mods) > 1 and name[len(_STEPDEF_PREFIX) :] not in _ALLOWED_SHADOWS
    }


class TestBddNoShadowedSteps:
    """Structural guard: no step text registered by 2+ imported step modules."""

    def test_no_shadowed_step_registrations(self):
        """Every step text must be registered by at most one imported module.

        A step text registered by 2+ ``pytest_plugins`` modules means pytest-bdd
        silently picks one definition by registration order and discards the
        other's verification logic.
        """
        shadows = _scan_shadowed_steps()
        if not shadows:
            return

        lines = [f"\n  {text!r} registered by: {mods}" for text, mods in sorted(shadows.items())]
        assert not shadows, (
            f"Found {len(shadows)} step text(s) registered by 2+ imported modules "
            f"(silent shadowing — one verification body is dead):" + "".join(lines)
        )
