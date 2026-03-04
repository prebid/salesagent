"""Structural test: MCP transport boundary uses resolve_identity(), not get_principal_from_context().

Core Invariant: All transports (MCP, A2A, REST) resolve identity through the single
resolve_identity() function; no transport-specific auth logic exists outside the
transport boundary.

This test ensures the MCP context wrapper delegates to the unified resolve_identity
path (via resolve_identity_from_context in transport_helpers.py) instead of the
duplicated get_principal_from_context in auth.py.
"""

import ast


class TestMCPWrapperUsesResolveIdentity:
    """Verify MCP context wrapper uses the unified identity resolution path."""

    def _get_source(self, filepath: str) -> str:
        """Read source code from a file."""
        with open(filepath) as f:
            return f.read()

    def test_mcp_context_wrapper_does_not_import_get_principal_from_context(self):
        """mcp_context_wrapper.py should not import get_principal_from_context.

        After migration, the MCP wrapper should use resolve_identity_from_context
        (from transport_helpers.py) which delegates to the unified resolve_identity().
        """
        source = self._get_source("src/core/mcp_context_wrapper.py")
        tree = ast.parse(source)

        forbidden_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    actual_name = alias.name
                    if actual_name == "get_principal_from_context":
                        forbidden_imports.append(f"line {node.lineno}: from {node.module} import {actual_name}")

        assert not forbidden_imports, (
            "mcp_context_wrapper.py still imports get_principal_from_context. "
            "Should use resolve_identity_from_context instead.\n" + "\n".join(forbidden_imports)
        )

    def test_mcp_context_wrapper_uses_resolve_identity_from_context(self):
        """mcp_context_wrapper.py should import resolve_identity_from_context."""
        source = self._get_source("src/core/mcp_context_wrapper.py")

        # Check for import of the unified path
        assert "resolve_identity_from_context" in source or "resolve_identity" in source, (
            "mcp_context_wrapper.py should import resolve_identity_from_context "
            "from transport_helpers (or resolve_identity from resolved_identity)"
        )

    def test_context_helpers_does_not_import_get_principal_from_context(self):
        """context_helpers.py should not import get_principal_from_context.

        The get_principal_id_from_context wrapper should use the unified path.
        """
        source = self._get_source("src/core/helpers/context_helpers.py")
        tree = ast.parse(source)

        forbidden_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "get_principal_from_context":
                        forbidden_imports.append(f"line {node.lineno}: from {node.module} import {alias.name}")

        assert not forbidden_imports, (
            "context_helpers.py still imports get_principal_from_context. "
            "Should use resolve_identity_from_context instead.\n" + "\n".join(forbidden_imports)
        )

    def test_activity_helpers_does_not_import_get_principal_from_context(self):
        """activity_helpers.py should not import get_principal_from_context."""
        source = self._get_source("src/core/helpers/activity_helpers.py")
        tree = ast.parse(source)

        forbidden_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "get_principal_from_context":
                        forbidden_imports.append(f"line {node.lineno}: from {node.module} import {alias.name}")

        assert not forbidden_imports, (
            "activity_helpers.py still imports get_principal_from_context. "
            "Should use resolve_identity_from_context instead.\n" + "\n".join(forbidden_imports)
        )
