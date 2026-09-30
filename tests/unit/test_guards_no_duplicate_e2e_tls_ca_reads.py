"""Guard: no new tests/e2e/*.py module re-reads E2E_CA_BUNDLE / E2E_TLS_BASE_URL directly.

tests/e2e/conftest.py's e2e_ca_bundle() / e2e_tls_base_url() are the ONE canonical
source for these two env vars. Before this guard, the TLS/CA-verified-client seam
had grown independent re-implementations in multiple e2e/bdd test modules instead
of importing the conftest fixtures -- exactly the duplication class the project's
DRY invariant bans. tests/e2e/_signing_e2e.py's ca_bundle()/tls_base_url() are thin
assert-loudly WRAPPERS that call through to the conftest functions, not reimplementations,
so they don't read the env vars directly and this guard stays green.

Scoped to tests/e2e/ (excluding conftest.py itself, the canonical source). A
sibling instance predates this guard in tests/bdd/conftest.py -- a different test
suite (BDD, not e2e), out of this guard's scope.
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import REPO_ROOT

_E2E_DIR = REPO_ROOT / "tests" / "e2e"
_ENV_VAR_NAMES = {"E2E_CA_BUNDLE", "E2E_TLS_BASE_URL"}


def _reads_target_env_var(node: ast.Call) -> str | None:
    """Return the env var name if *node* is an os.getenv/os.environ.get/os.environ[...]
    call whose key argument is one of the two target env vars, else None."""
    func = node.func
    is_getenv = isinstance(func, ast.Attribute) and func.attr == "getenv"
    is_environ_get = (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "environ"
    )
    if not (is_getenv or is_environ_get) or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value in _ENV_VAR_NAMES:
        return arg.value
    return None


def _reads_target_env_subscript(node: ast.Subscript) -> str | None:
    """Return the env var name if *node* is an os.environ["X"] subscript read."""
    if not (isinstance(node.value, ast.Attribute) and node.value.attr == "environ"):
        return None
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in _ENV_VAR_NAMES:
        return key.value
    return None


def _scan() -> set[tuple[str, int, str]]:
    violations: set[tuple[str, int, str]] = set()
    for path in sorted(_E2E_DIR.glob("*.py")):
        if path.name == "conftest.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                var = _reads_target_env_var(node)
                if var:
                    violations.add((rel, node.lineno, var))
            elif isinstance(node, ast.Subscript):
                var = _reads_target_env_subscript(node)
                if var:
                    violations.add((rel, node.lineno, var))
    return violations


class TestNoDuplicateE2eTlsCaReads:
    """No tests/e2e/*.py module (other than conftest.py) reads the TLS/CA env vars directly."""

    def test_no_new_direct_env_reads(self):
        violations = _scan()
        assert not violations, (
            "tests/e2e/*.py modules reading E2E_CA_BUNDLE/E2E_TLS_BASE_URL directly instead of "
            "importing tests/e2e/conftest.py's e2e_ca_bundle()/e2e_tls_base_url() "
            "(the canonical source):\n" + "\n".join(f"  - {p}:{n} ({v})" for p, n, v in sorted(violations))
        )

    def test_positive_meta_detects_getenv(self):
        tree = ast.parse('import os\nx = os.getenv("E2E_CA_BUNDLE")')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _reads_target_env_var(n)]
        assert found, "detector failed to flag a direct os.getenv(E2E_CA_BUNDLE) read"

    def test_positive_meta_detects_environ_get(self):
        tree = ast.parse('import os\nx = os.environ.get("E2E_TLS_BASE_URL")')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _reads_target_env_var(n)]
        assert found, "detector failed to flag a direct os.environ.get(E2E_TLS_BASE_URL) read"

    def test_positive_meta_detects_environ_subscript(self):
        tree = ast.parse('import os\nx = os.environ["E2E_CA_BUNDLE"]')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.Subscript) and _reads_target_env_subscript(n)]
        assert found, "detector failed to flag a direct os.environ[E2E_CA_BUNDLE] subscript read"

    def test_negative_meta_ignores_unrelated_env_var(self):
        tree = ast.parse('import os\nx = os.getenv("SOME_OTHER_VAR")')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _reads_target_env_var(n)]
        assert not found, "detector false-positived on an unrelated env var"

    def test_negative_meta_ignores_string_mention(self):
        """A string literal merely MENTIONING the env var name (e.g. in an error
        message) is not a read -- this is what keeps _signing_e2e.py's assertion
        messages from being flagged."""
        tree = ast.parse('x = "set E2E_CA_BUNDLE to fix this"')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _reads_target_env_var(n)]
        assert not found
