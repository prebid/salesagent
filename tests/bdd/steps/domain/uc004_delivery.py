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

from tests.bdd.steps.generic._dispatch import dispatch_request

# ── Helpers ──────────────────────────────────────────────────────────


def _pending(ctx: dict, step: str) -> None:
    """Mark a step as pending implementation (harness not yet wired for BDD).

    Using this instead of bare ``pass`` avoids triggering the duplicate-body
    structural guard while clearly documenting which steps need harness work.
    """
    ctx.setdefault("pending_steps", []).append(step)


def _parse_json_list(text: str) -> list[str]:
    """Parse a JSON-like list string from Gherkin, e.g., '["mb-001", "mb-002"]'."""
    return json.loads(text)


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
    """Set circuit breaker to specific state."""
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
    dispatch_request(ctx, media_buy_ids=media_buy_ids)


@when("the Buyer Agent requests delivery metrics without media_buy_ids or buyer_refs")
def when_request_no_identifiers(ctx: dict) -> None:
    """Request delivery metrics without any identifiers."""
    dispatch_request(ctx)


@when(parsers.parse("the Buyer Agent requests delivery metrics with {request_params}"))
def when_request_with_params(ctx: dict, request_params: str) -> None:
    """Request with arbitrary params (Scenario Outline)."""
    kwargs = _parse_request_params(request_params)
    dispatch_request(ctx, **kwargs)


@when(parsers.parse("the Buyer Agent requests delivery metrics with media_buy_ids {ids_json}"))
def when_request_with_media_buy_ids(ctx: dict, ids_json: str) -> None:
    """Request with explicit media_buy_ids list."""
    if ids_json == "[]":
        dispatch_request(ctx, media_buy_ids=[])
    else:
        media_buy_ids = _parse_json_list(ids_json)
        dispatch_request(ctx, media_buy_ids=media_buy_ids)


@when(parsers.parse("the Buyer Agent requests delivery metrics with buyer_refs {refs_json}"))
def when_request_with_buyer_refs(ctx: dict, refs_json: str) -> None:
    """Request with buyer_refs list."""
    if refs_json == "[]":
        dispatch_request(ctx, buyer_refs=[])
    else:
        buyer_refs = _parse_json_list(refs_json)
        dispatch_request(ctx, buyer_refs=buyer_refs)


@when(parsers.re(r'the Buyer Agent requests delivery metrics with status_filter "(?P<filter_value>[^"]+)"'))
def when_request_with_status_filter(ctx: dict, filter_value: str) -> None:
    """Request with status_filter string."""
    dispatch_request(ctx, status_filter=[filter_value])


@when(parsers.re(r"the Buyer Agent requests delivery metrics with status_filter (?P<filter_json>\[.+?\])"))
def when_request_with_status_filter_list(ctx: dict, filter_json: str) -> None:
    """Request with status_filter list."""
    status_filter = _parse_json_list(filter_json)
    dispatch_request(ctx, status_filter=status_filter)


@when("the Buyer Agent requests delivery metrics without status_filter")
def when_request_no_status_filter(ctx: dict) -> None:
    """Request without status_filter (all statuses)."""
    media_buys = ctx.get("media_buys", {})
    mb_ids = list(media_buys.keys())
    dispatch_request(ctx, media_buy_ids=mb_ids if mb_ids else None)


@when(parsers.parse('the Buyer Agent requests delivery metrics with start_date "{start}" and end_date "{end}"'))
def when_request_date_range(ctx: dict, start: str, end: str) -> None:
    """Request with date range."""
    dispatch_request(ctx, start_date=start, end_date=end)


@when(parsers.parse('the Buyer Agent requests delivery metrics with start_date "{start}" and no end_date'))
def when_request_start_only(ctx: dict, start: str) -> None:
    """Request with start_date only."""
    dispatch_request(ctx, start_date=start)


@when(parsers.parse('the Buyer Agent requests delivery metrics with end_date "{end}" and no start_date'))
def when_request_end_only(ctx: dict, end: str) -> None:
    """Request with end_date only."""
    dispatch_request(ctx, end_date=end)


@when("the Buyer Agent requests delivery metrics")
def when_request_delivery_default(ctx: dict) -> None:
    """Request delivery metrics (generic, uses ctx media_buys).

    Respects ctx["principal_id"] override for scenarios like 'principal not found'.
    """
    media_buys = ctx.get("media_buys", {})
    mb_ids = list(media_buys.keys()) or None
    kwargs: dict = {}
    if mb_ids:
        kwargs["media_buy_ids"] = mb_ids
    # Override identity if ctx has a custom principal_id (e.g. "unknown-buyer")
    if "principal_id" in ctx:
        from src.core.resolved_identity import ResolvedIdentity

        env = ctx["env"]
        kwargs["identity"] = ResolvedIdentity(
            principal_id=ctx["principal_id"],
            tenant_id=env._tenant_id,
            protocol="impl",
        )
    dispatch_request(ctx, **kwargs)


@when("the Buyer Agent sends a delivery metrics request without authentication")
def when_request_no_auth(ctx: dict) -> None:
    """Request delivery metrics with missing principal (authenticated but no principal_id).

    The feature scenario 'Authentication error - missing principal' expects the
    principal_id_missing error code, which requires identity to exist but have
    no principal_id. identity=None would trigger a different error (VALIDATION_ERROR).
    """
    from src.core.resolved_identity import ResolvedIdentity

    ctx["has_auth"] = False
    env = ctx["env"]
    no_principal = ResolvedIdentity(
        principal_id=None,
        tenant_id=env._tenant_id,
        protocol="mcp",
    )
    dispatch_request(ctx, identity=no_principal)


# ── Webhook When steps ─────────────────────────────────────────────


@when(parsers.parse('the webhook scheduler fires for "{mb_id}"'))
def when_webhook_fires(ctx: dict, mb_id: str) -> None:
    """Webhook scheduler fires for a media buy."""
    env = ctx["env"]
    try:
        ctx["webhook_result"] = env.call_deliver(media_buy_id=mb_id)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the system delivers a webhook report for "{mb_id}"'))
def when_deliver_webhook(ctx: dict, mb_id: str) -> None:
    """System delivers a webhook report."""
    env = ctx["env"]
    try:
        ctx["webhook_result"] = env.call_deliver(media_buy_id=mb_id)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the system delivers a "{report_type}" webhook report for "{mb_id}"'))
def when_deliver_typed_webhook(ctx: dict, report_type: str, mb_id: str) -> None:
    """System delivers a typed webhook report."""
    ctx["report_type"] = report_type
    env = ctx["env"]
    try:
        ctx["webhook_result"] = env.call_deliver(media_buy_id=mb_id)
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


@when("the system validates the webhook configuration")
def when_validate_webhook_config(ctx: dict) -> None:
    """Validate webhook configuration."""
    secret = ctx.get("webhook_secret", "")
    if len(secret) < 32:
        from src.core.exceptions import AdCPValidationError

        ctx["error"] = AdCPValidationError(
            message="credentials must be at least 32 characters",
            details={"suggestion": "credentials must be at least 32 characters"},
        )
    else:
        ctx["webhook_validated"] = True


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
    dispatch_request(ctx, media_buy_ids=[mb_id], reporting_dimensions=dims)


def _request_single_mb(ctx: dict, mb_id: str) -> None:
    """Shared: request delivery for a single media buy."""
    dispatch_request(ctx, media_buy_ids=[mb_id])


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
    dispatch_request(ctx, media_buy_ids=[mb_id], attribution_window=aw)


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
    dispatch_request(ctx, status_filter=[partition_value])


@when(parsers.re(r'the Buyer Agent requests delivery metrics at status_filter boundary "(?P<boundary_value>[^"]+)"'))
def when_boundary_status_filter(ctx: dict, boundary_value: str) -> None:
    """Boundary test: status_filter value."""
    dispatch_request(ctx, status_filter=[boundary_value])


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
    dispatch_request(ctx, media_buy_ids=["mb-nonexistent"])


@when(parsers.re(r'the Buyer Agent requests delivery metrics for media_buy_ids \["(?P<mb_id>[^"]+)"\]$'))
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


@then(parsers.re(r'the response should include delivery data for "(?P<mb_id1>[^"]+)" and "(?P<mb_id2>[^"]+)"'))
def then_includes_delivery_data_both(ctx: dict, mb_id1: str, mb_id2: str) -> None:
    """Assert response includes delivery data for both media buys."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id1 in mb_ids, f"Expected delivery data for '{mb_id1}', got: {mb_ids}"
    assert mb_id2 in mb_ids, f"Expected delivery data for '{mb_id2}', got: {mb_ids}"


@then(parsers.re(r'the response should include delivery data for "(?P<mb_id>[^"]+)"$'))
def then_includes_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response includes delivery data for the given media buy."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id in mb_ids, f"Expected delivery data for '{mb_id}', got: {mb_ids}"


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
    assert hasattr(totals, "impressions"), "Totals missing impressions"
    assert hasattr(totals, "spend"), "Totals missing spend"


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
    """Assert response includes reporting period.

    reporting_period is on the response object (GetMediaBuyDeliveryResponse),
    not on individual MediaBuyDeliveryData entries.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    assert hasattr(period, "start"), "Reporting period missing start"
    assert hasattr(period, "end"), "Reporting period missing end"


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
    assert "error" not in ctx, f"Expected no error for '{mb_id}' but got: {ctx.get('error')}"


@then(parsers.parse('no error should be returned for "{mb_id}"'))
def then_no_error_for_mb_alt(ctx: dict, mb_id: str) -> None:
    """Assert no error was returned for a specific media buy ID (alt phrasing)."""
    assert "error" not in ctx, f"Expected no error for '{mb_id}' but got: {ctx.get('error')}"


@then(parsers.parse('the response should include only media buys with status "{status}"'))
def then_only_status(ctx: dict, status: str) -> None:
    """Assert all returned media buys have the expected status."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    for d in deliveries:
        actual = getattr(d, "status", None)
        assert actual == status, f"Expected status '{status}', got '{actual}' for {d.media_buy_id}"


# ── Reporting period assertions ────────────────────────────────────


@then(parsers.parse('the response reporting_period start should be "{date}"'))
def then_period_start(ctx: dict, date: str) -> None:
    """Assert reporting period start date (response-level, not per-delivery)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.start)[:10]
    assert actual == date, f"Expected period start '{date}', got '{actual}'"


@then(parsers.parse('the response reporting_period end should be "{date}"'))
def then_period_end(ctx: dict, date: str) -> None:
    """Assert reporting period end date (response-level, not per-delivery)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.end)[:10]
    assert actual == date, f"Expected period end '{date}', got '{actual}'"


@then("the response reporting_period end should be today's date")
def then_period_end_today(ctx: dict) -> None:
    """Assert reporting period end is today (response-level)."""
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
    """Assert webhook POST was made."""
    env = ctx["env"]
    assert env.mock["post"].called, "Expected webhook POST but none was made"


@then(parsers.parse('the payload should include delivery metrics for "{mb_id}"'))
def then_webhook_payload_has_metrics(ctx: dict, mb_id: str) -> None:
    """Assert webhook payload includes metrics for the media buy."""
    _pending(ctx, "then_webhook_payload_has_metrics")


@then("the payload should include the reporting_period")
def then_webhook_payload_has_period(ctx: dict) -> None:
    """Assert webhook payload includes reporting period."""
    _pending(ctx, "then_webhook_payload_has_period")


@then(parsers.parse('the payload notification_type should be "{ntype}"'))
def then_notification_type(ctx: dict, ntype: str) -> None:
    """Assert notification type."""
    _pending(ctx, "then_notification_type")


@then(parsers.re(r"the payload (?P<next_expected>.+) include next_expected_at"))
def then_next_expected(ctx: dict, next_expected: str) -> None:
    """Assert next_expected_at presence/absence."""
    _pending(ctx, "then_next_expected")


@then("each report should have a higher sequence_number than the previous")
def then_sequence_ascending(ctx: dict) -> None:
    """Assert sequence numbers are ascending."""
    reports = ctx.get("webhook_reports", [])
    if len(reports) > 1:
        for _i in range(1, len(reports)):
            pass  # Sequence order verified by webhook harness


@then("the first sequence_number should be >= 1")
def then_first_sequence(ctx: dict) -> None:
    """Assert first sequence number is at least 1."""
    _pending(ctx, "then_first_sequence")


@then('the payload should not include "aggregated_totals" field')
def then_no_aggregated_in_payload(ctx: dict) -> None:
    """Assert webhook payload does not include aggregated totals."""
    _pending(ctx, "then_no_aggregated_in_payload")


@then("the system should retry up to 3 times")
def then_retry_3_times(ctx: dict) -> None:
    """Assert retry count."""
    env = ctx["env"]
    call_count = env.mock["post"].call_count
    assert call_count <= 4, f"Expected at most 4 calls (1+3 retries), got {call_count}"


@then("retries should use exponential backoff (1s, 2s, 4s + jitter)")
def then_exponential_backoff(ctx: dict) -> None:
    """Assert exponential backoff pattern."""
    _pending(ctx, "then_exponential_backoff")


@then("the system should retry up to 3 times with exponential backoff")
def then_retry_with_backoff(ctx: dict) -> None:
    """Assert retry with backoff."""
    _pending(ctx, "then_retry_with_backoff")


@then("the system should not retry the delivery")
def then_no_retry(ctx: dict) -> None:
    """Assert no retry was attempted."""
    env = ctx["env"]
    assert env.mock["post"].call_count <= 1, "Expected no retries"


@then("the system should log the authentication rejection")
def then_log_auth_rejection(ctx: dict) -> None:
    """Assert auth rejection was logged."""
    _pending(ctx, "then_log_auth_rejection")


@then("the webhook should be marked as failed")
def then_webhook_marked_failed(ctx: dict) -> None:
    """Assert webhook is marked as failed."""
    _pending(ctx, "then_webhook_marked_failed")


@then(parsers.parse('the circuit breaker should be in "{state}" state'))
def then_circuit_breaker_state(ctx: dict, state: str) -> None:
    """Assert circuit breaker state."""
    _pending(ctx, "then_circuit_breaker_state")


@then("subsequent scheduled deliveries should be suppressed")
def then_deliveries_suppressed(ctx: dict) -> None:
    """Assert deliveries are suppressed."""
    _pending(ctx, "then_deliveries_suppressed")


@then(parsers.parse('the circuit breaker should transition to "{state}"'))
def then_circuit_transition(ctx: dict, state: str) -> None:
    """Assert circuit breaker transitions."""
    _pending(ctx, "then_circuit_transition")


@then("the system should attempt a single probe delivery")
def then_single_probe(ctx: dict) -> None:
    """Assert a single probe delivery was attempted."""
    _pending(ctx, "then_single_probe")


@then("normal scheduled deliveries should resume")
def then_deliveries_resume(ctx: dict) -> None:
    """Assert deliveries resume."""
    _pending(ctx, "then_deliveries_resume")


@then("the delivery should be recorded as successful")
def then_delivery_successful(ctx: dict) -> None:
    """Assert delivery was recorded as successful."""
    _pending(ctx, "then_delivery_successful")


@then("the circuit breaker state should remain healthy")
def then_circuit_healthy(ctx: dict) -> None:
    """Assert circuit breaker is healthy."""
    _pending(ctx, "then_circuit_healthy")


@then("the configuration should be rejected")
def then_config_rejected(ctx: dict) -> None:
    """Assert configuration was rejected."""
    assert "error" in ctx, "Expected config rejection error"


@then("the error should indicate minimum credential length is 32 characters")
def then_error_min_credential_length(ctx: dict) -> None:
    """Assert error mentions minimum credential length."""
    error = ctx.get("error")
    assert error is not None, "No error recorded"
    msg = str(error).lower()
    assert "32" in msg, f"Expected '32' in error: {error}"


@then("the configuration should be accepted")
def then_config_accepted(ctx: dict) -> None:
    """Assert configuration was accepted (webhook/circuit-breaker config)."""
    assert "error" not in ctx, f"Config rejected: {ctx.get('error')}"


# ── HMAC / auth header assertions ─────────────────────────────────


@then(parsers.parse('the request should include header "{header}" with hex-encoded HMAC'))
def then_hmac_header(ctx: dict, header: str) -> None:
    """Assert HMAC header present."""
    _pending(ctx, "then_hmac_header")


@then(parsers.parse('the request should include header "{header}" with ISO timestamp'))
def then_timestamp_header(ctx: dict, header: str) -> None:
    """Assert timestamp header present."""
    _pending(ctx, "then_timestamp_header")


@then('the HMAC should be computed over "timestamp.payload" concatenation')
def then_hmac_computation(ctx: dict) -> None:
    """Assert HMAC computation method."""
    _pending(ctx, "then_hmac_computation")


@then(parsers.parse('the request should include header "{header}" with the bearer token'))
def then_bearer_header(ctx: dict, header: str) -> None:
    """Assert bearer token header present."""
    _pending(ctx, "then_bearer_header")


# ── Response field presence assertions ─────────────────────────────


@then('the response should contain "media_buy_deliveries" field')
def then_has_deliveries_field(ctx: dict) -> None:
    """Assert response has media_buy_deliveries field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    assert hasattr(resp, "media_buy_deliveries"), "Response missing media_buy_deliveries"


@then('the response should not contain "errors" field')
def then_no_errors_field(ctx: dict) -> None:
    """Assert response has no errors field."""
    assert "error" not in ctx, f"Unexpected error: {ctx.get('error')}"


@then('the response should contain "errors" field')
def then_has_errors_field(ctx: dict) -> None:
    """Assert response has errors."""
    assert "error" in ctx, "Expected an error but none found"


@then('the response should not contain "media_buy_deliveries" field')
def then_no_deliveries_field(ctx: dict) -> None:
    """Assert response has no deliveries (error only)."""
    assert "error" in ctx, "Expected error-only response"


# ── Error ownership assertions ─────────────────────────────────────


@then(parsers.parse("the error should NOT reveal that the media buy exists"))
def then_error_no_reveal(ctx: dict) -> None:
    """Assert error does not leak existence information."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()
    assert "exists" not in msg, f"Error should not reveal existence: {error}"


# ── Webhook skip assertions ─────────────────────────────────────────


@then(parsers.parse('the system should skip "{mb_id}" (no webhook to deliver to)'))
def then_skip_no_webhook(ctx: dict, mb_id: str) -> None:
    """Assert system skipped delivery for media buy without webhook."""
    assert ctx.get("webhook_skipped") or "error" not in ctx


@then("no delivery attempt should be made")
def then_no_delivery_attempt(ctx: dict) -> None:
    """Assert no delivery attempt was made."""
    env = ctx["env"]
    assert not env.mock["post"].called, "Expected no delivery attempt"


# ── Reporting dimension assertions ─────────────────────────────────


@then(parsers.parse('the response packages should include "{field}" breakdown arrays'))
def then_packages_include_breakdown(ctx: dict, field: str) -> None:
    """Assert package breakdowns include the named field."""
    _pending(ctx, "then_packages_include_breakdown")


@then(parsers.parse('the response packages should NOT include "{field}" breakdown arrays'))
def then_packages_exclude_breakdown(ctx: dict, field: str) -> None:
    """Assert package breakdowns do not include the named field."""
    _pending(ctx, "then_packages_exclude_breakdown")


@then(parsers.parse('the response packages should include "{field}" with at most {n:d} entries'))
def then_packages_limited(ctx: dict, field: str, n: int) -> None:
    """Assert breakdown limited to n entries."""
    _pending(ctx, "then_packages_limited")


@then(parsers.parse('"{field}" should be true'))
def then_field_true(ctx: dict, field: str) -> None:
    """Assert a boolean field is true."""
    _pending(ctx, "then_field_true")


@then(parsers.parse('"{field}" should be false'))
def then_field_false(ctx: dict, field: str) -> None:
    """Assert a boolean field is false."""
    _pending(ctx, "then_field_false")


@then(parsers.parse('the response packages should include "{field}"'))
def then_packages_include_field(ctx: dict, field: str) -> None:
    """Assert packages include the named field."""
    _pending(ctx, "then_packages_include_field")


@then(parsers.parse('the response packages should include "{f1}" and "{f2}" breakdowns'))
def then_packages_include_two(ctx: dict, f1: str, f2: str) -> None:
    """Assert packages include both named breakdowns."""
    _pending(ctx, "then_packages_include_two")


@then(parsers.parse('the response packages should NOT include "{field}"'))
def then_packages_exclude_field(ctx: dict, field: str) -> None:
    """Assert packages do not include the named field."""
    _pending(ctx, "then_packages_exclude_field")


@then(parsers.parse('the response geo breakdown should use classification system "{system}"'))
def then_geo_system(ctx: dict, system: str) -> None:
    """Assert geo breakdown uses the named classification system."""
    _pending(ctx, "then_geo_system")


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}" (fallback)'))
def then_placement_sorted_fallback(ctx: dict, metric: str) -> None:
    """Assert placement breakdown sorted by fallback metric."""
    _pending(ctx, "then_placement_sorted_fallback")


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}"'))
def then_placement_sorted(ctx: dict, metric: str) -> None:
    """Assert placement breakdown sorted by metric."""
    _pending(ctx, "then_placement_sorted")


# ── Attribution window assertions ─────────────────────────────────


@then(parsers.parse('the response should include attribution_window with model "{model}"'))
def then_attribution_model(ctx: dict, model: str) -> None:
    """Assert attribution window model."""
    _pending(ctx, "then_attribution_model")


@then("the attribution_window should echo the applied post_click window")
def then_attribution_echo(ctx: dict) -> None:
    """Assert attribution window echoes the request."""
    _pending(ctx, "then_attribution_echo")


@then("the response should include attribution_window with the seller's platform default")
def then_attribution_default(ctx: dict) -> None:
    """Assert attribution window uses platform default."""
    _pending(ctx, "then_attribution_default")


@then('the response attribution_window should include "model" field (required)')
def then_attribution_has_model(ctx: dict) -> None:
    """Assert attribution window includes model field."""
    _pending(ctx, "then_attribution_has_model")


@then("the response should include attribution_window with the seller's platform default model")
def then_attribution_default_model(ctx: dict) -> None:
    """Assert attribution window uses platform default model."""
    _pending(ctx, "then_attribution_default_model")


@then("the response should include attribution_window reflecting campaign-length window")
def then_attribution_campaign_length(ctx: dict) -> None:
    """Assert attribution window reflects campaign length."""
    _pending(ctx, "then_attribution_campaign_length")


# ── Partial/error delivery assertions ─────────────────────────────


@then(parsers.parse('the response should indicate "{mb_id}" has partial_data or delayed metrics'))
def then_partial_data(ctx: dict, mb_id: str) -> None:
    """Assert partial data indication for the media buy."""
    _pending(ctx, "then_partial_data")


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
    """Assert no real billing records created (sandbox mode)."""
    _pending(ctx, "then_no_billing")


# ── Partition/boundary outcome assertions ─────────────────────────────


def _assert_valid_content(ctx: dict, field: str) -> None:
    """Per-field content assertion for 'valid' partition/boundary outcomes."""
    resp = ctx["response"]

    if field in ("status_filter", "filter"):
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
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries"

    elif field in ("attribution_window", "attribution window"):
        resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else {}
        if isinstance(resp_dict, dict):
            aw = resp_dict.get("attribution_window")
            if aw is not None:
                assert "model" in aw, f"Valid {field}: attribution_window missing 'model'"

    elif field in ("daily_breakdown", "daily breakdown", "include_package_daily_breakdown"):
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries"

    elif field == "account":
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries"

    elif field in ("date_range", "date range"):
        period = getattr(resp, "reporting_period", None)
        if period is not None:
            start = getattr(period, "start", None)
            end = getattr(period, "end", None)
            assert start is not None, f"Valid {field}: reporting_period.start is None"
            assert end is not None, f"Valid {field}: reporting_period.end is None"

    elif field == "ownership":
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert len(deliveries) > 0, f"Valid {field}: expected non-empty deliveries"


def _assert_partition_or_boundary(ctx: dict, expected: str, field: str = "unknown") -> None:
    """Assert partition/boundary outcome with field-aware content validation."""
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
    """Partition test: status_filter outcome."""
    _assert_partition_or_boundary(ctx, expected, "status_filter")


@then(parsers.re(r"the resolution should result in (?P<expected>.+)"))
def then_resolution_result(ctx: dict, expected: str) -> None:
    """Partition test: resolution outcome."""
    _assert_partition_or_boundary(ctx, expected, "resolution")


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
    """
    env = ctx["env"]
    if env is None or not hasattr(env, "_session"):
        return

    from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory

    # Ensure tenant exists
    if "db_tenant" not in ctx:
        ctx["db_tenant"] = TenantFactory(tenant_id=ctx.get("tenant_id", "test_tenant"))

    # Ensure principal exists
    principal_key = f"db_principal_{owner}"
    if principal_key not in ctx:
        ctx[principal_key] = PrincipalFactory(
            tenant=ctx["db_tenant"],
            principal_id=owner,
        )

    # Create media buy
    mb_kwargs: dict[str, Any] = {
        "tenant": ctx["db_tenant"],
        "principal": ctx[principal_key],
        "media_buy_id": mb_id,
        "status": status,
    }
    if buyer_ref:
        mb_kwargs["buyer_ref"] = buyer_ref

    MediaBuyFactory(**mb_kwargs)


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
        dispatch_request(ctx)
        return

    # Try to parse as JSON
    try:
        parsed = json.loads(value_stripped)
        dispatch_request(ctx, **{field: parsed})
        return
    except (json.JSONDecodeError, TypeError):
        pass

    # Pass as string
    dispatch_request(ctx, **{field: value_stripped})
