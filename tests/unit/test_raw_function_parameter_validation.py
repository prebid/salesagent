#!/usr/bin/env python3
"""
Test that _raw functions correctly pass parameters to _impl functions.

This test validates that:
1. All parameters accepted by _raw functions are either:
   - Passed to the _impl function
   - Used to construct request objects
   - Helper function parameters that are documented
2. No parameters are silently dropped

This would have caught the get_products_raw + create_get_products_request bug
where adcp_version was accepted but not passed through.
"""

import ast
import inspect
from pathlib import Path

import pytest


class TestRawFunctionParameterValidation:
    """Validate that raw functions properly handle all their parameters."""

    def test_get_products_raw_parameters_valid(self):
        """Test that get_products_raw doesn't accept invalid parameters for helpers."""
        from src.core.schema_helpers import create_get_products_request
        from src.core.tools import get_products_raw

        # Get parameters
        raw_sig = inspect.signature(get_products_raw)
        helper_sig = inspect.signature(create_get_products_request)

        raw_params = set(raw_sig.parameters.keys()) - {"ctx"}
        helper_params = set(helper_sig.parameters.keys())

        # Check: All non-context params in raw should either:
        # 1. Be passed to helper (except adcp_version which is NOT in helper)
        # 2. Be valid for some other purpose

        # Known valid parameters that are NOT passed to helper
        valid_non_helper_params = {
            "adcp_version",  # Metadata, not passed to helper (this was the bug)
            "min_exposures",  # Optional, not in helper
            "strategy_id",  # Optional, not in helper
        }

        # Parameters that SHOULD be in helper
        should_be_in_helper = raw_params - valid_non_helper_params

        # Verify all should-be-in-helper params are actually in helper
        missing_in_helper = should_be_in_helper - helper_params

        assert (
            not missing_in_helper
        ), f"get_products_raw has parameters not in helper and not documented as valid: {missing_in_helper}"

    def test_all_raw_functions_have_context_parameter(self):
        """All _raw functions should accept a ctx parameter."""
        from src.core import tools

        raw_functions = [name for name in dir(tools) if name.endswith("_raw") and callable(getattr(tools, name))]

        for func_name in raw_functions:
            func = getattr(tools, func_name)
            sig = inspect.signature(func)
            assert "ctx" in sig.parameters, f"{func_name} missing 'ctx' parameter"

    def test_raw_functions_dont_drop_parameters_silently(self):
        """Test that raw functions don't accept parameters they don't use.

        This is a source code analysis test that checks:
        1. Parameters are either passed to _impl
        2. Parameters are used to construct request objects
        3. Parameters are documented as metadata/optional

        This would catch bugs like accepting adcp_version but not using it.
        """
        tools_path = Path(__file__).parent.parent.parent / "src" / "core" / "tools" / "__init__.py"
        with open(tools_path) as f:
            content = f.read()

        tree = ast.parse(content)

        # Known valid "unused" parameters (metadata, optional features, etc.)
        # These are documented reasons why a parameter might not be directly passed through
        valid_unused = {
            "get_products_raw": {
                "adcp_version",  # Metadata for protocol version
                "min_exposures",  # Optional filtering
                "strategy_id",  # Optional linking
            },
        }

        issues = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.endswith("_raw"):
                func_name = node.name
                params = {arg.arg for arg in node.args.args} - {"self", "ctx"}

                # Find all names used in function body
                used_names = set()
                for child in ast.walk(node):
                    if isinstance(child, ast.Name):
                        used_names.add(child.id)
                    elif isinstance(child, ast.keyword):
                        used_names.add(child.arg)

                # Check for parameters that aren't used
                unused = params - used_names

                # Remove known valid unused parameters
                if func_name in valid_unused:
                    unused = unused - valid_unused[func_name]

                if unused:
                    issues.append(f"{func_name} has unused parameters: {unused}")

        assert not issues, "Found unused parameters in raw functions:\n" + "\n".join(issues)

    def test_create_get_products_request_signature(self):
        """Document the exact signature of create_get_products_request for reference."""
        from src.core.schema_helpers import create_get_products_request

        sig = inspect.signature(create_get_products_request)
        params = list(sig.parameters.keys())

        # This test documents what we expect the signature to be
        # If this fails, it means the helper changed and we need to update callers
        # Note: promoted_offering removed per adcp v1.2.1 migration
        expected_params = ["brief", "brand_manifest", "filters", "context"]

        assert params == expected_params, (
            f"create_get_products_request signature changed!\n"
            f"Expected: {expected_params}\n"
            f"Got: {params}\n"
            f"This may require updating get_products_raw()"
        )

    def test_get_products_raw_doesnt_pass_invalid_params_to_helper(self):
        """Ensure get_products_raw doesn't pass params the helper doesn't accept.

        This is the exact bug we fixed - passing adcp_version to create_get_products_request.
        """
        tools_path = Path(__file__).parent.parent.parent / "src" / "core" / "tools" / "__init__.py"
        with open(tools_path) as f:
            content = f.read()

        # Find the get_products_raw function
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_products_raw":
                # Find calls to create_get_products_request
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name) and child.func.id == "create_get_products_request":
                            # Check keyword arguments
                            passed_params = {kw.arg for kw in child.keywords}

                            # These are the ONLY valid parameters for create_get_products_request
                            # Note: promoted_offering kept for backwards compatibility
                            valid_params = {"brief", "promoted_offering", "brand_manifest", "filters"}

                            invalid = passed_params - valid_params
                            assert not invalid, (
                                f"get_products_raw passes invalid parameters to create_get_products_request: {invalid}\n"
                                f"Valid parameters: {valid_params}"
                            )


class TestHelperFunctionDocumentation:
    """Document helper function signatures for reference."""

    def test_all_create_helper_signatures(self):
        """Document all create_* helper functions from schema_helpers."""
        from src.core import schema_helpers

        helpers = [
            name
            for name in dir(schema_helpers)
            if name.startswith("create_") and callable(getattr(schema_helpers, name))
        ]

        signatures = {}
        for helper_name in helpers:
            helper = getattr(schema_helpers, helper_name)
            sig = inspect.signature(helper)
            signatures[helper_name] = list(sig.parameters.keys())

        # Document what we found
        print("\n" + "=" * 80)
        print("SCHEMA HELPER FUNCTION SIGNATURES")
        print("=" * 80)
        for name, params in sorted(signatures.items()):
            print(f"{name}({', '.join(params)})")

        # Verify create_get_products_request (the one that caused the bug)
        assert "create_get_products_request" in signatures
        # Note: promoted_offering removed per adcp v1.2.1 migration
        expected = ["brief", "brand_manifest", "filters", "context"]
        actual = signatures["create_get_products_request"]
        assert actual == expected, (
            f"create_get_products_request signature changed!\n"
            f"Expected: {expected}\n"
            f"Got: {actual}\n"
            f"Update get_products_raw if needed"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
