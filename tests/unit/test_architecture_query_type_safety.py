"""Guard: Database queries must use types matching column definitions.

When filtering by ID columns, the Python type of the filter value must match
the SQLAlchemy column type. Passing strings to Integer columns (or vice versa)
causes silent query failures where the query returns 0 rows.

Scanning approach: Hybrid — introspection to build column type inventory from
SQLAlchemy models, AST to find .in_() and filter_by() query sites and verify
type compatibility.

beads: salesagent-v0kb (structural-guard epic), salesagent-mq3n (PricingOption bug)
"""

import ast
from pathlib import Path

# Files to scan for database queries
QUERY_FILES = [
    "src/core/tools/media_buy_delivery.py",
    "src/core/tools/media_buy_create.py",
    "src/core/tools/media_buy_update.py",
    "src/core/tools/media_buy_list.py",
    "src/core/tools/products.py",
    "src/core/tools/creatives/listing.py",
    "src/core/tools/creatives/_sync.py",
    "src/core/tools/creatives/_assignments.py",
    "src/core/tools/performance.py",
    "src/core/tools/signals.py",
    "src/core/tools/task_management.py",
    "src/core/context_manager.py",
]

# Models with Integer PK columns — queries filtering on these need int values
INTEGER_PK_MODELS = {
    "PricingOption": "id",
    "TenantAuthConfig": "id",
    "AuditLog": "log_id",
    "CreativeAgent": "id",
    "SignalsAgent": "id",
    "GAMInventory": "id",
    "InventoryProfile": "id",
    "ProductInventoryMapping": "id",
    "FormatPerformanceMetrics": "id",
    "GAMOrder": "id",
    "GAMLineItem": "id",
    "SyncJob": "sync_id",
    "WorkflowStep": "step_id",
    "ObjectWorkflowMapping": "id",
    "PublisherPartner": "id",
    "PushNotificationConfig": "id",
    "WebhookDeliveryRecord": "delivery_id",
    "WebhookDeliveryLog": "id",
}

# Known violations: (file_path, line_number, description)
# Each entry is a known type mismatch that needs fixing.
# FIXME: salesagent-mq3n — PricingOption.id queried with string values
KNOWN_VIOLATIONS = {
    "src/core/tools/media_buy_delivery.py::PricingOption.id.in_",
}


def _find_in_queries_on_integer_columns(filepath: str) -> list[tuple[int, str, str]]:
    """Find .in_() calls on Integer PK model columns.

    Returns list of (line_number, model_column, description) tuples.
    """
    path = Path(filepath)
    if not path.exists():
        return []

    source = path.read_text()
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Match pattern: Model.column.in_(args)
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "in_":
            continue

        # The value should be Model.column (another Attribute node)
        value = func.value
        if not isinstance(value, ast.Attribute):
            continue

        column_name = value.attr

        # The model is value.value (a Name node)
        if isinstance(value.value, ast.Name):
            model_name = value.value.id
        elif isinstance(value.value, ast.Attribute):
            model_name = value.value.attr
        else:
            continue

        # Check if this model.column is an Integer PK
        if model_name in INTEGER_PK_MODELS:
            pk_col = INTEGER_PK_MODELS[model_name]
            if column_name == pk_col:
                desc = f"{model_name}.{column_name}.in_(...)"
                results.append((node.lineno, f"{model_name}.{column_name}", desc))

    return results


def _find_filter_by_on_integer_columns(filepath: str) -> list[tuple[int, str, str]]:
    """Find filter_by() calls that pass values to Integer PK columns.

    Returns list of (line_number, model_column, description) tuples.
    """
    path = Path(filepath)
    if not path.exists():
        return []

    source = path.read_text()
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "filter_by":
            continue

        # Check keyword arguments for Integer PK column names
        for kw in node.keywords:
            if kw.arg is None:
                continue
            # Check if any kwarg matches an Integer PK column
            for model_name, pk_col in INTEGER_PK_MODELS.items():
                if kw.arg == pk_col or kw.arg == f"{model_name.lower()}_{pk_col}":
                    # Check if the value is a string literal (definite violation)
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        desc = f"filter_by({kw.arg}=<string literal>)"
                        results.append((node.lineno, f"?.{kw.arg}", desc))

    return results


class TestQueryTypeSafety:
    """Database queries must use types matching the column definition."""

    def test_no_in_queries_on_integer_pk_with_wrong_type(self):
        """All .in_() calls on Integer PK columns must be reviewed.

        Any .in_() on an Integer PK column is flagged because the argument type
        cannot be verified statically — the developer must ensure int values.
        Known violations are allowlisted with linked beads tasks.
        """
        violations = []

        for filepath in QUERY_FILES:
            sites = _find_in_queries_on_integer_columns(filepath)
            for line_no, model_col, desc in sites:
                key = f"{filepath}::{model_col}.in_"
                if key in KNOWN_VIOLATIONS:
                    continue  # Known, tracked by beads
                violations.append(f"  {filepath}:{line_no}: {desc}")

        assert not violations, (
            "New .in_() queries on Integer PK columns detected. "
            "These need type verification — ensure int values are passed:\n" + "\n".join(violations)
        )

    def test_no_string_literals_in_filter_by_for_integer_pks(self):
        """filter_by() must not pass string literals for Integer PK columns."""
        violations = []

        for filepath in QUERY_FILES:
            sites = _find_filter_by_on_integer_columns(filepath)
            for line_no, _model_col, desc in sites:
                violations.append(f"  {filepath}:{line_no}: {desc}")

        assert not violations, "String literals passed to Integer PK columns in filter_by():\n" + "\n".join(violations)

    def test_known_violations_still_exist(self):
        """Known violations in the allowlist must still be actual violations.

        If a violation gets fixed, remove it from KNOWN_VIOLATIONS.
        """
        still_violated = set()

        for violation_key in KNOWN_VIOLATIONS:
            filepath, pattern = violation_key.split("::", 1)
            # Parse the pattern: "Model.column.in_"
            parts = pattern.replace(".in_", "").split(".")
            if len(parts) != 2:
                continue
            model_name, column_name = parts

            sites = _find_in_queries_on_integer_columns(filepath)
            for _, model_col, _ in sites:
                if model_col == f"{model_name}.{column_name}":
                    still_violated.add(violation_key)
                    break

        fixed = KNOWN_VIOLATIONS - still_violated
        if fixed:
            msg = "These known violations have been FIXED — remove from KNOWN_VIOLATIONS:\n" + "\n".join(
                f"  - {v}" for v in sorted(fixed)
            )
            raise AssertionError(msg)
