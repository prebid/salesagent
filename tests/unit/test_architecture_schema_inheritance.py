"""Guard: Schema classes must extend adcp library base types.

Every schema class in src/core/schemas.py that corresponds to an adcp library
type must inherit from it via the Library* alias pattern. This prevents field
drift, ensures forward compatibility with adcp upgrades, and maintains protocol
compliance.

Scanning approach: Introspection — import the schemas module, discover all
Library* aliases (imported from adcp), then verify that for each Library alias,
the corresponding local class inherits from it.

"""

import importlib
import inspect
from collections.abc import Sequence
from typing import Annotated

import annotated_types
import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist


def _is_adcp_module(module: str) -> bool:
    """True iff ``module`` is the ``adcp`` package or one of its submodules.

    The single answer to "is this the adcp library?" for this file — used both on
    AST ``ImportFrom.module`` strings and on runtime ``__module__`` values, so the
    two membership sites cannot drift apart.

    Exact package match, not a bare ``startswith("adcp")`` prefix: that prefix also
    matches an unrelated top-level package whose name merely begins with those four
    letters (``adcpx``, ``adcp_local``), which would let a non-library base decide
    membership. No such module is imported today — this is a tightening that can
    only ever reject a false member, never miss a real one, since the library is
    ``adcp`` and its submodules are ``adcp.*``.
    """
    return module == "adcp" or module.startswith("adcp.")


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
            if isinstance(node, ast.ImportFrom) and node.module and _is_adcp_module(node.module):
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


# Library aliases whose local name is a plain re-export/alias, not a subclass.
ALIAS_ONLY_TYPES = {
    "AdCPBaseModel",
    "BrandManifest",
    "GetSignalsRequest",
    "PackageUpdate",
    "Property",
    "PromotedProducts",
    "ResponsePagination",
}

# Bases every schema in the package inherits. They are not a library type a class
# "narrows", so redefinition-grading must not treat them as one.
_UNIVERSAL_BASES = {"AdCPBaseModel"}


def _get_redefinition_targets() -> list[tuple[str, type, type]]:
    """Yield ``(local_name, local_cls, lib_base)`` for every local class that actually
    extends an imported ``Library*`` type.

    Membership is decided by the MRO, not by the class's NAME. The name-derived
    mapping above answers "which local class SHOULD extend LibraryX", which is the
    right question for the inheritance test but the wrong one for redefinition: a
    subclass whose name is not ``alias-minus-Library`` was never visited at all, so
    its redefinitions went ungraded and — worse — its allowlist entries read as
    *stale* rather than as unreachable. Three classes were invisible this way
    (AdCPPackageUpdate, SyncAccountsResponse, SyncCreativesResponse), carrying six
    live redefinitions between them.

    Bases that everything inherits (``AdCPBaseModel`` and the local base built on it)
    are excluded: they are not a "library type this class narrows", and treating them
    as one would flag every schema in the package.
    """
    local_classes = _get_local_schema_classes()

    targets: list[tuple[str, type, type]] = []
    for local_name, local_cls in sorted(local_classes.items()):
        if local_name in ALIAS_ONLY_TYPES:
            continue
        for base in inspect.getmro(local_cls)[1:]:
            # Membership by ``__module__``, not by how the import was spelled. The
            # alias-derived mapping this used to consult could only see classes
            # imported as ``Library*``; anything imported under another prefix was
            # never in the scan set at all. That is not a gap an allowlist can
            # record, because an unvisited class produces no violation to allow --
            # it produces silence. ``UpdateMediaBuySuccess`` is imported here as
            # ``AdCPUpdateMediaBuySuccess`` and was invisible for exactly that
            # reason, which is also how the probe that justified deleting this
            # guard came to aim outside the guard's own scan set.
            if not _is_adcp_module(getattr(base, "__module__", "") or ""):
                continue
            if not hasattr(base, "model_fields"):
                continue
            if base.__name__ in _UNIVERSAL_BASES:
                continue
            targets.append((local_name, local_cls, base))
            break
    return targets


def _is_admissible(child: object, parent: object) -> bool:
    """Whether a redeclaration needs no allowlist row: SHAPE **and** NOT-WEAKER.

    SHAPE -- the annotation is identical, or every non-``None`` class in the child's
    annotation subclasses some non-``None`` class in the parent's. Container shape is
    deliberately ignored: ``Sequence[X]`` and ``list[X]`` accept the same inputs, and
    both dump to identical JSON, so narrowing one to the other is not a widening on any
    observable axis. Locality is deliberately NOT required -- whether the narrowed-to
    class happens to live in ``src/`` has nothing to do with whether the redeclaration
    is weaker, and requiring it only buries the optionality cases among the shape ones.

    NOT-WEAKER -- nullability is not added, ``is_required()`` is not relaxed, metadata
    is a superset, and no default is introduced.

    Both clauses are needed, and the second is the one that is easy to omit. Seven
    redeclarations in this tree match their parent's annotation EXACTLY while dropping a
    constraint that lives in the ``FieldInfo`` -- ``Ge(ge=1)`` off ``revision``,
    ``MinLen(1)`` off ``packages`` -- so a shape-only rule admits them, and admitting
    them is strictly worse than a hand-written row: a row names itself and can be
    audited, whereas a derived admission is invisible and permanent.

    A redeclaration that satisfies SHAPE but is WEAKER is not admitted here and is not
    rejected either -- it requires a row naming the weakened axis. That routing is
    load-bearing: most such rows already exist and record a deliberate divergence
    (``account`` is optional because identity is resolved at the transport boundary),
    and a rule that admitted them would delete that documentation.
    """
    import typing

    none_type = type(None)

    def classes(annotation: object) -> list[type]:
        args = typing.get_args(annotation)
        if not args:
            return [annotation] if inspect.isclass(annotation) and annotation is not none_type else []
        return [c for arg in args for c in classes(arg)]

    def is_nullable(annotation: object) -> bool:
        return none_type in typing.get_args(annotation) or annotation is none_type

    child_ann, parent_ann = child.annotation, parent.annotation
    child_classes, parent_classes = classes(child_ann), classes(parent_ann)
    shape = child_ann == parent_ann or (
        bool(child_classes)
        and bool(parent_classes)
        and all(any(issubclass(c, p) for p in parent_classes) for c in child_classes)
    )
    if not shape:
        return False
    if is_nullable(child_ann) and not is_nullable(parent_ann):
        return False
    if parent.is_required() and not child.is_required():
        return False
    # Subset, not proper-superset. ``a > b`` is False for INCOMPARABLE sets, so writing
    # the rejection as ``parent > child`` admits a child that drops one constraint
    # while adding an unrelated one -- exactly the case this clause exists to catch.
    if not {repr(m) for m in parent.metadata} <= {repr(m) for m in child.metadata}:
        return False
    # The defaults axis, and it applies only where the CHILD is still optional.
    # ``is_required()`` does not cover it: that check is False for any parent which has a
    # default at all, so a field the parent already made optional can have its default
    # silently rewritten and still look "not weaker". Three fields in this tree do exactly
    # that -- ``None`` replaced by ``default_factory=list`` -- which changes what a caller
    # who omits the field puts on the wire. That is a divergence from the pin whether or
    # not it is an improvement, so it needs a row saying so.
    #
    # A child that is REQUIRED has no default by definition, and dropping the parent's
    # default to force the caller to supply a value is a narrowing, not a widening. Those
    # are admitted -- comparing defaults there would reject every optional-to-required
    # tightening in the tree, which is the opposite of what this clause is for.
    if child.is_required() or parent.is_required():
        return True
    if repr(parent.default) != repr(child.default):
        return False
    return (parent.default_factory is None) == (child.default_factory is None)


def _get_local_schema_classes() -> dict[str, type]:
    """Get all classes defined in src.core.schemas (including submodules)."""
    schemas = importlib.import_module("src.core.schemas")
    classes = {}
    for name, obj in inspect.getmembers(schemas, inspect.isclass):
        # Include classes defined in the schemas package or its submodules
        if obj.__module__ and obj.__module__.startswith("src.core.schemas"):
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


# Fixture table for _is_admissible, one row per axis it claims to check.
#
# This predicate decides which redeclarations need no allowlist row, so a hole in it is
# silent by construction: a field it wrongly admits simply stops being reported. It has
# been corrected by review three times -- a shape-only version admitted seven widenings
# hidden in FieldInfo metadata, a locality clause over-rowed sixteen fields that were not
# weaker, the defaults axis was documented but unimplemented, and the metadata clause was
# written as a proper-superset test that admitted incomparable sets -- and until now it
# was graded by no test at all. Each row below FAILS if its clause is removed.
def _field(annotation, **kwargs):
    """Build a FieldInfo the way Pydantic does.

    Constraints must arrive through the ANNOTATION: ``FieldInfo(metadata=[...])`` accepts
    the keyword and silently discards it, which would have made the two metadata rows
    below pass against an empty list and prove nothing.
    """
    from pydantic.fields import FieldInfo

    info = FieldInfo.from_annotation(annotation)
    if kwargs:
        info = FieldInfo.merge_field_infos(info, FieldInfo(**kwargs))
    return info


_ADMISSIBILITY_CASES = [
    # (label, parent, child, admissible)
    #
    # Each rejecting row ISOLATES one clause: it is admitted by every other clause, so
    # deleting the clause it targets turns it green. A row rejected by two clauses proves
    # nothing about either -- the first version of this table had three such rows, and a
    # mutation run (delete one clause, see what fails) showed only the metadata clause was
    # graded at all.
    ("identical", _field(int), _field(int), True),
    ("container narrowed", _field(Sequence[int]), _field(list[int]), True),
    ("optional to required", _field(int | None, default=None), _field(int), True),
    ("same default kept", _field(int, default=1), _field(int, default=1), True),
    ("unrelated type", _field(int), _field(str), False),
    # nullability: both optional, same default, same metadata -- only nullability differs
    ("nullability added", _field(int, default=1), _field(int | None, default=1), False),
    # requiredness: identical annotation and metadata; the defaults clause cannot fire
    # because it is skipped whenever either side is required
    ("required relaxed", _field(int), _field(int, default=1), False),
    ("constraint dropped", _field(Annotated[int, annotated_types.Ge(1)]), _field(int), False),
    (
        "constraint swapped for an unrelated one",
        _field(Annotated[int, annotated_types.Ge(1)]),
        _field(Annotated[int, annotated_types.Le(10)]),
        False,
    ),
    # defaults: both optional, same annotation, same metadata -- only the default differs
    ("default rewritten", _field(int, default=1), _field(int, default=2), False),
    (
        "default_factory introduced",
        _field(list | None, default=None),
        _field(list | None, default_factory=list),
        False,
    ),
]


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    ("parent", "child", "admissible"),
    [pytest.param(p, c, ok, id=label) for label, p, c, ok in _ADMISSIBILITY_CASES],
)
def test_admissibility_predicate_grades_each_axis(parent, child, admissible) -> None:
    """Every axis _is_admissible claims to check is exercised in both directions."""
    assert _is_admissible(child, parent) is admissible


# Classes whose name is NOT their library parent's alias-minus-"Library", so the
# name-derived mapping never names them. They are reachable only because
# _get_redefinition_targets decides membership by walking the MRO. Each carries live
# redeclarations, so if the collector regressed to alias-keying they would stop being
# graded — and their KNOWN_OVERRIDES rows would read as STALE rather than as
# unreachable, which is the failure mode that makes the regression look like a fix.
_MRO_ONLY_TARGETS = [
    "AdCPPackageUpdate",
    "CreateMediaBuySuccess",
    "SyncAccountsResponse",
    "SyncCreativesResponse",
    "UpdateMediaBuySuccess",
]


class TestMembershipKeying:
    """The collector's own oracle.

    ``_get_redefinition_targets`` is what decides WHICH classes get graded, so a
    regression in it produces silence, not a failure — the one defect shape an
    allowlist cannot record. These tests are the mechanism that goes red for it.
    """

    @pytest.mark.arch_guard
    @pytest.mark.parametrize("local_name", _MRO_ONLY_TARGETS)
    def test_collector_reaches_classes_the_alias_mapping_cannot_name(self, local_name: str) -> None:
        """MRO keying is load-bearing: these classes are graded ONLY because of it.

        Asserts both halves, because either alone is passable by accident: the class
        IS collected, and the name-derived mapping does NOT name it. Re-keying the
        collector to the alias mapping turns the first assertion red.
        """
        assert local_name not in _get_library_type_mapping(), (
            f"{local_name} is now reachable by NAME, so it no longer demonstrates that "
            f"membership is MRO-keyed — move it off this list and pick another class "
            f"whose name differs from its library parent's."
        )
        collected = {name for name, _, _ in _get_redefinition_targets()}
        assert local_name in collected, (
            f"{local_name} is no longer collected by _get_redefinition_targets. Its adcp "
            f"parent is imported under a non-'Library' alias, so a collector keyed on the "
            f"import spelling cannot see it: its redeclarations go ungraded and its "
            f"KNOWN_OVERRIDES rows misreport as stale. Membership must be decided by "
            f"walking the MRO and testing __module__."
        )

    @pytest.mark.arch_guard
    def test_every_collected_base_is_an_adcp_type(self) -> None:
        """The base a class is graded against is a real adcp type, never a local one."""
        offenders = [
            f"{name} -> {base.__module__}.{base.__name__}"
            for name, _, base in _get_redefinition_targets()
            if not _is_adcp_module(base.__module__) or base.__name__ in _UNIVERSAL_BASES
        ]
        assert not offenders, "Collected bases that are not a narrowable adcp type:\n" + "\n".join(
            f"  - {o}" for o in offenders
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("module", "expected"),
        [
            ("adcp", True),
            ("adcp.types", True),
            ("adcp.types.generated_poc.core.targeting", True),
            # Lookalike top-level packages: a bare startswith("adcp") admits these.
            ("adcpx", False),
            ("adcp_local", False),
            ("adcpclient.types", False),
            # Unrelated, and the empty string a missing __module__ falls back to.
            ("src.core.schemas._base", False),
            ("", False),
        ],
    )
    def test_adcp_module_predicate_matches_the_package_not_the_prefix(self, module: str, expected: bool) -> None:
        """Membership is an exact package match, so a lookalike name cannot decide it."""
        assert _is_adcp_module(module) is expected


class TestSchemaInheritance:
    """Every local schema class that has a Library* counterpart must inherit from it."""

    @pytest.mark.arch_guard
    def test_all_library_types_have_local_subclass(self):
        """For each Library* import, a local class with that name exists and inherits from it."""
        mapping = _get_library_type_mapping()
        local_classes = _get_local_schema_classes()

        # ALIAS_ONLY_TYPES (module scope) lists the Library* imports used as TypeAliases
        # or type hints rather than subclassed — legitimate, so no local subclass is due.
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

        Graded with ``assert_violations_match_allowlist`` so the allowlist can only
        SHRINK: an entry that stops being a real redefinition fails as stale instead
        of accumulating silently.
        """

        # Known exceptions: fields intentionally overridden with tighter types,
        # custom validators, nested serialization (Critical Pattern #4), or
        # exclude=True additions. Format: (ClassName, field_name)
        # Each override must have a documented reason. Do NOT add new entries
        # without verifying the override is intentional.
        KNOWN_OVERRIDES: set[tuple[str, str]] = {
            # Nested serialization overrides (Critical Pattern #4) —
            # Parent models re-declare list fields to use local subclass types
            ("GetMediaBuyDeliveryResponse", "media_buy_deliveries"),
            ("GetSignalsResponse", "signals"),
            ("ListCreativesResponse", "query_summary"),
            ("PackageRequest", "creatives"),
            # Mirror of PackageRequest.targeting_overlay for the update path —
            # makes collection_list typed at the request boundary instead of
            # leaking through library extra="allow" as a raw dict.
            ("Signal", "deployments"),
            # WEAKENED AXIS: nullability. The parent types confirmed_at as a non-null
            # datetime; this redeclares it ``AwareDatetime | None``. Rowed rather than
            # admitted, because a widening a derived rule lets through is invisible and
            # permanent, while a row names itself and can be audited.
            #
            # The weakening is toward the PIN, not away from it:
            # create-media-buy-response.json @ 3.1.1 arm0 (CreateMediaBuySuccess) types
            # confirmed_at ["string","null"] AND lists it in ``required``. The SDK parent
            # is the side that diverges -- it under-specifies its own schema by typing the
            # field non-null. MediaBuy.confirmed_at is Mapped[datetime | None] and the
            # column is nullable, so this annotation was the only layer narrower than the
            # contract.
            #
            # Forced by the create path: a ``pending_creatives`` create returns this arm, and
            # that buy is a HOLD with no seller commitment to report. While the status sat
            # in _SELLER_COMMITTED_STATUSES it was stamped and the non-null type held --
            # but the stamp was the defect.
            ("CreateMediaBuySuccess", "confirmed_at"),
            # adcp 6.6 (spec 3.1.1) re-added status/changes/warnings/platform_id/assignment_errors/
            # assigned_to to the library sync_creatives_response Creative — status/platform_id/
            # assignment_errors/assigned_to are INHERITED (PR #1567). Internal review-routing
            # state was renamed to `internal_status` (a non-parent field, excluded from the wire).
            # changes/warnings/errors are deliberately REDECLARED with default_factory=list
            # (PR #1567 round-2 item 3): spec 3.1.1 types them `array`, and the parent's None default
            # serialized as null on the MCP structured_content path (bypasses model_dump strips).
            # Those three, plus QuerySummary.filters_applied, keep the parent's SHAPE and
            # requiredness and differ only in the DEFAULT — which is the axis a shape-and-
            # requiredness rule cannot see, since is_required() is already False on both
            # sides. They are rowed rather than admitted because replacing None with
            # default_factory=list changes what an omitting caller puts on the wire, and
            # that is a divergence from the pin whether or not it is an improvement.
            ("SyncCreativeResult", "changes"),
            ("SyncCreativeResult", "errors"),
            ("SyncCreativeResult", "warnings"),
            ("QuerySummary", "filters_applied"),
            ("SyncCreativesRequest", "creatives"),
            # Creative overrides — listing base requires these fields, but we add
            # defaults for partial construction and override assets to untyped dict
            ("Creative", "status"),
            ("Creative", "created_date"),
            ("Creative", "updated_date"),
            # assets: widened to an untyped dict. Narrowing it to the pinned typed
            # form is NOT a safe change on its own -- mock_creative_engine.py reads it
            # with isinstance(value, dict), so typed values make that branch dead and
            # silently disable the long-video suggestion. Field and consumer must be
            # narrowed in the same change, and that change needs an oracle asserting
            # the suggestion IS emitted for a >15s asset -- there is none today, which
            # is why a green suite says nothing about this field.
            ("Creative", "assets"),
            # Nested serialization — creative delivery uses local CreativeDeliveryData
            ("GetCreativeDeliveryResponse", "creatives"),
            # adcp 3.9 field overrides — library added fields we already had locally
            # with wider types (optional vs required) or salesagent-specific semantics
            ("CreateMediaBuyRequest", "account"),  # optional override (library requires it)
            # GetMediaBuyDeliveryRequest: SDK 5.7 provides all fields; no local
            # redeclarations remain. Removed: account, attribution_window,
            # include_package_daily_breakdown, reporting_dimensions.
            # buying_mode: the row is legitimate (the field IS weaker -- it drops the
            # parent's enum for a bare str) but the reason previously given here was
            # false: the parent types the FULL enum and the pin declares no
            # discriminator union at all. Deleting the redeclaration restores enum
            # validation and will surface any caller passing an arbitrary string.
            ("GetProductsRequest", "buying_mode"),
            ("SyncCreativesRequest", "account"),  # optional override (library requires it)
            ("UpdateMediaBuyRequest", "end_time"),  # datetime|None (library uses AwareDatetime)
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
            # Pattern #4: the get_media_buys item chain. ALL THREE narrowings below are
            # load-bearing, and for three different reasons — the comment used to say
            # "both" while listing three, which leaves a reader to guess which one was
            # unaccounted for:
            #   targeting_overlay — our Targeting adds ~30 fields the library's
            #     TargetingOverlay lacks; serializing through the library annotation
            #     would drop every one of them.
            #   packages — the local GetMediaBuysPackage carries the narrowed
            #     targeting_overlay above, so the item must declare the local element
            #     type or the narrowing never reaches the wire.
            #   media_buys — same one step up: the response must declare the local item
            #     type, or GetMediaBuysMediaBuy's own model_dump (and the
            #     required-nullable retention on it) is never invoked.
            #
            # Note what is NOT here, and why, because the two reasons are different:
            #   snapshot, creative_approvals — local SUBCLASSES that add no fields, so
            #     they inherit the parent's declaration outright.
            #   snapshot_unavailable_reason, approval_status — not subclasses at all.
            #     Their types are plain ALIASES of the library enums
            #     (SnapshotUnavailableReason, ApprovalStatus), so there is no local
            #     declaration for this guard to see in the first place. Aliasing is what
            #     stopped the members drifting; the local SnapshotUnavailableReason copy
            #     had lost one of the pinned three.
            # Required-field tightening (#1399 Plan-B): pinned 3.1 marks these
            # success-arm fields required; the SDK base declares them optional, so
            # we redeclare required to match the spec.
            # Pattern #4 on the two sync success arms. Both narrow the parent's item
            # type to a local subclass that adds fields the library type lacks
            # (SyncResponseAccount; SyncCreativeResult's assigned_to /
            # assignment_errors), so serializing through the parent annotation would
            # drop them. Newly VISIBLE rather than newly introduced: the collector
            # keyed on alias-minus-"Library" until now, and neither class's name
            # matches its parent's, so neither was ever visited.
            ("SyncAccountsResponse", "accounts"),
            # Both drop the pin's ``Ge(ge=1)`` from ``revision`` while matching its
            # annotation exactly, so they are WEAKER on the metadata axis: the pin
            # rejects 0 and -1, these accept both. That is not a deliberate
            # divergence -- it is a defect, and these two are the only redeclarations
            # in the tree with no prior row, i.e. the ones the alias-blind scan set
            # never reached. Rowed rather than admitted
            # so the relaxation stays visible until it is fixed; DO NOT resolve this
            # by relaxing the admissibility predicate.
        }

        found: set[tuple[str, str]] = set()
        for local_name, local_cls, lib_base in _get_redefinition_targets():
            # Fields declared DIRECTLY on the local class. Can't use __annotations__ —
            # Pydantic model_rebuild populates it with inherited fields — so read
            # source-level declarations out of the AST.
            own = _get_class_own_field_names(local_name)
            for field in own & set(lib_base.model_fields.keys()):
                # A redeclaration that is neither reshaped nor weakened restates the
                # parent and needs no row: the guard exists to catch drift from the
                # pin, and a field that cannot drift from it is not drift. Anything
                # weaker on any axis still needs a row NAMING that axis — see
                # _is_admissible.
                if _is_admissible(local_cls.model_fields[field], lib_base.model_fields[field]):
                    continue
                found.add((local_name, field))

        assert_violations_match_allowlist(
            found,
            KNOWN_OVERRIDES,
            fix_hint=(
                "A new violation means a field was copied instead of inherited — delete the "
                "redeclaration, or add it to KNOWN_OVERRIDES with the reason it must differ. "
                "A stale entry means the redeclaration is gone (delete the entry) OR that the "
                "class stopped being collected — check it is still reachable from "
                "_get_redefinition_targets before assuming it was fixed."
            ),
        )
