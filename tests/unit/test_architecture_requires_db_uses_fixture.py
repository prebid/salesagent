"""Guard: ``pytest.mark.requires_db`` must be paired with the ``integration_db`` fixture.

The ``requires_db`` marker is *just a label* — it does not trigger any fixture
setup. A test file that says ``pytestmark = [..., pytest.mark.requires_db]`` but
never actually depends on the ``integration_db`` fixture (directly or
transitively) will not skip when ``DATABASE_URL`` is unset; it will instead crash
on the first ``get_db_session()`` call with the engine guardrail. PR #139 fixed
this exact bug in ``test_sync_accounts_premap.py``.

Scanning approach: AST — for each test file under ``tests/`` that uses
``requires_db``, verify the file references ``integration_db`` directly OR uses
one of the known transitively-dependent fixtures / harness classes. If neither,
flag.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]

# Fixtures that themselves take ``integration_db`` (or an equivalent
# per-test-DB fixture like ``migration_db``) as a parameter. Using any of these
# in a test signature satisfies the guard.
_TRANSITIVE_INTEGRATION_DB_FIXTURES: frozenset[str] = frozenset(
    {
        "admin_client",
        "authenticated_admin_client",
        "authenticated_admin_session",
        "factory_session",
        "integration_db",
        "mcp_server",
        "migration_db",
        "test_database",
        "test_database_url",
        "mock_identity",
        "populated_db",
        "sample_principal",
        "sample_products",
        "sample_tenant",
        "test_admin_app",
        "test_audit_logger",
        "test_media_buy_workflow",
        "test_tenant_with_data",
    }
)

# Harness classes that bind their own DB session to factories. Their
# ``__enter__`` performs DB setup equivalent to ``integration_db``. The
# inclusion criterion is *binds a real DB session*, so unit-mode envs
# (``DeliveryPollEnvUnit``, ``ProductEnvUnit``, ``MediaBuyUpdateEnv``) and the
# abstract ``BaseTestEnv`` are deliberately excluded — they mock the DB.
# ``IntegrationEnv`` is the integration-mode base class.
_HARNESS_ENV_CLASSES: frozenset[str] = frozenset(
    {
        "AccountListEnv",
        "AccountSyncEnv",
        "CircuitBreakerEnv",
        "CreativeFormatsEnv",
        "CreativeListEnv",
        "CreativeSyncEnv",
        "DeliveryPollEnv",
        "IntegrationEnv",
        "ProductEnv",
        "WebhookEnv",
    }
)

# Allowlist — must only shrink, never grow. Each entry needs a FIXME comment at
# the source location explaining why the marker is decoupled from the fixture.
_ALLOWLIST: frozenset[Path] = frozenset()


def _file_uses_requires_db(tree: ast.Module) -> bool:
    """True if the file has ``pytest.mark.requires_db`` anywhere — module-level
    pytestmark, class-level decorator, or per-test decorator."""
    for node in ast.walk(tree):
        # Decorator form: @pytest.mark.requires_db or @pytest.mark.requires_db(...)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "requires_db"
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "mark"
                ):
                    return True
        # pytestmark = ... (Assign or AnnAssign with pytest.mark.requires_db inside)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Name) and tgt.id == "pytestmark":
                    for sub in ast.walk(node):
                        if (
                            isinstance(sub, ast.Attribute)
                            and sub.attr == "requires_db"
                            and isinstance(sub.value, ast.Attribute)
                            and sub.value.attr == "mark"
                        ):
                            return True
    return False


def _file_uses_integration_db_fixture(tree: ast.Module) -> bool:
    """True if any function/method/fixture in the file takes a fixture parameter
    matching one of the known transitive integration_db dependencies.

    Walks all parameter-list slots — positional, regular, and keyword-only —
    so signatures that use ``*`` to force fixture-as-kwonly still count.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.arg in _TRANSITIVE_INTEGRATION_DB_FIXTURES:
                return True
    return False


def _file_uses_harness_env(tree: ast.Module) -> bool:
    """True if the file references a harness Env class by name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _HARNESS_ENV_CLASSES:
            return True
    return False


def _file_calls_get_db_session(tree: ast.Module) -> bool:
    """True if the file calls ``get_db_session()`` directly.

    This is the only scenario the guard cares about: a file that marks
    ``requires_db`` AND directly opens a real DB session, without going through
    a fixture that provides per-test DB setup. Files that mark ``requires_db``
    but only use mocks or harness classes are out of scope — their marker may
    be decorative, but it can't crash the way the premap bug did.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "get_db_session":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "get_db_session":
                return True
    return False


def _candidate_files() -> list[Path]:
    """All test files under ``tests/`` excluding harness/factory plumbing."""
    out: list[Path] = []
    for p in sorted(_TESTS_DIR.rglob("test_*.py")):
        rel = p.relative_to(_TESTS_DIR)
        # Skip harness/factories — they define helpers, not test files
        if rel.parts and rel.parts[0] in {"harness", "factories"}:
            continue
        out.append(p)
    return out


def test_requires_db_marker_paired_with_fixture() -> None:
    """Every file using ``requires_db`` must depend on ``integration_db``.

    Without the fixture dependency, the marker is a label only — pytest never
    invokes the fixture, so when ``DATABASE_URL`` is absent the tests crash on
    ``get_db_session()`` instead of skipping. PR #139 fixed this in
    ``test_sync_accounts_premap.py``; this guard prevents reintroduction.
    """
    violations: list[str] = []
    for path in _candidate_files():
        if path in _ALLOWLIST:
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        if not _file_uses_requires_db(tree):
            continue
        if not _file_calls_get_db_session(tree):
            # Decorative ``requires_db`` marker — file uses mocks or harness
            # only. Possibly misleading (see PR #180 for cleanup precedent),
            # but cannot trip the engine guardrail. Out of scope for this guard.
            continue
        if _file_uses_integration_db_fixture(tree):
            continue
        if _file_uses_harness_env(tree):
            continue
        violations.append(str(path.relative_to(_TESTS_DIR.parent)))

    if violations:
        joined = "\n  ".join(violations)
        raise AssertionError(
            "Files use @pytest.mark.requires_db without a paired integration_db "
            f"fixture dependency:\n  {joined}\n\n"
            "The marker is just a label — without the fixture, the test will "
            "not skip when DATABASE_URL is absent and will instead crash on "
            "get_db_session(). Either:\n"
            "  1. Add `integration_db` as a fixture parameter to each test, or\n"
            "  2. Add an autouse fixture that depends on integration_db (see\n"
            "     tests/integration/test_sync_accounts_premap.py for the\n"
            "     `_ensure_integration_db` pattern).\n"
            "If the marker is decorative (test doesn't actually need DB),\n"
            "remove the marker — see PR #180 for the GAM cleanup precedent.\n"
            "If the file uses a NEW fixture that itself depends on\n"
            "integration_db, add the fixture name to\n"
            "_TRANSITIVE_INTEGRATION_DB_FIXTURES in this guard."
        )
