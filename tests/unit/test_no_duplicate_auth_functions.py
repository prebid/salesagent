"""Test that auth_utils.py does not have a duplicate get_principal_from_context.

Task salesagent-ygg3: Two parallel get_principal_from_context functions exist with
different signatures. The auth_utils.py version is dead code (0 callers) and should
be removed. All transports use resolve_identity() in resolved_identity.py.
"""

import ast


class TestNoDuplicateAuthFunctions:
    """Verify only one get_principal_from_context exists (in auth.py for test compat)."""

    def test_auth_utils_has_no_get_principal_from_context(self):
        """auth_utils.py should not define get_principal_from_context.

        This function is dead code (0 callers). The canonical version lives in
        auth.py and is only used by tests. All production code uses resolve_identity().
        """
        import pathlib

        source = (pathlib.Path(__file__).resolve().parents[2] / "src" / "core" / "auth_utils.py").read_text()

        tree = ast.parse(source)

        func_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert "get_principal_from_context" not in func_names, (
            "auth_utils.py still defines get_principal_from_context — "
            "this is dead code (0 callers) and should be removed. "
            "All production code uses resolve_identity()."
        )
