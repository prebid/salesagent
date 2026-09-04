"""Format resolution with product overrides and dynamic creative agent discovery.

Provides layered format lookup:
1. Product-level overrides (from product.implementation_config.format_overrides)
2. Dynamic format discovery from creative agents (via CreativeAgentRegistry)

Note: Tenant custom formats (creative_formats table) were removed in favor of
creative agent-based format discovery per AdCP v2.4.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from adcp.canonical_formats import format_is_supported, formats_are_equivalent
from adcp.types import FormatId as LibraryFormatId

from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPError, AdCPFormatNotFoundError, AdCPNotFoundError
from src.core.logging_config import log_safe
from src.core.schemas import Format
from src.core.security.outbound_http import UrlProvenance
from src.core.validation_helpers import run_async_in_sync_context

# What callers may hand the identity helpers: a structured reference, a bare
# dict off the wire, or a legacy string id.
FormatRef = str | LibraryFormatId | Mapping[str, Any]


def _as_ref(value: FormatRef) -> Any:
    """Normalize a reference to the shape the SDK predicates accept.

    A Pydantic model is dumped to its wire dict: the SDK reads (agent_url, id)
    off a mapping, and dumping is also what strips the LOCAL SUBCLASS identity
    that made ``==`` fail in the first place — the value survives, the class
    does not, which is the whole point.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value


logger = logging.getLogger(__name__)


# ── Format identity and kind: asked here, never re-decided at a call site ─────
#
# Two format references name the same format when the SDK's own equivalence
# predicate says so. Call sites used to compare with ``==`` on the model, which
# is Pydantic's STRUCTURAL equality — same fields AND same class. That works
# only while every producer happens to build the same concrete class, an
# invariant nothing declared and one transport quietly broke: the local
# ``FormatId`` subclass (schemas/_base.py, four convenience methods, zero extra
# fields) never compares equal to the library value every other producer makes,
# so a lookup keyed on ``==`` missed on A2A and the whole agent-dial arm was
# skipped in silence.
#
# Identity is (agent_url, id) per the pinned core/format-id.json, and the SDK
# already implements it — including the canonicalization. Nothing here
# re-implements that; these are thin, named delegations so there is ONE answer
# to "same format?" instead of one per call site.


def format_ref_id(ref: FormatRef) -> str | None:
    """The bare ``id`` of a format reference, whatever shape it arrives in.

    A ``format_id`` reaches this module as a structured model, as a wire dict,
    or as a legacy bare string, depending on which producer built it — so
    reaching for ``.id`` works only until it does not. Asking here keeps that
    fact in ONE place instead of at each call site, which is the same mistake
    the ``==`` comparisons made about class identity.
    """
    if isinstance(ref, str):
        return ref
    if isinstance(ref, Mapping):
        value = ref.get("id")
        return str(value) if value is not None else None
    value = getattr(ref, "id", None)
    return str(value) if value is not None else None


def same_format(left: FormatRef, right: FormatRef) -> bool:
    """Whether two format references name the same format.

    Delegates to ``adcp.canonical_formats.formats_are_equivalent`` — the spec
    authors' own rule, which canonicalizes both agent_urls before comparing and
    treats an omitted parameter as a wildcard. Deliberately NOT ``==``: that
    compares Python classes as well as values.
    """
    return formats_are_equivalent(_as_ref(left), _as_ref(right))


def format_accepted_by(requested: FormatRef, supported: FormatRef) -> bool:
    """Whether *supported* satisfies a request for *requested*.

    Distinct from :func:`same_format`: support is directional (a parameterized
    supported format can satisfy a narrower request), which is why the SDK gives
    it its own predicate rather than reusing equivalence.
    """
    return format_is_supported(_as_ref(requested), _as_ref(supported))


def find_format(ref: FormatRef, formats: Sequence[Format]) -> Format | None:
    """The catalog Format naming the same format as *ref*, or None.

    PURE: no HTTP and no DB, so it is callable from inside a savepoint where
    ``get_format`` is not — the creative-sync paths pre-fetch their catalog
    outside the transaction for exactly that reason and then need to select from
    it without dialling anything.
    """
    return next((fmt for fmt in formats if same_format(fmt.format_id, ref)), None)


def is_agent_backed(fmt: Format) -> bool:
    """Whether this format is served by a creative agent.

    Asks the format, rather than probing it: ``fmt.agent_url`` is a declared
    property on the local Format (schemas/_base.py), not an attribute that may
    or may not be there. Compose with — do not duplicate —
    :func:`is_dialled_agent_url`: an adapter pseudo-URL is agent-backed but
    never dialled.
    """
    return fmt.agent_url is not None


def is_generative(fmt: Format) -> bool:
    """Whether this format is built by the agent rather than supplied whole.

    ``output_format_ids`` IS a declared field on ``adcp.types.Format``, so the
    ``getattr(fmt, "output_format_ids", None)`` guard call sites used protected
    against nothing while the sibling read of ``agent_url`` — the one that could
    actually surprise — went undefended.
    """
    return bool(fmt.output_format_ids)


def is_dialled_agent_url(agent_url: str) -> bool:
    """Whether *agent_url* names an endpoint we will actually dial over HTTP.

    False for an adapter-provided pseudo-URL like ``broadstreet://<tenant_id>``
    (advertised by ``creative_formats.py`` and the Broadstreet adapter): those
    formats are served by the adapter in-process, so there is no request to
    make, no address to judge, and an egress gate applied to one would refuse
    a format the SELLER published to the buyer.

    Shared by every format-fetch call site (``creatives/_validation.py``'s
    ingest gate, ``media_buy_create.py``'s pre-adapter validation and asset
    build) so the adapter-format exemption is decided once, here, rather than
    re-derived per call site.
    """
    return agent_url.startswith(("http://", "https://"))


def fetch_format_spec(agent_url: str, format_id: str, *, provenance: UrlProvenance | None = None) -> Format | None:
    """Fetch one format spec from the creative-agent registry (sync bridge).

    THE single fetch path for format specs — create_media_buy,
    sync_creatives validation, and get_format all route through here so typed
    transient errors behave identically on every tool:

    - Typed ``AdCPError`` from the registry (429 -> AdCPRateLimitError,
      5xx/timeout/connect -> AdCPServiceUnavailableError) PROPAGATES: it carries
      its own recovery semantics, and swallowing it into ``None`` degrades a
      transient agent failure to a terminal "unknown format" rejection.
    - ``None`` means the agent genuinely doesn't expose the format (unknown-
      format semantics — the caller decides how to reject or fall back).
    - Untyped exceptions are logged and become ``None``: the registry types all
      its network errors, so an untyped one here is a programming surprise, not
      a transport signal.

    ``provenance`` passes straight through to the registry — a
    :class:`CounterpartyUrl` when a buyer's request document supplied
    ``agent_url``, optionally naming the request path; ``None`` for an
    operator-registered agent.
    """
    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()
    try:
        return run_async_in_sync_context(registry.get_format(agent_url, format_id, provenance=provenance))
    except AdCPError:
        raise
    except Exception as e:
        logger.warning(
            "Could not fetch format %s from %s: %s",
            log_safe(format_id),
            log_safe(agent_url),
            log_safe(e),
        )
        return None


def get_format(
    format_id: str,
    agent_url: str | None = None,
    tenant_id: str | None = None,
    product_id: str | None = None,
    *,
    provenance: UrlProvenance | None = None,
) -> Format:
    """Resolve format with priority: product override → creative agent discovery.

    Args:
        format_id: Format identifier (e.g., "display_300x250_image")
        agent_url: Optional creative agent URL (defaults to AdCP standard agent)
        tenant_id: Optional tenant ID for agent lookup
        product_id: Optional product ID for product-level overrides
        provenance: Whose URL ``agent_url`` is, forwarded to
            :func:`fetch_format_spec` unchanged. A caller re-dialling a
            buyer-supplied URL it already fetched once (e.g. a fallback path)
            MUST pass the same ``CounterpartyUrl`` it used the first time —
            omitting it silently reclassifies the same URL as operator
            configuration and routes it off the egress seam entirely.

    Returns:
        Format object with all configuration

    Raises:
        AdCPFormatNotFoundError: If format_id not found in any source
    """
    # Check product override first
    if product_id and tenant_id:
        override = _get_product_format_override(tenant_id, product_id, format_id, agent_url=agent_url)
        if override:
            return override

    # Get from creative agent registry
    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()

    # If agent_url provided, get format directly from that agent
    # Coerce to str: FormatId.agent_url is Pydantic AnyUrl (not a str subclass)
    if agent_url:
        fmt = fetch_format_spec(str(agent_url), format_id, provenance=provenance)
        if fmt:
            return fmt
    else:
        # Search all agents for this format. The caller supplied NO agent scope,
        # so the match is on the id alone — matching on full (agent_url, id)
        # identity here would silently narrow "any agent" to "the reference
        # agent", because upgrading a bare string defaults agent_url to the
        # canonical creative agent.
        #
        # This branch was dead before: it compared ``fmt.format_id`` (a FormatId
        # MODEL) against ``format_id`` (a str), which is never equal — so the
        # one canonical resolver's fallback always fell through to the raise.
        all_formats = run_async_in_sync_context(registry.list_all_formats(tenant_id=tenant_id))
        for fmt in all_formats:
            if format_ref_id(fmt.format_id) == format_id:
                return fmt

    # Uniform response (AdCP 3.1.1): class default message; no buyer parameter
    # name here so field stays unset (do not bake format_id). Wire → REFERENCE_NOT_FOUND.
    # Spec constrains the buyer-facing message only — keep identifiers in server logs
    # (log_safe strips CR/LF so buyer-supplied ids cannot forge log lines — CodeQL).
    logger.warning(
        "FORMAT_NOT_FOUND: format_id=%s agent_url=%s tenant_id=%s product_id=%s",
        log_safe(format_id),
        log_safe(agent_url),
        log_safe(tenant_id),
        log_safe(product_id),
    )
    raise AdCPFormatNotFoundError()


def _get_product_format_override(
    tenant_id: str, product_id: str, format_id: str, agent_url: str | None = None
) -> Format | None:
    """Get product-level format override from product.implementation_config.

    Product can override any format's platform_config. Example:
    {
        "format_overrides": {
            "display_300x250": {
                "platform_config": {
                    "gam": {
                        "creative_placeholder": {
                            "width": 1,
                            "height": 1,
                            "creative_template_id": 12345678
                        }
                    }
                }
            }
        }
    }

    Args:
        tenant_id: Tenant identifier
        product_id: Product identifier
        format_id: Format to look up
        agent_url: Optional creative agent URL (needed to fetch base format)

    Returns:
        Format with overridden config, or None if no override exists
    """
    from sqlalchemy import text

    with get_db_session() as session:
        result = session.execute(
            text(
                "SELECT implementation_config FROM products WHERE tenant_id = :tenant_id AND product_id = :product_id"
            ),
            {"tenant_id": tenant_id, "product_id": product_id},
        )
        row = result.fetchone()
        if not row or not row[0]:
            return None

        # Parse implementation_config JSON
        impl_config = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        format_overrides = impl_config.get("format_overrides", {})

        if format_id not in format_overrides:
            return None

        # Get base format from creative agent registry (WITHOUT product_id to avoid recursion)
        from src.core.creative_agent_registry import get_creative_agent_registry

        registry = get_creative_agent_registry()

        try:
            # format_id is a string key in format_overrides dict
            # Pass agent_url to find the base format from the correct creative agent
            base_format = get_format(format_id, agent_url=agent_url, tenant_id=tenant_id, product_id=None)
        except (AdCPNotFoundError, Exception):
            # Base format not found - cannot apply override
            return None

        # Apply override to base format
        override_config = format_overrides[format_id]

        # Merge platform_config override
        if "platform_config" in override_config:
            # Access platform_config directly from the model, not via model_dump(),
            # because platform_config has exclude=True and model_dump() drops it.
            base_platform_config = base_format.platform_config or {}
            override_platform_config = override_config["platform_config"]

            # Deep merge platform configs (override takes precedence)
            merged_platform_config = {**base_platform_config}
            for platform, config in override_platform_config.items():
                if platform in merged_platform_config:
                    # Merge platform-specific configs
                    merged_platform_config[platform] = {
                        **merged_platform_config[platform],
                        **config,
                    }
                else:
                    merged_platform_config[platform] = config

            return base_format.model_copy(update={"platform_config": merged_platform_config})

        return base_format


def list_available_formats(
    tenant_id: str | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    is_responsive: bool | None = None,
    asset_types: list[str] | None = None,
    name_search: str | None = None,
) -> list[Format]:
    """List all formats available to a tenant from all registered creative agents.

    Args:
        tenant_id: Optional tenant ID to include tenant-specific agents
        max_width: Maximum width in pixels (inclusive)
        max_height: Maximum height in pixels (inclusive)
        min_width: Minimum width in pixels (inclusive)
        min_height: Minimum height in pixels (inclusive)
        is_responsive: Filter for responsive formats
        asset_types: Filter by asset types
        name_search: Search by name

    Returns:
        List of all available Format objects from all registered agents
    """
    import logging

    logger = logging.getLogger(__name__)

    from src.core.creative_agent_registry import get_creative_agent_registry

    logger.info(f"[list_available_formats] Starting format fetch for tenant_id={tenant_id}")

    try:
        registry = get_creative_agent_registry()
    except Exception as e:
        logger.error(f"[list_available_formats] Failed to get creative agent registry: {e}", exc_info=True)
        return []

    # Get formats from all agents (default + tenant-specific)
    try:
        formats = run_async_in_sync_context(
            registry.list_all_formats(
                tenant_id=tenant_id,
                max_width=max_width,
                max_height=max_height,
                min_width=min_width,
                min_height=min_height,
                is_responsive=is_responsive,
                asset_types=asset_types,
                name_search=name_search,
            )
        )
    except Exception as e:
        logger.error(f"[list_available_formats] Error fetching formats: {e}", exc_info=True)
        return []

    logger.info(f"[list_available_formats] Successfully fetched {len(formats)} formats")
    return formats
