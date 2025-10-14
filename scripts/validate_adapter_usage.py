#!/usr/bin/env python3
"""
Static validation of adapter schema usage.

Checks that all response constructor calls in main.py use correct field names
as defined in schema_adapters.py.

Exit codes:
  0 - All validations passed
  1 - Validation errors found
  2 - Script error (file not found, parse error, etc.)
"""

import ast
import sys
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path


@dataclass
class AdapterSchema:
    """Schema definition for an adapter class."""

    required: list[str]
    optional: list[str]


@dataclass
class ConstructorCall:
    """A constructor call site in implementation code."""

    class_name: str
    fields: list[str]
    location: tuple[Path, int, int]  # (file, line, col)


@dataclass
class ValidationError:
    """A validation error found in the code."""

    class_name: str
    location: tuple[Path, int, int]
    error_type: str  # "missing_field", "unknown_field"
    field_name: str
    suggestion: str | None = None


def extract_adapter_schemas(adapter_file: Path) -> dict[str, AdapterSchema]:
    """
    Extract schema definitions from schema_adapters.py.

    Returns:
        Dictionary mapping class names to their field definitions.
        Example: {
            "GetMediaBuyDeliveryResponse": AdapterSchema(
                required=["media_buy_deliveries", "reporting_period", "currency"],
                optional=["errors", "dry_run"]
            )
        }
    """
    if not adapter_file.exists():
        raise FileNotFoundError(f"Adapter file not found: {adapter_file}")

    tree = ast.parse(adapter_file.read_text())
    schemas = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if _inherits_from_basemodel(node):
                schema_name = node.name
                fields = _extract_fields(node)
                schemas[schema_name] = fields

    return schemas


def _inherits_from_basemodel(node: ast.ClassDef) -> bool:
    """Check if a class inherits from BaseModel or AdCPBaseModel."""
    for base in node.bases:
        if isinstance(base, ast.Name):
            if base.id in ("BaseModel", "AdCPBaseModel"):
                return True
    return False


def _extract_fields(class_node: ast.ClassDef) -> AdapterSchema:
    """Extract required and optional fields from Pydantic model."""
    required = []
    optional = []

    for item in class_node.body:
        # Pattern: field: Type or field: Type = default
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            field_name = item.target.id

            # Skip special fields
            if field_name == "model_config":
                continue

            is_optional = _is_optional_annotation(item.annotation)

            # Check if field has Field(...) - Ellipsis means required
            is_required_field = _is_required_field_call(item.value)

            if is_required_field:
                required.append(field_name)
            elif is_optional or item.value is not None:
                optional.append(field_name)
            else:
                required.append(field_name)

    return AdapterSchema(required=required, optional=optional)


def _is_required_field_call(value_node) -> bool:
    """Check if value is Field(...) indicating a required field."""
    if not isinstance(value_node, ast.Call):
        return False

    # Check if it's a Field() call
    if isinstance(value_node.func, ast.Name) and value_node.func.id == "Field":
        # Check if first arg is Ellipsis (...)
        if value_node.args and isinstance(value_node.args[0], ast.Constant):
            if value_node.args[0].value is ...:  # Ellipsis constant
                return True

    return False


def _is_optional_annotation(annotation) -> bool:
    """Check if annotation is Optional[T] or T | None."""
    # Handle T | None (Python 3.10+)
    if isinstance(annotation, ast.BinOp):
        if isinstance(annotation.op, ast.BitOr):
            return _has_none_type(annotation)

    # Handle Optional[T]
    if isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name):
            if annotation.value.id == "Optional":
                return True

    return False


def _has_none_type(node: ast.BinOp) -> bool:
    """Check if BinOp contains None type."""
    left = node.left
    right = node.right

    # Check if either side is None constant
    if isinstance(left, ast.Constant) and left.value is None:
        return True
    if isinstance(right, ast.Constant) and right.value is None:
        return True

    return False


def find_response_constructors(impl_file: Path, adapter_classes: set[str]) -> list[ConstructorCall]:
    """
    Find all constructor calls for adapter response classes.

    Args:
        impl_file: Path to implementation file (e.g., main.py)
        adapter_classes: Set of adapter class names to look for

    Returns:
        List of constructor calls with their field names and locations.
    """
    if not impl_file.exists():
        raise FileNotFoundError(f"Implementation file not found: {impl_file}")

    tree = ast.parse(impl_file.read_text())
    constructor_calls = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if it's a class constructor call
            if isinstance(node.func, ast.Name):
                class_name = node.func.id

                if class_name in adapter_classes:
                    fields = _extract_call_fields(node)
                    location = (impl_file, node.lineno, node.col_offset)
                    constructor_calls.append(ConstructorCall(class_name=class_name, fields=fields, location=location))

    return constructor_calls


def _extract_call_fields(call_node: ast.Call) -> list[str]:
    """Extract field names from constructor call."""
    fields = []

    # Keyword arguments: ClassName(field1=value1, field2=value2)
    for keyword in call_node.keywords:
        if keyword.arg:  # Not **kwargs
            fields.append(keyword.arg)

    return fields


def validate_constructor_calls(
    schemas: dict[str, AdapterSchema], constructor_calls: list[ConstructorCall]
) -> list[ValidationError]:
    """Validate all constructor calls against schema definitions."""
    errors = []

    for call in constructor_calls:
        schema = schemas.get(call.class_name)
        if not schema:
            continue  # Skip if schema not found (defensive)

        expected_fields = set(schema.required + schema.optional)
        actual_fields = set(call.fields)

        # Find unknown fields (likely typos or outdated)
        unknown = actual_fields - expected_fields
        for field in unknown:
            suggestion = _find_closest_match(field, expected_fields)
            errors.append(
                ValidationError(
                    class_name=call.class_name,
                    location=call.location,
                    error_type="unknown_field",
                    field_name=field,
                    suggestion=suggestion,
                )
            )

        # Find missing required fields
        missing = set(schema.required) - actual_fields
        for field in missing:
            errors.append(
                ValidationError(
                    class_name=call.class_name,
                    location=call.location,
                    error_type="missing_field",
                    field_name=field,
                    suggestion=None,
                )
            )

    return errors


def _find_closest_match(field: str, valid_fields: set[str]) -> str | None:
    """Use Levenshtein distance to suggest corrections."""
    matches = get_close_matches(field, valid_fields, n=1, cutoff=0.6)
    return matches[0] if matches else None


def format_errors(errors: list[ValidationError]) -> str:
    """Format validation errors for human consumption."""
    if not errors:
        return "✅ All adapter schemas valid"

    output = [f"❌ Adapter Schema Validation Failed ({len(errors)} errors)\n"]

    # Sort by location for consistent output
    sorted_errors = sorted(errors, key=lambda e: (str(e.location[0]), e.location[1], e.location[2]))

    for error in sorted_errors:
        file_path, line, col = error.location
        output.append(f"{file_path}:{line}:{col} - {error.class_name}")

        if error.error_type == "unknown_field":
            output.append(f"  Unknown field: '{error.field_name}'")
            if error.suggestion:
                output.append(f"  Did you mean: '{error.suggestion}'?")

        elif error.error_type == "missing_field":
            output.append(f"  Missing required field: '{error.field_name}'")

        output.append("")  # Blank line between errors

    return "\n".join(output)


def main() -> int:
    """Entry point for pre-commit hook."""
    try:
        # Paths relative to repo root
        repo_root = Path(__file__).parent.parent
        adapter_file = repo_root / "src/core/schema_adapters.py"
        impl_file = repo_root / "src/core/main.py"

        # Stage 1: Extract schemas
        schemas = extract_adapter_schemas(adapter_file)

        if not schemas:
            print("⚠️  No adapter schemas found in schema_adapters.py")
            return 0

        # Filter to only Response classes (not Request classes)
        # This focuses validation on the original problem: response construction
        response_schemas = {name: schema for name, schema in schemas.items() if name.endswith("Response")}

        if not response_schemas:
            print("⚠️  No response adapter schemas found")
            return 0

        # Stage 2: Find constructor calls
        adapter_classes = set(response_schemas.keys())
        constructor_calls = find_response_constructors(impl_file, adapter_classes)

        if not constructor_calls:
            print("✅ No adapter constructor calls found (nothing to validate)")
            return 0

        # Stage 3: Validate
        errors = validate_constructor_calls(response_schemas, constructor_calls)

        # Stage 4: Report
        print(format_errors(errors))

        return 1 if errors else 0

    except Exception as e:
        print(f"❌ Script error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
