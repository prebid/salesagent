"""Structural guard: `src/admin/blueprints/` has been renamed to `src/admin/routers/`.

Landed with the L0-00 codemod of the Flask→FastAPI v2.0 migration. Prevents
reintroduction of the old directory name and prevents `from src.admin.blueprints`
style imports from creeping back in.

Two assertions:

1. The `src/admin/blueprints/` directory MUST NOT exist at any layer of the
   migration post-L0-00. Once Flask is fully removed the module name must
   remain `src.admin.routers`.
2. No Python source under `src/`, `tests/`, or `scripts/` may contain a
   `from src.admin.blueprints` or `import src.admin.blueprints` statement.
   The allowlist is FROZEN — empty — and cannot grow. If you hit this guard
   while landing new code, update your imports to `src.admin.routers`.

A meta-test proves the AST scanner actually detects the import form by
parsing a deliberately violating fixture (lives under `fixtures/` as
`.py.txt` so pytest/collector/guard skip it and the import guard itself
does not recursively flag it).

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-00.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    REPO_ROOT,
    SCRIPTS,
    SRC,
    TESTS,
    iter_python_files,
    relpath,
)

FIXTURE = FIXTURES_DIR / "test_no_blueprints_meta_fixture.py.txt"

# FROZEN — no allowlist growth permitted. Imports of src.admin.blueprints
# have been eliminated in the L0-00 rename commit.
ALLOWLIST: frozenset[str] = frozenset()


def _module_imports_blueprints(tree: ast.AST) -> bool:
    """True iff AST contains a `src.admin.blueprints[...]` import."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src.admin.blueprints" or alias.name.startswith("src.admin.blueprints."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and (
                node.module == "src.admin.blueprints" or node.module.startswith("src.admin.blueprints.")
            ):
                return True
    return False


def _file_imports_blueprints(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _module_imports_blueprints(tree)


def test_blueprints_directory_does_not_exist() -> None:
    """The directory `src/admin/blueprints/` was renamed to `src/admin/routers/` in L0-00."""
    blueprints_dir = SRC / "admin" / "blueprints"
    assert not blueprints_dir.exists(), (
        f"`{blueprints_dir.relative_to(REPO_ROOT).as_posix()}` must not exist. "
        "The directory was renamed to `src/admin/routers/` by the L0-00 codemod "
        "of the Flask→FastAPI v2.0 migration. If this directory reappeared, "
        "someone un-did the rename — restore the routers/ layout."
    )


def test_no_src_admin_blueprints_imports() -> None:
    """No .py file under src/, tests/, scripts/ may import from src.admin.blueprints."""
    violations = {relpath(p) for p in iter_python_files([SRC, TESTS, SCRIPTS]) if _file_imports_blueprints(p)}
    new_violations = violations - ALLOWLIST
    assert not new_violations, (
        "Imports of `src.admin.blueprints` detected. The module was renamed to "
        "`src.admin.routers` in the L0-00 codemod. Update these imports: "
        f"{sorted(new_violations)}"
    )


def test_allowlist_is_frozen_empty() -> None:
    """Meta-test: the allowlist is frozen empty and must not grow."""
    assert ALLOWLIST == frozenset(), (
        "ALLOWLIST must remain frozen empty. The L0-00 codemod eliminated "
        "every `src.admin.blueprints` import — do not add new ones."
    )


def test_meta_fixture_exists() -> None:
    """Meta-test: the synthetic-violator fixture file is present."""
    assert FIXTURE.exists(), (
        f"Meta-fixture missing at {FIXTURE}. This file is required to prove "
        "the AST scanner actually detects `from src.admin.blueprints` imports."
    )


def test_detector_catches_meta_fixture() -> None:
    """Meta-test: parsing the synthetic-violator fixture MUST trip the detector."""
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _module_imports_blueprints(tree), (
        f"AST scanner FAILED to detect the `from src.admin.blueprints` import in "
        f"the meta-fixture {FIXTURE.name}. The guard is broken — real violations "
        "would slip past undetected."
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "from src.admin.blueprints import auth\n",
        "from src.admin.blueprints.auth import auth_bp\n",
        "import src.admin.blueprints\n",
        "import src.admin.blueprints.oidc\n",
        # Nested inside a function — still a violation.
        "def f():\n    from src.admin.blueprints import x\n    return x\n",
    ],
)
def test_detector_catches_synthetic_violations(snippet: str) -> None:
    """Meta-test: AST scanner catches every form of `src.admin.blueprints` import."""
    tree = ast.parse(snippet)
    assert _module_imports_blueprints(tree), f"Detector missed violation in snippet: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        # Clean code the detector MUST NOT flag.
        "from src.admin.routers import auth\n",
        "from src.admin.routers.auth import auth_bp\n",
        "import src.admin.routers\n",
        "from src.admin import app\n",
        # Mentions "blueprints" as a string/variable — not an import.
        'x = "src.admin.blueprints was renamed"\n',
        "blueprints = []\n",
    ],
)
def test_detector_accepts_clean_snippets(snippet: str) -> None:
    """Meta-test: AST scanner does not false-positive on clean code."""
    tree = ast.parse(snippet)
    assert not _module_imports_blueprints(tree), f"Detector false-positive on: {snippet!r}"
