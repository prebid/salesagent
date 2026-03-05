"""Regression tests for PR #1071 review findings.

Tests for bugs found during Chris's code review of the AdCP v3.6 upgrade PR.
Each test demonstrates the bug and guards against regression.
"""

from __future__ import annotations

import pytest


class TestDeliveryLoopErrorHandling:
    """salesagent-m06j: raise e on line 415 kills entire multi-buy response.

    The except block in _get_media_buy_delivery_impl re-raises immediately,
    making logger.error() dead code. A single media buy error should be logged
    and skipped, not kill the entire response.
    """

    def test_raise_e_makes_logger_error_dead_code(self):
        """The except block must NOT re-raise — it should log and continue.

        Verify by inspecting the source AST: if `raise` appears before
        `logger.error()` in the except block, the test fails.
        """
        import ast
        from pathlib import Path

        source = Path("src/core/tools/media_buy_delivery.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue

            # Find the except block that handles media buy delivery errors
            # It should contain logger.error with "Error getting delivery"
            has_logger_error = False
            has_raise_before_logger = False

            for i, stmt in enumerate(node.body):
                # Check for logger.error call with "Error getting delivery"
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == "error"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "logger"
                    ):
                        # Check if message contains "Error getting delivery"
                        if stmt.value.args:
                            arg = stmt.value.args[0]
                            if isinstance(arg, ast.JoinedStr):
                                for val in arg.values:
                                    if isinstance(val, ast.Constant) and "Error getting delivery" in str(
                                        val.value
                                    ):
                                        has_logger_error = True
                                        break

                # Check for raise statement before this logger.error
                if isinstance(stmt, ast.Raise) and not has_logger_error:
                    has_raise_before_logger = True

            if has_logger_error and has_raise_before_logger:
                pytest.fail(
                    "media_buy_delivery.py: 'raise e' appears before logger.error() "
                    "in the delivery loop except block, making error logging dead code. "
                    "A single media buy error kills the entire multi-buy response. "
                    "Fix: remove 'raise e' so the loop can log and continue."
                )


class TestBrandExtractionFromPydanticModel:
    """salesagent-7bzt: isinstance(req.brand, dict) always False after Pydantic parsing.

    Pydantic coerces dict input to BrandReference, so isinstance(req.brand, dict)
    is always False. The brand domain is never extracted, and tenants with
    require_brand policy reject ALL product discovery requests.
    """

    def test_brand_reference_is_not_dict_after_pydantic(self):
        """After Pydantic parsing, req.brand is BrandReference, not dict."""
        from src.core.schemas import GetProductsRequest

        req = GetProductsRequest(
            brand={"domain": "example.com"},
            brief="test products",
        )

        # After Pydantic coercion, brand is NOT a dict
        assert not isinstance(req.brand, dict), "Pydantic should coerce dict to BrandReference"
        # It should be a BrandReference with .domain attribute
        assert hasattr(req.brand, "domain")
        assert req.brand.domain == "example.com"

    def test_brand_domain_extraction_uses_model_attribute(self):
        """The products.py code must access req.brand.domain, not brand_dict.get('domain').

        Verify by inspecting source: if isinstance(req.brand, dict) pattern
        is used for domain extraction, the test fails.
        """
        import ast
        from pathlib import Path

        source = Path("src/core/tools/products.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Look for isinstance(req.brand, dict) or isinstance(*.brand, dict)
            if not isinstance(node.func, ast.Name) or node.func.id != "isinstance":
                continue

            if len(node.args) < 2:
                continue

            # Check if second arg is 'dict'
            if not (isinstance(node.args[1], ast.Name) and node.args[1].id == "dict"):
                continue

            # Check if first arg accesses .brand
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Attribute) and first_arg.attr == "brand":
                pytest.fail(
                    "products.py uses isinstance(req.brand, dict) which is always False "
                    "because Pydantic coerces dict input to BrandReference. "
                    "Fix: access req.brand.domain directly instead."
                )


class TestAuditLogBrandFieldName:
    """salesagent-bff0: audit log key 'has_brand_manifest' is stale after 3.6 rename.

    In adcp 3.6.0, brand_manifest was renamed to brand. The audit log detail key
    should reflect this rename for clarity and consistency.
    """

    def test_audit_log_uses_has_brand_not_has_brand_manifest(self):
        """Audit log details should use 'has_brand' key, not 'has_brand_manifest'.

        The field was renamed from brand_manifest to brand in adcp 3.6.0.
        """
        import ast
        from pathlib import Path

        source = Path("src/core/tools/products.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue

            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "has_brand_manifest":
                    pytest.fail(
                        "products.py audit log uses stale key 'has_brand_manifest'. "
                        "In adcp 3.6.0, brand_manifest was renamed to brand. "
                        "Fix: rename key to 'has_brand'."
                    )
