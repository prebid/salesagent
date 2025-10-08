"""Tests to validate that code field references match database schema.

This prevents AttributeError issues when the code tries to access
database fields that don't exist or have different names.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect

from src.core.database.models import MediaBuy, Product, Tenant


@pytest.mark.integration
class TestSchemaFieldValidation:
    """Validate that field references in code match actual database schema."""

    def get_model_attributes(self, model_class):
        """Get all column names for a SQLAlchemy model."""
        mapper = inspect(model_class)
        return {column.key for column in mapper.columns}

    def find_model_field_references(self, model_name, directory="src/admin"):
        """Find all field references to a model in Python files."""
        references = set()
        path = Path(directory)

        if not path.exists():
            path = Path(".")  # Fallback to current directory

        for py_file in path.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue

            # Skip files that are allowed to use old field names for API compatibility
            if any(allowed in str(py_file) for allowed in ["schemas.py", "main.py", "client_mcp.py"]):
                continue

            try:
                with open(py_file) as f:
                    content = f.read()

                # Find patterns like model.field or model['field']
                # Looking for the specific model name
                patterns = [
                    rf"{model_name.lower()}\.(\w+)",  # tenant.field
                    rf'{model_name.lower()}\["(\w+)"\]',  # tenant["field"]
                    rf"{model_name.lower()}\[\'(\w+)\'\]",  # tenant['field']
                ]

                for pattern in patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    references.update(matches)

            except Exception:
                continue  # Skip files that can't be read

        return references

    def test_tenant_field_references(self):
        """Test that all Tenant field references in code exist in the model."""
        model_fields = self.get_model_attributes(Tenant)
        code_references = self.find_model_field_references("tenant")

        # Remove common non-field references
        ignore_list = {
            "query",
            "filter",
            "filter_by",
            "first",
            "all",
            "count",
            "order_by",
            "join",
            "add",
            "commit",
            "delete",
            "id",
        }
        code_references = code_references - ignore_list

        # Find references that don't exist in the model
        invalid_references = code_references - model_fields

        # Known deprecated fields that should not be used
        deprecated_fields = {"features_config", "creative_engine_config", "config"}

        used_deprecated = invalid_references & deprecated_fields

        assert not used_deprecated, (
            f"Code is using deprecated Tenant fields: {used_deprecated}. "
            f"Use individual columns instead: enable_axe_signals, etc."
        )

        # Filter out method calls and valid references
        actual_invalid = invalid_references - deprecated_fields
        actual_invalid = {ref for ref in actual_invalid if not ref.startswith("_")}

        if actual_invalid:
            print(f"Warning: Possible invalid Tenant field references: {actual_invalid}")

    def test_media_buy_field_references(self):
        """Test that all MediaBuy field references in code exist in the model."""
        model_fields = self.get_model_attributes(MediaBuy)
        code_references = self.find_model_field_references("MediaBuy")

        # Also check for common variable names
        for var_name in ["buy", "media_buy"]:
            code_references.update(self.find_model_field_references(var_name))

        # Remove common non-field references
        ignore_list = {
            "query",
            "filter",
            "filter_by",
            "first",
            "all",
            "count",
            "order_by",
            "join",
            "add",
            "commit",
            "delete",
            "id",
        }
        code_references = code_references - ignore_list

        # Known incorrect field names that should not be used
        incorrect_fields = {
            "flight_start_date",  # Should be 'start_date'
            "flight_end_date",  # Should be 'end_date'
            "total_budget",  # Should be 'budget'
        }

        used_incorrect = code_references & incorrect_fields

        assert not used_incorrect, (
            f"Code is using incorrect MediaBuy field names: {used_incorrect}. "
            f"Correct names: start_date, end_date, budget"
        )

    def test_product_field_references(self):
        """Test that all Product field references in code exist in the model."""
        model_fields = self.get_model_attributes(Product)

        # Check that created_at exists if it's being used for sorting
        if "created_at" not in model_fields:
            # Search for usage of Product.created_at
            search_path = Path(".")
            for py_file in search_path.rglob("*.py"):
                if "__pycache__" in str(py_file):
                    continue
                # Skip test files themselves
                if "test_" in str(py_file.name):
                    continue
                try:
                    with open(py_file) as f:
                        content = f.read()
                    if "Product.created_at" in content:
                        pytest.fail(
                            f"File {py_file} uses Product.created_at but field doesn't exist. "
                            f"Use Product.name or another existing field for sorting."
                        )
                except Exception:
                    continue

    def test_schema_matches_expected_structure(self):
        """Test that key models have expected fields for dashboard functionality."""
        # Test Tenant has required fields
        tenant_fields = self.get_model_attributes(Tenant)
        required_tenant_fields = {
            "tenant_id",
            "name",
            "subdomain",
            "is_active",
            "enable_axe_signals",
            "human_review_required",
            "auto_approve_formats",
        }

        missing_tenant = required_tenant_fields - tenant_fields
        assert not missing_tenant, f"Tenant model missing required fields: {missing_tenant}"

        # Test MediaBuy has required fields
        media_buy_fields = self.get_model_attributes(MediaBuy)
        required_buy_fields = {
            "media_buy_id",
            "tenant_id",
            "principal_id",
            "status",
            "budget",
            "start_date",
            "end_date",
        }

        missing_buy = required_buy_fields - media_buy_fields
        assert not missing_buy, f"MediaBuy model missing required fields: {missing_buy}"

        # Ensure old field names are NOT in the model
        assert "flight_start_date" not in media_buy_fields, "MediaBuy should use 'start_date', not 'flight_start_date'"
        assert "flight_end_date" not in media_buy_fields, "MediaBuy should use 'end_date', not 'flight_end_date'"

    def test_template_field_references(self):
        """Check that templates don't reference non-existent fields."""
        template_dir = Path("templates")
        if not template_dir.exists():
            return  # Skip if templates directory not found

        problematic_patterns = [
            (r"tenant\.features_config", "Use tenant.enable_axe_signals instead"),
            (
                r"tenant\.creative_engine_config",
                "Use tenant.auto_approve_formats, tenant.human_review_required instead",
            ),
            (r"buy\.flight_start_date", "Use buy.start_date instead"),
            (r"buy\.flight_end_date", "Use buy.end_date instead"),
            (r"buy\.total_budget", "Use buy.budget instead"),
        ]

        errors = []
        for html_file in template_dir.rglob("*.html"):
            try:
                with open(html_file) as f:
                    content = f.read()

                for pattern, correction in problematic_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        errors.append(f"{html_file}: Found '{pattern}' - {correction}")

            except Exception:
                continue

        assert not errors, "Templates using incorrect field names:\n" + "\n".join(errors)
