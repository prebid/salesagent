"""
AdCP V2.3 Request Builder Helpers

Utilities for building valid AdCP-compliant requests for E2E tests.
All helpers enforce the NEW AdCP V2.3 format with proper schema validation.
"""

import uuid
import warnings
from datetime import UTC, datetime
from typing import Any

_DEFAULT_BRAND: dict[str, Any] = {"domain": "testbrand.com"}


def generate_buyer_ref(prefix: str = "test") -> str:
    """Generate a unique buyer reference."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _build_reporting_webhook(url: str, reporting_frequency: str | None = None) -> dict[str, Any]:
    """Build a 4.4-compliant ``ReportingWebhook`` block.

    AdCP 4.4 made ``authentication.schemes`` and ``authentication.credentials``
    required (Bearer or HMAC-SHA256, credentials min length 32). The
    ``{"type": "none"}`` shape was rejected by SDK validation in 4.4.

    Returns the inner block — callers wrap it under
    ``push_notification_config`` (e.g. update_media_buy) or
    ``reporting_webhook`` (e.g. create_media_buy) per spec naming.
    """
    block: dict[str, Any] = {
        "url": url,
        "authentication": {
            "credentials": "test-webhook-bearer-token-at-least-32-chars-long",
            "schemes": ["Bearer"],
        },
    }
    if reporting_frequency is not None:
        block["reporting_frequency"] = reporting_frequency
    return block


def _inject_wire_required_fields(
    request: dict[str, Any],
    *,
    brand: dict[str, Any] | None,
    idempotency_prefix: str,
) -> None:
    """Inject the adcp 4.4 wire-required ``account`` + ``idempotency_key``.

    AdCP 4.4 made both fields required at the wire boundary on the
    create_media_buy / update_media_buy / sync_creatives requests.
    Real buyers must supply them; the test builders synthesise valid
    values from the caller-supplied brand so e2e flows behave like real
    callers without bypassing SDK validation.
    ``adcp_version`` is intentionally omitted so MCP requests exercise the
    protocol's legacy 3.0 default path.

    * ``account`` is a natural-key reference (``{brand, operator}``)
      where the operator defaults to the brand's domain — buyers using
      a seller-assigned ``account_id`` instead can pass it through
      manually after the builder returns.
    * ``idempotency_key`` is a fresh UUID per call (no memoisation),
      satisfying the spec pattern ``^[A-Za-z0-9_.:-]{16,255}$``. The
      ``idempotency_prefix`` is a short tool-name marker (e.g.
      ``"e2e"``, ``"e2e-update"``, ``"e2e-sync"``) for log-grepping.
    """
    actual_brand = brand if brand is not None else _DEFAULT_BRAND
    operator = actual_brand.get("domain", "testbrand.com") if isinstance(actual_brand, dict) else "testbrand.com"
    request["idempotency_key"] = f"{idempotency_prefix}-{uuid.uuid4()}"
    request["account"] = {"brand": actual_brand, "operator": operator}


def parse_tool_result(result: Any) -> dict[str, Any]:
    """
    Parse MCP tool result into structured data.

    Extracts structured data from ToolResult.structured_content field.
    The text field contains human-readable text, structured_content has the JSON data.

    Args:
        result: MCP tool result object with structured_content

    Returns:
        Parsed result data as a dictionary

    Example:
        >>> products_result = await client.call_tool("get_products", {...})
        >>> products_data = parse_tool_result(products_result)
        >>> assert "products" in products_data
    """
    if hasattr(result, "structured_content") and result.structured_content:
        return result.structured_content

    raise ValueError(
        f"Unable to parse tool result: {type(result).__name__} has no structured_content field. "
        f"Expected ToolResult with structured_content."
    )


def build_adcp_media_buy_request(
    product_ids: list[str],
    total_budget: float,
    start_time: str | datetime,
    end_time: str | datetime,
    promoted_offering: str = "Test Campaign Product",  # For backward compat, converted to brand
    targeting_overlay: dict[str, Any] | None = None,
    currency: str = "USD",
    pacing: str = "even",
    webhook_url: str | None = None,
    reporting_frequency: str = "daily",
    brand: dict[str, Any] | None = None,  # AdCP 3.6.0: BrandReference with domain
    context: dict[str, Any] | None = None,
    creative_ids: list[str] | None = None,
    # ``cpm_usd_fixed`` matches the auto-generated pricing_option_id for the
    # CI seed products (``prod_display_premium``, ``prod_video_premium``) —
    # built from ``f"{pricing_model}_{currency.lower()}_{fixed_str}"``. The
    # previous default ``"default"`` did not match any seeded product, so
    # ``create_media_buy`` returned a VALIDATION_ERROR; pre-#350 the e2e
    # tests silently no-op'd on that error (returned early when
    # ``media_buy_id`` was missing), masking real coverage. Once the wire
    # promotes that error to a raise, the early-return mask is gone.
    pricing_option_id: str = "cpm_usd_fixed",
) -> dict[str, Any]:
    """
    Build a valid AdCP create_media_buy request.

    Args:
        product_ids: List of product IDs to include
        total_budget: Total budget for the campaign
        start_time: Campaign start (ISO 8601 string or datetime)
        end_time: Campaign end (ISO 8601 string or datetime)
        promoted_offering: DEPRECATED - Use brand instead. Auto-converted if provided.
        targeting_overlay: Optional targeting parameters
        currency: Currency code (default: USD)
        pacing: Budget pacing strategy (default: even)
        webhook_url: Optional webhook for async notifications
        brand: Brand reference dict with required 'domain' field (adcp 3.6.0 BrandReference)

    Returns:
        Valid AdCP CreateMediaBuyRequest dict

    Example:
        >>> request = build_adcp_media_buy_request(
        ...     product_ids=["prod_1"],
        ...     total_budget=5000.0,
        ...     start_time="2025-10-01T00:00:00Z",
        ...     end_time="2025-10-31T23:59:59Z",
        ...     brand={"domain": "testbrand.com"}
        ... )
    """
    # Convert datetime to ISO 8601 string if needed
    if isinstance(start_time, datetime):
        start_time = start_time.isoformat()
    if isinstance(end_time, datetime):
        end_time = end_time.isoformat()

    # Convert promoted_offering to brand if needed (backward compatibility)
    if brand is None and promoted_offering:
        brand = {"domain": "testbrand.com"}

    # Build the request following AdCP spec exactly
    # Note: ALL budgets are plain numbers per spec (currency from pricing_option_id)
    # Per AdCP spec: Package requires product_id (singular) and pricing_option_id
    request: dict[str, Any] = {
        "brand": brand,  # AdCP 3.6.0: BrandReference with domain
        "packages": [
            {
                "product_id": (
                    product_ids[0] if len(product_ids) == 1 else product_ids[0]
                ),  # AdCP spec: singular product_id
                "budget": total_budget,  # Package budget is plain number per AdCP spec
                "pricing_option_id": pricing_option_id,  # Required per AdCP spec,
                "creative_ids": creative_ids,
            }
        ],
        "start_time": start_time,
        "end_time": end_time,
    }

    # Add optional fields
    if targeting_overlay:
        request["packages"][0]["targeting_overlay"] = targeting_overlay

    if webhook_url:
        request["reporting_webhook"] = _build_reporting_webhook(webhook_url, reporting_frequency=reporting_frequency)

    if context:
        request["context"] = context

    _inject_wire_required_fields(request, brand=brand, idempotency_prefix="e2e")
    return request


def build_sync_creatives_request(
    creatives: list[dict[str, Any]],
    dry_run: bool = False,
    webhook_url: str | None = None,
    assignments: dict[str, list[str]] | None = None,
    creative_ids: list[str] | None = None,
    delete_missing: bool = False,
    validation_mode: str = "strict",
    brand: dict[str, Any] | None = None,
    # Deprecated: patch parameter removed in AdCP 2.5 - kept for backward compat
    patch: bool | None = None,
) -> dict[str, Any]:
    """
    Build a valid AdCP V2.5 sync_creatives request.

    Args:
        creatives: List of creative objects to sync
        dry_run: If True, preview changes without applying (default: False)
        webhook_url: Optional webhook for async notifications
        assignments: Optional dict mapping creative_id to list of package_ids
        creative_ids: Filter to limit sync scope to specific creatives (AdCP 2.5)
        delete_missing: If True, delete creatives not in the sync list (default: False)
        validation_mode: Validation mode - "strict" or "lenient" (default: strict)
        patch: DEPRECATED - ignored (AdCP 2.5 removed this parameter)

    Returns:
        Valid AdCP V2.5 SyncCreativesRequest dict
    """
    if patch is not None:
        warnings.warn(
            "The 'patch' parameter is deprecated and ignored. "
            "AdCP 2.5 removed patch semantics in favor of full upsert. "
            "Use 'creative_ids' to scope which creatives are synced.",
            DeprecationWarning,
            stacklevel=2,
        )

    request: dict[str, Any] = {
        "creatives": creatives,
        "dry_run": dry_run,
        "validation_mode": validation_mode,
        "delete_missing": delete_missing,
    }
    # adcp 4.4 made both ``account`` and ``idempotency_key`` required at the
    # wire boundary on sync_creatives. Real buyers must supply them; tests
    # follow the same contract so the SDK validator accepts the call without
    # server-side autogen.
    _inject_wire_required_fields(request, brand=brand, idempotency_prefix="e2e-sync")

    if assignments:
        # adcp 4.4 changed Assignment from a dict
        # ``{creative_id: [package_id, ...]}`` to a flat list of
        # ``Assignment`` objects ``{creative_id, package_id, weight?, placement_ids?}``.
        # Accept the legacy dict shape for caller convenience and explode
        # it to the 4.4 list shape — eventually callers should pass the
        # list themselves and this branch can go.
        if isinstance(assignments, dict):
            request["assignments"] = [
                {"creative_id": cid, "package_id": pid} for cid, pids in assignments.items() for pid in pids
            ]
        else:
            request["assignments"] = assignments

    if creative_ids:
        request["creative_ids"] = creative_ids

    if webhook_url:
        request["push_notification_config"] = _build_reporting_webhook(webhook_url)

    return request


_DEFAULT_FORMAT_AGENT_URL = "https://creative.adcontextprotocol.org/"


def build_creative(
    creative_id: str,
    format_id: str | dict[str, Any],
    name: str,
    asset_url: str,
    click_through_url: str | None = None,
    status: str = "processing",
    asset_type: str = "url",
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """
    Build a valid AdCP creative object with assets.

    Args:
        creative_id: Unique creative identifier
        format_id: Format ID — either a string (wrapped to FormatReference using
            the AdCP creative-agent default agent_url) or a full FormatReference dict.
        name: Human-readable creative name
        asset_url: URL to the creative asset (converted to assets structure)
        click_through_url: Optional click-through destination
        status: Creative status (default: processing).
        asset_type: AssetVariant discriminator. Defaults to ``url`` because that
            shape requires only ``url``. Pass ``image`` etc. and supply width+height
            when testing dimension-bearing variants.
        width / height: Required when ``asset_type == "image"`` or ``"video"``.

    Returns:
        Valid AdCP Creative dict with assets.
    """
    # Normalise format_id from string (pre-4.4 shape) to FormatReference.
    if isinstance(format_id, str):
        format_id = {"agent_url": _DEFAULT_FORMAT_AGENT_URL, "id": format_id}

    primary_asset: dict[str, Any] = {"asset_type": asset_type, "url": asset_url}
    if asset_type in ("image", "video"):
        if width is None or height is None:
            raise ValueError(
                f"build_creative(asset_type={asset_type!r}) requires width and height — "
                f"AdCP 4.4 makes them required on image/video asset variants."
            )
        primary_asset["width"] = width
        primary_asset["height"] = height

    creative: dict[str, Any] = {
        "creative_id": creative_id,
        "format_id": format_id,
        "name": name,
        "content_uri": asset_url,
        "assets": {"primary": primary_asset},
        "status": status,
    }

    if click_through_url:
        creative["click_through_url"] = click_through_url

    return creative


def build_update_media_buy_request(
    media_buy_id: str,
    active: bool | None = None,
    budget: float | None = None,
    packages: list[dict[str, Any]] | None = None,
    webhook_url: str | None = None,
    brand: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a valid AdCP update_media_buy request.

    Args:
        media_buy_id: Media buy ID to update (required)
        active: Optional active status update
        budget: Optional budget update (number per AdCP spec)
        packages: Optional package updates
        webhook_url: Optional webhook for async notifications
        brand: BrandReference dict — used to synthesise the ``account`` natural
            key (defaults to ``{"domain": "testbrand.com"}``).
        context: Optional buyer-supplied context echoed on the response.

    Returns:
        Valid AdCP UpdateMediaBuyRequest dict.
    """
    request: dict[str, Any] = {"media_buy_id": media_buy_id}

    if active is not None:
        request["active"] = active
    if budget is not None:
        request["budget"] = budget
    if packages is not None:
        request["packages"] = packages
    if webhook_url:
        request["push_notification_config"] = _build_reporting_webhook(webhook_url)
    if context is not None:
        request["context"] = context

    _inject_wire_required_fields(request, brand=brand, idempotency_prefix="e2e-update")
    return request


def get_test_date_range(days_from_now: int = 1, duration_days: int = 30) -> tuple[str, str]:
    """
    Get a test-friendly date range in ISO 8601 format.

    Args:
        days_from_now: How many days in the future to start (default: 1)
        duration_days: Campaign duration in days (default: 30)

    Returns:
        Tuple of (start_time, end_time) as ISO 8601 strings
    """
    from datetime import timedelta

    now = datetime.now(UTC)
    start = now + timedelta(days=days_from_now)
    end = start + timedelta(days=duration_days)

    return (start.isoformat(), end.isoformat())
