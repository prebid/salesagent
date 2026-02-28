"""Test that header case-insensitive lookup is in a single shared utility.

Task salesagent-jsk0: _get_header_case_insensitive is duplicated in 3 files.
Extract to src/core/http_utils.py and import everywhere.
"""

import ast
import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestSharedHeaderUtil:
    """Verify header utility exists in one place and is imported by consumers."""

    def test_http_utils_module_exists(self):
        """src/core/http_utils.py should exist with the shared function."""
        source = (_PROJECT_ROOT / "src" / "core" / "http_utils.py").read_text()

        tree = ast.parse(source)
        func_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert "get_header_case_insensitive" in func_names, (
            "src/core/http_utils.py should define get_header_case_insensitive"
        )

    def test_auth_py_imports_from_http_utils(self):
        """auth.py should import from http_utils, not define its own copy."""
        source = (_PROJECT_ROOT / "src" / "core" / "auth.py").read_text()

        tree = ast.parse(source)

        # Should NOT define _get_header_case_insensitive locally
        func_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert "_get_header_case_insensitive" not in func_names, (
            "auth.py still defines its own _get_header_case_insensitive — should import from http_utils"
        )

    def test_resolved_identity_imports_from_http_utils(self):
        """resolved_identity.py should import from http_utils, not define its own copy."""
        source = (_PROJECT_ROOT / "src" / "core" / "resolved_identity.py").read_text()

        tree = ast.parse(source)

        func_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert "_get_header_case_insensitive" not in func_names, (
            "resolved_identity.py still defines its own _get_header_case_insensitive — should import from http_utils"
        )

    def test_app_py_imports_from_http_utils(self):
        """app.py should import from http_utils, not define its own copy."""
        source = (_PROJECT_ROOT / "src" / "app.py").read_text()

        tree = ast.parse(source)

        func_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert "_get_header_case_insensitive" not in func_names, (
            "app.py still defines its own _get_header_case_insensitive — should import from http_utils"
        )
