"""Guard: tests must not CALL a src-defined ``*_raw`` wrapper that production never calls.

Regression guard for salesagent-klkg (#1417): the request-validation
suggestion-parity test asserted "every transport" but drove
``get_media_buys_raw`` — a wrapper with ZERO production callers — while the
real A2A path (``_handle_get_media_buys_skill``) leaked bare
ValidationErrors. A test passing against a dead path is false confidence: it
pins the behavior of code buyers never reach and leaves the live path
unexercised.

Rule: for every ``*_raw`` function DEFINED in ``src/`` and CALLED anywhere in
``tests/``, at least one call site must exist in ``src/`` (REST routes and A2A
skill handlers call the raw wrappers — that is what makes them a production
surface). Both sides are import-alias-aware: ``from x import get_products_raw
as core_get_products_tool`` followed by ``core_get_products_tool(...)`` counts
as a call of ``get_products_raw`` (a naive name scan would false-flag it as
dead). Test-local helpers that merely end in ``_raw`` are ignored — only names
defined in ``src/`` are considered.

Ships with ZERO violations; no allowlist (repo hard rule: allowlists never
grow). If this guard fires: either re-point the test at the real transport
dispatch (``_run_a2a_handler`` / REST client / MCP client — see
tests/harness/media_buy_list.py::call_a2a), or delete the dead wrapper.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
TESTS_ROOT = REPO_ROOT / "tests"


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return None


def raw_functions_defined(tree: ast.AST) -> set[str]:
    """Names of ``*_raw`` functions defined in this module."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.endswith("_raw")
    }


def raw_calls(tree: ast.AST) -> dict[str, list[int]]:
    """``{original_raw_name: [linenos]}`` for every ``*_raw`` call in this module.

    Alias-aware: ``from x import foo_raw as bar`` makes a later ``bar(...)``
    count as a call of ``foo_raw``. Attribute calls (``module.foo_raw(...)``)
    match on the attribute name.
    """
    alias_to_orig: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name.endswith("_raw"):
                    alias_to_orig[a.asname or a.name] = a.name
    calls: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
        if name is None:
            continue
        original = name if name.endswith("_raw") else alias_to_orig.get(name)
        if original:
            calls.setdefault(original, []).append(node.lineno)
    return calls


def find_dead_path_raw_calls(
    src_files: dict[str, ast.AST],
    test_files: dict[str, ast.AST],
) -> list[str]:
    """``file:line: name`` for every test call of a src-defined, src-uncalled ``*_raw``."""
    defined: set[str] = set()
    src_called: set[str] = set()
    for tree in src_files.values():
        defined |= raw_functions_defined(tree)
        src_called |= set(raw_calls(tree))
    dead = defined - src_called
    offenders: list[str] = []
    for path, tree in sorted(test_files.items()):
        for name, linenos in sorted(raw_calls(tree).items()):
            if name in dead:
                offenders.extend(f"{path}:{lineno}: {name}" for lineno in linenos)
    return offenders


def test_no_test_calls_dead_path_raw_wrappers():
    src_files = {str(p.relative_to(REPO_ROOT)): t for p in sorted(SRC_ROOT.rglob("*.py")) if (t := _parse(p))}
    test_files = {str(p.relative_to(REPO_ROOT)): t for p in sorted(TESTS_ROOT.rglob("*.py")) if (t := _parse(p))}
    violations = find_dead_path_raw_calls(src_files, test_files)
    assert not violations, (
        "Test code calls a src-defined *_raw wrapper that has ZERO production call "
        "sites — the test drives a dead path while the live transport path goes "
        "unexercised (salesagent-klkg, #1417). Re-point the test at the real "
        "transport dispatch (harness _run_a2a_handler / REST client / MCP client) "
        "or delete the dead wrapper. Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(src_snippets: dict[str, str], test_snippets: dict[str, str]) -> list[str]:
    return find_dead_path_raw_calls(
        {k: ast.parse(v) for k, v in src_snippets.items()},
        {k: ast.parse(v) for k, v in test_snippets.items()},
    )


class TestGuardDetector:
    def test_positive_dead_raw_called_from_test(self):
        # Defined in src, never called in src, called in a test → violation.
        assert _detect(
            {"src/t.py": "def get_media_buys_raw():\n    pass"},
            {"tests/t.py": "from src.t import get_media_buys_raw\nget_media_buys_raw()"},
        )

    def test_negative_raw_with_production_caller(self):
        # A REST route (src) calls the wrapper → tests may drive it too.
        assert not _detect(
            {"src/t.py": "def sync_creatives_raw():\n    pass\n\ndef route():\n    sync_creatives_raw()"},
            {"tests/t.py": "from src.t import sync_creatives_raw\nsync_creatives_raw()"},
        )

    def test_negative_aliased_production_caller(self):
        # klkg false-flag hazard: src calls it under an import alias.
        assert not _detect(
            {
                "src/defs.py": "def get_products_raw():\n    pass",
                "src/a2a.py": (
                    "from src.defs import get_products_raw as core_get_products_tool\ncore_get_products_tool()"
                ),
            },
            {"tests/t.py": "from src.defs import get_products_raw\nget_products_raw()"},
        )

    def test_positive_aliased_test_call_of_dead_raw(self):
        # Aliasing in the TEST must not hide a dead-path call.
        assert _detect(
            {"src/t.py": "def get_media_buys_raw():\n    pass"},
            {"tests/t.py": "from src.t import get_media_buys_raw as helper\nhelper()"},
        )

    def test_negative_test_local_raw_helper(self):
        # A *_raw helper defined inside tests is not a src wrapper.
        assert not _detect(
            {"src/t.py": "def unrelated():\n    pass"},
            {"tests/t.py": "def _call_impl_raw():\n    pass\n_call_impl_raw()"},
        )

    def test_negative_dead_raw_not_called_by_tests(self):
        # Dead code in src is a different disease; this guard only fires on
        # tests that DRIVE the dead path.
        assert not _detect(
            {"src/t.py": "def activate_signal_raw():\n    pass"},
            {"tests/t.py": "x = 1"},
        )
