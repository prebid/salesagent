"""Creative format parsing and asset conversion helpers.

The six ``attr-defined`` suppressions this module carried
(adcontextprotocol/adcp-client-python#913) are gone, and not by widening a type. Each sat
on a ``.asset_type`` or ``.asset_id`` read performed after narrowing a declared slot to
``adcp.types.ImageFormatAsset`` -- which is the IMAGE arm of a discriminated union, so the
reads mypy could not see through were also reads of the wrong arm. :func:`declared_slots`
replaced the narrowing with a read of the discriminator itself, and a suppression that
exists because of a wrong cast disappears with the cast.

Written without the directive's own spelling on purpose: ``.type-ignore-baseline`` counts
the literal text under ``src/``, so a sentence ABOUT a suppression it no longer has would
count as one -- a comment that invalidates the number it describes.
"""

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from adcp.types import ValidationMode
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as LibraryFormatId
from pydantic import BaseModel, ValidationError

from src.core.tenant_context import TenantContext

if TYPE_CHECKING:
    from adcp.types import AccountReference as LibraryAccountReference

    from src.core.resolved_identity import ResolvedIdentity
    from src.core.schemas import PackageRequest

from src.core.errors.details import CreativeRejectionDetails

logger = logging.getLogger(__name__)


def _extract_format_namespace(format_value: Any) -> tuple[str, str]:
    """Extract agent_url and format ID from format_id field (AdCP v2.4).

    Args:
        format_value: FormatId dict/object with agent_url+id fields

    Returns:
        Tuple of (agent_url, format_id) - both as strings

    Raises:
        ValueError: If format_value doesn't have required agent_url and id fields

    Note:
        Converts Pydantic AnyUrl types to strings for database compatibility.
        The adcp library's FormatId.agent_url is typed as AnyUrl, but PostgreSQL
        needs strings.
    """
    if isinstance(format_value, dict):
        agent_url = format_value.get("agent_url")
        format_id = format_value.get("id")
        if not agent_url or not format_id:
            raise ValueError(f"format_id must have both 'agent_url' and 'id' fields. Got: {format_value}")
        # Convert to string in case agent_url is AnyUrl from Pydantic model
        return str(agent_url), format_id
    if isinstance(format_value, LibraryFormatId):
        # Convert AnyUrl to string for database compatibility
        return str(format_value.agent_url), format_value.id
    if isinstance(format_value, str):
        raise ValueError(
            f"format_id must be an object with 'agent_url' and 'id' fields (AdCP v2.4). "
            f"Got string: '{format_value}'. "
            f"String format_id is no longer supported - all formats must be namespaced."
        )
    raise ValueError(f"Invalid format_id format. Expected object with agent_url and id, got: {type(format_value)}")


def _normalize_format_value(format_value: Any) -> str:
    """Normalize format value to string ID (for legacy code compatibility).

    Args:
        format_value: FormatId dict/object with agent_url+id fields

    Returns:
        String format identifier

    Note: This is a legacy compatibility function. New code should use _extract_format_namespace
    to properly handle the agent_url namespace.
    """
    _, format_id = _extract_format_namespace(format_value)
    return format_id


def _validate_creative_assets(assets: Any) -> dict[str, dict[str, Any]] | None:
    """Validate that creative assets are in AdCP v2.1+ dictionary format.

    AdCP v2.1+ requires assets to be a dictionary keyed by asset_id from the format's
    asset_requirements.

    Args:
        assets: Assets in dict format keyed by asset_id, or None

    Returns:
        Dictionary of assets keyed by asset_id, or None if no assets provided

    Raises:
        ValueError: If assets are not in the correct dict format, or if asset structure is invalid

    Example:
        # Correct format (AdCP v2.1+)
        assets = {
            "main_image": {"asset_type": "image", "url": "https://..."},
            "logo": {"asset_type": "image", "url": "https://..."}
        }
    """
    if assets is None:
        return None

    # Must be a dict
    if not isinstance(assets, dict):
        raise ValueError(
            f"Invalid assets format: expected dict keyed by asset_id (AdCP v2.1+), got {type(assets).__name__}. "
            f"Assets must be a dictionary like: {{'main_image': {{'asset_type': 'image', 'url': '...'}}}}"
        )

    # Validate structure of each asset
    for asset_id, asset_data in assets.items():
        # Asset ID must be a non-empty string
        if not isinstance(asset_id, str):
            raise ValueError(
                f"Asset key must be a string (asset_id from format), got {type(asset_id).__name__}: {asset_id!r}"
            )
        if not asset_id.strip():
            raise ValueError("Asset key (asset_id) cannot be empty or whitespace-only")

        # Asset data must be a dict or Pydantic model (typed Asset from CreativeAsset)
        if not isinstance(asset_data, dict) and not isinstance(asset_data, BaseModel):
            raise ValueError(
                f"Asset '{asset_id}' data must be a dict or model, got {type(asset_data).__name__}. "
                f"Expected format: {{'asset_type': '...', 'url': '...', ...}}"
            )

    return assets


def _detect_snippet_type(snippet: str) -> str:
    """Auto-detect snippet type from content for legacy support."""
    if snippet.startswith("<?xml") or ".xml" in snippet:
        return "vast_xml"
    elif snippet.startswith("http") and "vast" in snippet.lower():
        return "vast_url"
    elif snippet.startswith("<script"):
        return "javascript"
    else:
        return "html"  # Default


def process_and_upload_package_creatives(
    packages: list["PackageRequest"],
    *,
    identity: "ResolvedIdentity",
    # The OUTER create_media_buy's account, because the nested sync is built as a real
    # SyncCreativesRequest and these creatives belong to that account. No idempotency_key:
    # this calls the creative-sync SERVICE, and idempotency is the controller's job.
    account: "LibraryAccountReference | None" = None,
    principal_id: str,
    tenant: TenantContext,
) -> tuple[list["PackageRequest"], dict[str, list[str]]]:
    """Upload creatives from package.creatives arrays and return updated packages.

    For each package with a non-empty `creatives` array:
    1. Converts Creative objects to dicts
    2. Uploads them via _sync_creatives_impl
    3. Extracts uploaded creative IDs
    4. Creates updated package with merged creative_ids

    This function is immutable - it returns new Package instances instead of
    modifying the input packages.

    Args:
        packages: List of Package objects to process
        identity: The caller the create controller already resolved
        account: The outer create_media_buy's account, carried onto the nested sync request
        principal_id: The already-resolved caller, passed to the creative-sync service
        tenant: The already-resolved tenant, likewise

    Returns:
        Tuple of (updated_packages, uploaded_ids_by_product):
        - updated_packages: New Package instances with creative_ids merged
        - uploaded_ids_by_product: Mapping of product_id -> uploaded creative IDs

    Raises:
        ToolError: If creative upload fails for any package (CREATIVES_UPLOAD_FAILED)

    Example:
        >>> packages = [PackageRequest(product_id="p1", creatives=[creative1, creative2])]
        >>> updated_pkgs, uploaded_ids = process_and_upload_package_creatives(packages, identity=identity, ...)
        >>> # updated_pkgs[0].creative_ids contains uploaded IDs
        >>> assert uploaded_ids["p1"] == ["c1", "c2"]
    """
    import logging

    # Lazy import to avoid circular dependency
    from src.core.exceptions import AdCPAdapterError, AdCPCreativeRejectedError, AdCPSalesAgentError
    from src.core.schemas import SyncCreativesRequest
    from src.core.tools.creatives import sync_creatives

    logger = logging.getLogger(__name__)
    uploaded_by_product: dict[str, list[str]] = {}
    updated_packages: list[PackageRequest] = []

    for pkg_idx, pkg in enumerate(packages):
        # Skip packages without creatives (type system guarantees this attribute exists)
        if not pkg.creatives:
            updated_packages.append(pkg)  # No changes needed
            continue

        product_id = pkg.product_id or f"package_{pkg_idx}"
        logger.info(f"Processing {len(pkg.creatives)} creatives for package with product_id {product_id}")

        try:
            # Step 1: Upload creatives to database via sync_creatives
            # Phase 1a: Pass models directly (impl handles both models and dicts)
            # Built through the SAME builder the three transports use, rather than handed
            # to _impl as loose fields. _sync_creatives_impl takes a request; an in-process
            # caller that could not produce one was reaching into the tool instead of
            # invoking it.
            #
            # No request_hash is passed: there is no transmission here to canonicalise, and
            # that absence is what keeps the outer media buy's key out of the shared
            # (agent, account, key) idempotency cache scope -- see _sync_creatives_impl.
            # A pydantic ValidationError from this builder must NOT reach the
            # `except Exception` below, which reclassifies it as AdCPAdapterError --
            # "Service temporarily unavailable" -- telling a buyer who sent a malformed
            # inline creative that the SERVER is broken and to retry, instead of which of
            # their fields to fix. That is why the handler list below re-raises it.
            sync_req = SyncCreativesRequest(
                creatives=pkg.creatives,
                account=account,
                # ITS OWN key, not the outer request's. sync-creatives-request.json puts
                # idempotency_key in /required so the model needs one, but this request is
                # never sent by anyone and the SERVICE never reads it -- idempotency belongs
                # to the controller, which ran once for the create the buyer actually sent.
                # Borrowing the outer key here is what used to make this call look like a
                # second buyer request wearing the same identifier.
                idempotency_key=f"internal-creative-upload-{uuid.uuid4().hex}",
                # Full upsert semantics (no patch parameter)
                assignments=None,  # Assign separately after creation
                dry_run=False,
                validation_mode=ValidationMode.strict,
                push_notification_config=None,
            )
            # The create controller resolved the caller before reaching here; no auth is
            # re-run inside a service, which is the layering this extraction removed.
            # ``assigned_by_caller``: every creative here is assigned to this package once the
            # buy exists, and the create gates grade its manifest against the declaration the
            # package's product selects. Held to its kind's slots here instead, a creative keyed
            # to a slot the product renamed would be refused for a slot it was never owed.
            sync_response = sync_creatives(
                sync_req, identity=identity, principal_id=principal_id, tenant=tenant, assigned_by_caller=True
            )

            # A failed sync result means the creative was REJECTED (e.g. missing
            # required URL / dimensions in strict validation). Surface it instead
            # of silently merging the failed id and letting the downstream
            # "Creative IDs not found" check mask the real reason ('No Quiet
            # Failures'). The per-creative error message names the offending
            # field (e.g. the missing URL) so the buyer can remediate (POST-F3).
            failed_results = [r for r in sync_response.creatives if r.action == "failed"]
            if failed_results:
                detail_msgs = [f"{r.creative_id}: {err.message}" for r in failed_results for err in (r.errors or [])]
                error_msg = "Creative validation failed:\n" + "\n".join(f"  • {m}" for m in detail_msgs)
                logger.error(error_msg)
                raise AdCPCreativeRejectedError(
                    # `creative_errors` was a synonym for the pin's `reasons`.
                    details=CreativeRejectionDetails(reasons=detail_msgs),
                )

            # Extract creative IDs from successfully synced creatives only.
            uploaded_ids = [
                result.creative_id
                for result in sync_response.creatives
                if result.creative_id and result.action != "failed"
            ]

            logger.info(
                f"Synced {len(uploaded_ids)} creatives to database for package "
                f"with product_id {product_id}: {uploaded_ids}"
            )

            # Note: Ad server upload happens later in media buy creation flow
            # This function runs BEFORE media_buy_id exists, so we can't call
            # adapter.add_creative_assets() here (it requires media_buy_id, assets, today).
            # The creatives are synced to database above and will be uploaded to
            # the ad server during media buy creation when media_buy_id is available.

            # Create updated package with merged creative_ids (immutable)
            existing_ids = pkg.creative_ids or []
            merged_ids = [*existing_ids, *uploaded_ids]
            updated_pkg = pkg.model_copy(update={"creative_ids": merged_ids})
            updated_packages.append(updated_pkg)

            # Track uploads for return value
            uploaded_by_product[product_id] = uploaded_ids

        except (AdCPSalesAgentError, ValidationError):
            # ValidationError travels with the typed errors, not with the adapter
            # failures. It is the buyer's document failing the request SCHEMA, and the
            # transport boundary turns it into INVALID_REQUEST carrying the field and the
            # issues (adcp_error_for tests ValidationError before ValueError, deliberately).
            # Reclassifying it below as AdCPAdapterError would tell the buyer the server is
            # unavailable and to retry an unfixed request. This branch is what replaced the
            # adcp_validation_boundary that used to wrap the builder call: the boundary
            # produced the same envelope, one frame earlier, at the cost of a translation
            # this layer has no business performing.
            raise
        except Exception as e:
            error_msg = f"Failed to upload creatives for package with product_id {product_id}: {str(e)}"
            logger.error(error_msg)
            # Re-raise as ToolError for consistent error handling
            raise AdCPAdapterError() from e

    return updated_packages, uploaded_by_product


# =============================================================================
# URL Extraction Helpers
# =============================================================================
# These functions extract media URLs, click-through URLs, and impression tracker
# URLs from creative data. They are used by both media_buy_create.py and
# creatives.py to ensure consistent URL extraction logic.
# =============================================================================

# Asset types that contain media content with URLs
# Based on AdCP creative agent format specs:
# - image: banner_image, main_image, thumbnail, billboard_image, etc.
# - video: video_file
# - audio: audio_file
# NOT included:
# - url: Used for clickthrough URLs and trackers (url_type field distinguishes them)
# - html/javascript/vast/text: These have 'content' field, not 'url' field
#
# frozen because it is also the ASSET-TYPE FILTER handed to
# ``canonical_slot_asset_ids``, which caches on its arguments.
MEDIA_ASSET_TYPES = frozenset({"image", "video", "audio"})

# Asset types that carry dimensions. Audio does not, so a slot of that type contributes a
# URL and nothing else -- the same split priority 1 has always made, stated once.
SIZED_ASSET_TYPES = frozenset({"image", "video"})

# Known media asset IDs for a creative whose format spec did NOT resolve and whose
# canonical kind declares nothing -- a LEGACY reference the registry could not project.
# Based on AdCP creative agent format specs + common conventions.
#
# THE LAST RESORT, and no longer the only one: a creative identified by its canonical kind
# reads its asset ids off the declaration's slots (priority 2 below), which is what the
# pin actually says they are. This list is the v1-era stand-in for that and MAY ONLY
# SHRINK. Measured against the declaration-derived ids at the pinned SDK: the slots name
# ``icon``, ``main_image`` and ``video`` among them, so those three are redundant for any
# creative carrying a kind -- but ``Creative.format_kind`` is nullable ("a legacy reference
# only has a kind once something projects it"), and for a creative with no kind this list
# is still the only path to its media asset. So none of the thirteen is dead yet, and the
# list shrinks when the kindless row does.
MEDIA_ASSET_FALLBACK_IDS = {
    # image assets
    "banner_image",
    "billboard_image",
    "icon",
    "main_image",
    "product_image",
    "screen_image",
    "thumbnail",
    # video assets
    "video_file",
    # audio assets
    "audio_file",
    # common conventions
    "main",
    "image",
    "video",
    "audio",
}

# Asset ids this seller once acted on by NAME, and no longer does.
#
# These are what is left of two hand-maintained guess lists -- ``CLICKTHROUGH_ASSET_IDS``
# (six members) and ``IMPRESSION_TRACKER_ASSET_IDS`` (four) -- after measuring both against
# ``core/asset-group-vocabulary.json``, the pin's normative registry of ``asset_group_id``
# values. Six of those ten ARE registry names or declared aliases and are now answered by
# the registry lookup in :func:`url_asset_identification`:
#
#   click_url, landing_url  -> aliases of ``landing_page_url``
#   impression_tracker      -> a canonical entry ("Renderer-fired impression tracker URL")
#   impression_pixel        -> a declared alias of it
#
# The six below appear in NO registry entry. Matching one of them and invoking the URL is
# exactly what ``core/assets/url-asset.json`` forbids: "receivers MUST NOT silently pick a
# mechanism".
#
# They are KEPT, and they no longer resolve anything: a creative keyed one of them is
# REFUSED with the name said. Deleting them outright would mean a creative that resolves a
# clickthrough today stops doing so with nobody told, which is the same silence as the bug
# arrived at from the other side. The pin's own remedy is the rest of that sentence: "they
# SHOULD reject the manifest."
#
# SHRINK-ONLY, and nothing in this repository keys an asset with any of them (measured over
# every .py, .json and .feature under src/ and tests/: the only hits are an unrelated
# ``creative_policy.landing_page`` field and a ``tracking_urls.click`` list). The set
# reaches zero when no deployment's stored creatives use them.
_DEPRECATED_URL_ASSET_IDS = frozenset(
    {
        # once honoured as the click destination
        "click",
        "clickthrough",
        "destination_url",
        "landing_page",
        # once honoured as an impression tracker. ``tracker_pixel`` is an asset_type and a
        # url_type value, never an asset_group_id; ``pixel`` names nothing at all.
        "pixel",
        "tracker_pixel",
    }
)

# The registry's canonical name for a slot IS the role name, for every url-bearing entry
# but one. ``core/requirements/url-asset-requirements.json``'s role enum is
# {clickthrough, landing_page, impression_tracker, click_tracker, viewability_tracker,
# third_party_tracker}, and the registry declares entries named ``impression_tracker``,
# ``click_tracker`` and ``viewability_tracker`` -- the same words. So an ``asset_group_id``
# resolved through the registry lands directly in :data:`_SLOT_ROLE_TO_MECHANISM`, and the
# only entry needing a translation is the destination, whose canonical name carries the
# ``_url`` suffix:
#
#   landing_page_url : "Click-through destination URL for the ad."
#
# That is the whole of the registry-to-role mapping, and ``test_url_asset_identification``
# asserts it against the registry so a pin that renames or adds an entry surfaces here
# rather than silently widening or narrowing what this seller invokes.
_GROUP_ID_ROLE_OVERRIDES = {"landing_page_url": "clickthrough"}

# The pin's OWN role -> mechanism mapping, from the top-level description of
# ``core/assets/url-asset.json``, verbatim: "When ``url_type`` is absent, receivers SHOULD
# fall back to the format's ``url-asset-requirements.role`` per this mapping:
# clickthrough/landing_page -> ``clickthrough``; impression_tracker/click_tracker ->
# ``tracker_pixel``; viewability_tracker -> ``tracker_script`` (OMID and equivalent
# verification SDKs require a <script> tag -- firing them as a pixel produces no
# measurement); third_party_tracker -> no safe fallback (mechanism is integration-specific
# -- DV/IAS ship both pixel and script forms -- receivers MAY reject or warn)."
#
# ``third_party_tracker`` is ABSENT ON PURPOSE and not an oversight: the pin states it has
# no safe fallback, so a slot declaring it is identified (the seller knows what the URL is
# FOR) while its mechanism stays unknown. Mapping it to anything would be the mechanism
# guess the schema forbids one sentence later.
#
# The field is ``role``. ``core/requirements/url-asset-requirements.json`` declares
# ``role`` with this six-member enum and declares no ``url_type`` at all -- both url readers
# here used to look for ``requirements.url_type``, a field that schema does not have.
_SLOT_ROLE_TO_MECHANISM = {
    "clickthrough": "clickthrough",
    "landing_page": "clickthrough",
    "impression_tracker": "tracker_pixel",
    "click_tracker": "tracker_pixel",
    "viewability_tracker": "tracker_script",
}


def _pinned_schema(*parts: str) -> dict[str, Any]:
    """One pinned schema document, by its path under the bundle this SDK release targets.

    Reached through the SDK's PUBLIC version API -- the way ``src/core/errors/codes.py``
    reaches the error-code enum -- so a private schema-root helper moving between SDK
    releases cannot take this down. Callers cache; nothing here runs at import, because the
    no-import-time-filesystem-IO guard covers this module.
    """
    from adcp import get_adcp_spec_version
    from adcp.validation.version import resolve_bundle_key

    bundle = resolve_bundle_key(get_adcp_spec_version())
    document = files("adcp") / "_schemas" / bundle
    for part in parts:
        document = document / part
    parsed: dict[str, Any] = json.loads(document.read_text(encoding="utf-8"))
    return parsed


@lru_cache(maxsize=1)
def _asset_group_vocabulary() -> dict[str, str]:
    """Every ``asset_group_id`` the pin DEFINES, each mapped to its canonical name.

    ``core/asset-group-vocabulary.json`` is the "canonical registry of asset_group_id
    values", and under a heading it marks "Two-tier boundary (normative)" it says it
    "codifies the recommended canonical set so that buyers and sellers share a vocabulary
    for the most common slot ROLES". So resolving an asset id through it IS the role lookup
    the pin's fallback ladder asks for, and not a name guess: the registry DEFINES what the
    id means.

    Canonical names and declared aliases both map to the canonical name, because the
    registry's own ``governance.alias_policy`` says "Aliases listed for canonical entries
    ... are recognized as v1-era variants ... v1 aliases continue to work for backwards
    compatibility".

    THE WHOLE REGISTRY, not just the click-destination entry. Measured at the pinned SDK: 49
    entries, three of them url-typed -- ``landing_page_url``, ``privacy_policy_url`` and
    ``subtitle_file``. The latter two are a role this seller does not act on, which is a
    different fact from a role it was never told: an asset keyed ``privacy_policy_url`` is
    IDENTIFIED (the pin says what it is) and must not be refused merely because no
    mechanism follows from it. A reader that knew only ``landing_page_url`` would reject
    every manifest carrying a privacy policy.
    """
    defined: dict[str, str] = {}
    for canonical, entry in _pinned_schema("core", "asset-group-vocabulary.json")["vocabulary"].items():
        defined[canonical] = canonical
        for alias in entry.get("aliases") or ():
            defined[alias] = canonical
    return defined


@lru_cache(maxsize=1)
def _manifest_url_mechanisms() -> frozenset[str]:
    """Every ``url_type`` a manifest asset may declare — ``enums/url-asset-type.json``.

    READ off the enum rather than derived from :data:`_SLOT_ROLE_TO_MECHANISM`'s values,
    which is a different set and a shorter one: the enum carries ``ad_request``
    ("invokes a third-party display ad endpoint at impression time; it is not a tracker or
    click destination"), which no format-side role maps to. An asset declaring that
    ``url_type`` has told the seller its mechanism and must not be refused as unidentified,
    so deriving this set from the role map would have refused a legitimate manifest.
    """
    return frozenset(_pinned_schema("enums", "url-asset-type.json")["enum"])


def _slot_field(slot: Any, name: str) -> Any:
    """Read one field off a declared slot, whichever shape it arrived in.

    ``adcp.utils.get_format_assets`` returns the format's ``assets`` list — or a canonical
    declaration's ``params.slots`` — unchanged, so an element is a typed union arm when the
    spec was validated and a plain dict when it came in as a Mapping. One reader for both.
    """
    if isinstance(slot, Mapping):
        return slot.get(name)
    return getattr(slot, name, None)


def declared_slots(format_spec: Any | None) -> list[tuple[str, str, Any]]:
    """``(asset_id, asset_type, requirements)`` for every individual slot a spec declares.

    THE GUARD READS THE DISCRIMINATOR, NOT A CLASS. Every reader in this module used to
    open its format-spec branch with::

        from adcp.types import ImageFormatAsset as Assets
        if not isinstance(asset_spec, Assets):
            continue

    and the alias is the lie: ``adcp.types.ImageFormatAsset`` IS
    ``adcp.types.domains.core.format.Assets``, the IMAGE arm of a discriminated union whose
    other arms are not subclasses of it (measured at the pinned SDK: 45 arm classes, ``video``,
    ``audio``, ``url``, ``pixel_tracker`` and the rest all ``issubclass -> False``). So the
    guard admitted image slots only, and then every test the loop performed on
    ``asset_type`` was testing a value the guard had already pinned to ``"image"``:

    * the url readers' format-spec branch needed ``asset_type == "url"`` and so was
      structurally unreachable for any spec whatsoever;
    * the media reader saw image slots only, so a legacy VIDEO or AUDIO creative never
      resolved its media asset from its own declaration and fell through to a
      naming-convention allowlist.

    The union is DISCRIMINATED, which is the whole point: the arm that arrived says what it
    is, so there is nothing to narrow to. A slot with no readable ``asset_id`` or
    ``asset_type`` is dropped — that is the only thing the old guard legitimately did.
    """
    from adcp.utils import get_individual_assets, has_assets

    if format_spec is None or not has_assets(format_spec):
        return []
    slots: list[tuple[str, str, Any]] = []
    for slot in get_individual_assets(format_spec):
        asset_id = _slot_field(slot, "asset_id")
        asset_type = _slot_field(slot, "asset_type")
        if not asset_id or not asset_type:
            continue
        slots.append((str(asset_id), str(asset_type).lower(), _slot_field(slot, "requirements")))
    return slots


def _requirement_field(requirements: Any, name: str) -> Any:
    """Read one field off a slot's ``requirements``, typed or dict, or ``None``."""
    if requirements is None:
        return None
    if isinstance(requirements, Mapping):
        return requirements.get(name)
    return getattr(requirements, name, None)


def _url_roles_declared_by_the_format(format_spec: Any | None) -> tuple[dict[str, str], set[str]]:
    """``({asset_id: mechanism}, {asset_id with ANY declared role})`` for a spec's url slots.

    The pin's SECOND rung, consulted when a manifest asset omits ``url_type``:
    ``url-asset-requirements.role`` is "the receiver's fallback signal when a manifest URL
    asset omits ``url_type``", and :data:`_SLOT_ROLE_TO_MECHANISM` is the pin's own
    translation of it.

    TWO answers, because the pin asks two questions of the same field and they come apart
    on one role. A slot declaring ``third_party_tracker`` appears in the SECOND set and not
    the first: the seller has been told what the URL is FOR, so the asset is identified and
    must not be refused, while the pin gives its mechanism "no safe fallback" so nothing may
    be invoked from it. One walk, since both answers read the same ``role``.
    """
    mechanisms: dict[str, str] = {}
    identified: set[str] = set()
    for asset_id, asset_type, requirements in declared_slots(format_spec):
        if asset_type != "url":
            continue
        role = str(_requirement_field(requirements, "role") or "")
        if not role:
            continue
        identified.add(asset_id)
        mechanism = _SLOT_ROLE_TO_MECHANISM.get(role)
        if mechanism:
            mechanisms[asset_id] = mechanism
    return mechanisms, identified


def _manifest_assets(creative_data: dict[str, Any]) -> dict[str, Any]:
    """The creative's assets map, or an empty one for a creative that carries no assets."""
    assets = creative_data.get("assets") or {}
    return assets if isinstance(assets, dict) else {}


def url_asset_identification(
    creative_data: dict[str, Any], format_spec: Any | None
) -> tuple[dict[str, str], list[str]]:
    """What this manifest's url assets are FOR, per the pin's ladder — and which it never said.

    ``core/assets/url-asset.json`` states the receiver's rule normatively, and this is that
    sentence as code:

        "``url_type`` declares the mechanism a receiver uses to invoke the URL ... Senders
         SHOULD include ``url_type`` on every URL asset. When ``url_type`` is absent,
         receivers SHOULD fall back to the format's ``url-asset-requirements.role`` ... When
         neither ``url_type`` nor a format-side ``role`` is available, receivers MUST NOT
         silently pick a mechanism; they SHOULD reject the manifest."

    ONE ladder for every mechanism, consulted by both url readers. It used to be the same
    line shape written three times, each with its own hand-maintained list of guessed asset
    ids, and the clickthrough reader's list is the thing the MUST NOT condemns.

    Returns ``(mechanisms, unidentified)``:

    * ``mechanisms`` maps an asset id to the ``url-asset-type`` value a receiver may invoke
      it as. THREE rungs, in the pin's order: the asset's own ``url_type``; else the
      format-side ``role``; else the ``asset_group_id`` resolved through the vocabulary
      registry, which is the role signal for a CANONICAL manifest -- a canonical slot
      carries no ``role`` property of its own (measured: a slot declares asset_group_id,
      asset_type, required, min, max, max_chars, max_size_kb, pixel_ratios,
      required_pixel_ratios, logo_slots, required_logo_slots, consumed_for_production and
      description). There is no fourth rung, and that is the change: the two
      naming-convention lists that used to sit at the bottom are gone.
    * ``unidentified`` lists the url assets no rung could speak for. The caller refuses
      them; nothing here guesses.

    IDENTIFIED IS WIDER THAN INVOKABLE, and conflating the two would refuse correct
    manifests. An asset is identified, and so absent from ``unidentified``, when the pin
    says what it IS even if no mechanism follows: a ``third_party_tracker`` role (explicitly
    "no safe fallback"), or a registry entry whose role this seller does not invoke
    (``privacy_policy_url``, ``subtitle_file``).

    WHICH ASSETS COUNT AS URL ASSETS is deliberately narrow: one that positively declares
    ``asset_type: "url"``, plus :data:`_DEPRECATED_URL_ASSET_IDS`. An asset carrying a url
    and NO ``asset_type`` is not treated as one, because this seller's own asset validator
    does not require the field -- so a stored legacy creative keyed ``banner_image`` with no
    declared type would otherwise come back unidentified and be refused, which is the mirror
    of the bug being fixed. The named exceptions are there so that the creatives this change
    STOPS resolving a URL for are refused out loud rather than dropped in silence.

    The ``asset_group_id`` is read from the KEY the asset is stored under, which is what a
    canonical manifest keys by (the pinned storyboards under ``dist/compliance/<pin>/`` all
    key the destination ``landing_page_url``) and what this seller's slot-satisfaction
    reader already assumes.
    """
    declared_mechanisms, declared_roles = _url_roles_declared_by_the_format(format_spec)
    vocabulary = _asset_group_vocabulary()
    may_declare = _manifest_url_mechanisms()

    mechanisms: dict[str, str] = {}
    unidentified: list[str] = []
    # Sorted so two assets answering the same question resolve the same way on every run.
    for asset_id, asset_obj in sorted(_manifest_assets(creative_data).items()):
        if not isinstance(asset_obj, dict) or not asset_obj.get("url"):
            continue
        if str(asset_obj.get("asset_type") or "") != "url" and asset_id not in _DEPRECATED_URL_ASSET_IDS:
            continue

        url_type = str(asset_obj.get("url_type") or "")
        if url_type in may_declare:
            mechanisms[asset_id] = url_type
        elif asset_id in declared_mechanisms:
            mechanisms[asset_id] = declared_mechanisms[asset_id]
        elif asset_id in declared_roles:
            continue  # a role with no safe fallback: identified, not invokable
        elif asset_id in vocabulary:
            # A registry role with no mechanism -- a privacy policy, a subtitle file -- is
            # identified and simply not invoked.
            if mechanism := _registry_mechanism(asset_id):
                mechanisms[asset_id] = mechanism
        else:
            unidentified.append(asset_id)
    return mechanisms, unidentified


def _registry_mechanism(asset_id: str) -> str | None:
    """The mechanism the vocabulary registry's role for *asset_id* maps to, else ``None``.

    The registry names the slot's role and the pin's own role table says what may be
    invoked from it -- the third rung of :func:`url_asset_identification`.
    """
    group = _asset_group_vocabulary().get(asset_id)
    return None if group is None else _SLOT_ROLE_TO_MECHANISM.get(_GROUP_ID_ROLE_OVERRIDES.get(group, group))


def declared_slot_mechanism(asset_id: str, requirements: Any) -> str | None:
    """How a receiver invokes the URL a declared url slot holds, from the declaration alone.

    The format-side rungs of :func:`url_asset_identification`, asked of a SLOT rather than a
    manifest asset: the slot's ``url-asset-requirements.role`` when it declares one, else
    its ``asset_group_id`` resolved through the vocabulary registry (a canonical slot
    carries no ``role``). A declared role with no safe mechanism stops here and answers
    ``None``, as it does for an asset. ``"clickthrough"`` is the click destination, which
    is how a missing required destination is told apart from any other missing slot.
    """
    role = str(_requirement_field(requirements, "role") or "")
    return _SLOT_ROLE_TO_MECHANISM.get(role) if role else _registry_mechanism(asset_id)


def unidentified_url_asset_reason(
    creative_data: dict[str, Any], format_spec: Any | None, creative_id: str
) -> str | None:
    """One sentence NAMING a url asset nothing identified, or ``None`` when all are.

    A NAMER, not a refusal, and the difference is the governing spec version.
    ``core/assets/url-asset.json`` at 3.2.3 ends "...receivers MUST NOT silently pick a
    mechanism." At 3.1.1 it continued "; they SHOULD reject the manifest", and that clause
    was REMOVED. A version that drops a remedy is declining to require it, so the
    prohibition on GUESSING stands -- :func:`url_asset_identification` honours it by
    returning the asset as unidentified rather than inventing a mechanism -- while the
    remedy is the seller's to choose. Refusing the buy would be stricter than the spec on a
    buyer-facing path, and the costs are not symmetric: reject the manifest and the buyer
    loses the buy, return no clickthrough and they lose a clickthrough.

    What the seller owes is that nobody loses a clickthrough SILENTLY, which is why this
    returns a sentence rather than nothing. It names the asset, what signal is missing, and
    for a historically-honoured id the fact that it is no longer acted on -- a reader told
    only "no clickthrough" cannot act.

    Its callers currently LOG it. That is a gap and it is filed: the advisory lane
    (``docs/design/error-architecture.md``) rides a response's declared ``errors[]`` and
    ``CreateMediaBuySuccess`` declares none, while its ``warnings[]`` array -- which IS the
    right carrier by its own description -- takes a ``code`` from a closed three-member
    enum, none of whose members means this.
    """
    _mechanisms, unidentified = url_asset_identification(creative_data, format_spec)
    if not unidentified:
        return None
    deprecated = sorted(set(unidentified) & _DEPRECATED_URL_ASSET_IDS)
    detail = (
        f"{', '.join(repr(a) for a in sorted(unidentified))} "
        f"carr{'ies' if len(unidentified) == 1 else 'y'} a URL this seller cannot invoke: "
        f"no 'url_type' on the asset, no role declared by the format, and "
        f"{'an' if len(unidentified) == 1 else ''} asset id the canonical vocabulary does not define"
    )
    remedy = (
        "Declare 'url_type' on the asset, or key it by the canonical asset_group_id for its "
        "role (the click destination is 'landing_page_url')."
    )
    if deprecated:
        remedy = (
            f"{', '.join(repr(a) for a in deprecated)} was honoured as the click destination by "
            f"earlier versions of this seller and is in no canonical vocabulary entry, so it is no "
            f"longer acted on. {remedy}"
        )
    return f"Reference creative {creative_id}: asset {detail}. {remedy}"


def _ids_invoked_as(mechanisms: dict[str, str], mechanism: str) -> list[str]:
    """The asset ids a mechanism map assigns to *mechanism*, sorted.

    Sorted so two assets carrying the same mechanism resolve the same way on every run; a
    dict built from a spec's slot order or a manifest's key order would otherwise make which
    one wins depend on how the document was written.
    """
    return sorted(asset_id for asset_id, found in mechanisms.items() if found == mechanism)


def _coerce_dimension(raw: Any, axis: str) -> int | None:
    """One pinned-int read of a creative's declared dimension, or None with the reason said.

    ONE spelling of the coercion, which used to be four: the format-spec branch logged a
    warning on a value it could not read and the allowlist branch swallowed it with a bare
    ``pass``, so the same malformed asset was loud or silent depending on which branch found
    it. A quiet failure here answers the buyer "missing dimensions" and never says that a
    dimension WAS there and was unreadable.
    """
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid %s in creative assets: %r (type=%s)", axis, raw, type(raw).__name__)
        return None


def _media_asset_candidates(
    format_spec: Any | None, kind: str | None, declarations: "Sequence[Any]" = ()
) -> list[tuple[str, bool]]:
    """Which asset ids could hold this creative's media, in priority order.

    Each entry is ``(asset_id, carries_dimensions)``. THREE sources, and they are tried in
    this order because they differ in how much they actually know:

    1. A resolved LEGACY format spec, which names its own ``asset_id`` values and their
       types. Authoritative for the creative it was resolved for.
    2. The PRODUCT DECLARATIONS this creative's format actually resolved to, when the caller
       knows them. ``core/product-format-declaration.json`` lets a product REPLACE the
       kind's slot list outright -- its own ``audio_hosted`` example declares ``script`` and
       ``offering_ref``, neither of which any kind declares -- so a product that renames or
       adds a media slot names an id source 3 cannot produce.
    3. The CANONICAL declaration's slots for the kind the creative carries, which
       ``core/product-format-declaration.json`` states and each canonical schema defaults.
       The ONLY source for a creative identified by its kind when no product declaration
       reached here: it asserts no legacy reference, so no spec resolves and there is
       nothing to ask. Kept BESIDE source 2 rather than replaced by it, because a creative
       is graded against one product per package and the gate's question is only "where
       could its media be".
    4. :data:`MEDIA_ASSET_FALLBACK_IDS`, a guess by naming convention. Last, because it is
       a guess -- and before this existed it was the only thing left for a canonical
       creative, which meant its assets were readable by coincidence of naming.

    Sorted within each source so two candidate ids in one creative's assets resolve the same
    way on every run; the sets above are unordered, and which one won used to depend on hash
    iteration.
    """
    # Lazy import to avoid circular dependencies
    from src.core.format_satisfaction import canonical_slot_asset_ids, declaration_slot_asset_ids

    candidates: list[tuple[str, bool]] = []

    for asset_id, asset_type, _requirements in declared_slots(format_spec):
        if asset_type in MEDIA_ASSET_TYPES:
            candidates.append((asset_id, asset_type in SIZED_ASSET_TYPES))

    for declaration in declarations:
        sized = declaration_slot_asset_ids(declaration, SIZED_ASSET_TYPES)
        candidates.extend(
            (asset_id, asset_id in sized)
            for asset_id in sorted(declaration_slot_asset_ids(declaration, MEDIA_ASSET_TYPES))
        )

    if kind:
        sized = canonical_slot_asset_ids(kind, SIZED_ASSET_TYPES)
        candidates.extend(
            (asset_id, asset_id in sized) for asset_id in sorted(canonical_slot_asset_ids(kind, MEDIA_ASSET_TYPES))
        )

    # Dimensions assumed present: a convention name says nothing about its asset's type.
    candidates.extend((asset_id, True) for asset_id in sorted(MEDIA_ASSET_FALLBACK_IDS))
    return candidates


def extract_media_url_and_dimensions(
    creative_data: dict[str, Any],
    format_spec: Any | None,
    kind: str | None = None,
    declarations: "Sequence[Any]" = (),
) -> tuple[str | None, int | None, int | None]:
    """Extract media URL and dimensions from creative data.

    All production creatives now use AdCP v2.4+ format with data.assets[asset_id]
    containing typed asset objects per the creative format specification.

    Which asset ids are looked at, and in what order, is :func:`_media_asset_candidates`.
    Whatever those name, the first candidate present in the creative's assets fills each of
    the three fields, and the root-level fallback below fills whatever is still missing.

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets, when a legacy reference resolved one
        kind: The creative's canonical ``format_kind``, whose declaration names its slots.
            ``None`` for a creative carrying a legacy reference nothing projects -- the one
            row for which the fallback allowlist is still the only path.

    Returns:
        Tuple of (url, width, height). Values are None if not found.

    Note:
        - Media URL extracted from asset types: image, video, audio
        - Dimensions extracted from asset types: image, video
        - Type validation: width/height must be int or coercible to int
        - Uses adcp.utils.get_individual_assets() for backward compatibility with assets_required
    """
    url, width, height, _sized = _read_media_assets(creative_data, format_spec, kind, declarations)
    return url, width, height


def media_asset_requires_dimensions(
    creative_data: dict[str, Any],
    format_spec: Any | None,
    kind: str | None = None,
    declarations: "Sequence[Any]" = (),
) -> bool:
    """Does this creative owe the ad server a width and a height?

    The answer is a property of the slot its media URL came FROM, read off the declaration:
    an ``image`` or ``video`` slot carries dimensions and an ``audio`` one does not
    (:data:`SIZED_ASSET_TYPES`, and ``formats/canonical/audio_hosted.json`` declares
    ``audio_main`` as ``asset_type: "audio"``). So a canonical ``audio_hosted``,
    ``audio_vast`` or ``audio_daast`` creative resolves a URL and correctly no dimensions,
    and demanding them of it refuses a buy whose manifest is perfectly authored.

    NOT a property of the KIND. ``canonical_slot_asset_ids("audio_hosted",
    SIZED_ASSET_TYPES)`` answers ``{"companion_image"}`` -- that kind DOES declare a sized
    slot -- so a kind-level predicate would still demand dimensions of an audio-only
    manifest. It is the slot that was actually read that decides.

    True when nothing resolved, so a creative with no media asset at all keeps failing on
    the caller's URL check rather than slipping past it.

    A SECOND CALL of the same pure read, deliberately. Folding this into
    :func:`extract_media_url_and_dimensions`'s return would be tidier and would break six
    test patch sites that stub that name with a 3-tuple -- three of them in
    tests/integration/test_create_media_buy_behavioral.py -- and a production path that
    stopped calling the stubbed name would leave those tests silently unmocked. The shared
    implementation is :func:`_read_media_assets`; there is one rule, not two.
    """
    _url, _width, _height, sized = _read_media_assets(creative_data, format_spec, kind, declarations)
    return sized


def _read_media_assets(
    creative_data: dict[str, Any], format_spec: Any | None, kind: str | None, declarations: "Sequence[Any]"
) -> tuple[str | None, int | None, int | None, bool]:
    """``(url, width, height, the url's slot carries dimensions)`` — the one media read.

    Dimensions may come from a DIFFERENT slot than the URL: an ``audio_hosted`` manifest
    carrying both ``audio_main`` and ``companion_image`` takes the URL from the first and
    the dimensions from the second. The fourth value therefore reports the slot that
    supplied the URL, which is what decides whether dimensions are OWED.
    """
    url = None
    width = None
    height = None
    url_slot_is_sized = True

    assets = _manifest_assets(creative_data)
    if assets:
        for asset_id, carries_dimensions in _media_asset_candidates(format_spec, kind, declarations):
            asset_obj = assets.get(asset_id)
            if not isinstance(asset_obj, dict):
                continue
            if not url and asset_obj.get("url"):
                url = asset_obj["url"]
                url_slot_is_sized = carries_dimensions
                logger.debug("Extracted media URL from creative asset %r", asset_id)
            if carries_dimensions:
                width = width or _coerce_dimension(asset_obj.get("width"), "width")
                height = height or _coerce_dimension(asset_obj.get("height"), "height")
            if url and width and height:
                break

    # Last: root-level fields, for simple creatives that carry no assets structure at all.
    # A root-level url names no slot, so nothing says what type it is and dimensions stay
    # owed -- the same reading MEDIA_ASSET_FALLBACK_IDS gets.
    if not url:
        root_url = creative_data.get("url")
        if root_url:
            url = root_url
            url_slot_is_sized = True
            logger.debug("Extracted media URL from root-level 'url' field (legacy fallback)")

    width = width or _coerce_dimension(creative_data.get("width"), "width")
    height = height or _coerce_dimension(creative_data.get("height"), "height")

    return url, width, height, url_slot_is_sized


def extract_click_url(
    creative_data: dict[str, Any],
    format_spec: Any | None,
    apply_macro_substitution: bool = True,
) -> str | None:
    """Extract click-through URL from creative data.

    Which asset is the click destination is :func:`url_asset_identification`, the pin's own
    ladder: the asset's ``url_type``, else the format-declared role, else the
    ``asset_group_id`` the canonical vocabulary registry names as the destination. An asset
    no rung speaks for is NOT guessed at -- ``unidentified`` is the caller's refusal, not
    this reader's -- which is why there is no hand-maintained list of names here any more.

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets
        apply_macro_substitution: If True, apply AdCP-to-GAM macro substitution

    Returns:
        Click-through URL string (optionally with macros substituted), or None if not found.
    """
    click_url = None
    assets = _manifest_assets(creative_data)
    mechanisms, _unidentified = url_asset_identification(creative_data, format_spec)

    for asset_id in _ids_invoked_as(mechanisms, "clickthrough"):
        asset_obj = assets.get(asset_id)
        if isinstance(asset_obj, dict) and asset_obj.get("url"):
            click_url = asset_obj["url"]
            logger.debug("Extracted click URL from asset %r, identified as the click destination", asset_id)
            break

    # Apply macro substitution if requested
    if click_url and apply_macro_substitution:
        try:
            from src.adapters.gam.utils.macros import substitute_macros

            click_url = substitute_macros(click_url)
        except ImportError:
            # GAM adapter not available, skip macro substitution
            pass

    return click_url


def extract_impression_tracker_url(creative_data: dict[str, Any], format_spec: Any | None) -> str | None:
    """Extract impression tracker URL from creative data.

    Which asset is a tracker pixel is :func:`url_asset_identification`, the same ladder the
    click reader asks -- the asset's ``url_type``, else the format-declared role, else
    the ``asset_group_id`` resolved through the vocabulary registry, whose canonical
    ``impression_tracker`` entry is the one the old four-name guess list was approximating.
    This reader held three branches that were the click reader's three with different
    literals; they are one ladder now.

    A ``viewability_tracker`` role resolves to ``tracker_script`` and so is NOT returned
    here. The pin's parenthetical is the reason: "OMID and equivalent verification SDKs
    require a <script> tag -- firing them as a pixel produces no measurement".

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets (or deprecated assets_required)

    Returns:
        Impression tracker URL string or None if not found.
    """
    assets = _manifest_assets(creative_data)
    mechanisms, _unidentified = url_asset_identification(creative_data, format_spec)

    for asset_id in _ids_invoked_as(mechanisms, "tracker_pixel"):
        asset_obj = assets.get(asset_id)
        if isinstance(asset_obj, dict) and asset_obj.get("url"):
            logger.debug("Extracted impression tracker from asset %r, identified as a tracker pixel", asset_id)
            return str(asset_obj["url"])

    return None


def asset_value_attr(asset: Any, *attr_names: str) -> str | None:
    """Read a named attribute off one asset-slot value, whatever shape it arrived in.

    The library wraps a repeatable slot in an ``Assets`` RootModel holding a
    ``list[AssetVariant]``, each variant itself a RootModel proxying the concrete typed
    asset; a single slot is the concrete asset; a stored row may still hold a plain dict.
    The first truthy value among *attr_names* wins (for example ``"content", "text"``).
    Shared by the sync pipeline and the creative-engine adapters, so it lives here rather
    than inside a tool module.
    """
    if isinstance(asset, dict):
        for attr in attr_names:
            val = asset.get(attr)
            if val:
                return str(val)
        return None

    items = getattr(asset, "root", None)
    if isinstance(items, list) and items:
        first = items[0]
        inner = getattr(first, "root", first)
        for attr in attr_names:
            val = getattr(inner, attr, None) or getattr(first, attr, None)
            if val:
                return str(val)
        return None

    for attr in attr_names:
        val = getattr(asset, attr, None)
        if val:
            return str(val)
    return None
