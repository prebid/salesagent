"""Guard: no two registered BDD step defs may share one parse literal.

Regression for #1417: two ``@given`` defs registered the identical
parse expression ``the Buyer owns an existing media buy with media_buy_id
"..."`` (the ``{media_buy_id}`` vs ``{mb_id}`` param name is irrelevant to
pytest-bdd matching — the literal collides). First registration wins, so the
second def is permanently dead code: harmless until a resolution-order change
silently flips which body runs (e.g. flipping a seeded status and breaking a
value pin). This guard AST-scans every step module registered in
``tests/bdd/conftest.py`` ``pytest_plugins`` and fails when two step defs of
the same kind (given/when/then) normalize to the same parse literal.

Complements ``test_architecture_bdd_no_duplicate_steps`` (identical BODIES);
this one catches identical LITERALS with different bodies — the shadowing
case, which is strictly more dangerous because one body silently never runs.

Scope (#1417 round-8 review item 8): registered modules are checked for collisions
ACROSS the whole registered set (they all load into one global namespace).
Step modules OUTSIDE ``pytest_plugins`` — the intentionally-local
``import *`` modules (uc019) and the allowlisted-unregistered
dead-pending-harness modules (uc026) — are checked for collisions WITHIN
each module only: their cross-module duplicates of generic literals are
intentional scoped overrides (uc019) or tracked wiring debt
(``test_architecture_bdd_step_module_reachability``), but two identical
literals INSIDE one module are always a first-wins dead step.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFTEST = REPO_ROOT / "tests" / "bdd" / "conftest.py"

_STEP_KINDS = ("given", "when", "then")
# {param} and {param:d} placeholders match on position, not name — normalize.
_PLACEHOLDER = re.compile(r"\{[^}]*\}")


def registered_step_modules() -> list[Path]:
    """Step-definition modules listed in the BDD conftest's pytest_plugins."""
    mods = re.findall(r'"(tests\.bdd\.steps\.[\w.]+)"', CONFTEST.read_text())
    return [REPO_ROOT / (mod.replace(".", "/") + ".py") for mod in mods]


def unregistered_step_modules() -> list[Path]:
    """Step modules under tests/bdd/steps/ NOT listed in pytest_plugins.

    Covers the two loading paths the registered scan misses: intentionally-local
    ``import *`` modules (uc019) and allowlisted-unregistered modules pending a
    harness (uc026). Both are checked for WITHIN-module collisions only.
    """
    registered = set(registered_step_modules())
    steps_dir = REPO_ROOT / "tests" / "bdd" / "steps"
    return [
        path
        for path in sorted(steps_dir.rglob("*.py"))
        if path.name != "__init__.py" and not path.name.startswith("_") and path not in registered
    ]


def _step_literal(dec: ast.expr) -> tuple[str, str] | None:
    """Return (kind, normalized_literal) for a given/when/then decorator."""
    if not (isinstance(dec, ast.Call) and getattr(dec.func, "id", None) in _STEP_KINDS):
        return None
    kind = dec.func.id  # type: ignore[attr-defined]
    arg = dec.args[0] if dec.args else None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return kind, arg.value
    if isinstance(arg, ast.Call) and getattr(arg.func, "attr", None) in ("parse", "cfparse", "re"):
        inner = arg.args[0] if arg.args else None
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            return kind, _PLACEHOLDER.sub("{}", inner.value)
    return None


def collect_literal_collisions(paths: list[Path]) -> dict[tuple[str, str], list[str]]:
    """Map (kind, literal) -> def sites, keeping only colliding literals."""
    seen: dict[tuple[str, str], list[str]] = {}
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                key = _step_literal(dec)
                if key is not None:
                    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
                    seen.setdefault(key, []).append(f"{shown}:{node.lineno}:{node.name}")
    return {key: sites for key, sites in seen.items() if len(sites) > 1}


def _format_collisions(collisions: dict[tuple[str, str], list[str]]) -> str:
    return "\n".join(
        f"{kind} {literal!r}:\n    " + "\n    ".join(sites) for (kind, literal), sites in sorted(collisions.items())
    )


def test_no_duplicate_step_literals_in_registered_modules():
    collisions = collect_literal_collisions(registered_step_modules())
    assert not collisions, (
        "Duplicate BDD step parse literals across registered step modules — "
        "pytest-bdd resolves first-registered-wins, so the later def is dead "
        "code that can silently take over on a resolution-order change "
        "(#1417). Merge or delete the shadowed defs:\n" + _format_collisions(collisions)
    )


def test_no_duplicate_step_literals_within_unregistered_modules():
    """import*-loaded and allowlisted-unregistered step modules: no internal dups.

    These modules escape the registered scan (#1417 round-8 review item 8): uc019 loads via
    a test-module ``import *`` and uc026 is dead pending its harness. Their
    cross-module duplicates of generic literals are intentional/tracked, but two
    identical literals INSIDE one module mean the later def is dead the moment
    the module loads (first-wins) — with a DIFFERENT body, a silent shadow.
    """
    collisions: dict[tuple[str, str], list[str]] = {}
    for path in unregistered_step_modules():
        collisions.update(collect_literal_collisions([path]))
    assert not collisions, (
        "Duplicate BDD step parse literals WITHIN an unregistered/import*-loaded "
        "step module — first-wins makes the later def dead code, and differing "
        "bodies make it a silent shadow (#1417 round-8 review item 8). Merge or delete the "
        "shadowed defs:\n" + _format_collisions(collisions)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _collide(snippet: str, tmp_path: Path) -> dict:
    p = tmp_path / "steps_mod.py"
    p.write_text(snippet)
    return collect_literal_collisions([p])


class TestGuardDetector:
    def test_positive_same_literal_different_param_names(self, tmp_path: Path):
        # The fvva case: param NAME differs, literal collides anyway.
        assert _collide(
            "@given(parsers.parse('a buy with id \"{media_buy_id}\"'))\ndef a(ctx, media_buy_id): ...\n"
            "@given(parsers.parse('a buy with id \"{mb_id}\"'))\ndef b(ctx, mb_id): ...\n",
            tmp_path,
        )

    def test_positive_plain_string_steps(self, tmp_path: Path):
        assert _collide(
            '@then("the operation should fail")\ndef a(ctx): ...\n@then("the operation should fail")\ndef b(ctx): ...\n',
            tmp_path,
        )

    def test_negative_same_literal_different_kinds(self, tmp_path: Path):
        # A given and a then with the same text do not collide.
        assert not _collide(
            '@given("the media buy exists")\ndef a(ctx): ...\n@then("the media buy exists")\ndef b(ctx): ...\n',
            tmp_path,
        )

    def test_negative_distinct_literals(self, tmp_path: Path):
        assert not _collide(
            "@given(parsers.parse('a buy with id \"{x}\"'))\ndef a(ctx, x): ...\n"
            "@given(parsers.parse('a paused buy with id \"{x}\"'))\ndef b(ctx, x): ...\n",
            tmp_path,
        )
