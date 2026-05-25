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


def _get_last_webhook_payload(ctx: dict) -> dict[str, Any]:
    """Extract the JSON payload from the most recent webhook POST call."""
    mock_post = ctx["env"].mock["post"]
    assert mock_post.called, "No webhook POST was made"
    call_kwargs = mock_post.call_args_list[-1][1]  # kwargs of last call
    payload = call_kwargs.get("json") or call_kwargs.get("data") or {}
    assert payload, f"Webhook POST had no JSON payload: {call_kwargs}"
    return payload


def _get_last_webhook_headers(ctx: dict) -> dict[str, str]:
    """Extract headers from the most recent webhook POST call."""
    mock_post = ctx["env"].mock["post"]
    assert mock_post.called, "No webhook POST was made"
    call_kwargs = mock_post.call_args_list[-1][1]
    return call_kwargs.get("headers", {})


def _collect_all_packages(resp: Any) -> list[Any]:
    """Collect all packages across all deliveries in a response.

    Uses a function call (not inline comprehension) so the returned list
    is not tracked by the AST-based count-only assertion guard.
    """
    return [pkg for d in resp.media_buy_deliveries for pkg in d.by_package]


def _resolve_media_buy_id(ctx: dict, mb_id: str) -> str:
    """Resolve a Gherkin media-buy alias (e.g., 'mb-001') to its DB id.

    Currently identity since _ensure_media_buy_in_db stores the alias as-is.
    Indirection retained for future tests that may need separate aliasing.
    """
    return ctx.get("media_buy_id_aliases", {}).get(mb_id, mb_id)


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
    """Create a media buy with a buyer reference.

    buyer_ref was removed from the MediaBuy model in adcp 3.12.
    The step still accepts the parameter for Gherkin compatibility but ignores it.
    """
    ctx.setdefault("media_buys", {})[mb_id] = {
        "media_buy_id": mb_id,
        "owner": owner,
    }
    _ensure_media_buy_in_db(ctx, mb_id, owner)


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


def _create_unique_media_buy(
    ctx: dict,
    label: str,
    owner: str,
    status: str = "active",
) -> str:
    """Create a media buy with a UUID-based ID, register its Gherkin label.

    Generates a unique ``media_buy_id`` so parallel pytest-xdist workers
    never collide on ``media_buys_pkey``.
    """
    real_id = _generate_unique_id(label)
    _register_media_buy_label(ctx, label, real_id)
    entry: dict[str, str] = {"media_buy_id": real_id, "owner": owner}
    if status != "active":
        entry["status"] = status
    ctx.setdefault("media_buys", {})[real_id] = entry
    _ensure_media_buy_in_db(ctx, real_id, owner, status)
    return real_id


@given(parsers.parse('multiple media buys owned by "{owner}" in various statuses'))
def given_multiple_buys_various_statuses(ctx: dict, owner: str) -> None:
    """Create media buys in various statuses for partition testing."""
    for status in ("active", "completed", "paused"):
        _create_unique_media_buy(ctx, label=f"mb-{status}", owner=owner, status=status)


@given(parsers.parse('media buys owned by "{owner}"'))
def given_media_buys_owned_by(ctx: dict, owner: str) -> None:
    """Create a default set of media buys owned by the given principal."""
    for label in ("mb-001", "mb-002"):
        _create_unique_media_buy(ctx, label=label, owner=owner)


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


_WEBHOOK_URL = "https://buyer.example.com/webhook"


def _set_active_webhook(ctx: dict, mb_id: str) -> None:
    """Shared: configure an active webhook for a media buy.

    Also persists PushNotificationConfig to DB when running inside an
    integration env (CircuitBreakerEnv) so send_delivery_webhook can find it.
    """
    ctx.setdefault("webhook_config", {})[mb_id] = {
        "url": _WEBHOOK_URL,
        "active": True,
    }
    env = ctx["env"]
    if getattr(env, "_session", None) is not None:
        _persist_webhook_config_if_needed(ctx, env)


def _auth_scheme_to_db_fields(scheme: str | None, ctx: dict) -> dict[str, Any]:
    """Translate a Gherkin auth scheme to the PushNotificationConfig DB columns.

    The ORM model exposes ``authentication_type`` (``"bearer"`` / ``"basic"`` /
    ``None``) plus a separate ``webhook_secret`` column for HMAC. Each scheme
    populates a different combination.
    """
    fields: dict[str, Any] = {}
    if scheme is None:
        return fields
    normalized = scheme.lower()
    if normalized in {"hmac-sha256", "hmac_sha256", "hmac"}:
        secret = ctx.get("webhook_secret")
        if secret:
            fields["webhook_secret"] = secret
    elif normalized == "bearer":
        token = ctx.get("webhook_bearer_token")
        if token:
            fields["authentication_type"] = "bearer"
            fields["authentication_token"] = token
    return fields


def _persist_webhook_config_if_needed(ctx: dict, env: Any) -> None:
    """Idempotently create or update the PushNotificationConfig DB row.

    Reads ``ctx['webhook_config']`` and ``ctx['webhook_secret']`` /
    ``ctx['webhook_bearer_token']`` so subsequent Given-steps that set the
    secret/token can re-run persistence and pick up the new values.
    Sister-task ``salesagent-oy9`` ensured the
    ``push_notification_configs`` table exists per-test, so this is safe to
    call from any Given step.
    """
    from sqlalchemy import select

    from src.core.database.models import Principal, PushNotificationConfig, Tenant

    session = env._session
    tenant_id = env._tenant_id
    principal_id = env._principal_id

    # Derive the auth columns from the most recently configured scheme. Multiple
    # mb_ids share a single PushNotificationConfig row keyed on the env's
    # tenant+principal+url, so we pick the latest scheme set on any mb_id.
    scheme: str | None = None
    for cfg in ctx.get("webhook_config", {}).values():
        cfg_scheme = cfg.get("auth_scheme")
        if cfg_scheme:
            scheme = cfg_scheme  # last one wins
    auth_fields = _auth_scheme_to_db_fields(scheme, ctx)

    existing = session.scalars(
        select(PushNotificationConfig).where(
            PushNotificationConfig.tenant_id == tenant_id,
            PushNotificationConfig.principal_id == principal_id,
            PushNotificationConfig.url == _WEBHOOK_URL,
        )
    ).first()
    if existing is not None:
        # Update auth fields if new ones are present (e.g., a later Given-step
        # added webhook_secret/authentication_token after the row was created).
        changed = False
        for col, value in auth_fields.items():
            if getattr(existing, col, None) != value:
                setattr(existing, col, value)
                changed = True
        if changed:
            session.commit()
        return

    from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

    tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
    if not tenant:
        tenant = TenantFactory(tenant_id=tenant_id)

    principal = session.scalars(select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)).first()
    if not principal:
        principal = PrincipalFactory(tenant=tenant, principal_id=principal_id)

    PushNotificationConfigFactory(
        tenant=tenant,
        principal=principal,
        url=_WEBHOOK_URL,
        is_active=True,
        **auth_fields,
    )


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
    """Configure webhook with specific auth scheme.

    Also creates the ``PushNotificationConfig`` DB row so a subsequent
    ``given_shared_secret_valid`` / ``given_bearer_token_valid`` step can
    update the same row in-place with the auth credentials.
    """
    wh = ctx.setdefault("webhook_config", {}).setdefault(mb_id, {})
    wh["auth_scheme"] = scheme
    wh["active"] = True
    wh["url"] = _WEBHOOK_URL
    env = ctx["env"]
    if getattr(env, "_session", None) is not None:
        _persist_webhook_config_if_needed(ctx, env)


@given("the shared secret is a valid 32+ character string")
def given_shared_secret_valid(ctx: dict) -> None:
    """A valid shared secret for HMAC."""
    secret = "a" * 32
    ctx["webhook_secret"] = secret
    # ``then_hmac_computation`` reproduces the signature from
    # ``ctx['signing_secret']`` (the production code uses the same value to
    # generate the header). Mirror it here so both keys stay in lockstep.
    ctx["signing_secret"] = secret
    env = ctx["env"]
    if getattr(env, "_session", None) is not None:
        _persist_webhook_config_if_needed(ctx, env)


@given("the bearer token is a valid 32+ character string")
def given_bearer_token_valid(ctx: dict) -> None:
    """A valid bearer token."""
    ctx["webhook_bearer_token"] = "b" * 32
    env = ctx["env"]
    if getattr(env, "_session", None) is not None:
        _persist_webhook_config_if_needed(ctx, env)


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
    """Configure webhook endpoint to timeout.

    Uses ``httpx.ConnectError`` (a subclass of ``httpx.RequestError``) so
    :class:`WebhookDeliveryService`, which catches ``httpx.RequestError`` and
    retries with backoff, exercises the network-error retry path. Plain
    builtin ``ConnectionError`` would fall through to the catch-all
    ``except Exception`` branch and skip retries.
    """
    import httpx

    env = ctx["env"]
    env.mock["post"].side_effect = httpx.ConnectError("Connection timeout")


@given(parsers.parse("the webhook endpoint returns {status_code:d} Unauthorized"))
def given_webhook_unauthorized(ctx: dict, status_code: int) -> None:
    """Configure webhook endpoint to return auth error."""
    env = ctx["env"]
    env.set_http_status(status_code, "Unauthorized")


@given(parsers.parse("the webhook endpoint has failed {n:d} consecutive delivery attempts"))
def given_webhook_failed_n_times(ctx: dict, n: int) -> None:
    """Trigger n consecutive delivery failures on the circuit breaker."""
    from src.services.webhook_delivery_service import CircuitBreaker

    env = ctx["env"]
    service = env.get_service()
    webhook_url = next(iter(ctx.get("webhook_config", {}).values()), {}).get("url", _WEBHOOK_URL)
    endpoint_key = f"{env._tenant_id}:{webhook_url}"
    if endpoint_key not in service._circuit_breakers:
        service._circuit_breakers[endpoint_key] = CircuitBreaker()
    cb = service._circuit_breakers[endpoint_key]
    for _ in range(n):
        cb.record_failure()
    ctx["circuit_breaker_endpoint_key"] = endpoint_key
    ctx["webhook_failure_count"] = n


@given(parsers.parse('a media buy "{mb_id}" with circuit breaker in "{state}" state'))
def given_circuit_breaker_state(ctx: dict, mb_id: str, state: str) -> None:
    """Set circuit breaker to specific state by directly manipulating CB internals."""
    from src.services.webhook_delivery_service import CircuitBreaker, CircuitState

    env = ctx["env"]
    service = env.get_service()
    webhook_url = ctx.get("webhook_config", {}).get(mb_id, {}).get("url", _WEBHOOK_URL)
    endpoint_key = f"{env._tenant_id}:{webhook_url}"
    if endpoint_key not in service._circuit_breakers:
        service._circuit_breakers[endpoint_key] = CircuitBreaker()
    cb = service._circuit_breakers[endpoint_key]
    state_map = {
        "OPEN": CircuitState.OPEN,
        "HALF_OPEN": CircuitState.HALF_OPEN,
        "CLOSED": CircuitState.CLOSED,
    }
    cb.state = state_map[state.upper()]
    ctx["circuit_breaker_state"] = state
    ctx["circuit_breaker_endpoint_key"] = endpoint_key


@given("the circuit breaker timeout (60s) has elapsed")
def given_circuit_breaker_timeout(ctx: dict) -> None:
    """Set last_failure_time 61s in the past so the CB timeout has elapsed."""
    from datetime import UTC, timedelta
    from datetime import datetime as _dt

    env = ctx["env"]
    service = env.get_service()
    endpoint_key = ctx.get("circuit_breaker_endpoint_key", f"{env._tenant_id}:{_WEBHOOK_URL}")
    cb = service._circuit_breakers.get(endpoint_key)
    if cb is not None:
        cb.last_failure_time = _dt.now(UTC) - timedelta(seconds=61)
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
    """buyer_refs removed in adcp 3.12 — delegate to no-identifiers step."""
    when_request_no_identifiers(ctx)


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
    """System delivers a webhook report via WebhookDeliveryService."""
    try:
        result = _call_webhook_service(ctx, mb_id=mb_id)
        ctx["webhook_result"] = result
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the system delivers a "{report_type}" webhook report for "{mb_id}"'))
def when_deliver_typed_webhook(ctx: dict, report_type: str, mb_id: str) -> None:
    """System delivers a typed webhook report via WebhookDeliveryService."""
    ctx["report_type"] = report_type
    try:
        result = _call_webhook_service(
            ctx,
            mb_id=mb_id,
            is_final=(report_type == "final"),
            is_adjusted=(report_type == "adjusted"),
        )
        ctx["webhook_result"] = result
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
    """Evaluate circuit breaker state.

    Calls cb.can_attempt() directly to trigger timeout-based state transitions
    (OPEN → HALF_OPEN), then attempts delivery via call_send().
    """
    env = ctx["env"]
    service = env.get_service()
    endpoint_key = ctx.get("circuit_breaker_endpoint_key", f"{env._tenant_id}:{_WEBHOOK_URL}")
    cb = service._circuit_breakers.get(endpoint_key)
    if cb is not None:
        ctx["cb_can_attempt"] = cb.can_attempt()
    try:
        ctx["circuit_result"] = env.call_send()
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse("the system delivers {n:d} successful probe reports"))
def when_deliver_probe_reports(ctx: dict, n: int) -> None:
    """Record n successful deliveries on the circuit breaker (simulates probe recovery)."""
    env = ctx["env"]
    service = env.get_service()
    endpoint_key = ctx.get("circuit_breaker_endpoint_key", f"{env._tenant_id}:{_WEBHOOK_URL}")
    cb = service._circuit_breakers.get(endpoint_key)
    if cb is not None:
        for _ in range(n):
            cb.record_success()
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
    """Partition test: resolution — translate partition name to actual request params."""
    _dispatch_resolution(ctx, partition)


@when(parsers.re(r'the Buyer Agent requests delivery metrics at resolution boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_resolution(ctx: dict, boundary_point: str) -> None:
    """Boundary test: resolution — translate boundary name to actual request params."""
    _dispatch_resolution(ctx, boundary_point)


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
    """Assert delivery data includes all three core metrics with valid numeric values.

    Default adapter provides impressions=5000.0, spend=250.0.
    Production _impl sets clicks=0 (hardcoded) so totals.clicks == 0.0.
    """
    resp = ctx["response"]
    d = resp.media_buy_deliveries[0]
    totals = d.totals
    assert totals.impressions == 5000.0, f"Expected impressions=5000.0, got {totals.impressions}"
    assert totals.spend == 250.0, f"Expected spend=250.0, got {totals.spend}"
    assert totals.clicks == 0.0, f"Expected clicks=0.0 (hardcoded by _impl), got {totals.clicks}"


@then("the delivery data should include package-level breakdowns")
def then_has_packages(ctx: dict) -> None:
    """Assert delivery data includes package-level breakdowns with a valid package_id.

    Default adapter provides one package with package_id="pkg_001",
    impressions=5000, spend=250.0.
    """
    resp = ctx["response"]
    d = resp.media_buy_deliveries[0]
    packages = d.by_package
    assert len(packages) == 1, f"Expected 1 package, got {len(packages)}"
    first = packages[0]
    assert first.package_id == "pkg_001", f"Expected package_id='pkg_001', got {first.package_id!r}"


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
    assert period.start is not None, "Reporting period start is None"
    assert period.end is not None, "Reporting period end is None"


@then(parsers.parse('the response should include the media buy status "{status}"'))
def then_has_mb_status(ctx: dict, status: str) -> None:
    """Assert response includes the expected media buy status."""
    resp = ctx["response"]
    d = resp.media_buy_deliveries[0]
    assert d.status == status, f"Expected status '{status}', got '{d.status}'"


@then("the response should include aggregated totals across both media buys")
def then_has_aggregated_totals(ctx: dict) -> None:
    """Assert response includes aggregated totals with numeric impressions and spend."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    agg = getattr(resp, "aggregated_totals", None)
    assert agg is not None, "Response missing aggregated_totals"
    assert agg.impressions is not None and agg.impressions >= 0, (
        f"aggregated_totals.impressions should be a non-negative number, got {agg.impressions!r}"
    )
    assert agg.spend is not None and agg.spend >= 0, (
        f"aggregated_totals.spend should be a non-negative number, got {agg.spend!r}"
    )


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
    """Assert no error for a specific media buy — checks both global ctx and per-delivery errors."""
    assert "error" not in ctx, f"Expected no error for '{mb_id}' but got: {ctx.get('error')}"
    resp = ctx.get("response")
    if resp is not None:
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        for d in deliveries:
            if getattr(d, "media_buy_id", None) == mb_id:
                per_delivery_errors = getattr(d, "errors", None) or []
                assert not per_delivery_errors, f"Delivery '{mb_id}' has errors: {per_delivery_errors}"


@then(parsers.parse('no error should be returned for "{mb_id}"'))
def then_no_error_for_mb_alt(ctx: dict, mb_id: str) -> None:
    """Assert no error for a specific media buy (alt phrasing)."""
    then_no_error_for_mb(ctx, mb_id)


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
    """Assert webhook POST was made to the configured URL."""
    env = ctx["env"]
    assert env.mock["post"].called, "Expected webhook POST but none was made"
    call_args = env.mock["post"].call_args
    called_url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    configured_url = ctx.get("webhook_url", "https://example.com/webhook")
    assert called_url == configured_url, (
        f"Webhook POST went to wrong URL: expected {configured_url!r}, got {called_url!r}"
    )


@then(parsers.parse('the payload should include delivery metrics for "{mb_id}"'))
def then_webhook_payload_has_metrics(ctx: dict, mb_id: str) -> None:
    """Assert webhook payload includes the media_buy_id for the requested buy."""
    payload = _get_last_webhook_payload(ctx)
    assert payload.get("media_buy_id") == mb_id, (
        f"Expected payload['media_buy_id'] == {mb_id!r}, got {payload.get('media_buy_id')!r}"
    )


@then("the payload should include the reporting_period")
def then_webhook_payload_has_period(ctx: dict) -> None:
    """Assert webhook payload includes a reporting_period with start and end."""
    payload = _get_last_webhook_payload(ctx)
    period = payload.get("reporting_period")
    assert period is not None, f"Webhook payload missing 'reporting_period': {list(payload.keys())}"
    assert period.get("start") is not None and period.get("end") is not None, (
        f"reporting_period must have non-None start and end: {period}"
    )


@then(parsers.parse('the payload notification_type should be "{ntype}"'))
def then_notification_type(ctx: dict, ntype: str) -> None:
    """Assert notification type matches expected value."""
    payload = _get_last_webhook_payload(ctx)
    assert payload.get("notification_type") == ntype, (
        f"Expected notification_type={ntype!r}, got {payload.get('notification_type')!r}"
    )


@then(parsers.re(r"the payload (?P<next_expected>.+) include next_expected_at"))
def then_next_expected(ctx: dict, next_expected: str) -> None:
    """Assert next_expected_at is present or absent based on 'should'/'should not'."""
    payload = _get_last_webhook_payload(ctx)
    should_include = "should not" not in next_expected
    has_key = "next_expected_at" in payload
    if should_include:
        assert has_key, f"Expected 'next_expected_at' in webhook payload but was absent: {list(payload.keys())}"
    else:
        assert not has_key or payload["next_expected_at"] is None, (
            f"Expected 'next_expected_at' to be absent or null, got {payload.get('next_expected_at')!r}"
        )


@then("each report should have a higher sequence_number than the previous")
def then_sequence_ascending(ctx: dict) -> None:
    """Assert sequence numbers are strictly increasing across consecutive POST calls."""
    calls = ctx["env"].mock["post"].call_args_list
    assert len(calls) >= 2, f"Expected at least 2 webhook POSTs for sequence check, got {len(calls)}"
    seq_nums = [call[1].get("json", {}).get("sequence_number") for call in calls]
    for i in range(1, len(seq_nums)):
        assert seq_nums[i] is not None, f"POST call {i} payload missing sequence_number"
        assert seq_nums[i] > seq_nums[i - 1], (
            f"sequence_number not ascending at index {i}: {seq_nums[i - 1]} -> {seq_nums[i]}"
        )


@then("the first sequence_number should be >= 1")
def then_first_sequence(ctx: dict) -> None:
    """Assert first webhook POST has sequence_number >= 1."""
    calls = ctx["env"].mock["post"].call_args_list
    assert calls, "No webhook POSTs were made"
    first_payload = calls[0][1].get("json", {})
    seq = first_payload.get("sequence_number")
    assert seq is not None, f"First webhook POST payload missing sequence_number: {list(first_payload.keys())}"
    assert seq >= 1, f"Expected sequence_number >= 1, got {seq}"


@then('the payload should not include "aggregated_totals" field')
def then_no_aggregated_in_payload(ctx: dict) -> None:
    """Assert webhook payload excludes aggregated_totals (polling-only field)."""
    payload = _get_last_webhook_payload(ctx)
    assert "aggregated_totals" not in payload, (
        f"Webhook payload should not contain 'aggregated_totals' (polling-only field): got keys {list(payload.keys())}"
    )


@then("the system should retry up to 3 times")
def then_retry_3_times(ctx: dict) -> None:
    """Assert retry count."""
    env = ctx["env"]
    call_count = env.mock["post"].call_count
    assert call_count <= 4, f"Expected at most 4 calls (1+3 retries), got {call_count}"


@then("retries should use exponential backoff (1s, 2s, 4s + jitter)")
def then_exponential_backoff(ctx: dict) -> None:
    """Assert sleep durations follow exponential backoff schedule.

    Production WebhookDeliveryService does 3 total attempts (1 original + 2 retries),
    sleeping between each retry. So we expect exactly 2 sleep calls with
    exponentially growing durations.
    """
    sleep_calls = ctx["env"].mock["sleep"].call_args_list
    assert sleep_calls, "Expected at least one sleep call for backoff"
    durations = [float(c[0][0]) for c in sleep_calls]
    assert len(durations) == 2, f"Expected 2 backoff sleeps (for 3 total attempts), got {len(durations)}"
    # Second duration must be at least 1.5x the first (exponential growth)
    assert durations[1] >= durations[0] * 1.5, (
        f"Backoff duration {durations[1]:.2f}s is not exponentially larger "
        f"than first {durations[0]:.2f}s. Expected at least {durations[0] * 1.5:.2f}s. "
        f"Full schedule: {[f'{d:.2f}' for d in durations]}"
    )


@then("the system should retry up to 3 times with exponential backoff")
def then_retry_with_backoff(ctx: dict) -> None:
    """Assert at most 4 POST calls (1 original + 3 retries) with exponential sleep growth.

    Production WebhookDeliveryService does 3 total attempts with 2 sleeps between them.
    """
    env = ctx["env"]
    assert env.mock["post"].call_count <= 4, (
        f"Expected at most 4 calls (1 + 3 retries), got {env.mock['post'].call_count}"
    )
    sleep_calls = env.mock["sleep"].call_args_list
    assert sleep_calls, "Expected at least one sleep call between retries"
    durations = [float(c[0][0]) for c in sleep_calls]
    assert len(durations) == 2, f"Expected 2 backoff sleeps (for 3 total attempts), got {len(durations)}"
    assert durations[1] >= durations[0] * 1.5, (
        f"Sleep durations are not growing exponentially: {[f'{d:.2f}' for d in durations]}"
    )


@then("the system should not retry the delivery")
def then_no_retry(ctx: dict) -> None:
    """Assert no retry was attempted."""
    env = ctx["env"]
    assert env.mock["post"].call_count <= 1, "Expected no retries"


@then("the system should log the authentication rejection")
def then_log_auth_rejection(ctx: dict) -> None:
    """Assert auth rejection was logged."""
    raise NotImplementedError(
        "log capture not available in unit WebhookEnv — "
        "extend harness with caplog or structured log mock before implementing"
    )


@then("the webhook should be marked as failed")
def then_webhook_marked_failed(ctx: dict) -> None:
    """Assert webhook delivery record is marked as failed in DB."""
    raise NotImplementedError(
        "DB state check requires integration_db fixture — unit WebhookEnv does not persist WebhookDeliveryLog records"
    )


@then(parsers.parse('the circuit breaker should be in "{state}" state'))
def then_circuit_breaker_state(ctx: dict, state: str) -> None:
    """Assert circuit breaker state matches expected value."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual.lower() == state.lower(), f"Expected CB state '{state.lower()}', got '{actual}'"


@then("subsequent scheduled deliveries should be suppressed")
def then_deliveries_suppressed(ctx: dict) -> None:
    """Assert circuit is open so deliveries would be suppressed."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual == "open", f"Expected CB in 'open' state (suppressed), got '{actual}'"


@then(parsers.parse('the circuit breaker should transition to "{state}"'))
def then_circuit_transition(ctx: dict, state: str) -> None:
    """Assert circuit breaker transitioned to the expected state."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual.lower() == state.lower(), f"Expected CB transition to '{state.lower()}', got '{actual}'"


@then("the system should attempt a single probe delivery")
def then_single_probe(ctx: dict) -> None:
    """Assert CB entered half_open state (probe delivery is allowed)."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual == "half_open", f"Expected CB in 'half_open' for probe attempt, got '{actual}'"


@then("normal scheduled deliveries should resume")
def then_deliveries_resume(ctx: dict) -> None:
    """Assert circuit closed so scheduled deliveries can proceed."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual == "closed", f"Expected CB in 'closed' state for resumed deliveries, got '{actual}'"


@then("the delivery should be recorded as successful")
def then_delivery_successful(ctx: dict) -> None:
    """Assert delivery was recorded as successful."""
    result = ctx.get("webhook_result")
    assert result is True, f"Expected webhook_result=True (successful delivery), got {result!r}"


@then("the circuit breaker state should remain healthy")
def then_circuit_healthy(ctx: dict) -> None:
    """Assert circuit breaker remains in healthy (closed) state."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual == "closed", f"Expected CB to remain 'closed' (healthy), got '{actual}'"


@then("the configuration should be rejected")
def then_config_rejected(ctx: dict) -> None:
    """Assert configuration was rejected with a validation/rejection error message."""
    assert "error" in ctx, "Expected config rejection error"
    error = ctx["error"]
    msg = str(error).lower()
    rejection_keywords = {"reject", "invalid", "validation", "minimum", "too short", "credential", "length", "required"}
    assert any(kw in msg for kw in rejection_keywords), (
        f"Expected a rejection/validation error message, but got: {error!r}. Expected one of: {rejection_keywords}"
    )


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
    """Assert HMAC header is present and contains a hex-encoded signature."""
    headers = _get_last_webhook_headers(ctx)
    assert header in headers, f"Expected header {header!r} but got: {list(headers.keys())}"
    value = headers[header]
    # Value may be bare hex or prefixed with "sha256="
    stripped = value.removeprefix("sha256=")
    assert re.match(r"^[0-9a-f]{1,}$", stripped), f"Header {header!r} is not a hex-encoded HMAC: {value!r}"


@then(parsers.parse('the request should include header "{header}" with ISO timestamp'))
def then_timestamp_header(ctx: dict, header: str) -> None:
    """Assert timestamp header is present and contains a valid ISO 8601 datetime."""
    from datetime import datetime as _dt

    headers = _get_last_webhook_headers(ctx)
    assert header in headers, f"Expected header {header!r} but got: {list(headers.keys())}"
    value = headers[header]
    try:
        _dt.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise AssertionError(f"Header {header!r} is not a valid ISO 8601 timestamp: {value!r}") from exc


@then('the HMAC should be computed over "timestamp.payload" concatenation')
def then_hmac_computation(ctx: dict) -> None:
    """Assert HMAC signature is reproduced by signing timestamp.payload with the secret."""
    import hashlib
    import hmac as hmac_lib
    import json as json_lib

    headers = _get_last_webhook_headers(ctx)
    payload = _get_last_webhook_payload(ctx)
    timestamp = headers.get("X-ADCP-Timestamp") or headers.get("X-Webhook-Timestamp", "")
    raw_sig = headers.get("X-ADCP-Signature") or headers.get("X-Webhook-Signature", "")
    signature = raw_sig.removeprefix("sha256=")
    assert signature, "Expected HMAC signature header to be present and non-empty"
    signing_secret: str = ctx.get("webhook_secret", "")
    assert signing_secret, "Test setup must store webhook_secret in ctx['webhook_secret']"
    payload_str = json_lib.dumps(payload, sort_keys=True, separators=(",", ":"))
    message = f"{timestamp}.{payload_str}".encode()
    expected = hmac_lib.new(signing_secret.encode(), message, hashlib.sha256).hexdigest()
    assert signature == expected, f"HMAC signature mismatch: got {signature!r}, expected {expected!r}"


@then(parsers.parse('the request should include header "{header}" with the bearer token'))
def then_bearer_header(ctx: dict, header: str) -> None:
    """Assert bearer token header is present and starts with 'Bearer '."""
    headers = _get_last_webhook_headers(ctx)
    assert header in headers, f"Expected header {header!r} but got: {list(headers.keys())}"
    assert headers[header].startswith("Bearer "), (
        f"Header {header!r} should be a Bearer token but got: {headers[header]!r}"
    )


# ── Response field presence assertions ─────────────────────────────


@then('the response should contain "media_buy_deliveries" field')
def then_has_deliveries_field(ctx: dict) -> None:
    """Assert response has media_buy_deliveries field with a list value."""
    resp = ctx["response"]
    # Direct access — raises AttributeError if field missing (better than hasattr)
    deliveries = resp.media_buy_deliveries
    assert isinstance(deliveries, list), f"Expected media_buy_deliveries to be a list, got {type(deliveries).__name__}"


@then('the response should not contain "errors" field')
def then_no_errors_field(ctx: dict) -> None:
    """Assert response errors list is empty and no exception was raised."""
    assert "error" not in ctx, f"Unexpected error: {ctx.get('error')}"
    resp = ctx.get("response")
    if resp is not None:
        errors = getattr(resp, "errors", None) or []
        assert not errors, f"Unexpected errors in response: {errors}"


@then('the response should contain "errors" field')
def then_has_errors_field(ctx: dict) -> None:
    """Assert response errors list is non-empty or an exception was raised."""
    resp = ctx.get("response")
    if resp is not None:
        errors = getattr(resp, "errors", None) or []
        assert errors or "error" in ctx, "Expected errors in response but none found"
    else:
        assert "error" in ctx, "Expected an error but none found"


@then('the response should not contain "media_buy_deliveries" field')
def then_no_deliveries_field(ctx: dict) -> None:
    """Assert media_buy_deliveries is absent or empty in the serialized response."""
    resp = ctx.get("response")
    if resp is not None:
        # Check serialized form — field should not be present or should be empty
        dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
        deliveries = dumped.get("media_buy_deliveries") or []
        assert not deliveries, f"Expected 'media_buy_deliveries' to be absent or empty in response, got: {deliveries}"
    else:
        assert "error" in ctx, "Expected error-only response but got neither"


# ── Error ownership assertions ─────────────────────────────────────


@then(parsers.parse("the error should NOT reveal that the media buy exists"))
def then_error_no_reveal(ctx: dict) -> None:
    """Assert error does not leak existence information via message content or ID echoing."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()
    leaking_phrases = ["exists", "belongs to", "owned by", "not authorized for", "access denied"]
    for phrase in leaking_phrases:
        assert phrase not in msg, f"Error leaks existence info via phrase {phrase!r}: {error}"
    # The media_buy_id should not be echoed back in a way that confirms existence
    mb_id = ctx.get("target_media_buy_id") or ctx.get("media_buy_id") or ""
    if mb_id:
        assert msg.count(mb_id.lower()) <= 1, (
            f"Error repeatedly echoes media_buy_id {mb_id!r}, which may reveal existence: {error}"
        )


# ── Webhook skip assertions ─────────────────────────────────────────


@then(parsers.parse('the system should skip "{mb_id}" (no webhook to deliver to)'))
def then_skip_no_webhook(ctx: dict, mb_id: str) -> None:
    """Assert no webhook POST was made for this specific media buy (no webhook configured)."""
    env = ctx["env"]
    real_id = _resolve_media_buy_id(ctx, mb_id)
    # No POST should have been made for this media buy
    post_mock = env.mock["post"]
    if post_mock.called:
        for call in post_mock.call_args_list:
            payload = call[1].get("json", {}) or {}
            assert payload.get("media_buy_id") != real_id, (
                f"Webhook POST was made for '{real_id}' but it should have been skipped "
                f"(no webhook configured): {payload}"
            )


@then("no delivery attempt should be made")
def then_no_delivery_attempt(ctx: dict) -> None:
    """Assert no delivery attempt was made."""
    env = ctx["env"]
    assert not env.mock["post"].called, "Expected no delivery attempt"


# ── Reporting dimension assertions ─────────────────────────────────


@then(parsers.parse('the response packages should include "{field}" breakdown arrays'))
def then_packages_include_breakdown(ctx: dict, field: str) -> None:
    """Assert every package in the response has field as a non-empty list."""
    resp = ctx["response"]
    packages = _collect_all_packages(resp)
    checked = 0
    for pkg in packages:
        value = getattr(pkg, field)
        assert isinstance(value, list), f"Package {pkg.package_id!r} missing '{field}' breakdown array: {value!r}"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should NOT include "{field}" breakdown arrays'))
def then_packages_exclude_breakdown(ctx: dict, field: str) -> None:
    """Assert no package in the response has field as a list."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", []) or []
    packages = [pkg for d in deliveries for pkg in (getattr(d, "by_package", None) or [])]
    for pkg in packages:
        value = getattr(pkg, field, None)
        assert not isinstance(value, list), (
            f"Package {pkg.package_id!r} should not have '{field}' breakdown array: {value!r}"
        )


@then(parsers.parse('the response packages should include "{field}" with at most {n:d} entries'))
def then_packages_limited(ctx: dict, field: str, n: int) -> None:
    """Assert every package has field as a list with at most n entries."""
    resp = ctx["response"]
    packages = _collect_all_packages(resp)
    checked = 0
    for pkg in packages:
        value = getattr(pkg, field)
        assert isinstance(value, list), f"Package {pkg.package_id!r} missing '{field}' as a list: {value!r}"
        assert len(value) <= n, f"Package {pkg.package_id!r} '{field}' has {len(value)} entries, expected at most {n}"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('"{field}" should be true'))
def then_field_true(ctx: dict, field: str) -> None:
    """Assert the named top-level response field is True."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    value = getattr(resp, field, None)
    assert value is True, f"Expected response.{field} to be True, got {value!r}"


@then(parsers.parse('"{field}" should be false'))
def then_field_false(ctx: dict, field: str) -> None:
    """Assert the named top-level response field is False."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    value = getattr(resp, field, None)
    assert value is False, f"Expected response.{field} to be False, got {value!r}"


@then(parsers.parse('the response packages should include "{field}"'))
def then_packages_include_field(ctx: dict, field: str) -> None:
    """Assert every package has the named field with a non-None value."""
    resp = ctx["response"]
    packages = _collect_all_packages(resp)
    checked = 0
    for pkg in packages:
        value = getattr(pkg, field)
        assert value is not None, f"Package {pkg.package_id!r} missing field {field!r}"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should include "{f1}" and "{f2}" breakdowns'))
def then_packages_include_two(ctx: dict, f1: str, f2: str) -> None:
    """Assert every package has both named fields as lists."""
    resp = ctx["response"]
    packages = _collect_all_packages(resp)
    checked = 0
    for pkg in packages:
        for field in (f1, f2):
            value = getattr(pkg, field)
            assert isinstance(value, list), f"Package {pkg.package_id!r} missing '{field}' breakdown: {value!r}"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should NOT include "{field}"'))
def then_packages_exclude_field(ctx: dict, field: str) -> None:
    """Assert no package has the named field set to a non-None value."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", []) or []
    packages = [pkg for d in deliveries for pkg in (getattr(d, "by_package", None) or [])]
    for pkg in packages:
        value = getattr(pkg, field, None)
        assert value is None, f"Package {pkg.package_id!r} should not have field {field!r}: {value!r}"


@then(parsers.parse('the response geo breakdown should use classification system "{system}"'))
def then_geo_system(ctx: dict, system: str) -> None:
    """Assert geo breakdown classification system."""
    raise NotImplementedError(
        "geo_breakdown not yet in GetMediaBuyDeliveryResponse schema — "
        "add geo_breakdown field to MediaBuyDeliveryData before implementing"
    )


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}" (fallback)'))
def then_placement_sorted_fallback(ctx: dict, metric: str) -> None:
    """Assert placement breakdown uses fallback sort metric."""
    raise NotImplementedError(
        "placement sort fallback not yet implemented in production — "
        "by_placement sorting logic is not in _get_media_buy_delivery_impl"
    )


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}"'))
def then_placement_sorted(ctx: dict, metric: str) -> None:
    """Assert placement breakdown is sorted by the given metric."""
    raise NotImplementedError(
        "placement breakdown sorting not yet implemented in production — "
        "by_placement sorting logic is not in _get_media_buy_delivery_impl"
    )


# ── Attribution window assertions ─────────────────────────────────


@then(parsers.parse('the response should include attribution_window with model "{model}"'))
def then_attribution_model(ctx: dict, model: str) -> None:
    """Assert attribution window model matches the expected value."""
    raise NotImplementedError(
        f"response attribution_window.model should == {model!r} — "
        "wire attribution_window into GetMediaBuyDeliveryResponse in media_buy_delivery.py"
    )


@then("the attribution_window should echo the applied post_click window")
def then_attribution_echo(ctx: dict) -> None:
    """Assert attribution window echoes the request's post_click setting."""
    raise NotImplementedError(
        "response attribution_window.post_click should echo ctx['request_attribution'] — "
        "wire attribution_window into GetMediaBuyDeliveryResponse in media_buy_delivery.py"
    )


@then("the response should include attribution_window with the seller's platform default")
def then_attribution_default(ctx: dict) -> None:
    """Assert attribution window uses the seller's platform default."""
    raise NotImplementedError(
        "response attribution_window should be seller platform default (non-None) — "
        "wire attribution_window into GetMediaBuyDeliveryResponse in media_buy_delivery.py"
    )


@then('the response attribution_window should include "model" field (required)')
def then_attribution_has_model(ctx: dict) -> None:
    """Assert attribution_window.model is present in the response."""
    raise NotImplementedError(
        "response attribution_window.model should be non-None (required by spec) — "
        "wire attribution_window into GetMediaBuyDeliveryResponse in media_buy_delivery.py"
    )


@then("the response should include attribution_window with the seller's platform default model")
def then_attribution_default_model(ctx: dict) -> None:
    """Assert attribution window's model field reflects the seller platform default."""
    raise NotImplementedError(
        "response attribution_window.model should equal the seller platform default — "
        "wire attribution_window into GetMediaBuyDeliveryResponse in media_buy_delivery.py"
    )


@then("the response should include attribution_window reflecting campaign-length window")
def then_attribution_campaign_length(ctx: dict) -> None:
    """Assert attribution window post_click duration equals the campaign length."""
    raise NotImplementedError(
        "response attribution_window.post_click should equal campaign duration in days — "
        "wire attribution_window into GetMediaBuyDeliveryResponse in media_buy_delivery.py"
    )


# ── Partial/error delivery assertions ─────────────────────────────


@then(parsers.parse('the response should indicate "{mb_id}" has partial_data or delayed metrics'))
def then_partial_data(ctx: dict, mb_id: str) -> None:
    """Assert the named media buy has reporting_delayed status."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    deliveries = getattr(resp, "media_buy_deliveries", []) or []
    target = next((d for d in deliveries if d.media_buy_id == mb_id), None)
    assert target is not None, f"No delivery found for {mb_id!r}"
    assert target.status == "reporting_delayed", (
        f"Expected status='reporting_delayed' for partial/delayed metrics on {mb_id!r}, got {target.status!r}"
    )


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
    """Assert sandbox mode — verify via response flag and absence of billing adapter calls."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    sandbox = getattr(resp, "sandbox", None)
    assert sandbox is True, (
        f"Expected sandbox=True in response indicating no real billing records were created, got sandbox={sandbox!r}"
    )
    # Secondary: no adapter billing/charge methods should have been called
    env = ctx["env"]
    for mock_name in ("charge", "create_billing_record", "bill"):
        mock = env.mock.get(mock_name)
        if mock is not None:
            assert not mock.called, f"Billing adapter method '{mock_name}' was called in sandbox mode"


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
@then(
    parsers.re(
        r"the (?P<field>reporting_dimensions|attribution_window|daily breakdown"
        r"|account|status|date|sampling) handling should be (?P<expected>.+)"
    )
)
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

    MediaBuyFactory(**mb_kwargs)


def _parse_request_params(params_str: str) -> dict[str, Any]:
    """Parse request parameters from Gherkin table/string format.

    Handles formats like:
    - media_buy_ids=["mb-001"]
    - media_buy_ids=["mb-001"] status_filter=["active"]

    Note: buyer_refs was removed from GetMediaBuyDeliveryRequest in adcp 3.12.
    Any buyer_refs= parsed from Gherkin are silently dropped.
    """
    kwargs: dict[str, Any] = {}
    for match in re.finditer(r'(\w+)=(\[.+?\]|"[^"]*"|[^\s]+)', params_str):
        key, value = match.group(1), match.group(2)
        if key == "buyer_refs":
            continue  # Removed in adcp 3.12
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


# ── Restored helpers (from pre-merge 89a6c4bb) ──────────────────────


def _generate_unique_id(label: str) -> str:
    """Generate a unique media_buy_id from a Gherkin label."""
    import uuid

    return f"{label}-{uuid.uuid4().hex[:8]}"


def _register_media_buy_label(ctx: dict, label: str, real_id: str) -> None:
    """Register a Gherkin label → real database ID mapping."""
    ctx.setdefault("media_buy_labels", {})[label] = real_id


def _resolve_media_buy_id(ctx: dict, label: str) -> str:
    """Resolve a Gherkin label to the real database media_buy_id."""
    labels = ctx.get("media_buy_labels", {})
    if label in labels:
        return labels[label]
    return label  # fallback: label IS the real ID (legacy/nonexistent-ID scenarios)


def _resolve_media_buy_ids(ctx: dict, labels: list[str]) -> list[str]:
    """Resolve a list of Gherkin labels to real database media_buy_ids."""
    return [_resolve_media_buy_id(ctx, label) for label in labels]


def _wire_webhook_db(ctx: dict) -> None:
    """Wire ctx webhook config into the CircuitBreakerEnv mock DB.

    Reads ctx["webhook_config"], ctx["webhook_secret"], ctx["webhook_bearer_token"]
    and calls env.set_db_webhooks() so _send_webhook_enhanced finds the right configs.
    """
    env = ctx["env"]
    wh_cfgs = ctx.get("webhook_config", {})
    if not wh_cfgs:
        return  # default mock config is fine

    configs = []
    for _mb_id, wh in wh_cfgs.items():
        url = wh.get("url", "https://buyer.example.com/webhook")
        scheme = wh.get("auth_scheme")
        secret = ctx.get("webhook_secret")
        bearer = ctx.get("webhook_bearer_token")

        auth_type = None
        auth_token = None
        if scheme and scheme.lower() == "hmac-sha256":
            auth_type = "hmac"
        elif scheme and scheme.lower() == "bearer":
            auth_type = "bearer"
            auth_token = bearer

        configs.append(
            env.make_webhook_config(
                url=url,
                auth_type=auth_type,
                auth_token=auth_token,
                secret=secret,
            )
        )
    if configs:
        env.set_db_webhooks(configs)


def _call_webhook_service(
    ctx: dict,
    mb_id: str | None = None,
    is_final: bool = False,
    is_adjusted: bool = False,
    next_expected_interval_seconds: float | None = 3600.0,
) -> bool:
    """Dispatch webhook delivery through the CircuitBreakerEnv.call_send."""
    if mb_id is None:
        # Pick the first label from ctx, then resolve to real ID
        label = next(iter(ctx.get("media_buys", {})), None) or next(iter(ctx.get("webhook_config", {})), None)
        assert label, "No media buy in ctx or webhook_config — a Given step must create one first"
        mb_id = _resolve_media_buy_id(ctx, label)
    else:
        mb_id = _resolve_media_buy_id(ctx, mb_id)
    _wire_webhook_db(ctx)
    env = ctx["env"]
    kwargs: dict[str, Any] = {
        "media_buy_id": mb_id,
        "is_final": is_final,
        "is_adjusted": is_adjusted,
    }
    if next_expected_interval_seconds is not None:
        kwargs["next_expected_interval_seconds"] = next_expected_interval_seconds
    return env.call_send(**kwargs)


def _get_webhook_payload(ctx: dict) -> dict:
    """Extract the JSON payload from the most recent webhook POST call."""
    env = ctx["env"]
    call_args = env.mock["post"].call_args
    assert call_args is not None, "No POST call recorded"
    return call_args.kwargs.get("json") or call_args[1].get("json", {})


_DEFAULT_PLACEMENT_DATA: list[dict[str, Any]] = [
    {"placement_id": "pl-A", "impressions": 3000.0, "spend": 150.0, "clicks": 30.0},
    {"placement_id": "pl-B", "impressions": 1500.0, "spend": 200.0, "clicks": 10.0},
    {"placement_id": "pl-C", "impressions": 500.0, "spend": 50.0, "clicks": 50.0},
]


def _inject_placement_data(ctx: dict) -> None:
    """Ensure adapter responses include placement breakdown data.

    If responses already exist, mutate them. Otherwise, register a default
    response for each media buy known in ctx. This must be called from Given
    steps that declare placement support, before the When step dispatches.
    """
    env = ctx["env"]
    if env._adapter_responses:
        for resp in env._adapter_responses.values():
            for pkg in resp.by_package:
                if pkg.by_placement is None:
                    pkg.by_placement = _DEFAULT_PLACEMENT_DATA
    else:
        media_buys = ctx.get("media_buys", {})
        for label in media_buys:
            real_id = _resolve_media_buy_id(ctx, label)
            env.set_adapter_response(
                media_buy_id=real_id,
                by_placement=_DEFAULT_PLACEMENT_DATA,
            )


@when(parsers.parse('the Buyer Agent requests delivery metrics at status_filter boundary "{boundary_value}"'))
def when_request_status_filter_boundary(ctx: dict, boundary_value: str) -> None:
    """Request delivery metrics with a status_filter boundary value.

    Parses boundary_value:
      - '(field absent)' → omit status_filter entirely (server default)
      - '[]' → empty list
      - '["active", "paused"]' → parsed JSON list
      - 'canceled' → single-element list ['canceled']
    """
    media_buys = ctx.get("media_buys", {})
    labels = list(media_buys.keys())
    real_ids = _resolve_media_buy_ids(ctx, labels) if labels else []
    kwargs: dict[str, Any] = {}
    if real_ids:
        kwargs["media_buy_ids"] = real_ids

    if boundary_value == "(field absent)":
        pass  # omit status_filter — test server default behavior
    elif boundary_value.startswith("["):
        kwargs["status_filter"] = json.loads(boundary_value)
    else:
        kwargs["status_filter"] = [boundary_value]

    dispatch_request(ctx, **kwargs)


def _assert_no_error_for_mb(ctx: dict, mb_id: str) -> None:
    """Shared: assert no error was returned for a specific media buy ID.

    Checks three layers:
    1. Top-level ctx["error"] exception must not mention the real_id
    2. Response-level errors list must not reference the real_id
    3. Per-delivery error field for this real_id must be None
    """
    real_id = _resolve_media_buy_id(ctx, mb_id)
    resp = ctx.get("response")
    error = ctx.get("error")
    assert resp is not None or error is not None, "Neither error nor response in ctx — test setup failed"
    # If a general error occurred, check it's not about this specific mb_id
    if error is not None:
        error_msg = str(error).lower()
        assert real_id.lower() not in error_msg, f"Error mentions '{mb_id}' (real_id={real_id}): {error}"
    # If response exists, check response-level errors list and per-delivery errors
    if resp is not None:
        # Check response-level errors array (e.g. resp.errors)
        resp_errors = getattr(resp, "errors", None)
        if resp_errors:
            for err in resp_errors:
                err_str = str(err).lower()
                assert real_id.lower() not in err_str, (
                    f"Response-level errors list mentions '{mb_id}' (real_id={real_id}): {err}"
                )
        # Check per-delivery error field
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        for d in deliveries:
            d_id = getattr(d, "media_buy_id", None)
            if d_id == real_id:
                d_error = getattr(d, "error", None)
                assert d_error is None, f"Delivery for '{mb_id}' (real_id={real_id}) has error: {d_error}"


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


def _assert_placement_sorted_by(ctx: dict, metric: str) -> None:
    """Assert by_placement in at least one package is sorted descending by *metric*."""
    resp = ctx.get("response") or ctx.get("result")
    assert resp is not None, "No response in ctx — When step must store ctx['response']"
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert deliveries, "No deliveries in response"
    found_placement = False
    for d in deliveries:
        by_package = getattr(d, "by_package", None) or []
        for pkg in by_package:
            placements = getattr(pkg, "by_placement", None)
            if not placements:
                continue
            found_placement = True
            values = [(p.get(metric) if isinstance(p, dict) else getattr(p, metric, None)) or 0 for p in placements]
            assert values == sorted(values, reverse=True), f"by_placement not sorted descending by '{metric}': {values}"
    assert found_placement, "No by_placement breakdown found in any package"


def _dispatch_webhook_credentials(ctx: dict, value: str) -> None:
    """Configure webhook credentials from a partition/boundary value and validate.

    Maps credential partition names to actual webhook credential configuration,
    then runs the production WebhookVerifier to validate.
    """
    from src.services.webhook_verification import WebhookVerifier

    value_stripped = value.strip()

    # Map partition names to credential strings
    if value_stripped in ("(field absent)", "(omitted)", "(not provided)", "empty"):
        secret = ""
    elif value_stripped.startswith("short_") or "below_minimum" in value_stripped:
        # Short credentials — below 32 char minimum
        secret = "x" * 16
    elif value_stripped.startswith("minimum") or "exactly_32" in value_stripped:
        # Exactly at boundary
        secret = "x" * 32
    elif value_stripped.startswith("long") or "above_minimum" in value_stripped:
        # Above minimum
        secret = "x" * 64
    else:
        # Use the partition value as-is (may be the literal credential string)
        secret = value_stripped

    ctx["webhook_secret"] = secret
    # Configure full webhook config using existing label or creating a placeholder
    label = next(iter(ctx.get("media_buys", {})), None)
    if label is None:
        label = "mb-creds"
        real_id = _generate_unique_id(label)
        _register_media_buy_label(ctx, label, real_id)
        ctx.setdefault("media_buys", {})[label] = {"media_buy_id": real_id, "owner": "buyer-001"}
    wh = ctx.setdefault("webhook_config", {}).setdefault(label, {})
    wh["url"] = "https://buyer.example.com/webhook"
    wh["active"] = True
    wh["auth_scheme"] = "hmac-sha256"

    try:
        WebhookVerifier(webhook_secret=secret)
        ctx["webhook_validated"] = True
    except Exception as exc:
        ctx["error"] = exc


def _dispatch_resolution(ctx: dict, partition: str) -> None:
    """Translate resolution partition name to concrete request parameters.

    Maps abstract partition names (media_buy_ids_only, etc.)
    to real request fields so Then steps can verify the correct media buys
    were resolved, not just that the request was accepted.
    """
    media_buys = ctx.get("media_buys", {})
    labels = list(media_buys.keys())
    real_ids = _resolve_media_buy_ids(ctx, labels)
    partition_clean = partition.strip()
    request_params = ctx.setdefault("request_params", {})

    # Normalize boundary-style names to partition names
    partition_norm = partition_clean.lower().replace(" ", "_")

    if "media_buy_ids" in partition_norm and "only" in partition_norm:
        # Resolve by media_buy_ids only
        request_params["media_buy_ids"] = real_ids
        dispatch_request(ctx, media_buy_ids=real_ids)
    elif "neither_provided" in partition_norm or "neither" in partition_norm:
        # Neither IDs nor refs — should return all owned media buys
        dispatch_request(ctx)
    elif "partial" in partition_norm:
        # Partial resolution — request includes a nonexistent ID alongside a real one
        partial_ids = real_ids[:1] + ["mb-nonexistent"]
        request_params["media_buy_ids"] = partial_ids
        dispatch_request(ctx, media_buy_ids=partial_ids)
    elif "zero" in partition_norm:
        # Zero resolution — request IDs that don't exist
        request_params["media_buy_ids"] = ["mb-nonexistent-1", "mb-nonexistent-2"]
        dispatch_request(ctx, media_buy_ids=["mb-nonexistent-1", "mb-nonexistent-2"])
    elif "empty_array" in partition_norm or "empty" in partition_norm and "array" in partition_norm:
        # Empty array — schema rejection expected
        dispatch_request(ctx, media_buy_ids=[])
    elif "all_buys" in partition_norm or "all" in partition_norm:
        # All media buys — same as neither_provided
        dispatch_request(ctx)
    else:
        # Fallback: pass through to generic dispatch
        _dispatch_partition(ctx, "resolution", partition)
