"""Test that LazyTenantContext._resolve() does not mutate ContextVar.

Task salesagent-rjvp: Property access on LazyTenantContext triggers _resolve()
which calls set_current_tenant() as a side effect. This mutation should happen
at explicit transport boundaries, not as side effect of field access.
"""

import ast


class TestLazyTenantNoContextVarMutation:
    """Verify _resolve() does not call set_current_tenant()."""

    def test_resolve_does_not_call_set_current_tenant(self):
        """_resolve() should not call set_current_tenant().

        ContextVar mutation must happen at explicit transport boundaries,
        not as a side effect of property access on lazy objects.
        """
        import pathlib

        source = (pathlib.Path(__file__).resolve().parents[2] / "src" / "core" / "tenant_context.py").read_text()

        tree = ast.parse(source)

        # Find the LazyTenantContext class
        class_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LazyTenantContext":
                class_node = node
                break

        assert class_node is not None, "LazyTenantContext class not found"

        # Find the _resolve method within the class
        resolve_node = None
        for node in ast.walk(class_node):
            if isinstance(node, ast.FunctionDef) and node.name == "_resolve":
                resolve_node = node
                break

        assert resolve_node is not None, "_resolve method not found"

        resolve_source = ast.get_source_segment(source, resolve_node)
        assert resolve_source is not None

        assert "set_current_tenant" not in resolve_source, (
            "LazyTenantContext._resolve() calls set_current_tenant() — "
            "ContextVar mutation should happen at transport boundaries, "
            "not as side effect of property access"
        )
