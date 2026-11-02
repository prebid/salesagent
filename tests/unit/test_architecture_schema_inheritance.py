"""Guard: Schema classes must extend adcp library base types.

Every schema class in src/core/schemas.py that corresponds to an adcp library
type must inherit from it via the Library* alias pattern. This prevents field
drift, ensures forward compatibility with adcp upgrades, and maintains protocol
compliance.

Scanning approach: Introspection — import the schemas module, discover all
Library* aliases (imported from adcp), then verify that for each Library alias,
the corresponding local class inherits from it.

"""

import enum
import importlib
import inspect
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated

import annotated_types
import pytest

if TYPE_CHECKING:
    from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist


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
            if not getattr(base, "__module__", "").startswith("adcp"):
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
    load-bearing: such a row names itself and can be audited, and a rule that admitted
    them would delete that documentation. The example this used to give --
    ``CreateMediaBuyRequest.account`` is optional because identity is resolved at the
    transport boundary -- is gone, because the row was not documenting a deliberate
    divergence at all. It was the last surviving instance of salesagent-prkv.28: the pin
    lists ``account`` in /required for all three of create_media_buy, update_media_buy and
    sync_creatives, prkv.28 rejected the transport-boundary argument for the latter two,
    and create_media_buy was simply left behind. The row is deleted with the override.
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


def library_base_violation(local_name: str, local_cls: type, lib_type: type) -> str | None:
    """Why *local_cls* fails to be a local subclass of *lib_type*, or ``None`` if it does not.

    Extracted from the test loop so the DECISION can be graded directly. It was inline
    once, and the three tests written to pin its two accommodations asserted facts about
    synthetic classes without ever invoking it — so widening either accommodation left
    them green. A rule that cannot be driven from a test is a rule nothing checks.
    """
    if isinstance(lib_type, enum.EnumMeta):
        # Python FORBIDS extending an enum that has members, so "inherits from" is
        # unsatisfiable here. Agreement on the value set is the real invariant.
        if not isinstance(local_cls, enum.EnumMeta):
            return f"{local_name} is not an enum but its library counterpart is"
        local_values = {member.value for member in local_cls}
        library_values = {member.value for member in lib_type}
        if local_values != library_values:
            return (
                f"{local_name} enum members differ from {lib_type.__module__}.{lib_type.__name__}. "
                f"Local-only: {sorted(local_values - library_values)}; "
                f"library-only: {sorted(library_values - local_values)}"
            )
        return None

    # The mapping is keyed by NAME and the library reuses names across modules, so a
    # same-named base FROM THE ADCP PACKAGE satisfies the mapping's own resolution.
    mro = inspect.getmro(local_cls)
    same_named_library_base = any(
        base.__name__ == lib_type.__name__ and base.__module__.startswith("adcp.") for base in mro[1:]
    )
    if lib_type not in mro and not same_named_library_base:
        return (
            f"{local_name} does not inherit from {lib_type.__module__}.{lib_type.__name__}. "
            f"MRO: {[c.__name__ for c in mro]}"
        )
    return None


class TestSchemaInheritance:
    """Every local schema class that has a Library* counterpart must inherit from it."""

    @pytest.mark.arch_guard
    def test_all_library_types_have_local_subclass(self):
        """For each Library* import, a local class with that name exists and inherits from it.

        TWO THINGS THIS RULE CANNOT DEMAND LITERALLY, both measured rather than assumed:

        ENUMS CANNOT BE SUBCLASSED AT ALL once they have members —
        ``class _Probe(LibraryTaskStatus): pass`` raises ``TypeError: cannot extend``. So
        "inherits from the library type" is unsatisfiable for every enum in the mapping,
        and demanding it asks for something Python forbids. The real invariant for an enum
        is that the VALUE SET matches, which is checkable and is what a divergence would
        actually break, so that is what is asserted here.

        THE MAPPING IS KEYED BY NAME, and the library has more than one class per name.
        ``adcp.types`` exports ``Account`` twice: ``core.account.Account`` (the entity —
        advertiser, billing_proxy, governance_agents) and
        ``account.sync_accounts_response.Account`` (the per-entry RESULT — action, errors,
        warnings). They share a name and share almost no fields. The local ``Account``
        correctly extends the ENTITY; the alias-keyed mapping happened to resolve to the
        RESULT and reported a violation for inheriting the wrong one of two classes it
        cannot tell apart. A same-named library class in the MRO therefore satisfies the
        mapping's own resolution — the module the mapping picked is not evidence.

        Both accommodations are deliberately narrow: the class must still come from the
        adcp package, and an enum must still match member for member.
        """
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

            violation = library_base_violation(local_name, local_cls, lib_type)
            if violation is not None:
                violations.append(violation)

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
            # ("CreateMediaBuyRequest", "brand") and ("GetProductsRequest", "brand") were
            # here for a type WIDENED past the library parent (| dict | str) so the
            # advertised shape would admit the brand shorthand. The shorthand now lives in
            # the compat layer (_normalize_brand in src/core/request_compat.py), which
            # coerces it before validation on every transport, so both models declare the
            # library's type and neither redeclaration is a reshape any more.
            # Nested serialization overrides (Critical Pattern #4) —
            # Parent models re-declare list fields to use local subclass types
            # WEAKENED AXIS: nullability. core/creative-asset.json is a oneOf -- a creative is
            # identified by format_id OR by format_kind -- and codegen renders it as two
            # classes with identical field sets differing only in which identifier is
            # required, wrapped in a RootModel union. adcp.types exports the name
            # CreativeAsset bound to the FIRST BRANCH, where format_id is required.
            #
            # This subclasses that branch and relaxes format_id to optional, stating the oneOf
            # as what it is (an XOR validator) on one flat model. Keeping the parent's
            # requiredness announces half the schema, because the format_kind branch becomes
            # unsendable on all three transports at once. Rowed rather than admitted, because
            # a weakening a derived rule lets through is invisible and permanent.
            ("CreativeAssetRequest", "format_id"),
            ("GetSignalsResponse", "signals"),
            # RESHAPED AXIS: item type, and the reshape is the LIBRARY's, not ours.
            # get-media-buy-delivery-response.json renders daily_breakdown[] twice under
            # codegen: DailyBreakdownItem and DailyBreakdownItem1, in the SAME module, with
            # identical field sets and identical annotations (measured -- no field differs).
            # DailyBreakdown extends the first; the parent field is annotated with the
            # second. Materially the same shape, but the guard compares CLASSES and these are
            # two, so it cannot see that. Rowed rather than admitted: a rule widened to treat
            # "same fields" as "same class" would silently admit every future divergence
            # between two classes that merely look alike today.
            ("MediaBuyDeliveryData", "daily_breakdown"),
            # TWO AXES, and the second is a genuine weakening worth the row saying out loud.
            # The parent annotates totals as the codegen `Totals`; this declares
            # `DeliveryTotals`, which extends core/delivery_metrics.json's `DeliveryMetrics`.
            # Measured, Totals is DeliveryMetrics plus one field and minus one relaxation:
            #   RESHAPED: `Totals` carries `effective_rate` (optional) and DeliveryMetrics
            #             does not, so this seller cannot emit that field at all.
            #   WEAKENED: `Totals.spend` is REQUIRED (typed Any); DeliveryMetrics.spend is
            #             `float | None` and optional. So a totals object with no spend
            #             satisfies this model and would not satisfy the parent's.
            # Rowed, not fixed here, because both axes are questions about what this seller
            # emits rather than about the redeclaration -- filed rather than decided in an
            # allowlist comment.
            ("MediaBuyDeliveryData", "totals"),
            ("ListCreativesResponse", "query_summary"),
            # RESHAPED AXIS: item type. The parent types creatives[] as the SDK's
            # ``CreativeAsset``, a RootModel union over the two codegen branches of
            # core/creative-asset.json's oneOf; this declares ``list[CreativeAssetRequest]``,
            # which extends ONE of those branches (see CreativeAssetRequest's docstring: the
            # union cannot be extended without putting the codegen name ``CreativeAsset1``
            # into the buyer's error pointer, which core/error.json forbids). Same reshape,
            # same reason, as ("SyncCreativesRequest", "creatives") below -- the two tools
            # accept the same item, so they carry the same type.
            #
            # NOT a weakening: this row previously covered ``list[Creative]``, the
            # list_creatives RESPONSE model, which typed ``assets`` as an untyped dict and so
            # admitted package creatives the pin refuses (salesagent-b341x.17).
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
            # create-media-buy-response.json @ 3.1.1 branch0 (CreateMediaBuySuccess) types
            # confirmed_at ["string","null"] AND lists it in ``required``. The SDK parent
            # is the side that diverges -- it under-specifies its own schema by typing the
            # field non-null. MediaBuy.confirmed_at is Mapped[datetime | None] and the
            # column is nullable, so this annotation was the only layer narrower than the
            # contract.
            #
            # Forced by the create path: a ``pending_creatives`` create returns this branch, and
            # that buy is a HOLD with no seller commitment to report. While the status sat
            # in _SELLER_COMMITTED_STATUSES it was stamped and the non-null type held --
            # but the stamp was the defect.
            ("CreateMediaBuySuccess", "confirmed_at"),
            # QuerySummary.filters_applied keeps the parent's SHAPE and requiredness and
            # differs only in the DEFAULT — the axis a shape-and-requiredness rule cannot
            # see, since is_required() is already False on both sides. Rowed rather than
            # admitted because replacing None with default_factory=list changes what an
            # omitting caller puts on the wire, and that is a divergence from the pin whether
            # or not it is an improvement. The three sync-creatives lists that sat beside it
            # for the same reason are inherited now (the wrap serializer runs on every path,
            # so the parent's None default is omitted on MCP, A2A and REST alike).
            ("QuerySummary", "filters_applied"),
            ("SyncCreativesRequest", "creatives"),
            # Creative overrides — listing base requires these fields, but we add
            # defaults for partial construction. (assets is inherited as the library's
            # typed map; the stored blob is validated into it at the row-to-model read.)
            ("Creative", "status"),
            ("Creative", "created_date"),
            ("Creative", "updated_date"),
            # Nested serialization — creative delivery uses local CreativeDeliveryData
            ("GetCreativeDeliveryResponse", "creatives"),
            # adcp 3.9 field overrides — library added fields we already had locally
            # with wider types (optional vs required) or salesagent-specific semantics
            # GetMediaBuyDeliveryRequest: SDK 5.7 provides all fields; no local
            # redeclarations remain. Removed: account, attribution_window,
            # include_package_daily_breakdown, reporting_dimensions.
            # buying_mode: the row is legitimate (the field IS weaker -- it drops the
            # parent's enum for a bare str) but the reason previously given here was
            # false: the parent types the FULL enum and the pin declares no
            # discriminator union at all. Deleting the redeclaration restores enum
            # validation and will surface any caller passing an arbitrary string.
            ("GetProductsRequest", "buying_mode"),
            ("UpdateMediaBuyRequest", "end_time"),  # datetime|None (library uses AwareDatetime)
            ("UpdateMediaBuyRequest", "start_time"),  # datetime|Literal["asap"]|None (wider type)
            # AdCP 3.1.1 field overrides. The required-key rollout is FINISHED: every tool
            # the library made idempotency_key/account required on now inherits them --
            # create_media_buy, update_media_buy, sync_creatives and, as of prkv.86,
            # sync_accounts. No row here relaxes a spec-required field any more, and none
            # may be added: "our model does not require what the pin requires" is a defect
            # with three precedents, not an allowlistable exception.
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
            # success-branch fields required; the SDK base declares them optional, so
            # we redeclare required to match the spec.
            # Pattern #4 on the two sync success branches. Both narrow the parent's item
            # type to a local subclass that adds fields the library type lacks
            # (SyncResponseAccount; SyncCreativeResult's assigned_to /
            # assignment_errors), so serializing through the parent annotation would
            # drop them. Newly VISIBLE rather than newly introduced: the collector
            # keyed on alias-minus-"Library" until now, and neither class's name
            # matches its parent's, so neither was ever visited.
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


class TestSchemaInheritanceGuardItself:
    """The two accommodations in ``library_base_violation`` are narrow, and pinned here.

    They drive the DECISION FUNCTION with synthetic inputs. An earlier version of this
    class asserted facts about synthetic classes without calling it, so widening either
    accommodation left all three tests green — the vacuous-guard failure this repo keeps
    finding, committed while fixing one.
    """

    def test_an_enum_whose_members_diverge_is_a_violation(self):
        """The enum branch drops the MRO demand, NOT the agreement demand."""

        class Library(enum.StrEnum):
            A = "a"
            B = "b"

        class Local(enum.StrEnum):
            A = "a"

        assert "enum members differ" in library_base_violation("X", Local, Library)

    def test_matching_enum_members_are_accepted_without_inheritance(self):
        """The accommodation itself: Python cannot express this inheritance at all."""

        class Library(enum.StrEnum):
            A = "a"

        class Local(enum.StrEnum):
            A = "a"

        assert library_base_violation("X", Local, Library) is None

        # The accommodation exists because this is what Python does. A class STATEMENT is
        # the real form: type(name, (Library,), {}) fails differently (AttributeError, from
        # passing a plain dict where EnumMeta wants its own namespace) and would pin the
        # wrong reason.
        with pytest.raises(TypeError, match="cannot extend"):

            class _Probe(Library):
                pass

    def test_a_same_named_base_outside_adcp_does_not_satisfy_the_rule(self):
        """The name accommodation is scoped to adcp.*, or any accidental name match
        anywhere would satisfy it."""

        class Account:  # __module__ is this test module, not adcp.*
            pass

        class Local(Account):
            pass

        class LibraryAccount:
            pass

        LibraryAccount.__name__ = "Account"
        assert library_base_violation("Account", Local, LibraryAccount) is not None

    def test_a_class_inheriting_no_library_base_is_a_violation(self):
        """The base case the guard exists for, untouched by either accommodation."""

        class Library:
            pass

        class Local:
            pass

        assert "does not inherit from" in library_base_violation("X", Local, Library)
