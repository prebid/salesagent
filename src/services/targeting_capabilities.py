"""
Targeting capabilities configuration.

Defines which targeting dimensions are available for overlay vs managed-only access.
This is critical for AEE (Ad Effectiveness Engine) integration.

AdCP TargetingOverlay defines: geo_countries, geo_regions, geo_metros,
geo_postal_areas, frequency_cap, property_list, axe_include_segment,
axe_exclude_segment.  Everything else here is a seller extension — standard
ad-server dimensions (device, OS, browser, media type, audience) that AdCP
does not yet define but that adapters actively support.  These are candidates
for upstream inclusion in AdCP.
"""

from typing import TYPE_CHECKING, Any

from src.core.enum_helpers import enum_value
from src.core.errors.codes import ErrorCode
from src.core.errors.details import CapabilityRefusalDetails, ValidationDetails
from src.core.exceptions import AdCPValidationError
from src.core.schemas import Error, Targeting, TargetingCapability
from src.core.validation_helpers import PACKAGES_FIELD

if TYPE_CHECKING:
    from src.core.database.models import Product

# Define targeting capabilities for the platform
TARGETING_CAPABILITIES: dict[str, TargetingCapability] = {
    # ── AdCP-defined dimensions ──────────────────────────────────────────
    # These map directly to fields on adcp.types.TargetingOverlay.
    "geo_country": TargetingCapability(
        dimension="geo_country", access="overlay", description="Country-level targeting using ISO 3166-1 alpha-2 codes"
    ),
    "geo_region": TargetingCapability(dimension="geo_region", access="overlay", description="State/province targeting"),
    "geo_metro": TargetingCapability(dimension="geo_metro", access="overlay", description="Metro/DMA targeting"),
    "geo_zip": TargetingCapability(dimension="geo_zip", access="overlay", description="Postal code targeting"),
    "frequency_cap": TargetingCapability(
        dimension="frequency_cap", access="overlay", description="Impression frequency limits"
    ),
    "device_platform": TargetingCapability(
        dimension="device_platform",
        access="overlay",
        description="OS-level platform targeting (Sec-CH-UA-Platform values)",
        allowed_values=[
            "ios",
            "android",
            "windows",
            "macos",
            "linux",
            "chromeos",
            "tvos",
            "tizen",
            "webos",
            "fire_os",
            "roku_os",
        ],
    ),
    # ── Seller extensions ────────────────────────────────────────────────
    # Standard ad-server dimensions not yet in AdCP TargetingOverlay.
    # Adapters (GAM, Kevel, Triton, Xandr) actively consume these.
    # Candidates for upstream AdCP inclusion.
    "device_type": TargetingCapability(
        dimension="device_type",
        access="overlay",
        description="Device type targeting",
        allowed_values=["mobile", "desktop", "tablet", "ctv", "dooh", "audio"],
    ),
    "device_make": TargetingCapability(
        dimension="device_make", access="overlay", description="Device manufacturer targeting"
    ),
    "os": TargetingCapability(dimension="os", access="overlay", description="Operating system targeting"),
    "browser": TargetingCapability(dimension="browser", access="overlay", description="Browser targeting"),
    "content_category": TargetingCapability(
        dimension="content_category", access="overlay", description="IAB content category targeting"
    ),
    "content_language": TargetingCapability(
        dimension="content_language", access="overlay", description="Content language targeting"
    ),
    "content_rating": TargetingCapability(
        dimension="content_rating", access="overlay", description="Content rating targeting"
    ),
    "media_type": TargetingCapability(
        dimension="media_type",
        access="overlay",
        description="Media type targeting",
        allowed_values=["video", "display", "native", "audio", "dooh"],
    ),
    "audience_segment": TargetingCapability(
        dimension="audience_segment", access="overlay", description="Third-party audience segments"
    ),
    "custom": TargetingCapability(dimension="custom", access="both", description="Platform-specific custom targeting"),
}


def get_overlay_dimensions() -> list[str]:
    """Get list of dimensions available for overlay targeting."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.access in ["overlay", "both"]]


# Explicit mapping from Targeting field names to capability dimension names.
# Both inclusion and exclusion variants map to the same capability dimension.
#
# AdCP TargetingOverlay defines only the geo fields, frequency_cap, axe
# segments, and property_list.  The device/OS/browser/media/audience fields
# are seller extensions carried forward from the original seller engine —
# standard ad-server dimensions that adapters actively support but AdCP has
# not yet adopted.  See module docstring for details.
FIELD_TO_DIMENSION: dict[str, str] = {
    # ── AdCP-defined fields (from adcp.types.TargetingOverlay) ───────────
    "geo_countries": "geo_country",
    "geo_regions": "geo_region",
    "geo_metros": "geo_metro",
    "geo_postal_areas": "geo_zip",
    "frequency_cap": "frequency_cap",
    # ── Geo exclusion extensions (PR #1006, not yet in AdCP) ─────────────
    "geo_countries_exclude": "geo_country",
    "geo_regions_exclude": "geo_region",
    "geo_metros_exclude": "geo_metro",
    "geo_postal_areas_exclude": "geo_zip",
    # ── AdCP device_platform (OS-level, converted to device_type internally) ──
    "device_platform": "device_platform",
    # ── Seller extensions (not in AdCP, consumed by adapters) ────────────
    "device_type_any_of": "device_type",
    "device_type_none_of": "device_type",
    "os_any_of": "os",
    "os_none_of": "os",
    "browser_any_of": "browser",
    "browser_none_of": "browser",
    "content_cat_any_of": "content_category",
    "content_cat_none_of": "content_category",
    "media_type_any_of": "media_type",
    "media_type_none_of": "media_type",
    "audiences_any_of": "audience_segment",
    "audiences_none_of": "audience_segment",
    "custom": "custom",
}


def supports_property_list_filtering(adapter: object | type | None) -> bool:
    """Return True iff the bound adapter compiles ``targeting_overlay.property_list``.

    Today no adapter sets ``supports_property_list_filtering=True``; the
    declaration in ``get_adcp_capabilities`` is the canonical "False until an
    adapter actually compiles it" anchor. When Kevel's siteId resolver lands,
    Kevel's adapter class will set this ClassVar to True and the helper will
    start returning True for tenants on Kevel. Other adapters hard-reject, at
    which point this advisory path is unreachable for them. Centralizing the
    check here keeps the wire declaration (capabilities) and the per-call
    advisory (this module) in lockstep with one source of truth.

    Accepts either an adapter INSTANCE (the post-construction create_media_buy
    path) or an adapter CLASS (the principal-free capabilities-read path,
    salesagent-dn2s) — the flag is a class attribute either way.
    """
    if adapter is None:
        return False
    adapter_class = adapter if isinstance(adapter, type) else adapter.__class__
    return bool(getattr(adapter_class, "supports_property_list_filtering", False))


# ─── property_list targeting helpers ────────────────────────────────────
#
# Spec scope: these helpers only handle ``property_list``. The AdCP 3.0.0
# spec governs ``property_list`` via a per-product flag
# (``Product.property_targeting_allowed``) and a per-capability declaration
# (``MediaBuyFeatures.property_list_filtering``). ``collection_list`` and
# ``collection_list_exclude`` use a different mechanism — capability-level
# only — declared in ``get_adcp_capabilities`` per
# ``core/targeting.json:collection_list``: "Seller must declare support in
# get_adcp_capabilities." There is no per-product flag for collection_list,
# so the asymmetry below is spec-defined, not an oversight. Collection-list
# capability infrastructure lands separately.


def build_property_list_unsupported_advisories(
    packages: list[Any] | None,
    capability_supported: bool,
) -> list[Error]:
    """Build per-package ``UNSUPPORTED_FEATURE`` advisories for property_list use.

    AdCP spec 3.0.0 ``error-handling.mdx`` describes non-fatal errors as
    "populate only the payload... MUST NOT populate ``adcp_error``" — i.e.
    advisories ride on the success envelope. Buyers see the silent-drop
    window during the rollout of property_list round-trip and adapter
    compilation, without the request being rejected.

    Returns Error objects for each package whose ``targeting_overlay.property_list``
    is set when the bound adapter does not compile the field. Caller appends
    to ``CreateMediaBuySuccess.errors`` / ``UpdateMediaBuySuccess.errors``.
    """
    if capability_supported or not packages:
        return []

    advisories: list[Error] = []
    for index, package in enumerate(packages):
        overlay = getattr(package, "targeting_overlay", None)
        if overlay is None or getattr(overlay, "property_list", None) is None:
            continue
        advisories.append(
            # message, suggestion and recovery all come from CODE_TABLE via the
            # code. What is specific to THIS advisory -- which package, and which
            # capability is off -- travels structurally: field names the rejected
            # path, details names the feature.
            Error.of(
                ErrorCode.UNSUPPORTED_FEATURE,
                field=f"packages[{index}].targeting_overlay.property_list",
                details=CapabilityRefusalDetails(capability="property_list_filtering"),
            )
        )
    return advisories


def property_list_unsupported_advisories(
    packages: list[Any] | None,
    adapter: object | None,
) -> list[Error] | None:
    """High-level wrapper: build advisories or return ``None`` when none apply.

    Single entry point for both create and update paths; mirrors
    ``MediaBuyFeatures.property_list_filtering`` source-of-truth via
    ``supports_property_list_filtering()`` so the per-call advisory and the
    capability declaration cannot drift. ``None`` (not ``[]``) so the
    optional ``errors`` field round-trips cleanly through
    ``model_dump(exclude_none=True)``.
    """
    advisories = build_property_list_unsupported_advisories(packages, supports_property_list_filtering(adapter))
    return advisories or None


def validate_property_targeting_allowed(product: "Product | None", targeting_overlay: Targeting | None) -> str | None:
    """Reject property_list targeting against products that disallow it.

    AdCP 3.0.0 (core/product.json ``property_targeting_allowed``): "Sellers
    SHOULD return a validation error if the product has
    property_targeting_allowed: false."

    Used at both create_media_buy and update_media_buy validation sites; pulled
    here so the rule lives in one place. Pair with
    ``raise_if_property_targeting_violations`` to convert collected violations
    into the wire-shape AdCPValidationError.

    Returns a violation message string, or None when targeting is allowed or
    when the product is missing (caller is responsible for surfacing the
    not-found error via a separate path; this helper must not crash on None).
    """
    if product is None:
        return None
    if (
        targeting_overlay is not None
        and targeting_overlay.property_list is not None
        and not product.property_targeting_allowed
    ):
        return f"Product {product.product_id} does not allow property_list targeting (property_targeting_allowed=false)"
    return None


def raise_if_property_targeting_violations(violations: list[str]) -> None:
    """Raise ``AdCPValidationError`` when any property_targeting violations were collected.

    Centralizes the wire-error envelope shape for property_list rejection so
    create and update paths emit byte-identical error responses (same code,
    same field, same details shape). Caller collects ``violations`` using
    ``validate_property_targeting_allowed()`` — product resolution differs
    between create's in-memory ``product_map`` and update's
    ``uow.products.get_by_id`` lookup, so the collection stays at the call
    site; only the raise shape is shared.
    """
    if violations:
        # The COLLECTION: violations are gathered across every package before this
        # raises, so no single element is at fault and the array parameter is what
        # the pointer names. Which packages violated travels in details
        # (salesagent-rfxfu).
        raise AdCPValidationError(
            field=PACKAGES_FIELD,
            # list[str] of prose -> reasons. The dict-shaped `violations` the two
            # overlay sites pass is a different shape under the same old name.
            details=ValidationDetails(reasons=violations),
        )


# Geo inclusion/exclusion field pairs for same-value overlap detection.
# Per adcp PR #1010: sellers SHOULD reject when the same value appears in both
# the inclusion and exclusion field at the same level.
_GEO_SIMPLE_PAIRS: list[tuple[str, str]] = [
    ("geo_countries", "geo_countries_exclude"),
    ("geo_regions", "geo_regions_exclude"),
]
_GEO_STRUCTURED_PAIRS: list[tuple[str, str]] = [
    ("geo_metros", "geo_metros_exclude"),
    ("geo_postal_areas", "geo_postal_areas_exclude"),
]


def _extract_simple_values(items: list) -> set[str]:
    """Extract string values from a list of GeoCountry/GeoRegion (RootModel[str]) or plain strings."""
    return {getattr(item, "root", item) for item in items}


def _extract_system_values(items: list) -> dict[str, set[str]]:
    """Extract ``{system: set(values)}`` from geo items that carry a system and values.

    Reads ``system``/``values`` off whatever it is given rather than gating on a list of
    known classes. The gate that used to be here named ``GeoMetro`` and ``PostalArea``
    and dropped everything else through a bare ``else: continue`` — so an item type it
    did not recognise produced no values, and the caller could not tell that from a
    genuine absence of overlap.

    The drop was real but MASKED, and the distinction matters. The SDK emits a distinct
    class per position, and the pin's ``geo_metros_exclude`` elements are
    ``GeoMetrosExcludeItem``, which does NOT subclass ``GeoMetro`` — unlike the countries
    and regions exclude types, which do subclass their include-side classes. That element
    type would have fallen straight through the gate. It never did, because the schema
    redeclared the field as ``list[GeoMetro]``, so Pydantic coerced every value to the
    include-side class before this function saw it.

    So the redeclaration was load-bearing: it existed to keep one field out of a swallow
    that still ate every other unrecognised type. Deleting the gate is what makes it
    unnecessary, which is why the redeclaration could be removed in the same change rather
    than kept with a comment explaining why it must stay.
    """
    by_system: dict[str, set[str]] = {}
    for item in items:
        if isinstance(item, dict):
            system = enum_value(item.get("system", ""))
            vals = set(item.get("values", []))
        else:
            system = enum_value(item.system)
            vals = set(item.values)
        if system is None:
            continue
        by_system.setdefault(system, set()).update(vals)
    return by_system


def collect_targeting_violations(targeting: Targeting) -> dict[str, object]:
    """Assemble every targeting-overlay violation into buyer-facing ``details``.

    THE one place the per-dimension validators are composed. Both tools that validate an
    overlay -- create_media_buy and update_media_buy -- called them by hand and
    concatenated the results, which meant the same logical operation lived in two places
    and a new validator would have to be wired into both (DRY, CLAUDE.md). Neither call
    site read what it collected: both raised and discarded it.

    Returns a mapping suitable for ``AdCPInvalidRequestError(details=...)``, carrying only
    the keys that actually have content so the buyer can tell the reasons apart:

        geo_overlaps              {include, exclude, values} per conflicting field pair,
                                  plus `system` for metro/postal pairs

    Empty mapping means the overlay is clean, so the caller can branch on truthiness.

    These are BUSINESS RULES, discovered by checking them. Schema SHAPE is not among
    them and deliberately so: an unknown targeting field is rejected by pydantic at model
    construction (``Targeting`` resolves ``extra`` through ``get_pydantic_extra_mode()`` --
    ``forbid`` in dev/CI, ``ignore`` in production), so it never reaches business logic.
    A ``model_extra`` scan lived here until salesagent-3dawm.9 and could not fire in
    either mode. A "managed-only dimension" check lived here too, over a single seller
    key/value field the pinned core/targeting.json does not declare; the field and the
    check are gone (salesagent-3cs7o.22), and such a request is refused as an undeclared
    field, at model construction, like any other.

    Values, never sentences: the buyer-facing sentence is a function of the error CODE
    through CODE_TABLE, and a sentence in ``details`` is that message smuggled back in
    (salesagent-3dawm.9). The code is INVALID_REQUEST with ``field='targeting_overlay'``
    for every kind -- graded that way by UC-002 @ext-f and UC-003 @*-targeting-overlay --
    so ``details`` is what distinguishes them, not the code.
    """
    candidates: dict[str, object] = {
        "geo_overlaps": geo_overlap_conflicts(targeting),
    }
    return {key: value for key, value in candidates.items() if value}


def geo_overlap_conflicts(targeting: Targeting) -> list[dict[str, object]]:
    """Reject same-value overlap between geo inclusion and exclusion fields.

    Per AdCP spec (adcp PR #1010): sellers SHOULD reject requests where the
    same value appears in both the inclusion and exclusion field at the same
    level (e.g., geo_countries: ["US"] with geo_countries_exclude: ["US"]).

    Returns one RECORD per conflicting field pair -- ``{include, exclude, values}``, plus
    ``system`` for the structured pairs -- rather than a sentence, so the colliding values
    reach the buyer as data (salesagent-3dawm.9).
    """
    violations: list[dict[str, object]] = []

    # Simple fields: countries, regions (RootModel[str] or plain strings)
    for include_field, exclude_field in _GEO_SIMPLE_PAIRS:
        include_vals = getattr(targeting, include_field, None)
        exclude_vals = getattr(targeting, exclude_field, None)
        if not include_vals or not exclude_vals:
            continue
        inc_set = _extract_simple_values(include_vals)
        exc_set = _extract_simple_values(exclude_vals)
        overlap = sorted(inc_set & exc_set)
        if overlap:
            violations.append({"include": include_field, "exclude": exclude_field, "values": overlap})

    # Structured fields: metros, postal_areas (system + values)
    for include_field, exclude_field in _GEO_STRUCTURED_PAIRS:
        include_vals = getattr(targeting, include_field, None)
        exclude_vals = getattr(targeting, exclude_field, None)
        if not include_vals or not exclude_vals:
            continue
        inc_by_system = _extract_system_values(include_vals)
        exc_by_system = _extract_system_values(exclude_vals)
        for system in sorted(set(inc_by_system) & set(exc_by_system)):
            overlap = sorted(inc_by_system[system] & exc_by_system[system])
            if overlap:
                violations.append(
                    {"include": include_field, "exclude": exclude_field, "system": system, "values": overlap}
                )

    return violations
