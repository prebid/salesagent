"""Guard: No bare BaseModel inheritance in schema classes.

Schema classes in src/core/schemas/ must inherit from SalesAgentBaseModel,
AdCPBaseModel, or a Library* type that carries model_config with
ConfigDict(extra=get_pydantic_extra_mode()). Bare pydantic.BaseModel lacks
the environment-based extra-field policy (Pattern #7), which means:
  - Production: extra fields would raise ValidationError (Pydantic default)
    instead of being silently ignored for forward compatibility
  - Development: extra fields would NOT be rejected, hiding spec drift

Scanning approach: AST parse all files in src/core/schemas/, find ClassDef
nodes whose bases include the literal name 'BaseModel'. These are violations
unless they explicitly set model_config = ConfigDict(...).

Allowlist: Violations that predate this guard are tracked here. The list
can only shrink.
"""

import ast
from pathlib import Path

# ── Allowlist ────────────────────────────────────────────────────────────
# Format: (filename, class_name)
# Every entry must have a matching FIXME comment at the source location.
ALLOWLIST: set[tuple[str, str]] = {
    # SyncResponseAccount was extracted from SDK 5.7 as a plain BaseModel.
    # It should be migrated to SalesAgentBaseModel with model_config.
    # FIXME(#1360): migrate SyncResponseAccount to SalesAgentBaseModel
    ("account.py", "SyncResponseAccount"),
}


def _get_schema_files() -> list[Path]:
    """Get all Python source files in the schemas package."""
    schemas_dir = Path("src/core/schemas")
    if schemas_dir.is_dir():
        return sorted(schemas_dir.glob("*.py"))
    raise FileNotFoundError("src/core/schemas/ package not found")


def _has_model_config_in_body(class_node: ast.ClassDef) -> bool:
    """Check if a ClassDef sets model_config in its body.

    Looks for: model_config = ConfigDict(...)
    """
    for stmt in class_node.body:
        # model_config = ConfigDict(...)
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "model_config":
                    return True
        # model_config: ClassVar = ConfigDict(...)
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == "model_config":
                return True
    return False


def _inherits_bare_basemodel(class_node: ast.ClassDef) -> bool:
    """Check if any base is the literal name 'BaseModel'.

    Does NOT flag Library* types, SalesAgentBaseModel, AdCPBaseModel,
    NestedModelSerializerMixin, or any other non-BaseModel name.
    """
    for base in class_node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
    return False


class TestNoBarePydanticBaseModel:
    """Schema classes must not inherit bare pydantic.BaseModel without model_config."""

    def test_no_bare_basemodel_in_schemas(self):
        """Every class inheriting BaseModel must set model_config or be allowlisted."""
        violations = []
        for schema_file in _get_schema_files():
            source = schema_file.read_text()
            tree = ast.parse(source)
            filename = schema_file.name

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue

                if not _inherits_bare_basemodel(node):
                    continue

                # Bare BaseModel found -- check if it has model_config
                if _has_model_config_in_body(node):
                    continue

                # Check allowlist
                if (filename, node.name) in ALLOWLIST:
                    continue

                violations.append(
                    f"{filename}:{node.lineno} class {node.name} inherits bare "
                    f"BaseModel without model_config = ConfigDict(...). "
                    f"Use SalesAgentBaseModel or add model_config."
                )

        assert not violations, (
            "Schema classes inherit bare pydantic.BaseModel without "
            "model_config (missing Pattern #7 extra-field policy):\n" + "\n".join(f"  - {v}" for v in violations)
        )

    def test_allowlist_entries_are_still_violations(self):
        """Every allowlist entry must still be a real violation.

        If you fix a violation, remove it from ALLOWLIST. This test
        catches stale entries.
        """
        still_violations = set()
        for schema_file in _get_schema_files():
            source = schema_file.read_text()
            tree = ast.parse(source)
            filename = schema_file.name

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if _inherits_bare_basemodel(node) and not _has_model_config_in_body(node):
                    still_violations.add((filename, node.name))

        stale = ALLOWLIST - still_violations
        assert not stale, "Allowlist entries that are no longer violations (remove them):\n" + "\n".join(
            f"  - {f}:{c}" for f, c in sorted(stale)
        )
