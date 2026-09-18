"""Guard: no ad hoc TestClient(raise_server_exceptions=False) bypassing the
harness capability, in a test that already uses tests.harness (prkv.18).

Disease: a test builds a harness env (ProductEnv/IntegrationEnv/etc.) for
everything else, then locally reaches around it with a hand-rolled
``TestClient(app, raise_server_exceptions=False)`` construction to observe an
untyped-exception REST response, instead of the reusable harness capability
(``env.inject_untyped_exception()`` + ``IntegrationEnv.REST_RAISE_SERVER_EXCEPTIONS``,
added by prkv.18 specifically to eliminate this hand-rolling).
``tests/integration/test_prkv8_untyped_exception_wire_leak.py`` was the
original instance — prkv.18's own codebase-scan disposition table (MIGRATE)
committed to fixing it and this guard pins that it stays fixed.

Scope: whole tests/ tree — the pattern is a bypass of a shared harness
capability, not confined to one file/package.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"

# (file, lineno) pairs permitted to violate — shrink-only. The one remaining
# entry is legitimate per prkv.18's disposition table: the raw client IS the
# stated purpose, proving real-HTTP wiring distinct from the harness's
# in-process capture (test_a2a_wire_integer_serialization.py). The harness
# capability this guard steers toward — inject_untyped_exception() +
# call_via(Transport.REST, ...) — grades an untyped-exception REST envelope,
# which is not what that test observes (A2A JSON-RPC integer round-tripping
# through src.app's ASGI wrapper), so there is nothing to migrate it onto.
#
# Line 41 -> 42: pure drift. Same call, same method, same class; the class
# docstring above it gained one wrapped line during the spec-gaps-1210 merge.
ALLOWLIST: set[tuple[str, int]] = {
    ("tests/integration/test_a2a_wire_integer_serialization.py", 50),
}


def _imports_harness(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tests.harness"):
            return True
        if isinstance(node, ast.Import) and any(a.name.startswith("tests.harness") for a in node.names):
            return True
    return False


def _testclient_raise_server_exceptions_false(node: ast.Call) -> bool:
    """``TestClient(..., raise_server_exceptions=False)`` — the exact bypass shape."""
    if not (isinstance(node.func, ast.Name) and node.func.id == "TestClient"):
        return False
    for kw in node.keywords:
        if kw.arg == "raise_server_exceptions" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _scan_source_text(rel: str, text: str) -> list[tuple[str, int]]:
    tree = ast.parse(text)
    if not _imports_harness(tree):
        return []
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _testclient_raise_server_exceptions_false(node):
            hits.append((rel, node.lineno))
    return hits


def _scan() -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        hits.extend(_scan_source_text(rel, path.read_text()))
    return hits


class TestNoAdhocTestClientBypassGuard:
    def test_scope_is_nonempty(self):
        """The rglob must keep matching real test files (guard not vacuous).

        Anchored to the POPULATION, not to one filename: it used to name
        ``test_error_envelope.py``, which made a guard's non-vacuity depend on one
        file continuing to exist. Deleting that file broke the proof without
        breaking the scan.
        """
        matched = [p for p in TESTS_DIR.rglob("*.py") if p.name.startswith("test_")]
        assert len(matched) > 100, f"scan scope collapsed to {len(matched)} files"

    def test_no_adhoc_testclient_bypassing_harness(self):
        violations = [v for v in _scan() if v not in ALLOWLIST]
        assert not violations, (
            "Ad hoc TestClient(raise_server_exceptions=False) bypassing the harness "
            "capability, in a test that already uses tests.harness (prkv.18 disease). "
            "Use env.inject_untyped_exception(...) + env.call_via(Transport.REST, ...) "
            "instead:\n" + "\n".join(f"  {f}:{line}" for f, line in violations)
        )

    def test_allowlist_entries_still_violate(self):
        actual = set(_scan())
        stale = ALLOWLIST - actual
        assert not stale, f"Allowlist entries no longer violating — remove them: {sorted(stale)}"


class TestGuardMetaTests:
    def test_positive_detects_bypass_in_harness_using_file(self):
        src = (
            "from tests.harness.product import ProductEnv\n"
            "def f():\n"
            "    TestClient(app, raise_server_exceptions=False)\n"
        )
        hits = _scan_source_text("x.py", src)
        assert hits == [("x.py", 3)]

    def test_negative_no_harness_import_passes(self):
        """Same TestClient call, but the file never imports tests.harness --
        nothing to bypass, so it's out of this guard's scope entirely."""
        src = "def f():\n    TestClient(app, raise_server_exceptions=False)\n"
        assert _scan_source_text("x.py", src) == []

    def test_negative_raise_server_exceptions_true_passes(self):
        src = (
            "from tests.harness.product import ProductEnv\n"
            "def f():\n"
            "    TestClient(app, raise_server_exceptions=True)\n"
        )
        assert _scan_source_text("x.py", src) == []

    def test_negative_testclient_without_the_flag_passes(self):
        """Plain TestClient(app) — the default (True) is the harness's own
        REST_RAISE_SERVER_EXCEPTIONS default too; nothing to flag."""
        src = "from tests.harness.product import ProductEnv\ndef f():\n    TestClient(app)\n"
        assert _scan_source_text("x.py", src) == []

    def test_would_be_missed_indirect_import(self):
        """Known limitation: importing a harness symbol via re-export from a
        non-tests.harness module (e.g. a local conftest that does `from
        tests.harness.product import ProductEnv` and a test file imports
        THAT conftest helper instead) evades ``_imports_harness``'s direct-
        import check. Reviewers own this residual -- the direct-import form
        is what every real instance found during prkv.18's codebase scan
        actually used."""
        src = "from tests.local_conftest_helpers import make_env\ndef f():\n    TestClient(app, raise_server_exceptions=False)\n"
        assert _scan_source_text("x.py", src) == []
