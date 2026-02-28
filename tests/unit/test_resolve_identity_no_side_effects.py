"""Regression tests: resolve_identity() must have no ContextVar side effects.

Core invariant: resolve_identity() and its internal helpers (_detect_tenant,
get_principal_from_token) must not call set_current_tenant(). The ContextVar
is only set at transport boundaries, never deep inside resolver internals.

beads: salesagent-4csg
"""

import ast
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestDetectTenantPure:
    """_detect_tenant() must not call set_current_tenant()."""

    def test_no_set_current_tenant_in_detect_tenant(self):
        """_detect_tenant must not mutate the tenant ContextVar."""
        source = (PROJECT_ROOT / "src" / "core" / "resolved_identity.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_detect_tenant":
                func_source = ast.get_source_segment(source, node)
                assert func_source is not None
                assert "set_current_tenant" not in func_source, (
                    "_detect_tenant() calls set_current_tenant() — "
                    "tenant detection must be pure (return data, don't mutate global state)"
                )
                return

        pytest.fail("_detect_tenant function not found in resolved_identity.py")


class TestResolveIdentityPure:
    """resolve_identity() must not read from or write to the tenant ContextVar."""

    def test_no_set_current_tenant_in_resolve_identity(self):
        """resolve_identity must not call set_current_tenant()."""
        source = (PROJECT_ROOT / "src" / "core" / "resolved_identity.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_identity":
                func_source = ast.get_source_segment(source, node)
                assert func_source is not None
                assert "set_current_tenant" not in func_source, (
                    "resolve_identity() calls set_current_tenant() — "
                    "identity resolution must be pure (no ContextVar writes)"
                )
                return

        pytest.fail("resolve_identity function not found")

    def test_no_get_current_tenant_fallback(self):
        """resolve_identity must not fall back to get_current_tenant()."""
        source = (PROJECT_ROOT / "src" / "core" / "resolved_identity.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_identity":
                func_source = ast.get_source_segment(source, node)
                assert func_source is not None
                assert "get_current_tenant" not in func_source, (
                    "resolve_identity() reads get_current_tenant() as fallback — "
                    "tenant must come from _detect_tenant() or token lookup, not ambient state"
                )
                return

        pytest.fail("resolve_identity function not found")


class TestGetPrincipalFromTokenPure:
    """get_principal_from_token() must not call set_current_tenant()."""

    def test_no_set_current_tenant_in_get_principal(self):
        """get_principal_from_token must not mutate the tenant ContextVar."""
        source = (PROJECT_ROOT / "src" / "core" / "auth_utils.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_principal_from_token":
                func_source = ast.get_source_segment(source, node)
                assert func_source is not None
                assert "set_current_tenant" not in func_source, (
                    "get_principal_from_token() calls set_current_tenant() — "
                    "token lookup must return tenant data, not set it as side effect"
                )
                return

        # Function might contain nested helper — check all functions in the file
        source_text = (PROJECT_ROOT / "src" / "core" / "auth_utils.py").read_text()
        assert "set_current_tenant" not in source_text, "auth_utils.py still contains set_current_tenant() calls"


class TestTransportBoundariesSetTenant:
    """Transport boundaries must call set_current_tenant() after resolve_identity()."""

    def test_rest_boundary_sets_tenant(self):
        """REST auth deps must set tenant ContextVar after resolving identity."""
        source = (PROJECT_ROOT / "src" / "core" / "auth_context.py").read_text()
        assert "set_current_tenant" in source, "REST auth deps must call set_current_tenant() at the transport boundary"

    def test_a2a_boundary_sets_tenant(self):
        """A2A handler must set tenant ContextVar after resolving identity."""
        source = (PROJECT_ROOT / "src" / "a2a_server" / "adcp_a2a_server.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_resolve_a2a_identity":
                func_source = ast.get_source_segment(source, node)
                assert func_source is not None
                assert "set_current_tenant" in func_source, (
                    "_resolve_a2a_identity must call set_current_tenant() at the transport boundary"
                )
                return

        pytest.fail("_resolve_a2a_identity function not found")
