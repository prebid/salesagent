"""
AdCP V2.3 Request Builder Helpers

Utilities for building valid AdCP-compliant requests for E2E tests.
All helpers enforce the NEW AdCP V2.3 format with proper schema validation.
"""

import uuid
import warnings
from datetime import UTC, datetime
from typing import Any

from tests.factories.creative_asset import build_assets, image_spec, url_spec
from tests.factories.webhook import PushNotificationConfigRequestFactory, ReportingWebhookRequestFactory


def generate_buyer_ref(prefix: str = "test") -> str:
    """Generate a unique buyer reference."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


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


#: The account seeded for the demo tenant by init_db() beside "ci-test-principal".
#: `account` is REQUIRED on sync-creatives-request.json and create-media-buy-request.json,
#: so a builder that omits it produces a request every transport refuses with
#: INVALID_REQUEST before any behavior under test runs.
CI_TEST_ACCOUNT: dict[str, Any] = {"account_id": "ci-test-account"}


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
    pricing_option_id: str = "default",
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
        # Required on create-media-buy-request.json, same as on sync-creatives.
        "account": CI_TEST_ACCOUNT,
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
        # Required by AdCP 3.0.1 — unique per call (a reused key would replay the
        # original response instead of creating a new buy).
        "idempotency_key": f"e2e-key-{uuid.uuid4().hex}",
    }

    # Add optional fields
    if targeting_overlay:
        request["packages"][0]["targeting_overlay"] = targeting_overlay

    if webhook_url:
        # AdCP-compliant ReportingWebhook authentication requires:
        # - credentials: string with minLength 32 (shared secret or bearer token)
        # - schemes: array of authentication schemes ["Bearer" or "HMAC-SHA256"]
        request["reporting_webhook"] = ReportingWebhookRequestFactory.payload(
            url=webhook_url,
            reporting_frequency=reporting_frequency,
            authentication={"credentials": "test-webhook-bearer-token-at-least-32-chars-long", "schemes": ["Bearer"]},
        )

    if context:
        request["context"] = context

    return request


def build_sync_creatives_payload(
    creatives: list[dict[str, Any]],
    dry_run: bool = False,
    webhook_url: str | None = None,
    assignments: list[dict[str, Any]] | None = None,
    creative_ids: list[str] | None = None,
    delete_missing: bool = False,
    validation_mode: str = "strict",
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
        "account": CI_TEST_ACCOUNT,
        "creatives": creatives,
        "dry_run": dry_run,
        "validation_mode": validation_mode,
        "delete_missing": delete_missing,
        # sync-creatives-request.json /required lists idempotency_key. Unique per call --
        # a reused key replays the original response instead of performing the sync.
        "idempotency_key": f"e2e-sync-{uuid.uuid4().hex}",
    }

    if assignments:
        request["assignments"] = assignments

    if creative_ids:
        request["creative_ids"] = creative_ids

    if webhook_url:
        # AdCP push_notification_config: omitting `authentication` selects the
        # default RFC 9421 webhook-signing profile. The legacy {schemes,
        # credentials} block is only needed when opting into Bearer/HMAC.
        request["push_notification_config"] = PushNotificationConfigRequestFactory.payload(url=webhook_url)

    return request


def build_creative(
    creative_id: str,
    format_id: str | dict[str, Any],
    name: str,
    asset_url: str,
    click_through_url: str | None = None,
    status: str = "processing",
) -> dict[str, Any]:
    """
    Build a valid AdCP V2.4 creative object with assets.

    Args:
        creative_id: Unique creative identifier
        format_id: Format ID - either string (legacy) or FormatId dict with agent_url and id
        name: Human-readable creative name
        asset_url: URL to the creative asset (converted to assets structure)
        click_through_url: Optional click-through destination
        status: Creative status (default: processing). Valid: processing, approved, rejected, pending_review, archived

    Returns:
        Valid AdCP V2.4 Creative dict with assets
    """
    # Build assets structure based on format type
    # For display formats, use image asset
    # For video formats, use video asset
    # Default to image for now
    assets: dict[str, Any] = build_assets(image_spec("primary", url=asset_url, width=300, height=250))

    creative: dict[str, Any] = {
        "creative_id": creative_id,
        "format_id": format_id,
        "name": name,
        "assets": assets,
        "status": status,
    }

    if click_through_url:
        # 3.1 carries the click destination as a url ASSET, not a top-level key.
        # core/asset-group-vocabulary.json names `landing_page_url` canonical and lists
        # click_through_url only as a legacy alias; core/creative-asset.json declares
        # neither it nor content_uri as properties.
        creative["assets"] = build_assets(
            image_spec("primary", url=asset_url, width=300, height=250),
            url_spec("landing_page_url", url=click_through_url),
        )

    return creative


def build_update_media_buy_request(
    media_buy_id: str,
    active: bool | None = None,
    packages: list[dict[str, Any]] | None = None,
    webhook_url: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a valid AdCP update_media_buy request.

    NOTE: there is no top-level ``budget``. update-media-buy-request.json declares
    no such property -- budgets live on the package entries -- so sending one is
    rejected outright under the dev extra="forbid" mode. The parameter used to exist
    here and was removed with the field itself.
    """
    request: dict[str, Any] = {
        "media_buy_id": media_buy_id,
        # Required on update-media-buy-request.json too.
        "account": CI_TEST_ACCOUNT,
        # update-media-buy-request.json /required lists idempotency_key. Unique per call --
        # a reused key replays the original response instead of applying the update.
        "idempotency_key": f"e2e-update-{uuid.uuid4().hex}",
    }

    # Add optional fields
    if active is not None:
        request["active"] = active
    if packages is not None:
        request["packages"] = packages
    if webhook_url:
        # AdCP push_notification_config: omitting `authentication` selects the
        # default RFC 9421 webhook-signing profile. The legacy {schemes,
        # credentials} block is only needed when opting into Bearer/HMAC.
        request["push_notification_config"] = PushNotificationConfigRequestFactory.payload(url=webhook_url)
    if context is not None:
        request["context"] = context

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


def build_default_campaign_request(
    product_id: str,
    pricing_option_id: str,
    *,
    total_budget: float = 5000.0,
    days_from_now: int = 1,
    duration_days: int = 30,
    brand_domain: str = "testbrand.com",
    **overrides: Any,
) -> dict[str, Any]:
    """Build the default e2e campaign request (GH #1423 consolidation).

    Single home for the "$5000 testbrand.com buy starting tomorrow for 30 days"
    block previously copy-pasted across the lifecycle/reference/creative e2e
    suites. Anything test-specific (targeting_overlay, webhook_url, context, ...)
    passes through ``**overrides`` to :func:`build_adcp_media_buy_request`.
    """
    start_time, end_time = get_test_date_range(days_from_now=days_from_now, duration_days=duration_days)
    return build_adcp_media_buy_request(
        product_ids=[product_id],
        total_budget=total_budget,
        start_time=start_time,
        end_time=end_time,
        brand={"domain": brand_domain},
        pricing_option_id=pricing_option_id,
        **overrides,
    )
