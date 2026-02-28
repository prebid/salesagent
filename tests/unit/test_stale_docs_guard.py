"""Guard tests: documentation and comments must stay current after auth refactoring.

Core invariant: Comments and docs must accurately describe the current architecture.
ContextVar references, old multi-process diagrams, deprecated SQLAlchemy patterns,
and SQLite references are all stale after the unified FastAPI + ASGI middleware refactoring.

beads: salesagent-i7k9
"""

import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestNoStaleContextVarComments:
    """app.py comments must not reference ContextVar (removed from auth path)."""

    def test_app_no_contextvar_in_a2a_comment(self):
        """A2A integration comment must not mention ContextVar propagation."""
        source = (PROJECT_ROOT / "src" / "app.py").read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            if "ContextVar" in line and line.lstrip().startswith("#"):
                pytest.fail(f"src/app.py:{lineno} has stale ContextVar comment: {line.strip()}")

    def test_middleware_comment_no_contextvar(self):
        """Middleware stack comment must not reference ContextVar."""
        source = (PROJECT_ROOT / "src" / "app.py").read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            if "ContextVar" in line and "scope" in line.lower():
                pytest.fail(f"src/app.py:{lineno} references ContextVar in middleware comment: {line.strip()}")


class TestNoSQLiteReferences:
    """Production code docstrings must not reference SQLite (PostgreSQL-only mandate)."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/core/config_loader.py",
            "src/core/validation_helpers.py",
        ],
    )
    def test_no_sqlite_in_docstrings(self, rel_path):
        """Docstrings must not reference SQLite as a supported backend."""
        import ast

        source = (PROJECT_ROOT / rel_path).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                docstring = ast.get_docstring(node)
                if docstring and "sqlite" in docstring.lower():
                    pytest.fail(f"{rel_path}: docstring at line {node.body[0].lineno} references SQLite")


class TestSecurityDocPattern:
    """security.md must use SQLAlchemy 2.0 patterns, not deprecated session.query()."""

    def test_no_session_query_in_security_doc(self):
        """security.md code samples must not use deprecated session.query() pattern."""
        doc_path = PROJECT_ROOT / "docs" / "security.md"
        if not doc_path.exists():
            pytest.skip("docs/security.md not found")
        source = doc_path.read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            if ".query(" in line and "session" not in line.lower():
                # Check for db.query() pattern specifically
                pass
            if "db.query(" in line or "session.query(" in line:
                pytest.fail(f"docs/security.md:{lineno} uses deprecated session.query(): {line.strip()}")
