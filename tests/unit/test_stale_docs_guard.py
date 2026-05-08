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
    """``core/main.py`` comments must not reference *stale* ContextVars from the
    deleted auth-path design.

    The legacy ``src/app.py`` propagated auth identity via a ``ContextVar`` —
    that path was removed when the unified ASGI middleware stack landed, so
    leftover comments mentioning it would mislead readers about how identity
    flows today.

    Legitimate ContextVars live in ``core/main.py`` (e.g.
    ``core.middleware.transport_detect.current_transport``, set per-request to
    let platform delegates know whether the inbound transport was A2A or MCP);
    those are allowlisted by name. Add to ``_ALLOWED_CONTEXTVAR_NAMES`` only
    when a new contextvar is genuinely needed and documented.
    """

    _ALLOWED_CONTEXTVAR_NAMES: frozenset[str] = frozenset({"current_transport"})

    def _line_references_only_allowlisted_contextvar(self, line: str) -> bool:
        return any(name in line for name in self._ALLOWED_CONTEXTVAR_NAMES)

    def test_main_no_contextvar_in_a2a_comment(self):
        """A2A integration comment must not mention stale ContextVar propagation."""
        source = (PROJECT_ROOT / "core" / "main.py").read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            if "ContextVar" not in line or not line.lstrip().startswith("#"):
                continue
            if self._line_references_only_allowlisted_contextvar(line):
                continue
            pytest.fail(f"core/main.py:{lineno} has stale ContextVar comment: {line.strip()}")

    def test_middleware_comment_no_contextvar(self):
        """Middleware stack comment must not reference stale scope-keyed ContextVars."""
        source = (PROJECT_ROOT / "core" / "main.py").read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            if "ContextVar" not in line or "scope" not in line.lower():
                continue
            if self._line_references_only_allowlisted_contextvar(line):
                continue
            pytest.fail(f"core/main.py:{lineno} references ContextVar in middleware comment: {line.strip()}")


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
