"""Domain step definitions for UC-004: Deliver Media Buy Metrics.

Given steps: media buy setup, adapter response injection
When steps: delivery metric request dispatch
Then steps: delivery-specific assertions (metrics, periods, status, webhooks)

Steps store results in ctx:
    ctx["response"] — GetMediaBuyDeliveryResponse on success
    ctx["error"] — Exception on failure
"""

from __future__ import annotations

import json
import re
from typing import Any

from pytest_bdd import given, parsers, then, when

# ── Helpers ──────────────────────────────────────────────────────────


def _call_delivery(ctx: dict, **kwargs: Any) -> None:
    """Dispatch delivery request through ctx['transport'] via call_via."""
    transport = ctx.get("transport")
    env = ctx["env"]
    if transport is not None:
        try:
            result = env.call_via(transport, **kwargs)
            if result.is_error:
                ctx["error"] = result.error
            else:
                ctx["response"] = result.payload
        except Exception as exc:
            ctx["error"] = exc
    else:
        try:
            ctx["response"] = env.call_impl(**kwargs)
        except Exception as exc:
            ctx["error"] = exc


def _get_webhook_payload(ctx: dict) -> dict:
    """Extract the JSON payload from the most recent webhook POST call."""
    env = ctx["env"]
    call_args = env.mock["post"].call_args
    assert call_args is not None, "No POST call recorded"
    return call_args.kwargs.get("json") or call_args[1].get("json", {})


def _get_webhook_headers(ctx: dict) -> dict:
    """Extract the headers from the most recent webhook POST call."""
    env = ctx["env"]
    call_args = env.mock["post"].call_args
    assert call_args is not None, "No POST call recorded"
    return call_args.kwargs.get("headers", {})


def _parse_json_list(text: str) -> list[str]:
    """Parse a JSON-like list string from Gherkin, e.g., '["mb-001", "mb-002"]'."""
    return json.loads(text)


def _inject_placement_data(ctx: dict) -> None:
    """Inject synthetic placement data into adapter responses for placement sort tests.

    Creates 3 placements with distinct metric values so sort assertions can verify ordering.
    """
    env = ctx["env"]
    media_buys = ctx.get("media_buys", {})
    for mb_id in media_buys:
        env.set_adapter_response(
            media_buy_id=mb_id,
            impressions=10000,
            spend=500.0,
            clicks=200,
            packages=[
                {
                    "package_id": "pkg_001",
                    "impressions": 10000,
                    "spend": 500.0,
                    "by_placement": [
                        {"placement_id": "pl-1", "impressions": 5000, "spend": 300.0, "clicks": 120},
                        {"placement_id": "pl-2", "impressions": 3000, "spend": 100.0, "clicks": 50},
                        {"placement_id": "pl-3", "impressions": 2000, "spend": 100.0, "clicks": 30},
                    ],
                }
            ],
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — media buy setup and adapter configuration
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a media buy "{mb_id}" owned by "{owner}" with status "{status}"'))
def given_media_buy_with_status(ctx: dict, mb_id: str, owner: str, status: str) -> None:
    """Create a media buy with the given status in the test database."""
    ctx.setdefault("media_buys", {})[mb_id] = {
        "media_buy_id": mb_id,
        "owner": owner,
        "status": status,
    }
    _ensure_media_buy_in_db(ctx, mb_id, owner, status)


@given(parsers.parse('a media buy "{mb_id}" owned by "{owner}" with buyer_ref "{buyer_ref}"'))
def given_media_buy_with_buyer_ref(ctx: dict, mb_id: str, owner: str, buyer_ref: str) -> None:
    """Create a media buy with a buyer reference."""
    ctx.setdefault("media_buys", {})[mb_id] = {
        "media_buy_id": mb_id,
        "owner": owner,
        "buyer_ref": buyer_ref,
    }
    _ensure_media_buy_in_db(ctx, mb_id, owner, buyer_ref=buyer_ref)


@given(parsers.parse('a media buy "{mb_id}" owned by "{owner}"'))
def given_media_buy(ctx: dict, mb_id: str, owner: str) -> None:
    """Create a media buy owned by the given principal."""
    ctx.setdefault("media_buys", {})[mb_id] = {
        "media_buy_id": mb_id,
        "owner": owner,
    }
    _ensure_media_buy_in_db(ctx, mb_id, owner)


@given(parsers.parse('a media buy "{mb_id}" owned by "{owner}" created on "{created_date}"'))
def given_media_buy_created_on(ctx: dict, mb_id: str, owner: str, created_date: str) -> None:
    """Create a media buy with a specific creation date."""
    ctx.setdefault("media_buys", {})[mb_id] = {
        "media_buy_id": mb_id,
        "owner": owner,
        "created_date": created_date,
    }
    _ensure_media_buy_in_db(ctx, mb_id, owner)


@given(parsers.parse('a media buy "{mb_id}" with a known owner'))
def given_media_buy_known_owner(ctx: dict, mb_id: str) -> None:
    """Create a media buy with a known owner (default principal)."""
    owner = ctx.get("principal_id", "buyer-001")
    ctx.setdefault("media_buys", {})[mb_id] = {
        "media_buy_id": mb_id,
        "owner": owner,
    }
    _ensure_media_buy_in_db(ctx, mb_id, owner)


@given(parsers.parse('no media buy exists with id "{mb_id}"'))
def given_no_media_buy(ctx: dict, mb_id: str) -> None:
    """Ensure no media buy with this ID exists."""
    ctx.setdefault("nonexistent_media_buys", []).append(mb_id)


@given(parsers.parse('no media buy exists with id "{mb_id1}" or "{mb_id2}"'))
def given_no_media_buys(ctx: dict, mb_id1: str, mb_id2: str) -> None:
    """Ensure neither media buy exists."""
    ctx.setdefault("nonexistent_media_buys", []).extend([mb_id1, mb_id2])


@given(parsers.parse('the principal "{principal_id}" has no media buys'))
def given_principal_no_buys(ctx: dict, principal_id: str) -> None:
    """Principal exists but has no media buys."""
    ctx["media_buys"] = {}


@given(parsers.parse('no principal "{principal_id}" exists in the tenant database'))
def given_no_principal(ctx: dict, principal_id: str) -> None:
    """No principal with this ID exists."""
    ctx["principal_exists"] = False
    ctx["nonexistent_principal"] = principal_id


@given(parsers.parse('multiple media buys owned by "{owner}" in various statuses'))
def given_multiple_buys_various_statuses(ctx: dict, owner: str) -> None:
    """Create media buys in various statuses for partition testing."""
    for status in ("active", "completed", "paused"):
        mb_id = f"mb-{status}"
        ctx.setdefault("media_buys", {})[mb_id] = {
            "media_buy_id": mb_id,
            "owner": owner,
            "status": status,
        }
        _ensure_media_buy_in_db(ctx, mb_id, owner, status)


@given(parsers.parse('media buys owned by "{owner}"'))
def given_media_buys_owned_by(ctx: dict, owner: str) -> None:
    """Create a default set of media buys owned by the given principal."""
    for mb_id in ("mb-001", "mb-002"):
        ctx.setdefault("media_buys", {})[mb_id] = {
            "media_buy_id": mb_id,
            "owner": owner,
        }
        _ensure_media_buy_in_db(ctx, mb_id, owner)


# ── Adapter response configuration ────────────────────────────────────


@given(parsers.parse('the ad server adapter has delivery data for "{mb_id}"'))
def given_adapter_has_data(ctx: dict, mb_id: str) -> None:
    """Configure adapter mock to return delivery data for the media buy."""
    env = ctx["env"]
    env.set_adapter_response(media_buy_id=mb_id)


@given("the ad server adapter has delivery data for both media buys")
def given_adapter_has_data_both(ctx: dict) -> None:
    """Configure adapter mock to return data for both media buys."""
    env = ctx["env"]
    media_buys = ctx.get("media_buys", {})
    for mb_id in list(media_buys.keys())[:2]:
        env.set_adapter_response(media_buy_id=mb_id)


@given("the ad server adapter has delivery data for all media buys")
def given_adapter_has_data_all(ctx: dict) -> None:
    """Configure adapter mock to return data for all media buys."""
    env = ctx["env"]
    for mb_id in ctx.get("media_buys", {}):
        env.set_adapter_response(media_buy_id=mb_id)


@given("the ad server adapter is unavailable")
def given_adapter_unavailable(ctx: dict) -> None:
    """Configure adapter to raise an error."""
    env = ctx["env"]
    env.set_adapter_error(ConnectionError("Ad server adapter is unavailable"))


@given(parsers.parse('the ad server adapter returns data for "{mb_id1}" but errors for "{mb_id2}"'))
def given_adapter_partial_data(ctx: dict, mb_id1: str, mb_id2: str) -> None:
    """Configure adapter for partial success: data for one, error for another."""
    env = ctx["env"]
    env.set_adapter_response(media_buy_id=mb_id1)
    # mb_id2 has no response registered — will raise KeyError from the mixin


@given(parsers.parse('the ad server adapter has no delivery data for "{mb_id}" in the requested period'))
def given_adapter_no_data_period(ctx: dict, mb_id: str) -> None:
    """Configure adapter to return zero data for the media buy."""
    env = ctx["env"]
    env.set_adapter_response(media_buy_id=mb_id, impressions=0, spend=0.0)


# ── Webhook configuration steps ─────────────────────────────────────


def _set_active_webhook(ctx: dict, mb_id: str) -> None:
    """Shared: configure an active webhook for a media buy."""
    ctx.setdefault("webhook_config", {})[mb_id] = {
        "url": "https://buyer.example.com/webhook",
        "active": True,
    }


@given(parsers.parse('a media buy "{mb_id}" with an active reporting_webhook configured'))
def given_webhook_configured(ctx: dict, mb_id: str) -> None:
    """Media buy has an active webhook endpoint configured."""
    _set_active_webhook(ctx, mb_id)


@given(parsers.parse('a media buy "{mb_id}" with an active reporting_webhook'))
def given_webhook_active(ctx: dict, mb_id: str) -> None:
    """Media buy has an active webhook (same as configured)."""
    ctx.setdefault("webhook_variant", "active")
    _set_active_webhook(ctx, mb_id)


@given(parsers.parse('a media buy "{mb_id}" with webhook delivery configured'))
def given_webhook_delivery_configured(ctx: dict, mb_id: str) -> None:
    """Media buy has webhook delivery configured."""
    ctx.setdefault("webhook_variant", "delivery")
    _set_active_webhook(ctx, mb_id)


@given(parsers.parse('a media buy "{mb_id}" without a reporting_webhook configured'))
def given_no_webhook(ctx: dict, mb_id: str) -> None:
    """Media buy has no webhook configured."""
    ctx.setdefault("webhook_config", {})[mb_id] = {"active": False}


@given(parsers.parse('the reporting_frequency is "{frequency}"'))
def given_reporting_frequency(ctx: dict, frequency: str) -> None:
    """Set the reporting frequency for webhook delivery."""
    ctx["reporting_frequency"] = frequency


@given(parsers.parse('a media buy "{mb_id}" with webhook authentication scheme "{scheme}"'))
def given_webhook_auth_scheme(ctx: dict, mb_id: str, scheme: str) -> None:
    """Configure webhook with specific auth scheme."""
    wh = ctx.setdefault("webhook_config", {}).setdefault(mb_id, {})
    wh["auth_scheme"] = scheme
    wh["active"] = True
    wh["url"] = "https://buyer.example.com/webhook"


@given("the shared secret is a valid 32+ character string")
def given_shared_secret_valid(ctx: dict) -> None:
    """A valid shared secret for HMAC."""
    ctx["webhook_secret"] = "a" * 32


@given("the bearer token is a valid 32+ character string")
def given_bearer_token_valid(ctx: dict) -> None:
    """A valid bearer token."""
    ctx["webhook_bearer_token"] = "b" * 32


@given(parsers.parse("a media buy webhook configuration with credentials of {n:d} characters"))
def given_webhook_creds_length(ctx: dict, n: int) -> None:
    """Configure webhook credentials of specific length."""
    ctx["webhook_secret"] = "x" * n


# ── Webhook endpoint behavior ─────────────────────────────────────


@given(parsers.parse("the webhook endpoint returns {status_code:d} {reason}"))
def given_webhook_returns_status(ctx: dict, status_code: int, reason: str) -> None:
    """Configure webhook endpoint to return specific status."""
    env = ctx["env"]
    env.set_http_status(status_code, reason)


@given("the webhook endpoint is unreachable (connection timeout)")
def given_webhook_unreachable(ctx: dict) -> None:
    """Configure webhook endpoint to timeout."""
    env = ctx["env"]
    env.mock["post"].side_effect = ConnectionError("Connection timeout")


@given(parsers.parse("the webhook endpoint returns {status_code:d} Unauthorized"))
def given_webhook_unauthorized(ctx: dict, status_code: int) -> None:
    """Configure webhook endpoint to return auth error."""
    env = ctx["env"]
    env.set_http_status(status_code, "Unauthorized")


@given(parsers.parse("the webhook endpoint has failed {n:d} consecutive delivery attempts"))
def given_webhook_failed_n_times(ctx: dict, n: int) -> None:
    """Record n consecutive delivery failures."""
    ctx["webhook_failure_count"] = n


@given(parsers.parse('a media buy "{mb_id}" with circuit breaker in "{state}" state'))
def given_circuit_breaker_state(ctx: dict, mb_id: str, state: str) -> None:
    """Create a CircuitBreaker and set it to the specified state."""
    from src.services.webhook_delivery_service import CircuitBreaker, CircuitState

    breaker = CircuitBreaker()
    target_state = CircuitState(state.lower())
    breaker.state = target_state
    # If OPEN, simulate past failures so can_attempt() respects the state
    if target_state == CircuitState.OPEN:
        from datetime import UTC, datetime

        breaker.failure_count = breaker.failure_threshold
        breaker.last_failure_time = datetime.now(UTC)
    ctx["circuit_breaker"] = breaker
    ctx["circuit_breaker_state"] = state


@given("the circuit breaker timeout (60s) has elapsed")
def given_circuit_breaker_timeout(ctx: dict) -> None:
    """Circuit breaker timeout has elapsed."""
    ctx["circuit_breaker_timeout_elapsed"] = True


@given("the webhook endpoint has recovered and returns 200")
def given_webhook_recovered(ctx: dict) -> None:
    """Webhook endpoint is healthy again."""
    env = ctx["env"]
    env.set_http_status(200, "OK")


@given("the webhook endpoint fails on first attempt but succeeds on second")
def given_webhook_flaky(ctx: dict) -> None:
    """Configure webhook to fail then succeed."""
    env = ctx["env"]
    env.set_http_sequence([(500, "Error"), (200, "OK")])


# ── Reporting dimensions / attribution / seller capabilities ──────


@given(parsers.parse('the seller supports reporting dimension "{dimension}"'))
def given_seller_supports_dimension(ctx: dict, dimension: str) -> None:
    """Seller supports a specific reporting dimension."""
    ctx.setdefault("supported_dimensions", []).append(dimension)
    # When "placement" dimension is supported, inject placement data into adapter responses
    if dimension == "placement":
        _inject_placement_data(ctx)


@given(parsers.parse('the seller does NOT support reporting dimension "{dimension}"'))
def given_seller_no_dimension(ctx: dict, dimension: str) -> None:
    """Seller does not support a specific reporting dimension."""
    ctx.setdefault("unsupported_dimensions", []).append(dimension)


@given(parsers.parse('the seller supports reporting dimensions "{dim1}" and "{dim2}"'))
def given_seller_supports_dimensions(ctx: dict, dim1: str, dim2: str) -> None:
    """Seller supports multiple reporting dimensions."""
    ctx.setdefault("supported_dimensions", []).extend([dim1, dim2])


@given(parsers.parse('the seller does NOT support "{capability}"'))
def given_seller_no_capability(ctx: dict, capability: str) -> None:
    """Seller does not support a capability."""
    ctx.setdefault("unsupported_capabilities", []).append(capability)


@given("the seller supports configurable attribution windows")
def given_seller_supports_attribution(ctx: dict) -> None:
    """Seller supports configurable attribution windows."""
    ctx["supports_attribution_windows"] = True


@given("the seller does NOT support configurable attribution windows")
def given_seller_no_attribution(ctx: dict) -> None:
    """Seller does not support configurable attribution windows."""
    ctx["supports_attribution_windows"] = False


@given(parsers.parse('the seller does NOT report metric "{metric}"'))
def given_seller_no_metric(ctx: dict, metric: str) -> None:
    """Seller does not report a specific metric."""
    ctx.setdefault("unsupported_metrics", []).append(metric)


@given(parsers.parse('the seller reports metric "{metric}"'))
def given_seller_reports_metric(ctx: dict, metric: str) -> None:
    """Seller reports a specific metric."""
    ctx.setdefault("supported_metrics", []).append(metric)


@given("there are more geo breakdown entries than the requested limit")
def given_geo_exceeds_limit(ctx: dict) -> None:
    """More geo entries than limit — truncation expected."""
    ctx["geo_exceeds_limit"] = True


@given("the device_type breakdown has fewer entries than any limit")
def given_device_type_under_limit(ctx: dict) -> None:
    """Fewer device_type entries than limit — no truncation."""
    ctx["device_type_under_limit"] = True


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — delivery metric requests
# ═══════════════════════════════════════════════════════════════════════


@when(parsers.re(r"the Buyer Agent requests delivery metrics for media_buy_ids (?P<ids_json>\[.+?\])"))
def when_request_by_ids(ctx: dict, ids_json: str) -> None:
    """Request delivery metrics by media_buy_ids."""
    media_buy_ids = _parse_json_list(ids_json)
    _call_delivery(ctx, media_buy_ids=media_buy_ids)


@when("the Buyer Agent requests delivery metrics without media_buy_ids or buyer_refs")
def when_request_no_identifiers(ctx: dict) -> None:
    """Request delivery metrics without any identifiers."""
    _call_delivery(ctx)


@when(parsers.parse("the Buyer Agent requests delivery metrics with {request_params}"))
def when_request_with_params(ctx: dict, request_params: str) -> None:
    """Request with arbitrary params (Scenario Outline)."""
    kwargs = _parse_request_params(request_params)
    _call_delivery(ctx, **kwargs)


@when(parsers.parse("the Buyer Agent requests delivery metrics with media_buy_ids {ids_json}"))
def when_request_with_media_buy_ids(ctx: dict, ids_json: str) -> None:
    """Request with explicit media_buy_ids list."""
    if ids_json == "[]":
        _call_delivery(ctx, media_buy_ids=[])
    else:
        media_buy_ids = _parse_json_list(ids_json)
        _call_delivery(ctx, media_buy_ids=media_buy_ids)


@when(parsers.parse("the Buyer Agent requests delivery metrics with buyer_refs {refs_json}"))
def when_request_with_buyer_refs(ctx: dict, refs_json: str) -> None:
    """Request with buyer_refs list."""
    if refs_json == "[]":
        _call_delivery(ctx, buyer_refs=[])
    else:
        buyer_refs = _parse_json_list(refs_json)
        _call_delivery(ctx, buyer_refs=buyer_refs)


@when(parsers.re(r'the Buyer Agent requests delivery metrics with status_filter "(?P<filter_value>[^"]+)"'))
def when_request_with_status_filter(ctx: dict, filter_value: str) -> None:
    """Request with status_filter string."""
    _call_delivery(ctx, status_filter=[filter_value])


@when(parsers.re(r"the Buyer Agent requests delivery metrics with status_filter (?P<filter_json>\[.+?\])"))
def when_request_with_status_filter_list(ctx: dict, filter_json: str) -> None:
    """Request with status_filter list."""
    status_filter = _parse_json_list(filter_json)
    _call_delivery(ctx, status_filter=status_filter)


@when("the Buyer Agent requests delivery metrics without status_filter")
def when_request_no_status_filter(ctx: dict) -> None:
    """Request without status_filter (all statuses)."""
    media_buys = ctx.get("media_buys", {})
    mb_ids = list(media_buys.keys())
    _call_delivery(ctx, media_buy_ids=mb_ids if mb_ids else None)


@when(parsers.parse('the Buyer Agent requests delivery metrics with start_date "{start}" and end_date "{end}"'))
def when_request_date_range(ctx: dict, start: str, end: str) -> None:
    """Request with date range."""
    _call_delivery(ctx, start_date=start, end_date=end)


@when(parsers.parse('the Buyer Agent requests delivery metrics with start_date "{start}" and no end_date'))
def when_request_start_only(ctx: dict, start: str) -> None:
    """Request with start_date only."""
    _call_delivery(ctx, start_date=start)


@when(parsers.parse('the Buyer Agent requests delivery metrics with end_date "{end}" and no start_date'))
def when_request_end_only(ctx: dict, end: str) -> None:
    """Request with end_date only."""
    _call_delivery(ctx, end_date=end)


@when("the Buyer Agent requests delivery metrics")
def when_request_delivery_default(ctx: dict) -> None:
    """Request delivery metrics (generic, uses ctx media_buys)."""
    media_buys = ctx.get("media_buys", {})
    mb_ids = list(media_buys.keys()) or None
    _call_delivery(ctx, media_buy_ids=mb_ids)


@when("the Buyer Agent sends a delivery metrics request without authentication")
def when_request_no_auth(ctx: dict) -> None:
    """Request delivery metrics without authentication."""
    ctx["has_auth"] = False
    _call_delivery(ctx)


# ── Webhook When steps ─────────────────────────────────────────────


@when(parsers.parse('the webhook scheduler fires for "{mb_id}"'))
def when_webhook_fires(ctx: dict, mb_id: str) -> None:
    """Webhook scheduler fires for a media buy."""
    env = ctx["env"]
    webhook = ctx.get("webhook_config", {})
    try:
        ctx["webhook_result"] = env.call_deliver(
            payload={"event": "delivery.update", "media_buy_id": mb_id},
            signing_secret=webhook.get("signing_secret"),
        )
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the system delivers a webhook report for "{mb_id}"'))
def when_deliver_webhook(ctx: dict, mb_id: str) -> None:
    """System delivers a webhook report."""
    env = ctx["env"]
    webhook = ctx.get("webhook_config", {})
    try:
        ctx["webhook_result"] = env.call_deliver(
            payload={"event": "delivery.update", "media_buy_id": mb_id},
            signing_secret=webhook.get("signing_secret"),
        )
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the system delivers a "{report_type}" webhook report for "{mb_id}"'))
def when_deliver_typed_webhook(ctx: dict, report_type: str, mb_id: str) -> None:
    """System delivers a typed webhook report."""
    ctx["report_type"] = report_type
    env = ctx["env"]
    webhook = ctx.get("webhook_config", {})
    try:
        ctx["webhook_result"] = env.call_deliver(
            payload={"event": "delivery.update", "media_buy_id": mb_id, "notification_type": report_type},
            signing_secret=webhook.get("signing_secret"),
        )
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the system delivers three consecutive webhook reports for "{mb_id}"'))
def when_deliver_three_reports(ctx: dict, mb_id: str) -> None:
    """Deliver three consecutive webhook reports."""
    ctx["webhook_reports"] = []
    env = ctx["env"]
    for _ in range(3):
        try:
            result = env.call_deliver(media_buy_id=mb_id)
            ctx["webhook_reports"].append(result)
        except Exception as exc:
            ctx["error"] = exc
            break


@when("the system attempts to deliver a webhook report")
def when_attempt_webhook(ctx: dict) -> None:
    """System attempts webhook delivery."""
    env = ctx["env"]
    try:
        ctx["webhook_result"] = env.call_deliver()
    except Exception as exc:
        ctx["error"] = exc


@when("the system evaluates the circuit breaker state")
def when_evaluate_circuit_breaker(ctx: dict) -> None:
    """Evaluate circuit breaker state."""
    env = ctx["env"]
    # Ensure circuit breaker is in context for Then steps
    if "circuit_breaker" not in ctx:
        ctx["circuit_breaker"] = env.get_breaker()
    try:
        ctx["circuit_result"] = env.call_impl()
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse("the system delivers {n:d} successful probe reports"))
def when_deliver_probe_reports(ctx: dict, n: int) -> None:
    """Deliver n successful probe reports."""
    ctx["probe_count"] = n


@when("the system delivers a webhook report with retry")
def when_deliver_with_retry(ctx: dict) -> None:
    """System delivers webhook with retry on failure."""
    env = ctx["env"]
    try:
        ctx["webhook_result"] = env.call_send()
    except Exception as exc:
        ctx["error"] = exc
    # Expose circuit breaker for Then assertions (retry scenarios need it)
    if "circuit_breaker" not in ctx:
        ctx["circuit_breaker"] = env.get_breaker()


@when("the system validates the webhook configuration")
def when_validate_webhook_config(ctx: dict) -> None:
    """Validate webhook configuration via production WebhookVerifier."""
    from src.services.webhook_verification import WebhookVerifier

    secret = ctx.get("webhook_secret", "")
    try:
        WebhookVerifier(webhook_secret=secret)
        ctx["webhook_validated"] = True
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the webhook scheduler evaluates "{mb_id}"'))
def when_webhook_evaluates(ctx: dict, mb_id: str) -> None:
    """Webhook scheduler evaluates a media buy for delivery."""
    wh = ctx.get("webhook_config", {}).get(mb_id, {})
    if not wh.get("active"):
        ctx["webhook_skipped"] = True
    else:
        ctx["webhook_evaluated"] = mb_id


# ── Reporting dimensions When steps ─────────────────────────────────


@when(
    parsers.re(
        r'the Buyer Agent requests delivery metrics for "(?P<mb_id>[^"]+)" '
        r"with reporting_dimensions (?P<dims_json>\{.+\})"
    )
)
def when_request_with_dimensions(ctx: dict, mb_id: str, dims_json: str) -> None:
    """Request delivery metrics with reporting dimensions."""
    dims = json.loads(dims_json)
    # Extract sort_by from any dimension so Then fallback steps can verify
    for _dim_name, dim_opts in dims.items():
        if isinstance(dim_opts, dict) and "sort_by" in dim_opts:
            ctx["requested_sort_metric"] = dim_opts["sort_by"]
            break
    _call_delivery(ctx, media_buy_ids=[mb_id], reporting_dimensions=dims)


def _request_single_mb(ctx: dict, mb_id: str) -> None:
    """Shared: request delivery for a single media buy."""
    _call_delivery(ctx, media_buy_ids=[mb_id])


@when(parsers.parse('the Buyer Agent requests delivery metrics for "{mb_id}"'))
def when_request_single_mb(ctx: dict, mb_id: str) -> None:
    """Request delivery metrics for a single media buy."""
    _request_single_mb(ctx, mb_id)


@when(parsers.parse('the Buyer Agent requests delivery metrics for "{mb_id}" without attribution_window'))
def when_request_no_attribution(ctx: dict, mb_id: str) -> None:
    """Request without attribution window."""
    ctx.setdefault("omitted_fields", []).append("attribution_window")
    _request_single_mb(ctx, mb_id)


@when(
    parsers.re(
        r'the Buyer Agent requests delivery metrics for "(?P<mb_id>[^"]+)" '
        r"with attribution_window (?P<aw_json>\{.+\})"
    )
)
def when_request_with_attribution(ctx: dict, mb_id: str, aw_json: str) -> None:
    """Request with attribution window."""
    aw = json.loads(aw_json)
    _call_delivery(ctx, media_buy_ids=[mb_id], attribution_window=aw)


# ── Partition/boundary When steps ─────────────────────────────────


@when(parsers.parse("the Buyer Agent requests delivery metrics with reporting_dimensions {value}"))
def when_partition_dimensions(ctx: dict, value: str) -> None:
    """Partition test: reporting_dimensions value."""
    _dispatch_partition(ctx, "reporting_dimensions", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics at reporting_dimensions boundary {value}"))
def when_boundary_dimensions(ctx: dict, value: str) -> None:
    """Boundary test: reporting_dimensions value."""
    _dispatch_partition(ctx, "reporting_dimensions", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics with attribution_window {value}"))
def when_partition_attribution(ctx: dict, value: str) -> None:
    """Partition test: attribution_window value."""
    _dispatch_partition(ctx, "attribution_window", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics at attribution_window boundary {value}"))
def when_boundary_attribution(ctx: dict, value: str) -> None:
    """Boundary test: attribution_window value."""
    _dispatch_partition(ctx, "attribution_window", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics with include_package_daily_breakdown {value}"))
def when_partition_daily_breakdown(ctx: dict, value: str) -> None:
    """Partition test: daily breakdown value."""
    _dispatch_partition(ctx, "include_package_daily_breakdown", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics at daily breakdown boundary {value}"))
def when_boundary_daily_breakdown(ctx: dict, value: str) -> None:
    """Boundary test: daily breakdown value."""
    _dispatch_partition(ctx, "include_package_daily_breakdown", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics with account {value}"))
def when_partition_account(ctx: dict, value: str) -> None:
    """Partition test: account value."""
    _dispatch_partition(ctx, "account", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics at account boundary {value}"))
def when_boundary_account(ctx: dict, value: str) -> None:
    """Boundary test: account value."""
    _dispatch_partition(ctx, "account", value)


@when(parsers.re(r'the Buyer Agent requests delivery metrics with status_filter "(?P<partition_value>[^"]+)"'))
def when_partition_status_filter(ctx: dict, partition_value: str) -> None:
    """Partition test: status_filter value."""
    _call_delivery(ctx, status_filter=[partition_value])


@when(parsers.re(r'the Buyer Agent requests delivery metrics at status_filter boundary "(?P<boundary_value>[^"]+)"'))
def when_boundary_status_filter(ctx: dict, boundary_value: str) -> None:
    """Boundary test: status_filter value."""
    _call_delivery(ctx, status_filter=[boundary_value])


@when(parsers.re(r'the Buyer Agent requests delivery metrics with date range "(?P<partition>[^"]+)"'))
def when_partition_date_range(ctx: dict, partition: str) -> None:
    """Partition test: date range."""
    _dispatch_partition(ctx, "date_range", partition)


@when(parsers.re(r'the Buyer Agent requests delivery metrics at date boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_date_range(ctx: dict, boundary_point: str) -> None:
    """Boundary test: date range."""
    _dispatch_partition(ctx, "date_range", boundary_point)


@when(parsers.re(r'the webhook is configured with credentials "(?P<partition>[^"]+)"'))
def when_partition_credentials(ctx: dict, partition: str) -> None:
    """Partition test: webhook credentials."""
    _dispatch_partition(ctx, "credentials", partition)


@when(parsers.re(r'the webhook credentials are at boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_credentials(ctx: dict, boundary_point: str) -> None:
    """Boundary test: webhook credentials."""
    _dispatch_partition(ctx, "credentials", boundary_point)


@when(parsers.re(r'the Buyer Agent requests delivery metrics with resolution "(?P<partition>[^"]+)"'))
def when_partition_resolution(ctx: dict, partition: str) -> None:
    """Partition test: resolution."""
    _dispatch_partition(ctx, "resolution", partition)


@when(parsers.re(r'the Buyer Agent requests delivery metrics at resolution boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_resolution(ctx: dict, boundary_point: str) -> None:
    """Boundary test: resolution."""
    _dispatch_partition(ctx, "resolution", boundary_point)


@when(parsers.re(r'the Buyer Agent requests delivery metrics with principal "(?P<partition>[^"]+)"'))
def when_partition_principal(ctx: dict, partition: str) -> None:
    """Partition test: principal ownership."""
    _dispatch_partition(ctx, "principal", partition)


@when(parsers.re(r'the Buyer Agent requests delivery metrics at ownership boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_ownership(ctx: dict, boundary_point: str) -> None:
    """Boundary test: ownership."""
    _dispatch_partition(ctx, "ownership", boundary_point)


@when(parsers.re(r'the Buyer Agent queries delivery artifacts with sampling method "(?P<partition_value>[^"]+)"'))
def when_partition_sampling(ctx: dict, partition_value: str) -> None:
    """Partition test: sampling method."""
    _dispatch_partition(ctx, "sampling_method", partition_value)


@when(parsers.re(r'the Buyer Agent queries delivery artifacts at sampling boundary "(?P<boundary_value>[^"]+)"'))
def when_boundary_sampling(ctx: dict, boundary_value: str) -> None:
    """Boundary test: sampling method."""
    _dispatch_partition(ctx, "sampling_method", boundary_value)


@when(parsers.parse('the Buyer Agent queries delivery metrics for media buy "{mb_id}"'))
def when_query_single_mb(ctx: dict, mb_id: str) -> None:
    """Query delivery metrics for a single media buy (sandbox scenarios)."""
    ctx.setdefault("query_variant", True)
    _request_single_mb(ctx, mb_id)


@when("the Buyer Agent queries delivery metrics for a non-existent media buy")
def when_query_nonexistent(ctx: dict) -> None:
    """Query delivery metrics for a non-existent media buy."""
    _call_delivery(ctx, media_buy_ids=["mb-nonexistent"])


@when(parsers.parse('the Buyer Agent requests delivery metrics for media_buy_ids ["{mb_id}"]'))
def when_request_single_id_quoted(ctx: dict, mb_id: str) -> None:
    """Request for a single media buy ID (quoted format)."""
    ctx.setdefault("id_format", "quoted")
    _request_single_mb(ctx, mb_id)


@when(
    parsers.re(
        r'the Buyer Agent requests delivery metrics for "(?P<mb_id>[^"]+)" '
        r"without (?P<field>\w+)"
    )
)
def when_request_without_field(ctx: dict, mb_id: str, field: str) -> None:
    """Request without a specific optional field (attribution_window etc)."""
    ctx.setdefault("omitted_fields", []).append(field)
    _request_single_mb(ctx, mb_id)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — delivery-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the response should include delivery data for "{mb_id}"'))
def then_includes_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response includes delivery data for the given media buy."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id in mb_ids, f"Expected delivery data for '{mb_id}', got: {mb_ids}"


@then(parsers.parse('the response should include delivery data for "{mb_id1}" and "{mb_id2}"'))
def then_includes_delivery_data_both(ctx: dict, mb_id1: str, mb_id2: str) -> None:
    """Assert response includes delivery data for both media buys."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id1 in mb_ids, f"Expected delivery data for '{mb_id1}', got: {mb_ids}"
    assert mb_id2 in mb_ids, f"Expected delivery data for '{mb_id2}', got: {mb_ids}"


@then(parsers.parse('the response should include delivery data for "{mb_id}" only'))
def then_includes_delivery_data_only(ctx: dict, mb_id: str) -> None:
    """Assert response includes delivery data for ONLY the given media buy."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_ids == [mb_id], f"Expected only '{mb_id}', got: {mb_ids}"


@then(parsers.parse('the response should NOT include delivery data for "{mb_id}"'))
def then_excludes_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response does NOT include delivery data for the media buy."""
    resp = ctx.get("response")
    if resp is None:
        return  # No response at all = not included
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id not in mb_ids, f"Expected no delivery data for '{mb_id}', but found it"


@then(parsers.parse('the response should not include delivery data for "{mb_id}"'))
def then_no_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response does not include delivery data for the media buy."""
    resp = ctx.get("response")
    if resp is None:
        return
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id not in mb_ids, f"Expected no delivery data for '{mb_id}'"


@then("the response should have an empty media_buy_deliveries array")
def then_empty_deliveries(ctx: dict) -> None:
    """Assert response has empty media_buy_deliveries."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) == 0, f"Expected empty deliveries, got {len(deliveries)}"


@then("the delivery data should include impressions, spend, and clicks")
def then_has_metrics(ctx: dict) -> None:
    """Assert delivery data includes core metrics."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data to check"
    d = deliveries[0]
    totals = getattr(d, "totals", None)
    assert totals is not None, "Delivery data missing totals"
    assert isinstance(totals.impressions, (int, float)), (
        f"Expected numeric impressions, got {type(totals.impressions).__name__}"
    )
    assert isinstance(totals.spend, (int, float)), f"Expected numeric spend, got {type(totals.spend).__name__}"
    # clicks is optional per schema but step text claims it should be included
    assert totals.clicks is not None, "Totals missing clicks (step expects impressions, spend, and clicks)"


@then("the delivery data should include package-level breakdowns")
def then_has_packages(ctx: dict) -> None:
    """Assert delivery data includes package-level breakdowns."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data to check"
    d = deliveries[0]
    packages = getattr(d, "by_package", None)
    assert packages is not None, "Delivery data missing by_package"
    assert len(packages) > 0, "Package breakdown is empty"


@then("the response should include the reporting period start and end dates")
def then_has_reporting_period(ctx: dict) -> None:
    """Assert response includes reporting period (top-level, per AdCP spec)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    assert period.start is not None, "Reporting period start is None"
    assert period.end is not None, "Reporting period end is None"


@then(parsers.parse('the response should include the media buy status "{status}"'))
def then_has_mb_status(ctx: dict, status: str) -> None:
    """Assert response includes the expected media buy status."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data to check"
    d = deliveries[0]
    actual_status = getattr(d, "status", None)
    assert actual_status == status, f"Expected status '{status}', got '{actual_status}'"


@then("the response should include aggregated totals across both media buys")
def then_has_aggregated_totals(ctx: dict) -> None:
    """Assert response includes aggregated totals."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    agg = getattr(resp, "aggregated_totals", None)
    assert agg is not None, "Response missing aggregated_totals"
    # Verify aggregation was across multiple media buys
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) >= 2, (
        f"Step claims 'across both media buys' but only {len(deliveries)} deliveries in response"
    )
    # Verify aggregated totals contain at least one numeric metric
    agg_dict = agg.model_dump() if hasattr(agg, "model_dump") else vars(agg)
    numeric_fields = {k: v for k, v in agg_dict.items() if isinstance(v, (int, float)) and v != 0}
    assert len(numeric_fields) > 0, f"aggregated_totals has no non-zero numeric fields: {agg_dict}"


@then("the aggregated impressions should equal the sum of individual impressions")
def then_aggregated_impressions(ctx: dict) -> None:
    """Assert aggregated impressions equal sum of individual values."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    individual_sum = sum(getattr(getattr(d, "totals", None), "impressions", 0.0) for d in deliveries)
    agg = getattr(resp, "aggregated_totals", None)
    assert agg is not None, "Missing aggregated_totals"
    agg_impressions = getattr(agg, "impressions", 0.0)
    assert agg_impressions == individual_sum, f"Aggregated impressions {agg_impressions} != sum {individual_sum}"


@then("the aggregated spend should equal the sum of individual spend")
def then_aggregated_spend(ctx: dict) -> None:
    """Assert aggregated spend equals sum of individual values."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    individual_sum = sum(getattr(getattr(d, "totals", None), "spend", 0.0) for d in deliveries)
    agg = getattr(resp, "aggregated_totals", None)
    assert agg is not None, "Missing aggregated_totals"
    agg_spend = getattr(agg, "spend", 0.0)
    assert agg_spend == individual_sum, f"Aggregated spend {agg_spend} != sum {individual_sum}"


@then(parsers.parse('the response should not include an error for "{mb_id}"'))
def then_no_error_for_mb(ctx: dict, mb_id: str) -> None:
    """Assert no error was returned for a specific media buy ID."""
    assert "error" in ctx or "response" in ctx, "Neither error nor response in ctx — test setup failed"
    # If a general error occurred, check it's not about this specific mb_id
    error = ctx.get("error")
    if error is not None:
        error_msg = str(error).lower()
        assert mb_id.lower() not in error_msg, f"Error mentions '{mb_id}': {error}"
    # If response exists, verify delivery for this mb_id has no error
    resp = ctx.get("response")
    if resp is not None:
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        for d in deliveries:
            d_id = getattr(d, "media_buy_id", None)
            if d_id == mb_id:
                d_error = getattr(d, "error", None)
                assert d_error is None, f"Delivery for '{mb_id}' has error: {d_error}"


@then(parsers.parse('no error should be returned for "{mb_id}"'))
def then_no_error_for_mb_alt(ctx: dict, mb_id: str) -> None:
    """Assert no error was returned for a specific media buy ID (alt phrasing)."""
    assert "error" in ctx or "response" in ctx, "Neither error nor response in ctx — test setup failed"
    # If a general error occurred, check it's not about this specific mb_id
    error = ctx.get("error")
    if error is not None:
        error_msg = str(error).lower()
        assert mb_id.lower() not in error_msg, f"Error mentions '{mb_id}': {error}"
    # If response exists, verify delivery for this mb_id has no error
    resp = ctx.get("response")
    if resp is not None:
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        for d in deliveries:
            d_id = getattr(d, "media_buy_id", None)
            if d_id == mb_id:
                d_error = getattr(d, "error", None)
                assert d_error is None, f"Delivery for '{mb_id}' has error: {d_error}"


@then(parsers.parse('the response should include only media buys with status "{status}"'))
def then_only_status(ctx: dict, status: str) -> None:
    """Assert all returned media buys have the expected status.

    Guards against vacuous pass on empty list: if the Given step created
    media buys matching this status, the response must be non-empty.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    # Check if any Given-step media buy has this status — if so, results must be non-empty
    setup_buys = ctx.get("media_buys", {})
    has_matching_setup = any(mb.get("status") == status for mb in setup_buys.values())
    if has_matching_setup:
        assert len(deliveries) > 0, (
            f"Expected non-empty deliveries for status '{status}' "
            f"(setup has matching media buys: {[k for k, v in setup_buys.items() if v.get('status') == status]})"
        )
    else:
        assert len(deliveries) == 0, (
            f"Expected empty deliveries for status '{status}' (no setup data matches), "
            f"but got {len(deliveries)} results"
        )
    for d in deliveries:
        actual = getattr(d, "status", None)
        assert actual == status, f"Expected status '{status}', got '{actual}' for {d.media_buy_id}"


# ── Reporting period assertions ────────────────────────────────────


@then(parsers.parse('the response reporting_period start should be "{date}"'))
def then_period_start(ctx: dict, date: str) -> None:
    """Assert reporting period start date (top-level, per AdCP spec)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.start)[:10]
    assert actual == date, f"Expected period start '{date}', got '{actual}'"


@then(parsers.parse('the response reporting_period end should be "{date}"'))
def then_period_end(ctx: dict, date: str) -> None:
    """Assert reporting period end date (top-level, per AdCP spec)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.end)[:10]
    assert actual == date, f"Expected period end '{date}', got '{actual}'"


@then("the response reporting_period end should be today's date")
def then_period_end_today(ctx: dict) -> None:
    """Assert reporting period end is today (top-level, per AdCP spec)."""
    from datetime import UTC, datetime

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.end)[:10]
    assert actual == today, f"Expected period end '{today}', got '{actual}'"


# ── Webhook Then steps ─────────────────────────────────────────────


@then("the system should POST a delivery report to the configured webhook URL")
def then_webhook_post(ctx: dict) -> None:
    """Assert webhook POST was made to the configured URL with a delivery report payload."""
    env = ctx["env"]
    assert env.mock["post"].called, "Expected webhook POST but none was made"
    # Verify the POST was made to the configured webhook URL
    call_args = env.mock["post"].call_args
    posted_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
    assert posted_url is not None, "POST call missing URL argument"
    assert "webhook_config" in ctx, "webhook_config must be set by Given step"
    webhook_config = ctx["webhook_config"]
    configured_urls = [wh.get("url") for wh in webhook_config.values() if isinstance(wh, dict)]
    assert posted_url in configured_urls, f"POST URL '{posted_url}' not in configured webhook URLs: {configured_urls}"
    # Verify payload is a delivery report (must contain media_buy_deliveries)
    payload = call_args.kwargs.get("json") or (call_args[1].get("json", {}) if len(call_args) > 1 else {})
    assert isinstance(payload, dict), f"Expected dict payload, got {type(payload).__name__}"
    assert "media_buy_deliveries" in payload, (
        f"Payload missing 'media_buy_deliveries' key (step requires delivery report), got keys: {list(payload.keys())}"
    )


@then(parsers.parse('the payload should include delivery metrics for "{mb_id}"'))
def then_webhook_payload_has_metrics(ctx: dict, mb_id: str) -> None:
    """Assert webhook payload includes delivery metrics for the media buy."""
    payload = _get_webhook_payload(ctx)
    deliveries = payload.get("media_buy_deliveries", [])
    mb_ids = [d.get("media_buy_id") for d in deliveries]
    assert mb_id in mb_ids, f"Expected metrics for '{mb_id}' in payload, got: {mb_ids}"
    # Extract the matching delivery entry and verify it contains actual metrics
    delivery = next(d for d in deliveries if d.get("media_buy_id") == mb_id)
    totals = delivery.get("totals")
    assert isinstance(totals, dict), f"Delivery for '{mb_id}' missing 'totals' dict, got keys: {list(delivery.keys())}"
    # impressions and spend are always present per webhook_delivery_service
    assert "impressions" in totals, f"totals missing 'impressions', got keys: {list(totals.keys())}"
    assert totals["impressions"] is not None, "totals['impressions'] is None"
    assert "spend" in totals, f"totals missing 'spend', got keys: {list(totals.keys())}"
    assert totals["spend"] is not None, "totals['spend'] is None"


@then("the payload should include the reporting_period")
def then_webhook_payload_has_period(ctx: dict) -> None:
    """Assert webhook payload includes reporting period with valid timestamps."""
    from datetime import datetime as dt

    payload = _get_webhook_payload(ctx)
    assert "reporting_period" in payload, f"Payload missing reporting_period, keys: {list(payload.keys())}"
    period = payload["reporting_period"]
    assert isinstance(period, dict), f"Expected reporting_period to be a dict, got {type(period).__name__}: {period}"
    assert "start" in period, f"reporting_period missing 'start' key, got keys: {list(period.keys())}"
    assert "end" in period, f"reporting_period missing 'end' key, got keys: {list(period.keys())}"
    # Values must be parseable ISO-8601 timestamps, not just present
    start = dt.fromisoformat(period["start"])
    end = dt.fromisoformat(period["end"])
    assert end >= start, f"reporting_period end ({period['end']}) is before start ({period['start']})"


@then(parsers.parse('the payload notification_type should be "{ntype}"'))
def then_notification_type(ctx: dict, ntype: str) -> None:
    """Assert notification type."""
    payload = _get_webhook_payload(ctx)
    actual = payload.get("notification_type")
    assert actual == ntype, f"Expected notification_type '{ntype}', got '{actual}'"


@then(parsers.re(r"the payload (?P<next_expected>.+) include next_expected_at"))
def then_next_expected(ctx: dict, next_expected: str) -> None:
    """Assert next_expected_at presence/absence with value validation."""
    from datetime import datetime as dt

    payload = _get_webhook_payload(ctx)
    should_include = "should" in next_expected and "not" not in next_expected
    if should_include:
        assert "next_expected_at" in payload, "Payload missing next_expected_at"
        value = payload["next_expected_at"]
        assert value is not None, "next_expected_at is present but None"
        # Must be a valid ISO-8601 timestamp
        dt.fromisoformat(value)  # raises ValueError if unparseable
    else:
        assert payload.get("next_expected_at") is None, (
            f"Expected no next_expected_at, got {payload.get('next_expected_at')}"
        )


@then("each report should have a higher sequence_number than the previous")
def then_sequence_ascending(ctx: dict) -> None:
    """Assert sequence numbers are ascending across consecutive reports."""
    reports = ctx.get("webhook_reports", [])
    assert len(reports) > 1, "Need at least 2 reports to check ascending sequence"
    env = ctx["env"]
    sequences = []
    for call in env.mock["post"].call_args_list:
        payload = call.kwargs.get("json") or call[1].get("json", {})
        seq = payload.get("sequence_number")
        sequences.append(seq)
    # Every report must have a sequence number
    none_indices = [i for i, s in enumerate(sequences) if s is None]
    assert not none_indices, f"Reports at indices {none_indices} missing sequence_number"
    assert len(sequences) >= 2, f"Expected >= 2 sequence numbers, got {len(sequences)}"
    for i in range(1, len(sequences)):
        assert sequences[i] > sequences[i - 1], f"Sequence numbers not ascending: {sequences[i - 1]} -> {sequences[i]}"


@then("the first sequence_number should be >= 1")
def then_first_sequence(ctx: dict) -> None:
    """Assert first sequence number is at least 1."""
    payload = _get_webhook_payload(ctx)
    seq = payload.get("sequence_number")
    assert seq is not None, "Payload missing sequence_number"
    assert seq >= 1, f"Expected sequence_number >= 1, got {seq}"


@then('the payload should not include "aggregated_totals" field')
def then_no_aggregated_in_payload(ctx: dict) -> None:
    """Assert webhook payload does not include aggregated totals."""
    payload = _get_webhook_payload(ctx)
    assert "aggregated_totals" not in payload, "Webhook payload should not include aggregated_totals"


@then("the system should retry up to 3 times")
def then_retry_3_times(ctx: dict) -> None:
    """Assert retry count: at least 1 retry, at most 3 retries."""
    env = ctx["env"]
    call_count = env.mock["post"].call_count
    assert call_count >= 2, f"Expected at least 2 calls (1 initial + at least 1 retry), got {call_count}"
    assert call_count <= 4, f"Expected at most 4 calls (1 initial + up to 3 retries), got {call_count}"


@then("retries should use exponential backoff (1s, 2s, 4s + jitter)")
def then_exponential_backoff(ctx: dict) -> None:
    """Assert exponential backoff pattern by inspecting sleep intervals."""
    env = ctx["env"]
    call_count = env.mock["post"].call_count
    assert call_count > 1, f"Expected retries (backoff needs >1 attempt), got {call_count} call(s)"
    # Verify sleep was called with exponential intervals (1s, 2s, 4s base + jitter)
    sleep_mock = env.mock.get("sleep")
    assert sleep_mock is not None, "Harness bug: sleep mock not wired — backoff cannot be verified"
    assert sleep_mock.call_count > 0, "Sleep mock exists but was never called — backoff was not applied"
    intervals = [call.args[0] for call in sleep_mock.call_args_list]
    # Each interval should be >= the base exponential value (2^i)
    for i, interval in enumerate(intervals):
        base = 2**i  # 1, 2, 4, ...
        assert interval >= base, f"Backoff interval {i} was {interval}s, expected >= {base}s (2^{i})"


@then("the system should retry up to 3 times with exponential backoff")
def then_retry_with_backoff(ctx: dict) -> None:
    """Assert retry count (2-4 calls) and exponential backoff pattern."""
    env = ctx["env"]
    call_count = env.mock["post"].call_count
    assert call_count >= 2, f"Expected at least 2 calls (1 initial + at least 1 retry), got {call_count}"
    assert call_count <= 4, f"Expected at most 4 calls (1 initial + up to 3 retries), got {call_count}"
    # Verify exponential backoff intervals via sleep mock
    sleep_mock = env.mock.get("sleep")
    assert sleep_mock is not None, "Sleep mock must be wired for backoff verification"
    assert sleep_mock.call_count > 0, "Sleep mock exists but was never called — backoff was not applied"
    intervals = [call.args[0] for call in sleep_mock.call_args_list]
    for i, interval in enumerate(intervals):
        base = 2**i  # 1, 2, 4, ...
        assert interval >= base, f"Backoff interval {i} was {interval}s, expected >= {base}s (2^{i})"


@then("the system should not retry the delivery")
def then_no_retry(ctx: dict) -> None:
    """Assert no retry was attempted."""
    env = ctx["env"]
    assert env.mock["post"].call_count <= 1, "Expected no retries"


@then("the system should log the authentication rejection")
def then_log_auth_rejection(ctx: dict) -> None:
    """Assert auth rejection was logged with auth/rejection keywords."""
    env = ctx["env"]
    logger_mock = env.mock.get("logger")
    assert logger_mock is not None, "Logger mock must be wired in the harness env.mock['logger']"
    # Collect all log messages from warning and error calls
    all_calls = list(logger_mock.warning.call_args_list) + list(logger_mock.error.call_args_list)
    assert len(all_calls) > 0, "Expected logger.warning() or logger.error() to be called for auth rejection"
    # At least one log message must mention auth-related keywords
    messages = []
    for call in all_calls:
        msg = str(call.args[0]) if call.args else ""
        messages.append(msg)
    auth_keywords = {"auth", "unauthorized", "401", "credential", "blocked"}
    found = any(any(kw in msg.lower() for kw in auth_keywords) for msg in messages)
    assert found, f"Expected a log message containing auth/rejection keywords ({auth_keywords}), but got: {messages}"


@then("the webhook should be marked as failed")
def then_webhook_marked_failed(ctx: dict) -> None:
    """Assert webhook delivery returned failure with status 'failed'."""
    result = ctx.get("webhook_result")
    assert result is not None, (
        f"Expected webhook_result in ctx — When step must store the delivery result. ctx keys: {list(ctx.keys())}"
    )
    success, details = result
    assert not success, f"Expected webhook to fail, but it succeeded: {details}"
    assert details.get("status") == "failed", f"Expected status 'failed', got '{details.get('status')}'"


@then(parsers.parse('the circuit breaker should be in "{state}" state'))
def then_circuit_breaker_state(ctx: dict, state: str) -> None:
    """Assert circuit breaker state."""
    breaker = ctx.get("circuit_breaker")
    assert breaker is not None, "No circuit breaker in context"
    actual = breaker.state.value.upper()
    expected = state.upper()
    assert actual == expected, f"Expected circuit breaker state '{expected}', got '{actual}'"


@then("subsequent scheduled deliveries should be suppressed")
def then_deliveries_suppressed(ctx: dict) -> None:
    """Assert deliveries are suppressed: breaker blocks AND no POST after open."""
    breaker = ctx.get("circuit_breaker")
    assert breaker is not None, "No circuit breaker in context"
    assert not breaker.can_attempt(), "Expected circuit breaker to block attempts (OPEN state)"
    # Verify no additional POST was made after the breaker opened
    env = ctx["env"]
    post_mock = env.mock["post"]
    calls_before_open = ctx.get("calls_before_breaker_open", 0)
    assert post_mock.call_count == calls_before_open, (
        f"Expected no POST calls after breaker opened "
        f"(calls before open: {calls_before_open}, total: {post_mock.call_count})"
    )


@then(parsers.parse('the circuit breaker should transition to "{state}"'))
def then_circuit_transition(ctx: dict, state: str) -> None:
    """Assert circuit breaker transitions to expected state."""
    breaker = ctx.get("circuit_breaker")
    assert breaker is not None, "No circuit breaker in context"
    actual = breaker.state.value.upper()
    expected = state.upper()
    assert actual == expected, f"Expected circuit breaker to transition to '{expected}', got '{actual}'"


@then("the system should attempt a single probe delivery")
def then_single_probe(ctx: dict) -> None:
    """Assert a single probe delivery was attempted in half-open state."""
    breaker = ctx.get("circuit_breaker")
    assert breaker is not None, "No circuit breaker in context"
    # Guard: the Given/When step must have snapshotted the call count before
    # entering half-open state.  A missing key means a test-setup bug.
    assert "calls_before_half_open" in ctx, (
        "ctx['calls_before_half_open'] was never set — "
        "the Given/When step must snapshot post_mock.call_count before half-open"
    )
    # Verify exactly one probe POST was made after entering half-open state
    env = ctx["env"]
    post_mock = env.mock["post"]
    calls_before_half_open = ctx["calls_before_half_open"]
    probe_calls = post_mock.call_count - calls_before_half_open
    assert probe_calls == 1, (
        f"Expected exactly 1 probe delivery after half-open, "
        f"got {probe_calls} (total: {post_mock.call_count}, "
        f"before half-open: {calls_before_half_open})"
    )


@then("normal scheduled deliveries should resume")
def then_deliveries_resume(ctx: dict) -> None:
    """Assert deliveries resume after circuit closes."""
    breaker = ctx.get("circuit_breaker")
    assert breaker is not None, "No circuit breaker in context"
    assert breaker.can_attempt(), "Expected circuit breaker to allow deliveries (CLOSED state)"
    # Verify deliveries actually resumed — POST calls were made after circuit closed
    env = ctx["env"]
    post_mock = env.mock["post"]
    calls_before_close = ctx.get("calls_before_breaker_close", 0)
    resumed_calls = post_mock.call_count - calls_before_close
    assert resumed_calls > 0, (
        f"Circuit breaker is CLOSED but no delivery POST was made after close "
        f"(total calls: {post_mock.call_count}, before close: {calls_before_close})"
    )


@then("the delivery should be recorded as successful")
def then_delivery_successful(ctx: dict) -> None:
    """Assert delivery was recorded as successful."""
    result = ctx.get("webhook_result") or ctx.get("circuit_result")
    assert result is not None, (
        "No success indicator found: neither 'webhook_result' nor 'circuit_result' "
        "in context — cannot verify delivery was recorded as successful"
    )
    if isinstance(result, tuple):
        success, details = result
        assert success, f"Expected successful delivery, got failure: {details}"
    elif isinstance(result, bool):
        assert result, "Expected delivery to succeed"
    else:
        raise AssertionError(
            f"Unexpected result type {type(result).__name__}: {result!r} — expected tuple (success, details) or bool"
        )


@then("the circuit breaker state should remain healthy")
def then_circuit_healthy(ctx: dict) -> None:
    """Assert circuit breaker is in CLOSED state after retry sequence.

    The scenario involves a fail-then-succeed retry, so a circuit breaker
    MUST exist in context. The None fallback was hiding infrastructure bugs.
    """
    breaker = ctx.get("circuit_breaker")
    assert breaker is not None, (
        "Expected circuit_breaker in ctx — scenario involves retries, "
        "so the breaker must be exercised. ctx keys: " + str(list(ctx.keys()))
    )
    actual = breaker.state.value.upper()
    assert actual == "CLOSED", f"Expected healthy (CLOSED) circuit breaker, got '{actual}'"


@then("the configuration should be rejected")
def then_config_rejected(ctx: dict) -> None:
    """Assert configuration was rejected."""
    assert "error" in ctx, "Expected config rejection error"


@then("the error should indicate minimum credential length is 32 characters")
def then_error_min_credential_length(ctx: dict) -> None:
    """Assert error mentions minimum credential length with context."""
    error = ctx.get("error")
    assert error is not None, "No error recorded"
    msg = str(error).lower()
    assert "32" in msg, f"Expected '32' in error: {error}"
    # Must mention it's a minimum/length constraint, not just any number 32
    assert any(kw in msg for kw in ("minim", "at least", "length", "short", "character")), (
        f"Error mentions '32' but lacks context about minimum length requirement: {error}"
    )


@then("the configuration should be accepted")
def then_config_accepted(ctx: dict) -> None:
    """Assert configuration was accepted (webhook/circuit-breaker config)."""
    assert "error" not in ctx, f"Config rejected: {ctx.get('error')}"


# ── HMAC / auth header assertions ─────────────────────────────────


@then(parsers.parse('the request should include header "{header}" with hex-encoded HMAC'))
def then_hmac_header(ctx: dict, header: str) -> None:
    """Assert the exact requested HMAC header is present with valid hex-encoded value."""
    headers = _get_webhook_headers(ctx)
    assert header in headers, f"Missing HMAC header '{header}', got headers: {list(headers.keys())}"
    sig_value = headers[header]
    assert sig_value.startswith("sha256="), f"Expected sha256= prefix in header '{header}', got '{sig_value}'"
    hex_part = sig_value[len("sha256=") :]
    assert re.fullmatch(r"[0-9a-fA-F]+", hex_part), (
        f"Expected hex-encoded HMAC after 'sha256=' in header '{header}', got non-hex value: '{hex_part}'"
    )


@then(parsers.parse('the request should include header "{header}" with ISO timestamp'))
def then_timestamp_header(ctx: dict, header: str) -> None:
    """Assert timestamp header present in webhook request."""
    headers = _get_webhook_headers(ctx)
    assert header in headers, f"Missing timestamp header '{header}', got headers: {list(headers.keys())}"
    ts_value = headers[header]
    # Only accept ISO 8601 format (e.g. 2024-01-15T10:30:00Z)
    assert re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", ts_value), (
        f"Expected ISO 8601 timestamp in header '{header}', got '{ts_value}'"
    )


@then('the HMAC should be computed over "timestamp.payload" concatenation')
def then_hmac_computation(ctx: dict) -> None:
    """Assert HMAC is computed over timestamp.payload concatenation.

    Verifies independently (no circular use of production WebhookAuthenticator):
    1. Extract timestamp and signature from headers
    2. Serialize payload with sorted keys, compact separators
    3. Concatenate as f"{timestamp}.{payload_str}"
    4. Compute HMAC-SHA256 with the shared secret
    5. Compare against the signature in the header
    """
    import hashlib
    import hmac as hmac_mod
    import json as json_mod

    headers = _get_webhook_headers(ctx)
    payload = _get_webhook_payload(ctx)

    # Check both possible header name conventions
    sig_header = "X-Webhook-Signature" if "X-Webhook-Signature" in headers else "X-ADCP-Signature"
    ts_header = "X-Webhook-Timestamp" if "X-Webhook-Timestamp" in headers else "X-ADCP-Timestamp"
    assert sig_header in headers, f"Missing signature header, got: {list(headers.keys())}"
    assert ts_header in headers, f"Missing timestamp header, got: {list(headers.keys())}"

    secret = ctx["webhook_secret"]
    assert secret, "webhook_secret is empty — test setup must provide a non-empty secret"

    timestamp = headers[ts_header]
    signature_raw = headers[sig_header]

    # Strip "sha256=" prefix if present
    signature_hex = signature_raw[len("sha256=") :] if signature_raw.startswith("sha256=") else signature_raw

    # Independent HMAC computation: timestamp.payload (the documented contract)
    payload_str = json_mod.dumps(payload, separators=(",", ":"), sort_keys=True)
    signed_message = f"{timestamp}.{payload_str}"
    expected_hex = hmac_mod.new(secret.encode("utf-8"), signed_message.encode("utf-8"), hashlib.sha256).hexdigest()

    assert hmac_mod.compare_digest(signature_hex, expected_hex), (
        f"HMAC verification failed — signature does not match 'timestamp.payload' concatenation.\n"
        f"  Timestamp: {timestamp}\n"
        f"  Payload (first 100 chars): {payload_str[:100]}\n"
        f"  Expected signature: {expected_hex[:16]}...\n"
        f"  Actual signature:   {signature_hex[:16]}..."
    )


@then(parsers.parse('the request should include header "{header}" with the bearer token'))
def then_bearer_header(ctx: dict, header: str) -> None:
    """Assert bearer token header contains the configured token value."""
    headers = _get_webhook_headers(ctx)
    assert header in headers, f"Missing header '{header}', got headers: {list(headers.keys())}"
    value = headers[header]
    assert value.startswith("Bearer "), f"Expected Bearer token, got '{value}'"
    # Verify the actual token matches the one configured in the Given step
    configured_token = ctx.get("webhook_bearer_token")
    if configured_token:
        expected = f"Bearer {configured_token}"
        assert value == expected, f"Expected '{expected}', got '{value}'"


# ── Response field presence assertions ─────────────────────────────


@then('the response should contain "media_buy_deliveries" field')
def then_has_deliveries_field(ctx: dict) -> None:
    """Assert response has non-empty media_buy_deliveries field.

    The scenarios using this step inject delivery data via Given steps,
    so an empty list means the delivery pipeline failed silently.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    assert isinstance(resp.media_buy_deliveries, list), (
        f"Expected media_buy_deliveries to be a list, got {type(resp.media_buy_deliveries).__name__}"
    )
    assert len(resp.media_buy_deliveries) > 0, (
        "Expected non-empty media_buy_deliveries — Given step injected delivery data"
    )


@then('the response should not contain "errors" field')
def then_no_errors_field(ctx: dict) -> None:
    """Assert response has no errors field."""
    resp = ctx.get("response")
    assert resp is not None, "No response object — cannot check for 'errors' field"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else vars(resp)
    assert "errors" not in resp_dict or resp_dict["errors"] is None, (
        f"Response contains 'errors' field: {resp_dict.get('errors')}"
    )


@then('the response should contain "errors" field')
def then_has_errors_field(ctx: dict) -> None:
    """Assert the response object has a non-empty errors field."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response with errors field, but got exception instead: {ctx.get('error')}"
    assert hasattr(resp, "errors"), "Response object missing 'errors' attribute"
    assert resp.errors, f"Expected non-empty errors, got {resp.errors!r}"


@then('the response should not contain "media_buy_deliveries" field')
def then_no_deliveries_field(ctx: dict) -> None:
    """Assert response has no deliveries (error only)."""
    resp = ctx.get("response")
    if resp is None:
        # Error was raised, so no response object exists — no deliveries field
        assert "error" in ctx, "No response and no error — unexpected state"
        return
    # Response exists — verify the field is truly absent (None), not just empty
    deliveries = getattr(resp, "media_buy_deliveries", None)
    assert deliveries is None, f"Expected 'media_buy_deliveries' to be absent (None), got {deliveries!r}"


# ── Error ownership assertions ─────────────────────────────────────


@then(parsers.parse("the error should NOT reveal that the media buy exists"))
def then_error_no_reveal(ctx: dict) -> None:
    """Assert error does not leak existence information.

    Security property: an ownership mismatch must be indistinguishable from
    a genuine not-found.  We verify:
      1. The error message contains a generic "not found" phrase.
      2. The message does NOT contain specific media_buy_ids from ctx.
      3. The message does NOT contain the tenant_id.
    """
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()

    # 1. Must look like a generic not-found message
    assert "not found" in msg or "not_found" in msg, f"Error should match a generic 'not found' pattern, got: {error}"

    # 2. Must not leak specific media_buy_ids
    for mb_id, mb_info in ctx.get("media_buys", {}).items():
        concrete_id = mb_info.get("media_buy_id", mb_id)
        assert concrete_id.lower() not in msg, f"Error leaks media_buy_id '{concrete_id}': {error}"

    # 3. Must not leak tenant_id
    tenant_id = ctx.get("tenant_id", "")
    if tenant_id:
        assert tenant_id.lower() not in msg, f"Error leaks tenant_id '{tenant_id}': {error}"


# ── Webhook skip assertions ─────────────────────────────────────────


@then(parsers.parse('the system should skip "{mb_id}" (no webhook to deliver to)'))
def then_skip_no_webhook(ctx: dict, mb_id: str) -> None:
    """Assert no POST was made for the given media buy (webhook not configured)."""
    env = ctx["env"]
    post_mock = env.mock["post"]
    # Check that no POST call was made containing this mb_id in its payload
    for call in post_mock.call_args_list:
        call_payload = call.kwargs.get("json") or (call[1].get("json", {}) if len(call) > 1 else {})
        payload_mb_id = call_payload.get("media_buy_id", "")
        assert payload_mb_id != mb_id, (
            f"Expected no webhook POST for '{mb_id}', but found POST with media_buy_id='{payload_mb_id}' in payload"
        )
    # Also verify the webhook config shows this mb_id as inactive/missing
    wh_config = ctx.get("webhook_config", {}).get(mb_id, {})
    assert not wh_config.get("active", False), (
        f"Expected webhook for '{mb_id}' to be inactive, but config shows active=True"
    )


@then("no delivery attempt should be made")
def then_no_delivery_attempt(ctx: dict) -> None:
    """Assert no delivery attempt was made."""
    env = ctx["env"]
    assert not env.mock["post"].called, "Expected no delivery attempt"


# ── Reporting dimension assertions ─────────────────────────────────


@then(parsers.parse('the response packages should include "{field}" breakdown arrays'))
def then_packages_include_breakdown(ctx: dict, field: str) -> None:
    """Assert package breakdowns include the named field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    packages = getattr(deliveries[0], "by_package", None) or []
    assert len(packages) > 0, "No packages in delivery"
    pkg = packages[0]
    # The breakdown field is named by_ + dimension (e.g., by_device_type)
    breakdown_key = f"by_{field}" if not field.startswith("by_") else field
    pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
    assert breakdown_key in pkg_dict, f"Package missing '{breakdown_key}', fields: {list(pkg_dict.keys())}"
    breakdown = pkg_dict[breakdown_key]
    assert isinstance(breakdown, list), f"Expected '{breakdown_key}' to be an array, got {type(breakdown).__name__}"
    assert len(breakdown) > 0, f"'{breakdown_key}' is an empty array — expected entries"


@then(parsers.parse('the response packages should NOT include "{field}" breakdown arrays'))
def then_packages_exclude_breakdown(ctx: dict, field: str) -> None:
    """Assert ALL packages across ALL deliveries do not include the named breakdown."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    breakdown_key = f"by_{field}" if not field.startswith("by_") else field
    total_packages = 0
    for d_idx, delivery in enumerate(deliveries):
        packages = getattr(delivery, "by_package", None) or []
        for p_idx, pkg in enumerate(packages):
            total_packages += 1
            pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
            assert breakdown_key not in pkg_dict or pkg_dict[breakdown_key] is None, (
                f"Delivery[{d_idx}] package[{p_idx}] should NOT include '{breakdown_key}'"
            )
    assert total_packages > 0, "No packages found across any delivery"


@then(parsers.parse('the response packages should include "{field}" with at most {n:d} entries'))
def then_packages_limited(ctx: dict, field: str, n: int) -> None:
    """Assert breakdown limited to n entries across ALL deliveries and packages."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    breakdown_key = f"by_{field}" if not field.startswith("by_") else field
    total_packages = 0
    for d_idx, delivery in enumerate(deliveries):
        packages = getattr(delivery, "by_package", None) or []
        for p_idx, pkg in enumerate(packages):
            total_packages += 1
            pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
            breakdown = pkg_dict.get(breakdown_key, [])
            assert len(breakdown) <= n, (
                f"Delivery[{d_idx}] package[{p_idx}]: expected at most {n} entries "
                f"in '{breakdown_key}', got {len(breakdown)}"
            )
    assert total_packages > 0, "No packages found across any delivery"


def _find_field_in_response(resp: object, field: str) -> tuple[object, str]:
    """Find a boolean field in the response, searching through all nesting levels.

    Truncation flags (by_*_truncated) live at the package level inside
    media_buy_deliveries[*].by_package[*]. This function searches:
    1. Top-level response
    2. Delivery level (media_buy_deliveries[0])
    3. Package level (media_buy_deliveries[*].by_package[*])

    Returns (value, location_description) or raises AssertionError if not found.
    """
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    if isinstance(resp_dict, dict) and field in resp_dict:
        return resp_dict[field], "top-level response"

    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    for d in deliveries:
        d_dict = d.model_dump() if hasattr(d, "model_dump") else d
        if isinstance(d_dict, dict) and field in d_dict:
            return d_dict[field], f"delivery {getattr(d, 'media_buy_id', '?')}"
        # Check package level — where truncation flags actually live
        packages = d_dict.get("by_package", []) if isinstance(d_dict, dict) else []
        if not packages:
            packages = getattr(d, "by_package", None) or []
        for pkg in packages:
            pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else (pkg if isinstance(pkg, dict) else {})
            if field in pkg_dict:
                pkg_id = pkg_dict.get("package_id", "?")
                return pkg_dict[field], f"package {pkg_id}"

    raise AssertionError(
        f"Field '{field}' not found at any level (response, delivery, package). "
        f"Deliveries: {len(deliveries)}, "
        f"packages checked: {sum(len(getattr(d, 'by_package', None) or []) for d in deliveries)}"
    )


@then(parsers.parse('"{field}" should be true'))
def then_field_true(ctx: dict, field: str) -> None:
    """Assert a boolean field is true, searching through all response levels."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    value, location = _find_field_in_response(resp, field)
    assert value is True, f"Expected '{field}' to be true at {location}, got {value!r}"


@then(parsers.parse('"{field}" should be false'))
def then_field_false(ctx: dict, field: str) -> None:
    """Assert a boolean field is false, searching through all response levels."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    value, location = _find_field_in_response(resp, field)
    assert value is False, f"Expected '{field}' to be false at {location}, got {value!r}"


@then(parsers.parse('the response packages should include "{field}"'))
def then_packages_include_field(ctx: dict, field: str) -> None:
    """Assert ALL packages across ALL deliveries include the named field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    total_packages = 0
    for d_idx, delivery in enumerate(deliveries):
        packages = getattr(delivery, "by_package", None) or []
        for p_idx, pkg in enumerate(packages):
            total_packages += 1
            pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
            assert field in pkg_dict, (
                f"Delivery[{d_idx}] package[{p_idx}] missing '{field}', fields: {list(pkg_dict.keys())}"
            )
            assert pkg_dict[field] is not None, f"Delivery[{d_idx}] package[{p_idx}] field '{field}' is None"
    assert total_packages > 0, "No packages found across any delivery"


@then(parsers.parse('the response packages should include "{f1}" and "{f2}" breakdowns'))
def then_packages_include_two(ctx: dict, f1: str, f2: str) -> None:
    """Assert packages include both named breakdowns."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    packages = getattr(deliveries[0], "by_package", None) or []
    assert len(packages) > 0, "No packages in delivery"
    pkg = packages[0]
    pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
    key1 = f"by_{f1}" if not f1.startswith("by_") else f1
    key2 = f"by_{f2}" if not f2.startswith("by_") else f2
    assert key1 in pkg_dict, f"Package missing '{key1}', fields: {list(pkg_dict.keys())}"
    assert key2 in pkg_dict, f"Package missing '{key2}', fields: {list(pkg_dict.keys())}"


@then(parsers.parse('the response packages should NOT include "{field}"'))
def then_packages_exclude_field(ctx: dict, field: str) -> None:
    """Assert packages do not include the named field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    packages = getattr(deliveries[0], "by_package", None) or []
    for pkg in packages:
        pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
        assert field not in pkg_dict or pkg_dict[field] is None, f"Package should NOT include '{field}'"


@then(parsers.parse('the response geo breakdown should use classification system "{system}"'))
def then_geo_system(ctx: dict, system: str) -> None:
    """Assert geo breakdown uses the named classification system."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    packages = getattr(deliveries[0], "by_package", None) or []
    assert len(packages) > 0, "No packages in delivery"
    pkg = packages[0]
    pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
    geo = pkg_dict.get("by_geo", [])
    assert len(geo) > 0, "No geo breakdown entries"
    # Check classification_system on geo entries
    for entry in geo:
        entry_system = (
            entry.get("classification_system")
            if isinstance(entry, dict)
            else getattr(entry, "classification_system", None)
        )
        assert entry_system == system, f"Expected classification_system '{system}', got '{entry_system}'"


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}" (fallback)'))
def then_placement_sorted_fallback(ctx: dict, metric: str) -> None:
    """Assert placement breakdown sorted by fallback metric (spend)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    packages = getattr(deliveries[0], "by_package", None) or []
    assert len(packages) > 0, "No packages in delivery"
    pkg = packages[0]
    pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
    placements = pkg_dict.get("by_placement", [])
    assert len(placements) >= 2, f"Need >= 2 placements to verify sort, got {len(placements)}"
    values = [p.get(metric, 0) if isinstance(p, dict) else getattr(p, metric, 0) for p in placements]
    assert values == sorted(values, reverse=True), f"Placements not sorted by fallback metric '{metric}': {values}"
    # Verify this IS a fallback — the originally requested sort metric should differ
    assert "requested_sort_metric" in ctx, (
        "Test setup bug: 'requested_sort_metric' must be set in ctx by the Given/When step to prove fallback occurred"
    )
    requested_sort = ctx["requested_sort_metric"]
    assert requested_sort != metric, (
        f"Fallback not triggered: requested sort metric '{requested_sort}' is the same as fallback metric '{metric}'"
    )


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}"'))
def then_placement_sorted(ctx: dict, metric: str) -> None:
    """Assert placement breakdown sorted by requested metric."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) > 0, "No delivery data"
    packages = getattr(deliveries[0], "by_package", None) or []
    assert len(packages) > 0, "No packages in delivery"
    pkg = packages[0]
    pkg_dict = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg.__dict__
    placements = pkg_dict.get("by_placement", [])
    assert len(placements) >= 2, f"Need >= 2 placements to verify sort order, got {len(placements)}"
    values = [p.get(metric, 0) if isinstance(p, dict) else getattr(p, metric, 0) for p in placements]
    assert values == sorted(values, reverse=True), f"Placements not sorted by '{metric}': {values}"


# ── Attribution window assertions ─────────────────────────────────


@then(parsers.parse('the response should include attribution_window with model "{model}"'))
def then_attribution_model(ctx: dict, model: str) -> None:
    """Assert attribution window includes the specified model."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    aw = resp_dict.get("attribution_window")
    assert aw is not None, "Response missing attribution_window"
    assert aw.get("model") == model, f"Expected attribution model '{model}', got '{aw.get('model')}'"


@then("the attribution_window should echo the applied post_click window")
def then_attribution_echo(ctx: dict) -> None:
    """Assert attribution window echoes the applied post_click window."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    aw = resp_dict.get("attribution_window")
    assert aw is not None, "Response missing attribution_window"
    assert "post_click" in aw, f"Attribution window missing post_click, keys: {list(aw.keys())}"
    # Verify the value echoes what was applied (from the request).
    # Absence of both keys means the Given step didn't set up the attribution input — that's a test bug.
    applied_window = ctx.get("applied_attribution_window") or ctx.get("attribution_window_input")
    assert applied_window is not None, (
        "Neither 'applied_attribution_window' nor 'attribution_window_input' found in ctx — "
        "the Given step must set up the attribution input for echo comparison"
    )
    applied_pc = (
        applied_window.get("post_click")
        if isinstance(applied_window, dict)
        else getattr(applied_window, "post_click", None)
    )
    assert applied_pc is not None, (
        f"Applied attribution window has no post_click — cannot verify echo. Window: {applied_window}"
    )
    actual_pc = aw["post_click"]
    applied_pc_dict = (
        applied_pc
        if isinstance(applied_pc, dict)
        else (applied_pc.model_dump() if hasattr(applied_pc, "model_dump") else vars(applied_pc))
    )
    actual_pc_dict = actual_pc if isinstance(actual_pc, dict) else actual_pc
    assert actual_pc_dict == applied_pc_dict, (
        f"Attribution post_click doesn't echo applied window: expected {applied_pc_dict}, got {actual_pc_dict}"
    )


@then("the response should include attribution_window with the seller's platform default")
def then_attribution_default(ctx: dict) -> None:
    """Assert attribution window uses platform default with required fields."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    aw = resp_dict.get("attribution_window")
    assert aw is not None, "Response missing attribution_window (should contain platform default)"
    # Platform default must include model and at least one window definition
    assert "model" in aw, f"Attribution window missing 'model' field, keys: {list(aw.keys())}"
    assert aw["model"] is not None, "Attribution window model should not be None for platform default"
    assert "post_click" in aw or "post_view" in aw, (
        f"Platform default attribution_window should include post_click or post_view, got keys: {list(aw.keys())}"
    )
    # Verify against actual platform default — the Given step must provide this
    assert "platform_default_attribution" in ctx, (
        "Given step must store 'platform_default_attribution' in ctx — "
        "without it, we can only check structure, not that values match the seller's actual default"
    )
    expected_default = ctx["platform_default_attribution"]
    expected_dict = expected_default.model_dump() if hasattr(expected_default, "model_dump") else expected_default
    if isinstance(expected_dict, dict):
        if "model" in expected_dict:
            assert aw["model"] == expected_dict["model"], (
                f"Platform default model mismatch: expected {expected_dict['model']!r}, got {aw['model']!r}"
            )
        for key in ("post_click", "post_view"):
            if key in expected_dict:
                assert key in aw, f"Expected {key} in attribution_window but missing"
                assert aw[key] == expected_dict[key], (
                    f"Platform default {key} mismatch: expected {expected_dict[key]!r}, got {aw[key]!r}"
                )


@then('the response attribution_window should include "model" field (required)')
def then_attribution_has_model(ctx: dict) -> None:
    """Assert attribution window includes model field (required by spec)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    aw = resp_dict.get("attribution_window")
    assert aw is not None, "Response missing attribution_window"
    assert "model" in aw, f"Attribution window missing required 'model' field, keys: {list(aw.keys())}"
    model = aw["model"]
    assert model is not None, "Attribution window model should not be None (required field)"
    assert isinstance(model, str) and len(model) > 0, (
        f"Expected model to be a non-empty string, got {type(model).__name__}: {model!r}"
    )


@then("the response should include attribution_window with the seller's platform default model")
def then_attribution_default_model(ctx: dict) -> None:
    """Assert attribution window uses platform default model."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    aw = resp_dict.get("attribution_window")
    assert aw is not None, "Response missing attribution_window"
    assert "model" in aw, f"Attribution window missing 'model', keys: {list(aw.keys())}"
    model = aw["model"]
    assert model is not None, "Attribution window model should not be None"
    assert isinstance(model, str) and len(model) > 0, (
        f"Expected model to be a non-empty string, got {type(model).__name__}: {model!r}"
    )
    # Platform default model must be verifiable — the Given step must provide it
    assert "platform_default_model" in ctx, (
        "Given step must store 'platform_default_model' in ctx — "
        "without it, we can only check model exists, not that it matches the seller's actual default"
    )
    expected_model = ctx["platform_default_model"]
    assert model == expected_model, f"Expected platform default model '{expected_model}', got '{model}'"


@then("the response should include attribution_window reflecting campaign-length window")
def then_attribution_campaign_length(ctx: dict) -> None:
    """Assert attribution window reflects campaign-length window."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    aw = resp_dict.get("attribution_window")
    assert aw is not None, "Response missing attribution_window"
    post_click = aw.get("post_click", {})
    assert post_click.get("unit") == "campaign", (
        f"Expected campaign-length attribution (unit='campaign'), got unit='{post_click.get('unit')}'"
    )
    assert post_click.get("interval") == 1, f"Expected interval=1 for campaign-length, got {post_click.get('interval')}"


# ── Partial/error delivery assertions ─────────────────────────────


@then(parsers.parse('the response should indicate "{mb_id}" has partial_data or delayed metrics'))
def then_partial_data(ctx: dict, mb_id: str) -> None:
    """Assert partial data indication for the media buy."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Check response-level partial_data flag or delivery-level status
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    deliveries = resp_dict.get("media_buy_deliveries", [])
    found = False
    for d in deliveries:
        if d.get("media_buy_id") == mb_id:
            found = True
            has_partial = (
                resp_dict.get("partial_data") is True
                or d.get("status") == "reporting_delayed"
                or d.get("expected_availability") is not None
            )
            assert has_partial, f"Expected partial_data or delayed status for '{mb_id}', got status='{d.get('status')}'"
            break
    if not found:
        # If mb_id not in deliveries, check for errors
        assert "error" in ctx, f"No delivery data or error for '{mb_id}'"


@then(parsers.parse('the response should include "{mb_id}" with zero impressions and zero spend'))
def then_zero_metrics(ctx: dict, mb_id: str) -> None:
    """Assert zero metrics for the media buy."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    for d in deliveries:
        if d.media_buy_id == mb_id:
            totals = getattr(d, "totals", None)
            if totals:
                assert getattr(totals, "impressions", None) == 0.0
                assert getattr(totals, "spend", None) == 0.0
            return
    raise AssertionError(f"No delivery found for '{mb_id}'")


@then("no real billing records should have been created")
def then_no_billing(ctx: dict) -> None:
    """Assert no real billing records created (sandbox mode).

    .. warning::

        FIXME(salesagent-3bv): This step checks adapter mocks as a PROXY for
        "no billing records created". The correct assertion is a direct DB query
        (SELECT COUNT(*) FROM billing WHERE ... = 0). Replace once the billing
        table exists and the harness provides DB session access.
    """
    import warnings

    warnings.warn(
        "FIXME(salesagent-3bv): then_no_billing uses adapter mock proxy, "
        "not a real DB billing record check. See salesagent-3bv.",
        stacklevel=1,
    )
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    # Verify response explicitly indicates sandbox mode
    sandbox_flag = resp_dict.get("sandbox")
    sandbox_ext = resp_dict.get("ext", {}).get("sandbox")
    assert sandbox_flag is True or sandbox_ext is True, (
        f"Expected sandbox=True in response but got sandbox={sandbox_flag!r}, ext.sandbox={sandbox_ext!r}"
    )
    # FIXME(salesagent-3bv): Replace mock checks below with direct DB query:
    #   with get_db_session() as session:
    #       count = session.scalar(select(func.count()).select_from(BillingRecord).where(...))
    #       assert count == 0, f"Expected 0 billing records, found {count}"
    env = ctx["env"]
    adapter_mock = env.mock.get("adapter")
    assert adapter_mock is not None, "adapter mock must be present in env.mock — its absence is a harness bug"
    billing_methods = ["create_billing_record", "submit_billing", "charge", "invoice"]
    called_methods: list[str] = []
    for method_name in billing_methods:
        if not hasattr(adapter_mock, method_name):
            continue  # spec'd mock without this method — no call possible
        method = getattr(adapter_mock, method_name)
        if hasattr(method, "called") and method.called:
            called_methods.append(method_name)
    assert not called_methods, (
        f"Expected no billing calls but these adapter methods were called: {', '.join(called_methods)}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Helpers — internal
# ═══════════════════════════════════════════════════════════════════════


def _ensure_media_buy_in_db(
    ctx: dict,
    mb_id: str,
    owner: str,
    status: str = "active",
    buyer_ref: str | None = None,
) -> None:
    """Create a media buy in the test database using factories.

    Uses the env's integration DB session. If the env doesn't support
    DB operations (unit harness), this is a no-op — ctx state is enough.

    Also aligns the env's identity principal_id with the owner so the
    _impl function's DB query (which filters by principal_id) finds the
    media buy.
    """
    env = ctx["env"]
    if not env.use_real_db:
        return

    from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory

    # Ensure tenant exists
    if "db_tenant" not in ctx:
        ctx["db_tenant"] = TenantFactory.create(tenant_id=ctx.get("tenant_id", "test_tenant"))

    # Ensure principal exists
    principal_key = f"db_principal_{owner}"
    if principal_key not in ctx:
        ctx[principal_key] = PrincipalFactory.create(
            tenant=ctx["db_tenant"],
            principal_id=owner,
        )

    # Align env identity with DB owner so _impl queries find the media buy
    if env._principal_id != owner:
        env._principal_id = owner
        env._identity_cache.clear()

    # Create media buy
    mb_kwargs: dict[str, Any] = {
        "tenant": ctx["db_tenant"],
        "principal": ctx[principal_key],
        "media_buy_id": mb_id,
        "status": status,
    }
    if buyer_ref:
        mb_kwargs["buyer_ref"] = buyer_ref

    MediaBuyFactory.create(**mb_kwargs)


def _parse_request_params(params_str: str) -> dict[str, Any]:
    """Parse request parameters from Gherkin table/string format.

    Handles formats like:
    - media_buy_ids=["mb-001"]
    - buyer_refs=["ref-001"]
    - media_buy_ids=["mb-001"] buyer_refs=["ref-001"]
    """
    kwargs: dict[str, Any] = {}
    for match in re.finditer(r'(\w+)=(\[.+?\]|"[^"]*"|[^\s]+)', params_str):
        key, value = match.group(1), match.group(2)
        if value.startswith("["):
            kwargs[key] = json.loads(value)
        elif value.startswith('"'):
            kwargs[key] = value.strip('"')
        else:
            kwargs[key] = value
    return kwargs


def _dispatch_partition(ctx: dict, field: str, value: str) -> None:
    """Dispatch a partition/boundary test request.

    Parses the partition value and makes the appropriate call.
    For omitted/absent values, calls with no additional params.
    """
    value_stripped = value.strip()

    # Handle special partition values
    if value_stripped in ("(field absent)", "(omitted)", "(not provided)"):
        _call_delivery(ctx)
        return

    # Try to parse as JSON
    try:
        parsed = json.loads(value_stripped)
        _call_delivery(ctx, **{field: parsed})
        return
    except (json.JSONDecodeError, TypeError):
        pass

    # Pass as string
    _call_delivery(ctx, **{field: value_stripped})


# ── Partition/boundary Then steps ────────────────────────────────


def _assert_valid_content(ctx: dict, field: str) -> None:
    """Per-field content assertion for 'valid' partition/boundary outcomes.

    Instead of just checking 'response exists', verify the field under test
    actually affected the response content. Fields without a specific handler
    fall through to the basic existence check (backward compatible).
    """
    resp = ctx["response"]

    if field in ("status_filter", "filter"):
        # Verify returned statuses match the filter
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        request_params = ctx.get("request_params", {})
        requested_filter = request_params.get("status_filter")
        if requested_filter and deliveries:
            for d in deliveries:
                actual_status = getattr(d, "status", None)
                if actual_status:
                    assert actual_status in requested_filter, (
                        f"Status filter violation: got status '{actual_status}' but filter requested {requested_filter}"
                    )

    elif field == "resolution":
        # Verify resolved media buys match requested IDs/refs
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        request_params = ctx.get("request_params", {})
        requested_ids = request_params.get("media_buy_ids")
        if requested_ids and deliveries:
            returned_ids = {getattr(d, "media_buy_id", None) for d in deliveries}
            for req_id in requested_ids:
                assert req_id in returned_ids, (
                    f"Resolution violation: requested media_buy_id '{req_id}' not in response: {returned_ids}"
                )

    elif field in ("reporting_dimensions", "reporting dimensions"):
        # Verify response contains delivery data (field was accepted and processed)
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries (field should be processed)"

    elif field in ("attribution_window", "attribution window"):
        # Verify attribution metadata exists in response
        resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else {}
        if isinstance(resp_dict, dict):
            aw = resp_dict.get("attribution_window")
            if aw is not None:
                assert "model" in aw, f"Valid {field}: attribution_window missing 'model'"

    elif field in ("daily_breakdown", "daily breakdown", "include_package_daily_breakdown"):
        # Verify response has delivery data (field was processed)
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries"

    elif field == "account":
        # Verify response has delivery data scoped to the account
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries"

    elif field in ("date_range", "date range"):
        # Verify reporting_period exists and is valid
        period = getattr(resp, "reporting_period", None)
        if period is not None:
            start = getattr(period, "start", None)
            end = getattr(period, "end", None)
            assert start is not None, f"Valid {field}: reporting_period.start is None"
            assert end is not None, f"Valid {field}: reporting_period.end is None"

    elif field == "ownership":
        # Verify response contains only media buys owned by the principal
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries for owned media buys"

    # Fields without specific handlers: basic existence check is sufficient.
    # As features are implemented, add handlers above.


def _assert_partition_or_boundary(ctx: dict, expected: str, field: str = "unknown") -> None:
    """Assert partition/boundary outcome with field-aware content validation.

    Handles three expected-value formats from Scenario Outline Examples tables:
    - "valid"   — response exists, no error, AND field-specific content check
    - "invalid" — error exists
    - 'error "CODE" with suggestion' — error code matches + suggestion present
    """
    expected = expected.strip()

    if expected == "valid":
        assert "error" not in ctx, f"Expected valid {field} result but got error: {ctx.get('error')}"
        assert "response" in ctx, f"Expected response for valid {field} but none found"
        _assert_valid_content(ctx, field)
    elif expected == "invalid":
        from pydantic import ValidationError

        from src.core.exceptions import AdCPError

        assert "error" in ctx, f"Expected invalid {field} result but operation succeeded"
        error = ctx["error"]
        assert isinstance(error, (AdCPError, ValidationError)), (
            f"Expected AdCPError/ValidationError for invalid {field}, got {type(error).__name__}: {error}"
        )
    else:
        # Parse 'error "CODE" with suggestion'
        m = re.match(r'error "(.+?)" with suggestion', expected)
        if m:
            from src.core.exceptions import AdCPError

            code = m.group(1)
            assert "error" in ctx, f"Expected error '{code}' for {field} but operation succeeded"
            error = ctx["error"]
            assert isinstance(error, AdCPError), f"Expected AdCPError for {field}, got {type(error).__name__}: {error}"
            assert error.error_code == code, f"Expected error code '{code}' for {field}, got '{error.error_code}'"
            suggestion = (error.details or {}).get("suggestion")
            assert suggestion, f"Expected suggestion in error for {field}, got details: {error.details}"
        else:
            raise AssertionError(f"Unexpected expected value '{expected}' for {field}")


# Stacked decorators: one function handles all partition/boundary verb patterns.
# Each @then registers a separate pattern — no duplicate bodies.
@then(parsers.re(r"the (?P<field>.+) validation should result in (?P<expected>.+)"))
@then(parsers.re(r"the (?P<field>.+) handling should result in (?P<expected>.+)"))
@then(parsers.re(r"the (?P<field>.+) check should result in (?P<expected>.+)"))
@then(parsers.re(r"the (?P<field>.+) check should be (?P<expected>.+)"))
@then(parsers.re(r"the (?P<field>ownership|resolution) should be (?P<expected>.+)"))
def then_partition_or_boundary_outcome(ctx: dict, field: str, expected: str) -> None:
    """Partition/boundary test: assert outcome matches expected for the given field."""
    _assert_partition_or_boundary(ctx, expected, field)


@then(parsers.re(r"the filter should result in (?P<expected>.+)"))
def then_filter_result(ctx: dict, expected: str) -> None:
    """Partition test: status_filter outcome (no field param in step text)."""
    _assert_partition_or_boundary(ctx, expected, "status_filter")


@then(parsers.re(r"the resolution should result in (?P<expected>.+)"))
def then_resolution_result(ctx: dict, expected: str) -> None:
    """Partition test: resolution outcome (no field param in step text)."""
    _assert_partition_or_boundary(ctx, expected, "resolution")
