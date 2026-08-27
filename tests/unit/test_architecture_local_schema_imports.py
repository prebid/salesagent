"""Guard: src/ files must use local schema subclasses, not raw SDK types.

When src/core/schemas/ defines a local subclass of an adcp SDK type (e.g.
Product extends adcp.types.Product), all other src/ code must import from
src.core.schemas, not directly from adcp.types. Importing the SDK type
bypasses local model_config (Pattern #7), custom validators, and nested
serialization overrides (Pattern #4).

Legitimate exceptions:
  - TYPE_CHECKING blocks (type-only, never instantiated at runtime)
  - src/core/schemas/ itself (defines the subclasses; must import SDK parents)
  - Explicit allowlist entries for code that intentionally needs the raw SDK type

Scanning approach: AST parse all .py files in src/ (excluding src/core/schemas/).
For each ``from adcp.types import X`` or ``from adcp.types.generated_poc... import X``,
check if X matches a locally-exported schema name AND the imported SDK class is
actually an ancestor of that local schema class -- the SDK's flat namespace has
name collisions (e.g. two distinct ``Account`` types: the sync_accounts domain
entity at ``adcp.types.Account`` and the capabilities response's embedded
account-block at ``adcp.types.generated_poc.protocol.get_adcp_capabilities_response.Account``)
so bare-name matching alone produces false positives. If so, flag it.
"""

import ast
import importlib
import inspect
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist

# ── Build set of locally-exported schema names ───────────────────────────

_LOCAL_SCHEMA_CLASSES: dict[str, type] | None = None


def _get_local_schema_classes() -> dict[str, type]:
    """Get name -> class for classes defined in src.core.schemas (cached)."""
    global _LOCAL_SCHEMA_CLASSES
    if _LOCAL_SCHEMA_CLASSES is not None:
        return _LOCAL_SCHEMA_CLASSES

    schemas = importlib.import_module("src.core.schemas")
    classes: dict[str, type] = {}
    for name, obj in inspect.getmembers(schemas, inspect.isclass):
        # Only include classes actually defined in the schemas package
        if obj.__module__ and obj.__module__.startswith("src.core.schemas"):
            classes[name] = obj
    _LOCAL_SCHEMA_CLASSES = classes
    return _LOCAL_SCHEMA_CLASSES


def _get_local_schema_names() -> set[str]:
    """Get names of classes defined in src.core.schemas (cached)."""
    return set(_get_local_schema_classes().keys())


def _shadows_local_schema(module: str, imported_name: str, local_classes: dict[str, type]) -> bool:
    """True only if the imported SDK class is an actual ancestor of the local subclass.

    Bare-name equality isn't enough -- the SDK's flat namespace has two distinct
    types both named ``Account`` (and potentially other collisions). Resolve the
    imported symbol from its exact module and check the real subclass
    relationship before flagging.
    """
    local_cls = local_classes.get(imported_name)
    if local_cls is None:
        return False
    try:
        imported_module = importlib.import_module(module)
        imported_cls = getattr(imported_module, imported_name)
    except (ImportError, AttributeError):
        # Can't resolve -- fall back to the pre-existing bare-name behavior
        # rather than silently skip a potential real violation.
        return True
    if not isinstance(imported_cls, type):
        return True
    return issubclass(local_cls, imported_cls)


# ── Allowlist ────────────────────────────────────────────────────────────
# Format: (relative_path_from_src, imported_name)
# Pre-existing violations that predate this guard. The list can only shrink.
ALLOWLIST: set[tuple[str, str]] = {
    # FIXME(#1388): xandr adapter imports SDK types directly
    ("src/adapters/xandr.py", "DeliveryMeasurement"),
    # NOTE: xandr.py's DeliveryType import and capabilities.py's Targeting import
    # were removed from this allowlist (salesagent-3s5a) -- the subclass-identity
    # check (_shadows_local_schema) proved neither is a real Pattern #1 violation:
    # both are SDK flat-namespace name collisions with an UNRELATED local schema
    # class of the same bare name, not the actual SDK parent the local class extends.
    # FIXME(#1388): admin blueprints import SDK types directly
    ("src/admin/blueprints/operations.py", "Package"),
    # FIXME(#1388): core modules import SDK types directly
    ("src/core/creative_agent_registry.py", "ListCreativeFormatsRequest"),
    ("src/core/schema_helpers.py", "GetProductsResponse"),
    ("src/core/schema_helpers.py", "Product"),
    ("src/core/schema_helpers.py", "ProductFilters"),
    # FIXME(#1388): tools import SDK types directly
    ("src/core/tools/creative_formats.py", "FormatId"),
    ("src/core/tools/products.py", "FormatId"),
    ("src/core/tools/products.py", "ProductFilters"),
    # FIXME(#1388): services import SDK types directly
    ("src/services/dynamic_pricing_service.py", "FormatId"),
}


def _get_src_files() -> list[Path]:
    """Get all Python files in src/ excluding src/core/schemas/."""
    src_dir = Path("src")
    files = []
    for p in sorted(src_dir.rglob("*.py")):
        # Exclude the schemas package itself
        if "src/core/schemas" in str(p):
            continue
        files.append(p)
    return files


def _is_inside_type_checking_block(node: ast.ImportFrom, tree: ast.Module) -> bool:
    """Check if an ImportFrom node is inside an `if TYPE_CHECKING:` block.

    Walks top-level If statements looking for TYPE_CHECKING guards.
    """
    for top_node in ast.walk(tree):
        if not isinstance(top_node, ast.If):
            continue
        # Check if the condition is TYPE_CHECKING
        test = top_node.test
        is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if not is_type_checking:
            continue
        # Check if our import node is in the body of this If
        for child in ast.walk(top_node):
            if child is node:
                return True
    return False


def _is_library_alias_import(alias: ast.alias) -> bool:
    """Check if import uses the Library* alias convention.

    e.g. ``from adcp.types import Product as LibraryProduct`` is a Library alias
    import used when defining subclasses in schemas. These are fine because
    they're importing the parent class explicitly for inheritance.
    """
    return alias.asname is not None and alias.asname.startswith("Library")


class TestLocalSchemaImports:
    """src/ files must import local schema subclasses, not raw SDK types."""

    def test_no_sdk_import_when_local_exists(self):
        """No src/ file imports an adcp type that has a local subclass in schemas."""
        local_classes = _get_local_schema_classes()
        violations = []

        for src_file in _get_src_files():
            source = src_file.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            rel_path = str(src_file)

            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue

                # Only check adcp imports
                module = node.module or ""
                if not module.startswith("adcp"):
                    continue

                # Skip TYPE_CHECKING blocks
                if _is_inside_type_checking_block(node, tree):
                    continue

                for alias in node.names:
                    imported_name = alias.asname or alias.name

                    # Skip Library* alias imports (used for defining subclasses)
                    if _is_library_alias_import(alias):
                        continue

                    # Check if this imported name shadows a local schema (by
                    # actual subclass relationship, not bare-name collision)
                    if _shadows_local_schema(module, imported_name, local_classes):
                        key = (rel_path, imported_name)
                        if key in ALLOWLIST:
                            continue
                        violations.append(
                            f"{rel_path}:{node.lineno} imports '{imported_name}' "
                            f"from '{module}' but src.core.schemas exports a local "
                            f"subclass. Use: from src.core.schemas import {imported_name}"
                        )

        assert not violations, (
            "src/ files importing SDK types that have local schema subclasses "
            "(use src.core.schemas instead):\n" + "\n".join(f"  - {v}" for v in violations)
        )

    def test_allowlist_entries_are_still_violations(self):
        """Every allowlist entry must still be a real violation.

        If you fix a bypass, remove it from ALLOWLIST.
        """
        if not ALLOWLIST:
            return  # Nothing to check

        local_classes = _get_local_schema_classes()
        still_violations = set()

        for src_file in _get_src_files():
            source = src_file.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            rel_path = str(src_file)

            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = node.module or ""
                if not module.startswith("adcp"):
                    continue
                if _is_inside_type_checking_block(node, tree):
                    continue
                for alias in node.names:
                    imported_name = alias.asname or alias.name
                    if _is_library_alias_import(alias):
                        continue
                    if _shadows_local_schema(module, imported_name, local_classes):
                        still_violations.add((rel_path, imported_name))

        assert_violations_match_allowlist(
            still_violations,
            ALLOWLIST,
            fix_hint="Remove fixed entries from ALLOWLIST.",
        )
