"""Guard: Schema classes must extend adcp library base types.

Every schema class in src/core/schemas.py that corresponds to an adcp library
type must inherit from it via the Library* alias pattern. This prevents field
drift, ensures forward compatibility with adcp upgrades, and maintains protocol
compliance.

Scanning approach: Introspection — import the schemas module, discover all
Library* aliases (imported from adcp), then verify that for each Library alias,
the corresponding local class inherits from it.

beads: salesagent-v0kb (structural-guard epic)
"""

import importlib
import inspect


def _get_library_type_mapping() -> dict[str, type]:
    """Build mapping of local class names to their expected library base types.

    Scans src.core.schemas for all imports aliased as Library*. For each such
    import, the local class with the un-prefixed name should inherit from it.

    Returns dict like: {"Product": <class adcp.types.Product>, ...}
    """
    import ast
    from pathlib import Path

    schemas_path = Path("src/core/schemas.py")
    source = schemas_path.read_text()
    tree = ast.parse(source)

    mapping: dict[str, type] = {}

    # Find all "from adcp... import X as LibraryX" statements
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("adcp"):
            for alias in node.names:
                if alias.asname and alias.asname.startswith("Library"):
                    # e.g. "from adcp.types import Product as LibraryProduct"
                    # Local class name = alias.asname without "Library" prefix
                    local_name = alias.asname.removeprefix("Library")
                    # Import the actual library type
                    try:
                        mod = importlib.import_module(node.module)
                        lib_type = getattr(mod, alias.name, None)
                        if lib_type is not None and inspect.isclass(lib_type):
                            mapping[local_name] = lib_type
                    except (ImportError, AttributeError):
                        pass

    return mapping


def _get_local_schema_classes() -> dict[str, type]:
    """Get all classes defined in src.core.schemas."""
    schemas = importlib.import_module("src.core.schemas")
    classes = {}
    for name, obj in inspect.getmembers(schemas, inspect.isclass):
        # Only include classes actually defined in schemas module
        if obj.__module__ == "src.core.schemas":
            classes[name] = obj
    return classes


# Cache for AST-based field detection (parsed once)
_CLASS_OWN_FIELDS: dict[str, set[str]] | None = None


def _get_class_own_field_names(class_name: str) -> set[str]:
    """Get field names declared directly in a class body using AST.

    This avoids Pydantic's __annotations__ pollution where inherited fields
    appear on subclasses after model_rebuild().
    """
    import ast
    from pathlib import Path

    global _CLASS_OWN_FIELDS
    if _CLASS_OWN_FIELDS is None:
        _CLASS_OWN_FIELDS = {}
        source = Path("src/core/schemas.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                fields = set()
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        fields.add(item.target.id)
                _CLASS_OWN_FIELDS[node.name] = fields

    return _CLASS_OWN_FIELDS.get(class_name, set())


class TestSchemaInheritance:
    """Every local schema class that has a Library* counterpart must inherit from it."""

    def test_all_library_types_have_local_subclass(self):
        """For each Library* import, a local class with that name exists and inherits from it."""
        mapping = _get_library_type_mapping()
        local_classes = _get_local_schema_classes()

        # Some Library* imports are used as TypeAliases or type hints, not subclassed.
        # These are legitimate and don't need a local subclass.
        ALIAS_ONLY_TYPES = {
            "AdCPBaseModel",  # Used as base for SalesAgentBaseModel (different naming)
            "BrandManifest",  # TypeAlias
            "GetSignalsRequest",  # Direct alias
            "Property",  # TypeAlias
            "PromotedProducts",  # Imported but unused (cleanup candidate)
            "ResponsePagination",  # Named differently in local code (Pagination)
        }

        violations = []
        for local_name, lib_type in sorted(mapping.items()):
            if local_name in ALIAS_ONLY_TYPES:
                continue

            local_cls = local_classes.get(local_name)
            if local_cls is None:
                # No local class with this name — might be used directly
                continue

            # Check MRO: local class must have library type in its inheritance chain
            mro = inspect.getmro(local_cls)
            if lib_type not in mro:
                violations.append(
                    f"{local_name} does not inherit from {lib_type.__module__}.{lib_type.__name__}. "
                    f"MRO: {[c.__name__ for c in mro]}"
                )

        assert not violations, "Schema classes not inheriting from their adcp library base:\n" + "\n".join(
            f"  - {v}" for v in violations
        )

    def test_no_field_redefinition_in_subclasses(self):
        """Local subclasses should not redefine fields that exist in the library parent.

        Redefinition means the field was copied instead of inherited, which causes
        drift when the library updates the field's type or validator.
        """
        mapping = _get_library_type_mapping()
        local_classes = _get_local_schema_classes()

        ALIAS_ONLY_TYPES = {
            "AdCPBaseModel",
            "BrandManifest",
            "GetSignalsRequest",
            "Property",
            "PromotedProducts",
            "ResponsePagination",
        }

        # Known exceptions: fields intentionally overridden with tighter types,
        # custom validators, nested serialization (Critical Pattern #4), or
        # exclude=True additions. Format: (ClassName, field_name)
        # Each override must have a documented reason. Do NOT add new entries
        # without verifying the override is intentional.
        KNOWN_OVERRIDES: set[tuple[str, str]] = {
            # Nested serialization overrides (Critical Pattern #4) —
            # Parent models re-declare list fields to use local subclass types
            ("CreateMediaBuyRequest", "packages"),
            ("GetMediaBuyDeliveryResponse", "aggregated_totals"),
            ("GetMediaBuyDeliveryResponse", "media_buy_deliveries"),
            ("GetProductsRequest", "pagination"),
            ("GetProductsRequest", "brand"),
            ("GetProductsRequest", "catalog"),
            ("GetProductsRequest", "buyer_campaign_ref"),
            ("GetSignalsResponse", "signals"),
            ("ListCreativesResponse", "pagination"),
            ("ListCreativesResponse", "query_summary"),
            ("ListCreativesResponse", "creatives"),
            ("PackageRequest", "targeting_overlay"),
            ("PackageRequest", "impressions"),
            ("PackageRequest", "creatives"),
            ("Placement", "format_ids"),
            ("Placement", "description"),
            ("Product", "channels"),
            ("QuerySummary", "filters_applied"),
            ("Signal", "signal_type"),
            ("Signal", "pricing"),
            ("Signal", "deployments"),
            ("SyncCreativeResult", "warnings"),
            ("SyncCreativeResult", "errors"),
            ("SyncCreativeResult", "changes"),
            ("SyncCreativesRequest", "creatives"),
            ("SyncCreativesRequest", "push_notification_config"),
            # Creative overrides — status gets a default, dates get default_factory,
            # assets overridden to untyped dict (DB stores arbitrary asset dicts)
            ("Creative", "status"),
            ("Creative", "created_date"),
            ("Creative", "updated_date"),
            ("Creative", "assets"),
            # Request field overrides — tighter validation
            ("GetMediaBuyDeliveryRequest", "account_id"),
        }

        violations = []
        for local_name, lib_type in sorted(mapping.items()):
            if local_name in ALIAS_ONLY_TYPES:
                continue

            local_cls = local_classes.get(local_name)
            if local_cls is None:
                continue

            mro = inspect.getmro(local_cls)
            if lib_type not in mro:
                continue  # Already flagged by previous test

            # Get fields defined DIRECTLY on the local class (not inherited).
            # Can't use __annotations__ — Pydantic model_rebuild populates it
            # with inherited fields. Use AST to find source-level declarations.
            if not hasattr(lib_type, "model_fields"):
                continue

            lib_fields = set(lib_type.model_fields.keys())
            local_own_annotations = _get_class_own_field_names(local_name)

            for field_name in local_own_annotations & lib_fields:
                if (local_name, field_name) not in KNOWN_OVERRIDES:
                    violations.append(
                        f"{local_name}.{field_name} redefines field from "
                        f"{lib_type.__name__} — inherit instead of redeclare"
                    )

        assert not violations, "Schema classes redefining library fields (should inherit):\n" + "\n".join(
            f"  - {v}" for v in violations
        )
