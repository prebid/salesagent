"""Helper functions for working with generated schemas.

This module provides convenience functions for constructing complex generated schemas
without losing type safety. Unlike adapters (which wrap schemas in dict[str, Any]),
these helpers work directly with the generated Pydantic models.

Philosophy:
- Generated schemas are the source of truth (always in sync with AdCP spec)
- Helpers make construction easier without sacrificing type safety
- Custom logic (validators, conversions) lives here, not in wrapper classes
"""

from typing import Any
from urllib.parse import urlparse

# FIXME(#1388): GetProductsResponse, Product have local subclasses; import from src.core.schemas.
from adcp import CreativeFilters, GetProductsResponse, Product

# FIXME(#1388): ProductFilters has a local subclass; import from src.core.schemas.
from adcp.types import (
    AccountReference,
    BrandReference,
    ContextObject,
    ProductFilters,
    PropertyListReference,
    PushNotificationConfig,
    ReportingWebhook,
)
from pydantic import BaseModel, ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.schemas.product import GetProductsRequest
from src.core.validation_helpers import adcp_validation_boundary


def _coerce_wire_object[ModelT: BaseModel](
    value: Any,
    model_cls: type[ModelT],
    context: str,
    *,
    strict: bool = False,
    field: str | None = None,
    field_prefix: str | None = None,
    suggestion: str | None = None,
) -> ModelT | None:
    """Shared dict → typed-model coercion with the boundary BUILT IN.

    Single home for the ``to_*`` helpers' isinstance ladder. The internal
    ``adcp_validation_boundary`` means a malformed wire dict rejects as a
    typed ``AdCPValidationError`` (message + field + top-level suggestion)
    from EVERY call site — callers cannot forget the boundary
    (#1417; mirrors ``coerce_creative_filters``).

    ``strict`` decides what an unexpected NON-dict type means:

    * ``False`` (default) — return ``None``, preserving the helpers'
      long-standing degrade-to-missing fallback. Required for ``context``,
      whose schema calls it opaque correlation data that is "never parsed by
      AdCP agents"; hard-failing a non-dict ``context`` would contradict that.
    * ``True`` — route the value through ``model_validate`` anyway so Pydantic
      rejects it and the boundary raises ``AdCPValidationError``. Used where a
      silent ``None`` would be a fail-OPEN: a dropped ``account`` leaves
      ``identity.sandbox`` ``False`` and dispatches to the LIVE adapter.

    ``field``/``suggestion`` pin the buyer-visible request field and hint. Without
    them both are derived from the pydantic location, which for these coercions is
    the *model* name (``AccountReference1``) — a name that appears in no buyer
    request. Passing them makes the two ways of malforming one value (non-dict and
    malformed dict) report an identical ``field`` and ``suggestion``, and share the
    ``Invalid <context>:`` message prefix; the message text after that prefix still
    carries the differing pydantic detail.
    """
    if value is None or isinstance(value, model_cls):
        return value
    if isinstance(value, dict) or strict:
        with adcp_validation_boundary(context=context, field=field, field_prefix=field_prefix, suggestion=suggestion):
            # model_validate handles plain models and RootModels alike
            # (AccountReference is a RootModel — field-unpacking would break it).
            return model_cls.model_validate(value)
    return None  # Fallback for unexpected types


def to_context_object(context: dict[str, Any] | ContextObject | None) -> ContextObject | None:
    """Convert dict context to ContextObject for adcp 2.12.0+ compatibility."""
    return _coerce_wire_object(context, ContextObject, "context value")


def to_reporting_webhook(webhook: dict[str, Any] | ReportingWebhook | None) -> ReportingWebhook | None:
    """Convert dict to ReportingWebhook for adcp type compatibility."""
    return _coerce_wire_object(webhook, ReportingWebhook, "reporting_webhook value")


def to_push_notification_config(
    config: dict[str, Any] | PushNotificationConfig | None,
    *,
    field_prefix: str = "push_notification_config",
) -> PushNotificationConfig | None:
    """Convert dict to PushNotificationConfig for adcp type compatibility.

    ``field_prefix`` defaults HERE rather than at the call sites: five callers
    each remembering the same string literal is the remembered-call shape this
    epic exists to delete, and the sixth caller is where the divergence comes
    back. A refusal from this funnel therefore names
    ``push_notification_config.authentication.credentials`` — the path into the
    document the buyer actually sent — which is what FastMCP already emits (it
    validates the whole argument model, so its pydantic loc carries the parameter
    name) and what the registration gate raises. This converges REST and A2A onto
    the spelling MCP and the ingest gate already use; it is not a third one.

    Scope note: the broader prefix inconsistency across every field this
    validator reports is gh-#1895 and stays open — this narrows exactly one
    helper's one field.
    """
    return _coerce_wire_object(
        config,
        PushNotificationConfig,
        "push_notification_config value",
        field_prefix=field_prefix,
    )


def require_push_notification_config(
    config: dict[str, Any] | PushNotificationConfig,
    *,
    field_prefix: str = "push_notification_config",
) -> PushNotificationConfig:
    """:func:`to_push_notification_config` for a caller that HAS a config.

    Same funnel, same refusals, same field paths -- the only difference is that
    ``None`` is not in the domain, so the result is not ``| None`` and a caller
    has nothing to narrow.

    The optional version exists because some callers legitimately hold "maybe a
    config"; the trouble was that callers who did NOT then had to prove the
    absence away, and two of them did it with a bare ``assert``. Under
    ``python -O`` an assert is deleted, so a function annotated as never
    returning ``None`` returned it. Stating the requirement in the SIGNATURE is
    what removes the narrowing rather than making it survive an interpreter
    flag.
    """
    coerced = to_push_notification_config(config, field_prefix=field_prefix)
    if coerced is None:
        # Unreachable via the annotated domain; a runtime guard rather than an
        # assert so it cannot be optimised away, and so a caller that passed
        # ``None`` through an ``Any`` gets a named failure instead of one
        # deferred to whatever first dereferences the result.
        raise ValueError(f"{field_prefix} is required but resolved to None")
    return coerced


def is_url_shorthand(value: str) -> bool:
    """Return True when a string looks like a URL (scheme or protocol-relative)."""
    return "://" in value or value.startswith("//")


def brand_shorthand_to_domain(value: str) -> str:
    """Normalize AdCP v3 brand string shorthand to a domain hostname.

    Storyboard runners may send ``https://test.example``; ``BrandReference.domain``
    expects a hostname (no scheme/path) per the adcp library pattern.

    Returns empty string when a URL-shaped value cannot be parsed into a hostname
    (malformed IPv6, etc.) so legacy ``brand_manifest`` middleware can silently
    strip the field. Callers on the explicit ``brand`` path must use
    ``to_brand_reference`` / ``_coerce_domain_or_raise`` instead — those raise
    ``AdCPValidationError(field="brand")`` rather than dropping the brand.
    """
    value = value.strip()
    if not value:
        return value
    if is_url_shorthand(value):
        try:
            parsed = urlparse(value if "://" in value else f"https:{value}")
        except ValueError:
            return ""
        if parsed.hostname:
            return parsed.hostname.lower()
        return ""
    return value.lower()


def _coerce_domain_or_raise(raw: str) -> str:
    """Normalize brand shorthand and validate against BrandReference.domain pattern.

    Used for explicit ``brand`` on tool boundaries — malformed input must surface
    as ``VALIDATION_ERROR / field="brand"``, not be coerced to missing brand
    (which would mis-route ``require_brand`` policy to an authorization error).

    Raises:
        AdCPValidationError: when the value cannot be normalized to a valid hostname
            (empty parse, path/underscore/IDN/pattern mismatch). Always tagged
            ``field="brand"`` so wire envelopes name the request field.
    """
    domain = brand_shorthand_to_domain(raw)
    if not domain:
        raise AdCPValidationError(
            f"Invalid brand: could not derive domain from brand shorthand {raw!r}",
            field="brand",
        )
    try:
        BrandReference(domain=domain)
    except ValidationError as e:
        raise AdCPValidationError(
            f"Invalid brand: domain {domain!r} is not a valid hostname",
            field="brand",
        ) from e
    return domain


def to_brand_reference(brand: dict[str, Any] | BrandReference | str | None) -> BrandReference | None:
    """Convert dict/string brand to BrandReference for adcp 3.6.0 compatibility.

    String and dict ``domain`` values share one normalize-then-validate funnel so
    ``"ACME.COM"`` / ``{"domain":"ACME.COM"}`` / URL-in-domain are equivalent.

    Args:
        brand: Brand as dict, string domain shorthand, BrandReference, or None

    Returns:
        BrandReference or None

    Raises:
        AdCPValidationError: when an explicit brand cannot be coerced to a valid
            ``BrandReference`` (tagged ``field="brand"``).
    """
    if brand is None:
        return None
    if isinstance(brand, BrandReference):
        return brand
    # Raise-capable coercion routes through the internal boundary (like
    # ``coerce_creative_filters``/``_coerce_wire_object``) so a malformed brand
    # rejects as a typed AdCPValidationError with field + top-level suggestion
    # from every call site — no hand-rolled ValidationError translation (#1417).
    with adcp_validation_boundary(context="brand", field="brand"):
        if isinstance(brand, str):
            return BrandReference(domain=_coerce_domain_or_raise(brand))
        if isinstance(brand, dict):
            domain_raw = brand.get("domain")
            if not isinstance(domain_raw, str):
                raise AdCPValidationError(
                    "Invalid brand: domain is required",
                    field="brand",
                )
            allowed = BrandReference.model_fields.keys()
            ref_data = {key: value for key, value in brand.items() if key in allowed}
            ref_data["domain"] = _coerce_domain_or_raise(domain_raw)
            return BrandReference(**ref_data)
        raise AdCPValidationError(
            f"Invalid brand: expected dict, string, or BrandReference, got {type(brand).__name__}",
            field="brand",
        )


def to_account_reference(account: dict[str, Any] | AccountReference | None) -> AccountReference | None:
    """Convert dict to AccountReference for adcp compatibility.

    Strict: an unexpected non-dict ``account`` raises ``AdCPValidationError``
    instead of degrading to ``None``. The A2A skills read ``account`` straight
    off raw ``parameters`` with no model in front of them, so a coerced-away
    account would skip identity enrichment, leave ``identity.sandbox`` ``False``,
    and route a sandbox request to the LIVE adapter — a quiet failure on exactly
    the axis account isolation defends.

    Both rejection paths are tagged ``field="account"`` with a matching
    ``suggestion`` (like ``to_brand_reference``'s ``field="brand"``) so wire
    envelopes name the request field rather than the pydantic union-member model
    name. On the malformed-dict path that is a FIX, not merely a scope note: the
    previous behavior derived both channels from the pydantic location and told the
    buyer to correct ``AccountReference1.account_id`` — a field no buyer request
    contains.

    That fix covers those two DIRECTIVE channels only. The generated names still
    reach ``message`` (built by ``format_validation_error``) and
    ``details.validation_errors[].loc`` (built by ``build_validation_error_details``)
    — two different builders, both assembled inside ``adcp_validation_boundary``. So
    the reach is every caller of that boundary plus ``format_validation_error``'s
    three direct callers, NOT every boundary in the codebase:
    ``normalize_to_adcp_error``, the three-transport normalizer, does not call it.

    Provenance differs by path and is not one story. On the malformed-DICT path the
    unscrubbed ``message``/``details`` are pre-existing, tracked at #1996. The
    non-dict path is a NEW emission site introduced here: before this narrowing it
    returned ``None`` with no error at all, so there was nothing to leak from.
    """
    # Error-code note for anyone tempted to "fix" this: the strict non-dict path
    # deliberately emits the SAME code as the pre-existing malformed-dict path
    # (both go through ``adcp_validation_boundary`` → VALIDATION_ERROR).
    #
    # What that choice rests on, and only this: nothing upstream grades it. No 3.1.1
    # conformance-storyboard step sends a malformed account, and the pinned
    # ``error-code.json`` lists BOTH candidate codes with recovery=correctable — so
    # either one is enum-conformant and tells the buyer the same thing about what to
    # do next. That is a reason the choice is not urgent; it is not a reason it is
    # right.
    #
    # It is explicitly NOT justified by consistency, and this note must not be read
    # as claiming any. ``src/app.py``'s RequestValidationError handler states the
    # repo's own storyboard-grounded rule — a structurally malformed field is
    # INVALID_REQUEST, a bad VALUE on a well-formed field is VALIDATION_ERROR — and a
    # non-dict ``account`` is structural under that rule. A REST body that fails
    # FastAPI's own parse already takes that handler and emits INVALID_REQUEST, so the
    # two codes for "bad account" ALREADY coexist across entry points; routing both of
    # THIS helper's paths through one boundary keeps them consistent with each other,
    # not with the rest of the surface. The generated corpus is split the same way
    # (BR-UC-002 grades the mutually-exclusive account shape VALIDATION_ERROR;
    # BR-UC-004 grades the identical shape INVALID_REQUEST).
    #
    # So this is deferred, not settled, and it is deferred because the fix is a
    # taxonomy decision rather than a code swap here: the value-vs-structural rule for
    # schema failures is #1984, and the account-boundary rows a rule would retire are
    # C1 (#1316), C2 (#1317) and C4 (#1319) in docs/test-debt-bdd-strict-markers.md.
    # Changing this emission alone, ahead of that rule, moves one site of a split
    # without closing it.
    return _coerce_wire_object(
        account,
        AccountReference,
        "account value",
        strict=True,
        field="account",
        suggestion="Correct the 'account' field to match the AdCP specification and resend.",
    )


def to_property_list_reference(
    property_list: dict[str, Any] | PropertyListReference | None,
) -> PropertyListReference | None:
    """Convert dict to PropertyListReference for adcp compatibility."""
    return _coerce_wire_object(property_list, PropertyListReference, "property_list value")


def coerce_creative_filters(filters: dict[str, Any] | CreativeFilters | None) -> CreativeFilters | None:
    """Coerce a raw list_creatives filters value into a typed CreativeFilters.

    Single source of truth for the dict -> CreativeFilters boundary so REST and
    A2A coerce identically (the MCP transport coerces via FastMCP's TypeAdapter on
    the tool signature).

    A malformed filter (e.g. ``concept_ids`` with an empty array, violating the
    schema's ``minItems: 1``) is raised as a *typed* ``AdCPValidationError`` carrying
    a recovery suggestion, so every transport surfaces the spec's two-layer
    ``VALIDATION_ERROR`` envelope (with a suggestion, per POST-F3). Constructing the
    model directly instead (as the ``to_*`` converters above do, via ``Model(**dict)``)
    surfaces a raw pydantic ``ValidationError`` that ``normalize_to_adcp_error``
    flattens into a suggestion-less envelope.

    Args:
        filters: Filters as a wire dict, an already-typed CreativeFilters, or None.

    Returns:
        CreativeFilters or None (when no filter was supplied).

    Raises:
        AdCPValidationError: when ``filters`` is a dict that fails CreativeFilters validation.
    """
    if filters is None or isinstance(filters, CreativeFilters):
        return filters
    with adcp_validation_boundary(context="list_creatives filters"):
        return CreativeFilters.model_validate(filters)


def create_get_products_request(
    brief: str = "",
    brand: dict[str, Any] | BrandReference | str | None = None,
    filters: dict[str, Any] | ProductFilters | None = None,
    property_list: dict[str, Any] | PropertyListReference | None = None,
    context: dict[str, Any] | ContextObject | None = None,
    account: dict[str, Any] | AccountReference | None = None,
) -> GetProductsRequest:
    """Create GetProductsRequest aligned with adcp v3.6.0 spec.

    Args:
        brief: Natural language description of campaign requirements
        brand: Brand reference per adcp 3.6.0 (BrandReference or dict with domain field).
               Example: BrandReference(domain="acme.com") or {"domain": "acme.com"}
        filters: Structured filters for product discovery (dict or ProductFilters)
        property_list: Property list reference for filtering by buyer's property list
        context: Application-level context (dict or ContextObject)
        account: Account reference for multi-account rate-card lookup and sandbox scoping

    Returns:
        GetProductsRequest

    Note:
        ``account`` on the returned ``req`` is schema conformance only (adcp 3.1.1
        declares ``account`` on ``GetProductsRequest``) — it has no production reader.
        Account-based identity enrichment happens via the raw ``account`` argument
        through ``enrich_identity_with_account``, independently of where this call sits
        relative to that enrichment step at each caller (the relative ordering differs
        per caller, so don't assume "above" or "below").

    Examples:
        >>> req = create_get_products_request(
        ...     brand=BrandReference(domain="acme.com"),
        ...     brief="Display ads"
        ... )
    """
    # Handle filters - can be dict, ProductFilters, or None
    filters_obj: ProductFilters | None = None
    if filters is not None:
        if isinstance(filters, ProductFilters):
            filters_obj = filters
        elif isinstance(filters, dict):
            filters_obj = ProductFilters(**filters)

    return GetProductsRequest(  # type: ignore[call-arg]
        brand=to_brand_reference(brand),
        brief=brief or None,
        filters=filters_obj,
        property_list=to_property_list_reference(property_list),
        context=to_context_object(context),
        account=to_account_reference(account),
    )


# Re-export commonly used generated types for convenience
__all__ = [
    "is_url_shorthand",
    "brand_shorthand_to_domain",
    "to_account_reference",
    "to_brand_reference",
    "to_context_object",
    "to_property_list_reference",
    "require_push_notification_config",
    "to_push_notification_config",
    "to_reporting_webhook",
    "coerce_creative_filters",
    "create_get_products_request",
    # Re-export types for type hints
    "BrandReference",
    "CreativeFilters",
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
    "ContextObject",
    "ReportingWebhook",
]
