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

import pytest


def _get_schemas_source_files() -> list["Path"]:
    """Get all Python source files in the schemas package.

    Handles both the old single-file layout (src/core/schemas.py) and
    the new package layout (src/core/schemas/__init__.py + submodules).
    """
    from pathlib import Path

    schemas_path = Path("src/core/schemas")
    if schemas_path.is_dir():
        return sorted(schemas_path.glob("**/*.py"))
    single_file = Path("src/core/schemas.py")
    if single_file.exists():
        return [single_file]
    raise FileNotFoundError("Cannot find src/core/schemas.py or src/core/schemas/ package")


def _get_library_type_mapping() -> dict[str, type]:
    """Build mapping of local class names to their expected library base types.

    Scans src.core.schemas for all imports aliased as Library*. For each such
    import, the local class with the un-prefixed name should inherit from it.

    Returns dict like: {"Product": <class adcp.types.Product>, ...}
    """
    import ast

    mapping: dict[str, type] = {}

    for schemas_path in _get_schemas_source_files():
        source = schemas_path.read_text()
        tree = ast.parse(source)

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
    """Get all classes defined in src.core.schemas (including submodules)."""
    schemas = importlib.import_module("src.core.schemas")
    classes = {}
    for name, obj in inspect.getmembers(schemas, inspect.isclass):
        # Include classes defined in the schemas package or its submodules
        if obj.__module__ and obj.__module__.startswith("src.core.schemas"):
            classes[name] = obj
    return classes


def _nearest_adcp_base(cls: type) -> type | None:
    """The nearest ancestor of ``cls`` defined in the ``adcp`` package, or None.

    Keys the redeclaration guard on the MRO rather than the ``Library*`` import
    alias, so a subclass whose adcp parent is imported under any alias
    (``AdCP*``, direct name) is still checked (#1618).
    """
    for base in inspect.getmro(cls)[1:]:
        module = getattr(base, "__module__", "") or ""
        # Exact package match, not a bare prefix: "adcpx"/"adcp_local" are not adcp.
        if (module == "adcp" or module.startswith("adcp.")) and hasattr(base, "model_fields"):
            return base
    return None


# Some Library* imports are used as TypeAliases or type hints, not subclassed.
# These are legitimate and don't need a local subclass, and they have no library
# parent to redeclare fields from.
ALIAS_ONLY_TYPES: set[str] = {
    "AdCPBaseModel",  # Used as base for SalesAgentBaseModel (different naming)
    "BrandManifest",  # TypeAlias
    "GetSignalsRequest",  # Direct alias
    "PackageUpdate",  # Local PackageUpdate is a simplified model; AdCPPackageUpdate extends library
    "Property",  # TypeAlias
    "PromotedProducts",  # Imported but unused (cleanup candidate)
    "ResponsePagination",  # Named differently in local code (Pagination)
}


# Cache for AST-based field detection (parsed once)
_CLASS_OWN_FIELDS: dict[str, set[str]] | None = None


def _get_class_own_field_names(class_name: str) -> set[str]:
    """Get field names declared directly in a class body using AST.

    This avoids Pydantic's __annotations__ pollution where inherited fields
    appear on subclasses after model_rebuild().
    """
    import ast

    global _CLASS_OWN_FIELDS
    if _CLASS_OWN_FIELDS is None:
        _CLASS_OWN_FIELDS = {}
        for schemas_path in _get_schemas_source_files():
            source = schemas_path.read_text()
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

    @pytest.mark.arch_guard
    def test_all_library_types_have_local_subclass(self):
        """For each Library* import, a local class with that name exists and inherits from it."""
        mapping = _get_library_type_mapping()
        local_classes = _get_local_schema_classes()

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

    @pytest.mark.arch_guard
    def test_no_field_redefinition_in_subclasses(self):
        """Local subclasses should not redefine fields that exist in the library parent.

        Redefinition means the field was copied instead of inherited, which causes
        drift when the library updates the field's type or validator.
        """
        local_classes = _get_local_schema_classes()

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
            ("GetSignalsResponse", "signals"),
            ("ListCreativesResponse", "pagination"),
            ("ListCreativesResponse", "query_summary"),
            ("ListCreativesResponse", "creatives"),
            ("PackageRequest", "targeting_overlay"),
            ("PackageRequest", "impressions"),
            ("PackageRequest", "creatives"),
            # Mirror of PackageRequest.targeting_overlay for the update path —
            # makes collection_list typed at the request boundary instead of
            # leaking through library extra="allow" as a raw dict.
            ("AdCPPackageUpdate", "targeting_overlay"),
            ("Placement", "format_ids"),
            ("Placement", "description"),
            ("QuerySummary", "filters_applied"),
            ("Signal", "signal_type"),
            ("Signal", "deployments"),
            # adcp 6.6 (spec 3.1.1) re-added status/changes/warnings/platform_id/assignment_errors/
            # assigned_to to the library sync_creatives_response Creative — status/platform_id/
            # assignment_errors/assigned_to are INHERITED (PR #1567). Internal review-routing
            # state was renamed to `internal_status` (a non-parent field, excluded from the wire).
            # changes/warnings/errors are deliberately REDECLARED with default_factory=list
            # (PR #1567 round-2 item 3): spec 3.1.1 types them `array`, and the parent's None default
            # serialized as null on the MCP structured_content path (bypasses model_dump strips).
            ("SyncCreativeResult", "changes"),
            ("SyncCreativeResult", "warnings"),
            ("SyncCreativeResult", "errors"),
            ("SyncCreativesRequest", "creatives"),
            ("SyncCreativesRequest", "push_notification_config"),
            # Creative overrides — listing base requires these fields, but we add
            # defaults for partial construction and override assets to untyped dict
            ("Creative", "name"),
            ("Creative", "status"),
            ("Creative", "created_date"),
            ("Creative", "updated_date"),
            ("Creative", "assets"),
            # Nested serialization — creative delivery uses local CreativeDeliveryData
            ("GetCreativeDeliveryResponse", "creatives"),
            # adcp 3.9 field overrides — library added fields we already had locally
            # with wider types (optional vs required) or salesagent-specific semantics
            ("CreateMediaBuyRequest", "account"),  # optional override (library requires it)
            ("CreativePolicy", "provenance_required"),  # custom description/default
            # GetMediaBuyDeliveryRequest: SDK 5.7 provides all fields; no local
            # redeclarations remain. Removed: account, attribution_window,
            # include_package_daily_breakdown, reporting_dimensions.
            ("GetProductsRequest", "buying_mode"),  # str|None override (library uses Literal discriminator)
            ("SyncCreativesRequest", "account"),  # optional override (library requires it)
            ("UpdateMediaBuyRequest", "end_time"),  # datetime|None (library uses AwareDatetime)
            ("UpdateMediaBuyRequest", "packages"),  # list[AdCPPackageUpdate] (local subclass type)
            ("UpdateMediaBuyRequest", "start_time"),  # datetime|Literal["asap"]|None (wider type)
            # adcp 4.3 field overrides — library made these required; we keep them
            # optional because identity is resolved at the transport boundary, and
            # required-key enforcement rolls out create_media_buy-first
            # (CreateMediaBuyRequest.idempotency_key now inherits the required field)
            ("Product", "reporting_capabilities"),  # optional override (not all products have it)
            ("SyncAccountsRequest", "idempotency_key"),  # optional override (required-key fast-follow)
            ("SyncCreativesRequest", "idempotency_key"),  # optional override (required-key fast-follow)
            ("UpdateMediaBuyRequest", "account"),  # optional override (resolved from identity)
            ("UpdateMediaBuyRequest", "idempotency_key"),  # optional override (required-key fast-follow)
            # Pattern #4: ListAccountsResponse.accounts uses local Account subclass
            ("ListAccountsResponse", "accounts"),
            # Required-field tightening (#1399 Plan-B): pinned 3.1 marks these
            # success-arm fields required; the SDK base declares them optional, so
            # we redeclare required to match the spec.
            ("GetProductsResponse", "products"),
            # --- Surfaced by the MRO re-key (#1618), triaged against adcp 6.6 ---
            # adcp 6.6 (spec 3.1.1) made status/confirmed_at/revision required on the
            # create success envelope (CreateMediaBuyResponse1 declares all three
            # required with no default). They are invariant for a synchronous committed
            # success, so the subclass declares spec-correct defaults instead of
            # threading identical literals through every constructor
            # (src/core/schemas/_base.py, CreateMediaBuySuccess). Because the parent
            # fields are required-with-no-default, these can never migrate to plain
            # inheritance.
            ("CreateMediaBuySuccess", "confirmed_at"),
            ("CreateMediaBuySuccess", "revision"),
            ("CreateMediaBuySuccess", "status"),
            # Same rationale as the create twin: UpdateMediaBuyResponse1 declares
            # status/revision required with no default; the subclass supplies the
            # spec-correct defaults for a synchronous applied update.
            ("UpdateMediaBuySuccess", "revision"),
            ("UpdateMediaBuySuccess", "status"),
            # Pattern #4: local list[AffectedPackage] carries changes_applied /
            # buyer_package_ref (exclude=True, off the wire); the parent types it
            # Sequence[Package].
            ("UpdateMediaBuySuccess", "affected_packages"),
            # Pattern #4 (mirrors ListAccountsResponse.accounts / ListCreativesResponse
            # .creatives): local SyncResponseAccount adds action/status/errors/setup, and
            # the field is redeclared required-with-no-default because pinned 3.1 types
            # sync-accounts-response as oneOf(success requires `accounts` | error requires
            # `errors`).
            ("SyncAccountsResponse", "accounts"),
            # Pattern #4: local SyncCreativeResult adds assigned_to/assignment_errors/
            # changes/warnings; required-no-default per pinned 3.1 SyncCreativesSuccess.
            ("SyncCreativesResponse", "creatives"),
            # Local FrequencyCap extends the library type with `scope`
            # (media_buy vs package level).
            ("Targeting", "frequency_cap"),
            # adcp 6.6 types the geo exclusion side with DISTINCT nominal classes
            # (GeoCountriesExcludeItem/GeoRegionsExcludeItem/GeoMetrosExcludeItem) whose
            # JSON constraints are identical to the include-side GeoCountry/GeoRegion/
            # GeoMetro. Salesagent deliberately unifies include and exclude to one type
            # per dimension because adapters merge them into a single list
            # (src/adapters/base.py::_validate_geo_systems) and normalize_legacy_geo
            # pushes both through identical transforms. Inheriting is not viable — the
            # parent rejects a GeoCountry in geo_countries_exclude ("Input should be a
            # valid string"). geo_postal_areas_exclude already uses the parent's
            # PostalArea element type and is redeclared only to keep the four exclusion
            # fields symmetric (list, not Sequence) for those merge sites.
            ("Targeting", "geo_countries_exclude"),
            ("Targeting", "geo_metros_exclude"),
            ("Targeting", "geo_postal_areas_exclude"),
            ("Targeting", "geo_regions_exclude"),
        }

        violations = []
        # Key on the MRO base, not the Library* alias: a class is a library
        # subclass iff any ancestor is defined in the adcp package, however it was
        # imported (Library*/AdCP*/direct). This catches the field-redeclaring
        # subclasses the alias-only check missed (#1618).
        for local_name, local_cls in sorted(local_classes.items()):
            if local_name in ALIAS_ONLY_TYPES:
                continue

            lib_type = _nearest_adcp_base(local_cls)
            if lib_type is None:
                continue

            # Fields declared DIRECTLY on the local class (not inherited). Can't
            # use __annotations__ — Pydantic model_rebuild pollutes it with
            # inherited fields — so read source-level declarations via AST.
            lib_fields = set(lib_type.model_fields.keys())
            local_own_annotations = _get_class_own_field_names(local_name)

            for field_name in local_own_annotations & lib_fields:
                key = (local_name, field_name)
                if key in KNOWN_OVERRIDES:
                    continue
                violations.append(
                    f"{local_name}.{field_name} redefines field from {lib_type.__name__} — inherit instead of redeclare"
                )

        assert not violations, "Schema classes redefining library fields (should inherit):\n" + "\n".join(
            f"  - {v}" for v in violations
        )

    @pytest.mark.arch_guard
    def test_mro_rekey_recognizes_adcp_base_under_any_alias(self):
        """The redeclaration check keys on the MRO, so a subclass whose adcp
        parent is imported under a NON-``Library`` alias is still recognized —
        the #1618 blind spot. Positive: an adcp subclass resolves to its adcp
        base regardless of alias, and the base exposes ``model_fields`` so the
        ``own & lib_fields`` overlap can flag a redeclaration. Negative: a class
        with no adcp ancestor resolves to None and is skipped.
        """
        from adcp.types import Product as _PlainAliasedProduct  # deliberately NOT "LibraryProduct"

        class _SubclassUnderPlainAlias(_PlainAliasedProduct):
            # Deliberately SHADOWS a parent field so the ``own & lib_fields``
            # intersection the guard runs on is non-empty — a base lookup that
            # worked but produced an empty overlap would flag nothing.
            product_id: str

        class _NoAdcpAncestor:
            pass

        base = _nearest_adcp_base(_SubclassUnderPlainAlias)
        assert base is _PlainAliasedProduct, "MRO base must be found regardless of import alias"
        assert hasattr(base, "model_fields") and base.model_fields, "base must expose fields to compare against"

        # The overlap is what test_no_field_redefinition_in_subclasses flags on.
        own = set(_SubclassUnderPlainAlias.__annotations__)
        assert own & set(base.model_fields) == {"product_id"}, (
            "a redeclared parent field must show up in the own-fields/library-fields "
            "overlap the guard flags on — if this is empty the guard silently passes "
            "every non-Library-aliased subclass (the #1618 regression)"
        )

        assert _nearest_adcp_base(_NoAdcpAncestor) is None, "a non-adcp class must not be treated as a subclass"
