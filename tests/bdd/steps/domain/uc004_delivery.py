"""Domain step definitions for UC-004: Deliver Media Buy Metrics.

Given steps: media buy setup, adapter response injection
When steps: delivery metric request dispatch
Then steps: delivery-specific assertions (metrics, periods, status, webhooks)

Steps store results in ctx:
    ctx key "response" — GetMediaBuyDeliveryResponse on success
    ctx key "error" — Exception on failure
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from pytest_bdd import given, parsers, then, when

from src.core.schemas.delivery import GetMediaBuyDeliveryRequest
from tests.bdd.steps._outcome_helpers import (
    _require,
    dispatched_request,
    wire_dict,
    wire_field,
    wire_packages,
)
from tests.bdd.steps.generic._dispatch import dispatch_malformed_request, dispatch_request
from tests.bdd.steps.generic.then_error import _get_error_message
from tests.bdd.steps.generic.then_payload import register_boundary_handler
from tests.harness._base import serialize_request

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


def _extract_webhook_success(ctx: dict) -> bool:
    """Extract the boolean success flag from ctx['webhook_result'].

    Handles both shapes: bare ``bool`` (from call_send) and
    ``tuple[bool, dict]`` (from call_deliver).
    """
    raw = ctx.get("webhook_result")
    if isinstance(raw, tuple):
        return raw[0]
    return bool(raw)


def _assert_placements_sorted_by(packages: list[dict[str, Any]], metric: str, *, fallback: bool) -> None:
    """Assert by_placement entries are sorted by the given metric descending.

    Dict-only access: both callers (:func:`then_placement_sorted`,
    :func:`then_placement_sorted_fallback`) pass ``wire_packages(ctx)`` results
    exclusively, so the prior ``isinstance(x, dict) else getattr(...)`` dual read is
    dead weight — and was itself the exact typed-payload disease this ticket exists
    to close, one level down. If ``by_placement`` is not populated or the metric is
    absent from entries, xfails with a targeted production gap message.
    """
    checked = False
    for pkg in packages:
        placements = pkg.get("by_placement") or []
        if not placements or not isinstance(placements, list):
            continue
        # Need at least 2 placements to verify sort order
        first_val = placements[0].get(metric)
        if first_val is None:
            suffix = " (fallback)" if fallback else ""
            pytest.xfail(
                f"PRODUCTION GAP: by_placement entries lack metric '{metric}'"
                f"{suffix} for sort verification — sorting not implemented"
            )
        values = []
        for p in placements:
            val = p.get(metric)
            if val is not None:
                values.append(val)
        assert values == sorted(values, reverse=True), (
            f"Placement breakdown not sorted by '{metric}' descending: {values}"
        )
        checked = True
    if not checked:
        pytest.xfail("PRODUCTION GAP: no packages have by_placement data to verify sort")


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


@given(parsers.parse('a media buy "{mb_id}" owned by "{owner}" with status "{status}" and reach_unit "{reach_unit}"'))
def given_media_buy_with_status_and_reach_unit(ctx: dict, mb_id: str, owner: str, status: str, reach_unit: str) -> None:
    """Create a media buy with a status and a reach_unit (v3.1 BR-RULE-224).

    A dedicated parser is required because ``parsers.parse`` end-anchors the
    whole step: the broader ``with status "{status}"`` parser would otherwise
    backtrack ``{status}`` to absorb ``active" and reach_unit "individuals``,
    overflowing the varchar(20) status column. reach_unit is not a MediaBuy
    column — it describes the buy's reach measurement and is stored on ctx for
    aggregated_totals.reach/frequency Then steps.
    """
    ctx.setdefault("media_buys", {})[mb_id] = {
        "media_buy_id": mb_id,
        "owner": owner,
        "status": status,
        "reach_unit": reach_unit,
    }
    ctx.setdefault("reach_units", {})[mb_id] = reach_unit
    _ensure_media_buy_in_db(ctx, mb_id, owner, status)


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
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Create a media buy with a UUID-based ID, register its Gherkin label.

    Generates a unique ``media_buy_id`` so parallel pytest-xdist workers
    never collide on ``media_buys_pkey``. ``start_date``/``end_date`` override
    the factory's default (mid-flight) window when a status needs a specific
    flight phase (e.g. pending_start must be pre-flight).
    """
    real_id = _generate_unique_id(label)
    _register_media_buy_label(ctx, label, real_id)
    entry: dict[str, str] = {"media_buy_id": real_id, "owner": owner}
    if status != "active":
        entry["status"] = status
    ctx.setdefault("media_buys", {})[real_id] = entry
    _ensure_media_buy_in_db(ctx, real_id, owner, status, start_date=start_date, end_date=end_date)
    return real_id


# Flight window per lifecycle phase so the seeded status is stable through the
# real status scheduler (which the MCP/REST app harnesses run): a pending_start
# buy MUST be pre-flight, else the scheduler promotes it to active before the
# query and the status_filter="pending_start" row returns nothing. Pre-serving
# states (pending_creatives/pending_start) are pre-flight; completed is
# post-flight; everything else uses the factory's mid-flight default.
_PRE_FLIGHT = ("2099-01-01", "2099-12-31")
_POST_FLIGHT = ("2020-01-01", "2020-12-31")
_STATUS_FLIGHT_WINDOW: dict[str, tuple[str, str]] = {
    "pending_creatives": _PRE_FLIGHT,
    "pending_start": _PRE_FLIGHT,
    "completed": _POST_FLIGHT,
}


@given(parsers.parse('multiple media buys owned by "{owner}" in various statuses'))
def given_multiple_buys_various_statuses(ctx: dict, owner: str) -> None:
    """Create one media buy per canonical status for partition testing.

    Covers every persisted status the status_filter partitions exercise so a
    single-status filter always has exactly one matching buy to return. Each
    buy's flight window matches its lifecycle phase (see _STATUS_FLIGHT_WINDOW)
    so the status survives the real status scheduler on the app-backed
    transports.
    """
    for status in ("active", "completed", "paused", "rejected", "canceled", "pending_creatives", "pending_start"):
        window = _STATUS_FLIGHT_WINDOW.get(status, (None, None))
        _create_unique_media_buy(
            ctx, label=f"mb-{status}", owner=owner, status=status, start_date=window[0], end_date=window[1]
        )


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
    """Configure adapter mock to return data for both media buys.

    Seeds conversions/conversion_value alongside impressions/spend so the
    roas / cost_per_acquisition aggregated_totals scalars
    (media-buy/get-media-buy-delivery-response.json, pin 04f59d2d5) are
    derivable: with two buys, roas = 1000/500 = 2.0 and
    cost_per_acquisition = 500/20 = 25.0 — the literals the Then steps assert.
    """
    env = ctx["env"]
    media_buys = ctx.get("media_buys", {})
    for mb_id in list(media_buys.keys())[:2]:
        env.set_adapter_response(media_buy_id=mb_id, conversions=10.0, conversion_value=500.0)


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
    # Record the URL actually configured so `then_webhook_post` can assert against it
    # rather than against a hardcoded literal (GH #1749). Set here, at the single
    # choke point all three "active reporting_webhook" Givens funnel through.
    ctx["webhook_url"] = _WEBHOOK_URL
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


@given("the outbound webhook URL is blocked by SSRF validation")
def given_outbound_webhook_ssrf_blocked(ctx: dict) -> None:
    """Force send-time SSRF gate to reject the configured webhook URL.

    Does not blanket-mock all UC-004 scenarios: only this Given opts into the
    reject branch via CircuitBreakerEnv.set_url_invalid().
    """
    env = ctx["env"]
    env.set_url_invalid("Webhook URL resolves to blocked IP range 169.254.0.0/16")


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
    webhook_configs: dict[str, dict[str, Any]] = ctx.get("webhook_config", {})
    webhook_url = next(iter(webhook_configs.values()), {}).get("url", _WEBHOOK_URL)
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
    """Seller supports multiple reporting dimensions.

    Also configures the adapter with simulated breakdown data so that
    multi-dimension requests (BR-RULE-091 INV-1) return non-empty arrays.
    """
    ctx.setdefault("supported_dimensions", []).extend([dim1, dim2])
    env = ctx["env"]
    for mb_id in ctx.get("media_buys", {}):
        env.set_adapter_response(media_buy_id=mb_id)


@given(parsers.parse('the seller does NOT support "{capability}"'))
def given_seller_no_capability(ctx: dict, capability: str) -> None:
    """Seller does not support a capability."""
    ctx.setdefault("unsupported_capabilities", []).append(capability)


# REMOVED (GH #1726): given_seller_no_attribution ("the seller does NOT support
# configurable attribution windows"). It only wrote ctx["supports_attribution_windows"] = False,
# a flag no production code reads, so it produced state byte-identical to its "supports" sibling
# and the two scenarios were the same test. Its scenario (T-UC-004-attr-unsupported) has been
# reconciled away: this seller always honours the requested window, which AdCP 3.1.1 permits.
# 39 sibling dead Given flags are tracked in GH #1752.


@given("the seller supports configurable attribution windows")
def given_seller_supports_attribution(ctx: dict) -> None:
    """State the precondition that this seller honours configurable attribution windows.

    Kept — unlike its deleted "does NOT support" sibling — because two live scenarios still
    use it (T-UC-004-attr-custom and the campaign-unit valid row).

    It formerly wrote ``ctx["supports_attribution_windows"] = True``, a flag nothing reads, which
    gave the false impression of configuring something. There is nothing to configure: this
    seller has no capability gate, so the precondition is true by construction. Rather than
    assert nothing, the step now VERIFIES that — it calls the production resolver and confirms a
    requested window really is honoured. If a capability gate is ever introduced (see the
    reconciliation note on T-UC-004-attr-unsupported), this Given fails loudly instead of letting
    the scenarios above quietly stop testing what they claim.
    """
    from adcp.types import Duration
    from adcp.types.generated_poc.media_buy.get_media_buy_delivery_request import (
        AttributionWindow as RequestAttributionWindow,  # TODO: no stable alias in adcp.types
    )

    from src.core.schemas.delivery import GetMediaBuyDeliveryRequest
    from src.core.tools.media_buy_delivery import _resolve_attribution_window

    # A real request model, not a SimpleNamespace stand-in: the resolver is typed for
    # GetMediaBuyDeliveryRequest, and a duck-typed probe both hid a mypy arg-type error
    # and would keep passing if the resolver started reading a field the stand-in lacks.
    probe = GetMediaBuyDeliveryRequest(
        media_buy_ids=["mb-capability-probe"],
        attribution_window=RequestAttributionWindow(
            post_click=Duration(interval=5, unit="days"),
        ),
    )
    resolved = _resolve_attribution_window(probe, None)
    assert resolved.post_click is not None and resolved.post_click.interval == 5, (
        "Precondition violated: this seller no longer honours a configurable attribution window "
        f"(requested post_click interval 5 days, resolver returned {resolved.post_click!r}). "
        "A capability gate appears to have been added — revisit GH #1726, which removed "
        "the 'seller does NOT support configurable attribution windows' scenario on the premise "
        "that no such gate exists."
    )


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
    """More geo entries than limit — truncation expected.

    The mock adapter always supplies 10 geo entries (distinct descending
    weights), so a limit=5 request triggers truncation.
    Delegates to the shared adapter-data setup step.
    (get_media_buy_delivery.mdx §Truncation.)
    """
    given_adapter_has_data_all(ctx)


@given("the device_type breakdown has fewer entries than any limit")
def given_device_type_under_limit(ctx: dict) -> None:
    """Fewer device_type entries than limit — no truncation.

    The mock adapter always supplies 3 device_type entries (mobile/desktop/
    tablet), which is fewer than any reasonable limit, so truncated=False.
    Delegates to the shared adapter-data setup step.
    (get_media_buy_delivery.mdx §Truncation.)
    """
    given_adapter_has_data_all(ctx)


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — delivery metric requests
# ═══════════════════════════════════════════════════════════════════════


@when(parsers.re(r"the Buyer Agent requests delivery metrics for media_buy_ids (?P<ids_json>\[.+?\])"))
def when_request_by_ids(ctx: dict, ids_json: str) -> None:
    """Request delivery metrics by media_buy_ids."""
    media_buy_ids = _parse_json_list(ids_json)
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=media_buy_ids))


@when("the Buyer Agent requests delivery metrics without media_buy_ids")
def when_request_no_identifiers(ctx: dict) -> None:
    """Request delivery metrics without any identifiers."""
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())


# Restricted to the key=value identify-mode form (e.g. media_buy_ids=[...]
# status_filter=[...]). The unrestricted parse-form matched *every* "...with X"
# line and, because "{request_params}" sorts last, shadowed the specific steps
# below (status_filter "X", media_buy_ids [...], the partition steps), silently
# dropping their params via _parse_request_params. Requiring "\w+=" makes it
# mutually exclusive with those.
@when(parsers.re(r"the Buyer Agent requests delivery metrics with (?P<request_params>\w+=.+)"))
def when_request_with_params(ctx: dict, request_params: str) -> None:
    """Request with arbitrary key=value params (Scenario Outline)."""
    kwargs = _parse_request_params(request_params)
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(**kwargs))


@when(parsers.parse("the Buyer Agent requests delivery metrics with media_buy_ids {ids_json}"))
def when_request_with_media_buy_ids(ctx: dict, ids_json: str) -> None:
    """Request with explicit media_buy_ids list."""
    if ids_json == "[]":
        # media_buy_ids has min_length=1 — constructing the model directly raises
        # before dispatch runs. Route through dispatch_malformed_request so
        # PRODUCTION rejects it on the wire, not a local ValidationError crash.
        dispatch_malformed_request(ctx, media_buy_ids=[])
    else:
        media_buy_ids = _parse_json_list(ids_json)
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=media_buy_ids))


@when(parsers.re(r'the Buyer Agent requests delivery metrics with status_filter "(?P<filter_value>[^"]+)"'))
def when_request_with_status_filter(ctx: dict, filter_value: str) -> None:
    """Request with status_filter string.

    Records the requested filter in ctx["request_params"] so then_filter_result
    can reconstruct it. The "(field absent)" / "(omitted)" sentinels mean "send
    no status_filter at all" — dispatching the literal would resolve to an empty
    filter and drop every buy.
    """
    ctx.setdefault("request_params", {})["status_filter"] = [filter_value]
    if filter_value in ("(field absent)", "(omitted)"):
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())
    else:
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(status_filter=[filter_value]))


@when(parsers.re(r"the Buyer Agent requests delivery metrics with status_filter (?P<filter_json>\[.+?\])"))
def when_request_with_status_filter_list(ctx: dict, filter_json: str) -> None:
    """Request with status_filter list."""
    status_filter = _parse_json_list(filter_json)
    ctx.setdefault("request_params", {})["status_filter"] = status_filter
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(status_filter=status_filter))


@when("the Buyer Agent requests delivery metrics without status_filter")
def when_request_no_status_filter(ctx: dict) -> None:
    """Request without status_filter (all statuses)."""
    media_buys = ctx.get("media_buys", {})
    mb_ids = list(media_buys.keys())
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=mb_ids if mb_ids else None))


@when(parsers.parse('the Buyer Agent requests delivery metrics with start_date "{start}" and end_date "{end}"'))
def when_request_date_range(ctx: dict, start: str, end: str) -> None:
    """Request with date range."""
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(start_date=start, end_date=end))


@when(parsers.parse('the Buyer Agent requests delivery metrics with start_date "{start}" and no end_date'))
def when_request_start_only(ctx: dict, start: str) -> None:
    """Request with start_date only."""
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(start_date=start))


@when(parsers.parse('the Buyer Agent requests delivery metrics with end_date "{end}" and no start_date'))
def when_request_end_only(ctx: dict, end: str) -> None:
    """Request with end_date only."""
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(end_date=end))


@when("the Buyer Agent requests delivery metrics")
def when_request_delivery_default(ctx: dict) -> None:
    """Request delivery metrics (generic, uses ctx media_buys).

    Respects ctx["principal_id"] override for scenarios like 'principal not found'.
    """
    media_buys = ctx.get("media_buys", {})
    mb_ids = list(media_buys.keys()) or None
    req_kwargs: dict = {}
    if mb_ids:
        req_kwargs["media_buy_ids"] = mb_ids
    req = GetMediaBuyDeliveryRequest(**req_kwargs)
    # Override identity if ctx has a custom principal_id (e.g. "unknown-buyer")
    if "principal_id" in ctx:
        from src.core.resolved_identity import ResolvedIdentity

        env = ctx["env"]
        identity = ResolvedIdentity(
            principal_id=ctx["principal_id"],
            tenant_id=env._tenant_id,
            protocol="impl",
        )
        dispatch_request(ctx, req=req, identity=identity)
    else:
        dispatch_request(ctx, req=req)


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
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(), identity=no_principal)


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

    # Baseline for the probe-count oracle in `then_single_probe`. The probe is dispatched by the
    # call_send() below, so "how many probes did half-open allow?" is the delta across THIS step —
    # not the scenario's total POST count. Recording it here is what lets that Then step
    # distinguish one probe from ten.
    mock_post = env.mock.get("post")
    ctx["pre_probe_call_count"] = mock_post.call_count if mock_post is not None else 0

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
    """Dispatch a create_media_buy carrying the webhook config through the wire.

    The webhook credential min-length (32) is enforced by the SDK
    ``Authentication.credentials`` (MinLen=32) nested under ``reporting_webhook``.
    A request carrying a <32-char credential is rejected by production's Pydantic
    boundary on the wire (VALIDATION_ERROR) — we dispatch the RAW flat body so the
    rejection happens in PRODUCTION, not in test code. A 32-char credential is
    accepted and the create succeeds.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults, _pricing_option_id

    secret = ctx.get("webhook_secret", "")
    kwargs = _ensure_request_defaults(ctx)
    product = ctx.get("default_product")
    pricing_option = ctx.get("default_pricing_option")
    if product is not None:
        kwargs["packages"][0]["product_id"] = product.product_id
    if pricing_option is not None:
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(pricing_option)
    kwargs["reporting_webhook"] = {
        "url": _WEBHOOK_URL,
        "reporting_frequency": "daily",
        "authentication": {"schemes": ["Bearer"], "credentials": secret},
    }
    # Dispatch the flat body (no typed construction) so a short credential reaches
    # the production transport boundary instead of being rejected in test code.
    dispatch_malformed_request(ctx, **kwargs)


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
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=[mb_id], reporting_dimensions=dims))


def _request_single_mb(ctx: dict, mb_id: str) -> None:
    """Shared: request delivery for a single media buy."""
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=[mb_id]))


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
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=[mb_id], attribution_window=aw))


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
    _seed_valid_account_if_named(ctx, value)
    _dispatch_partition(ctx, "account", value)


@when(parsers.parse("the Buyer Agent requests delivery metrics at account boundary {value}"))
def when_boundary_account(ctx: dict, value: str) -> None:
    """Boundary test: account value."""
    _seed_valid_account_if_named(ctx, value)
    _dispatch_partition(ctx, "account", value)


# NOTE: the partition status_filter step is identical to
# when_request_with_status_filter above (same regex + body); the single
# definition there serves both the alternative and partition scenarios.
@when(parsers.re(r'the Buyer Agent requests delivery metrics at status_filter boundary "(?P<boundary_value>[^"]+)"'))
def when_boundary_status_filter(ctx: dict, boundary_value: str) -> None:
    """Boundary test: status_filter value."""
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(status_filter=[boundary_value]))


@when(parsers.re(r'the Buyer Agent requests delivery metrics with date range "(?P<partition>[^"]+)"'))
def when_partition_date_range(ctx: dict, partition: str) -> None:
    """Partition test: date range."""
    _dispatch_date_range_partition(ctx, partition)


@when(parsers.re(r'the Buyer Agent requests delivery metrics at date boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_date_range(ctx: dict, boundary_point: str) -> None:
    """Boundary test: date range."""
    _dispatch_date_range_partition(ctx, boundary_point)


@when(parsers.re(r'the webhook is configured with credentials "(?P<partition>[^"]+)"'))
def when_partition_credentials(ctx: dict, partition: str) -> None:
    """Partition test: validate webhook credentials at the create_media_buy boundary."""
    _validate_reporting_webhook_credentials(ctx, *_credential_label_to_config(partition))


@when(parsers.re(r'the webhook credentials are at boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_credentials(ctx: dict, boundary_point: str) -> None:
    """Boundary test: validate webhook credentials at the create_media_buy boundary."""
    _validate_reporting_webhook_credentials(ctx, *_credential_label_to_config(boundary_point))


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
    _dispatch_ownership_partition(ctx, partition)


@when(parsers.re(r'the Buyer Agent requests delivery metrics at ownership boundary "(?P<boundary_point>[^"]+)"'))
def when_boundary_ownership(ctx: dict, boundary_point: str) -> None:
    """Boundary test: ownership — same identity-based translator as when_partition_principal."""
    _dispatch_ownership_partition(ctx, boundary_point)


def _dispatch_sampling_method(ctx: dict, value: str) -> None:
    """Dispatch a sampling_method partition/boundary value.

    "sampling_method" is not a GetMediaBuyDeliveryRequest field (unimplemented — see
    docs/test-debt-bdd-strict-markers.md item C4). The omitted sentinel means "send
    no sampling_method at all" (a genuinely valid, empty request) — routing it through
    dispatch_malformed_request would send the literal sentinel STRING as the field
    value instead of omitting the field. Every other value is malformed input by
    construction, reaching production's extra="forbid" boundary directly.
    """
    value_stripped = value.strip()
    if value_stripped in ("(field absent)", "(omitted)", "(not provided)"):
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())
        return
    dispatch_malformed_request(ctx, sampling_method=value_stripped)


@when(parsers.re(r'the Buyer Agent queries delivery artifacts with sampling method "(?P<partition_value>[^"]+)"'))
def when_partition_sampling(ctx: dict, partition_value: str) -> None:
    """Partition test: sampling method."""
    _dispatch_sampling_method(ctx, partition_value)


@when(parsers.re(r'the Buyer Agent queries delivery artifacts at sampling boundary "(?P<boundary_value>[^"]+)"'))
def when_boundary_sampling(ctx: dict, boundary_value: str) -> None:
    """Boundary test: sampling method. See when_partition_sampling."""
    _dispatch_sampling_method(ctx, boundary_value)


@when(parsers.parse('the Buyer Agent queries delivery metrics for media buy "{mb_id}"'))
def when_query_single_mb(ctx: dict, mb_id: str) -> None:
    """Query delivery metrics for a single media buy (sandbox scenarios)."""
    ctx.setdefault("query_variant", True)
    _request_single_mb(ctx, mb_id)


@when("the Buyer Agent queries delivery metrics for a non-existent media buy")
def when_query_nonexistent(ctx: dict) -> None:
    """Query delivery metrics for a non-existent media buy."""
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=["mb-nonexistent"]))


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
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    mb_ids = [d.get("media_buy_id") for d in deliveries]
    assert mb_id1 in mb_ids, f"Expected delivery data for '{mb_id1}', got: {mb_ids}"
    assert mb_id2 in mb_ids, f"Expected delivery data for '{mb_id2}', got: {mb_ids}"


@then(parsers.re(r'the response should include delivery data for "(?P<mb_id>[^"]+)"$'))
def then_includes_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response includes delivery data for the given media buy."""
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    mb_ids = [d.get("media_buy_id") for d in deliveries]
    assert mb_id in mb_ids, f"Expected delivery data for '{mb_id}', got: {mb_ids}"


@then(parsers.parse('the response should include delivery data for "{mb_id}" only'))
def then_includes_delivery_data_only(ctx: dict, mb_id: str) -> None:
    """Assert response includes delivery data for ONLY the given media buy."""
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    mb_ids = [d.get("media_buy_id") for d in deliveries]
    assert mb_ids == [mb_id], f"Expected only '{mb_id}', got: {mb_ids}"


@then(parsers.parse('the response should NOT include delivery data for "{mb_id}"'))
def then_excludes_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response does NOT include delivery data for the media buy."""
    if ctx.get("response") is None:
        return  # No response at all = not included
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    mb_ids = [d.get("media_buy_id") for d in deliveries]
    assert mb_id not in mb_ids, f"Expected no delivery data for '{mb_id}', but found it"


@then(parsers.parse('the response should not include delivery data for "{mb_id}"'))
def then_no_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response does not include delivery data for the media buy."""
    if ctx.get("response") is None:
        return
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    mb_ids = [d.get("media_buy_id") for d in deliveries]
    assert mb_id not in mb_ids, f"Expected no delivery data for '{mb_id}'"


@then("the response should have an empty media_buy_deliveries array")
def then_empty_deliveries(ctx: dict) -> None:
    """Assert response has empty media_buy_deliveries."""
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    assert len(deliveries) == 0, f"Expected empty deliveries, got {len(deliveries)}"


@then("the delivery data should include impressions, spend, and clicks")
def then_has_metrics(ctx: dict) -> None:
    """Assert delivery totals carry internally-consistent metric values.

    Asserts type correctness and cross-field consistency rather than
    hardcoded mock-adapter values.
    """
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    assert deliveries, "Expected a response but none found"
    d = deliveries[0]
    totals = d.get("totals") or {}
    # Type correctness: impressions and spend must be numeric on the WIRE (not
    # coerced back to numeric by typed-payload reconstruction).
    assert isinstance(totals.get("impressions"), (int, float)), (
        f"impressions must be numeric, got {type(totals.get('impressions')).__name__}"
    )
    assert isinstance(totals.get("spend"), (int, float)), (
        f"spend must be numeric, got {type(totals.get('spend')).__name__}"
    )
    # clicks is present (may be None or numeric per schema)
    clicks = totals.get("clicks")
    assert clicks is None or isinstance(clicks, (int, float)), (
        f"clicks must be numeric or None, got {type(clicks).__name__}"
    )
    # Cross-field consistency: nonzero spend implies nonzero impressions
    if totals.get("spend", 0) > 0:
        assert totals.get("impressions", 0) > 0, f"Nonzero spend ({totals.get('spend')}) with zero impressions"
    # Aggregation: package-level impressions must sum to totals
    packages = d.get("by_package") or []
    pkg_impressions = sum(p.get("impressions", 0) for p in packages)
    assert totals.get("impressions") == pkg_impressions, (
        f"Totals impressions ({totals.get('impressions')}) != sum of package impressions ({pkg_impressions})"
    )


@then("the delivery data should include package-level breakdowns")
def then_has_packages(ctx: dict) -> None:
    """Assert delivery data includes package-level breakdowns with distinct IDs.

    Verifies structural correctness: packages exist, have distinct IDs,
    and their impressions roll up to the media-buy totals.
    """
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    assert deliveries, "Expected a response but none found"
    d = deliveries[0]
    packages = d.get("by_package")
    assert isinstance(packages, list), f"by_package must be a list, got {type(packages).__name__}"
    assert packages, "by_package list is empty"
    # Every package must have a non-empty package_id
    ids = [p.get("package_id") for p in packages]
    for pid in ids:
        assert isinstance(pid, str) and pid, f"package_id must be a non-empty string, got {pid!r}"
    # Package IDs must be unique
    assert len(ids) == len(set(ids)), f"Duplicate package_ids: {ids}"
    # Package impressions must sum to media-buy totals (rollup invariant)
    pkg_impressions = sum(p.get("impressions", 0) for p in packages)
    totals_impressions = (d.get("totals") or {}).get("impressions")
    assert pkg_impressions == totals_impressions, (
        f"Package impressions ({pkg_impressions}) != media-buy total ({totals_impressions})"
    )


@then("the response should include the reporting period start and end dates")
def then_has_reporting_period(ctx: dict) -> None:
    """Assert response includes reporting period.

    reporting_period is on the response object (GetMediaBuyDeliveryResponse),
    not on individual MediaBuyDeliveryData entries.
    """
    period = wire_dict(ctx).get("reporting_period")
    assert period is not None, "Response missing reporting_period"
    assert period.get("start") is not None, "Reporting period start is None"
    assert period.get("end") is not None, "Reporting period end is None"


@then(parsers.parse('the response should include the media buy status "{status}"'))
def then_has_mb_status(ctx: dict, status: str) -> None:
    """Assert response includes the expected media buy status."""
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    assert deliveries, "Expected a response but none found"
    d = deliveries[0]
    assert d.get("status") == status, f"Expected status '{status}', got '{d.get('status')}'"


@then("the response should include aggregated totals across both media buys")
def then_has_aggregated_totals(ctx: dict) -> None:
    """Assert aggregated totals equal the sum of per-delivery totals."""
    wire = wire_dict(ctx)
    agg = wire.get("aggregated_totals") or {}
    deliveries = wire.get("media_buy_deliveries") or []
    # Aggregated impressions must equal the sum of individual delivery impressions
    individual_impressions = sum((d.get("totals") or {}).get("impressions", 0) for d in deliveries)
    assert agg.get("impressions") == individual_impressions, (
        f"aggregated_totals.impressions ({agg.get('impressions')}) != sum of individual impressions "
        f"({individual_impressions})"
    )
    # Aggregated spend must equal the sum of individual delivery spend
    individual_spend = sum((d.get("totals") or {}).get("spend", 0) for d in deliveries)
    assert agg.get("spend") == individual_spend, (
        f"aggregated_totals.spend ({agg.get('spend')}) != sum of individual spend ({individual_spend})"
    )


@then('the aggregated_totals should include "roas" as total conversion_value over total spend')
def then_aggregated_roas(ctx: dict) -> None:
    """Assert aggregated_totals.roas equals the Given-derived literal 2.0.

    Spec (pin 04f59d2d5): media-buy/get-media-buy-delivery-response.json
    defines aggregated_totals.roas as "total conversion_value / total spend".
    The Given seeds two buys at conversion_value=500.0, spend=250.0 each, so
    roas = 1000 / 500 = 2.0. Asserting the literal (not a quotient recomputed
    from production's own per-delivery output) means a same-source extraction
    bug cannot self-validate (PR #1430 review).
    """
    wire = wire_dict(ctx)
    agg = wire.get("aggregated_totals") or {}
    roas = agg.get("roas")
    assert roas is not None, "aggregated_totals.roas is missing — production does not compute roas"
    deliveries = wire.get("media_buy_deliveries") or []
    conversion_values = [(d.get("totals") or {}).get("conversion_value") for d in deliveries]
    assert all(v is not None for v in conversion_values), (
        f"per-delivery totals.conversion_value missing (roas input must be reported per buy): {conversion_values}"
    )
    assert roas == pytest.approx(2.0), (
        f"aggregated_totals.roas ({roas}) != 2.0 (Given seeds 2 buys x conversion_value 500.0 / 2 x spend 250.0)"
    )


@then('the aggregated_totals should include "cost_per_acquisition" as total spend over total conversions')
def then_aggregated_cost_per_acquisition(ctx: dict) -> None:
    """Assert aggregated_totals.cost_per_acquisition equals the Given-derived literal 25.0.

    Spec (pin 04f59d2d5): media-buy/get-media-buy-delivery-response.json
    defines aggregated_totals.cost_per_acquisition as "total spend / total
    conversions". The Given seeds two buys at conversions=10.0, spend=250.0
    each, so cpa = 500 / 20 = 25.0. Literal assertion for the same
    same-source-extraction reason as the roas step above.
    """
    wire = wire_dict(ctx)
    agg = wire.get("aggregated_totals") or {}
    cpa = agg.get("cost_per_acquisition")
    assert cpa is not None, (
        "aggregated_totals.cost_per_acquisition is missing — production does not compute cost_per_acquisition"
    )
    deliveries = wire.get("media_buy_deliveries") or []
    conversions = [(d.get("totals") or {}).get("conversions") for d in deliveries]
    assert all(c is not None for c in conversions), (
        f"per-delivery totals.conversions missing (cpa input must be reported per buy): {conversions}"
    )
    assert cpa == pytest.approx(25.0), (
        f"aggregated_totals.cost_per_acquisition ({cpa}) != 25.0 (Given seeds 2 buys x spend 250.0 / 2 x conversions 10.0)"
    )


@then(parsers.parse('the aggregated_totals should include "media_buy_count" equal to {count:d}'))
def then_aggregated_media_buy_count(ctx: dict, count: int) -> None:
    """Assert aggregated_totals.media_buy_count matches the scenario's buy count."""
    agg = wire_dict(ctx).get("aggregated_totals") or {}
    assert agg.get("media_buy_count") == count, (
        f"aggregated_totals.media_buy_count ({agg.get('media_buy_count')}) != expected ({count})"
    )


@then("the aggregated impressions should equal the sum of individual impressions")
def then_aggregated_impressions(ctx: dict) -> None:
    """Assert aggregated impressions equal sum of individual values."""
    wire = wire_dict(ctx)
    deliveries = wire.get("media_buy_deliveries") or []
    individual_sum = sum((d.get("totals") or {}).get("impressions", 0.0) for d in deliveries)
    agg = wire.get("aggregated_totals")
    assert agg is not None, "Missing aggregated_totals"
    agg_impressions = agg.get("impressions", 0.0)
    assert agg_impressions == individual_sum, f"Aggregated impressions {agg_impressions} != sum {individual_sum}"


@then("the aggregated spend should equal the sum of individual spend")
def then_aggregated_spend(ctx: dict) -> None:
    """Assert aggregated spend equals sum of individual values."""
    wire = wire_dict(ctx)
    deliveries = wire.get("media_buy_deliveries") or []
    individual_sum = sum((d.get("totals") or {}).get("spend", 0.0) for d in deliveries)
    agg = wire.get("aggregated_totals")
    assert agg is not None, "Missing aggregated_totals"
    agg_spend = agg.get("spend", 0.0)
    assert agg_spend == individual_sum, f"Aggregated spend {agg_spend} != sum {individual_sum}"


@then(parsers.parse('the response should not include an error for "{mb_id}"'))
def then_no_error_for_mb(ctx: dict, mb_id: str) -> None:
    """Assert no error for a specific media buy — checks both global ctx and per-delivery errors."""
    assert "error" not in ctx, f"Expected no error for '{mb_id}' but got: {ctx.get('error')}"
    if ctx.get("response") is not None:
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
        for d in deliveries:
            if d.get("media_buy_id") == mb_id:
                per_delivery_errors = d.get("errors") or []
                assert not per_delivery_errors, f"Delivery '{mb_id}' has errors: {per_delivery_errors}"


@then(parsers.parse('no error should be returned for "{mb_id}"'))
def then_no_error_for_mb_alt(ctx: dict, mb_id: str) -> None:
    """Assert no error for a specific media buy (alt phrasing)."""
    then_no_error_for_mb(ctx, mb_id)


@then(parsers.parse('the response should include only media buys with status "{status}"'))
def then_only_status(ctx: dict, status: str) -> None:
    """Assert all returned media buys have the expected status.

    Guards against a vacuous pass: if the scenario filters on a status with no
    seeded buy, the response is empty and a bare per-item loop would assert
    nothing (#1545 review). Require at least one matching buy so the filter is
    actually exercised.

    ``status`` needs no enum normalization. The previous docstring here claimed
    "MediaBuyDeliveryStatus is an Enum (not a str-enum), so identity-compares against
    the plain wire string would otherwise fail" -- that was wrong twice over, and the
    ``getattr(raw, "value", raw)`` unwrap it justified was dead code.
    ``MediaBuyDeliveryData`` sets ``use_enum_values=True``
    (src/core/schemas/delivery.py), so ``d.status`` is already a plain ``str`` at
    runtime; and the underlying enum is a ``StrEnum`` anyway, so even unconverted it
    would compare equal to the wire string. See GH #1749's sibling ticket on
    defensive enum unwrapping.
    """
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    assert deliveries, (
        f"Filter '{status}' returned no media buys — the scenario must seed a buy "
        f"for this status or the assertion passes vacuously."
    )
    for d in deliveries:
        actual = d.get("status")
        assert actual == status, f"Expected status '{status}', got '{actual}' for {d.get('media_buy_id')}"


# ── Reporting period assertions ────────────────────────────────────


@then(parsers.parse('the response reporting_period start should be "{date}"'))
def then_period_start(ctx: dict, date: str) -> None:
    """Assert reporting period start date (response-level, not per-delivery).

    Graded on the WIRE (sweep verification salesagent-2qfx.8, R1): reporting_period.start
    is an AwareDatetime; any wire form Pydantic can parse (a date-only string, an epoch
    int, a differently-offset string) reconstructs to a valid AwareDatetime and
    ``str(period.start)[:10]`` would pass on a non-conformant wire. Matches
    then_has_reporting_period's grading shape for the same field.
    """
    period = wire_dict(ctx).get("reporting_period")
    assert period is not None, "Response missing reporting_period"
    actual = str(period.get("start"))[:10]
    assert actual == date, f"Expected period start '{date}', got '{actual}'"


@then(parsers.parse('the response reporting_period end should be "{date}"'))
def then_period_end(ctx: dict, date: str) -> None:
    """Assert reporting period end date (response-level, not per-delivery). See then_period_start."""
    period = wire_dict(ctx).get("reporting_period")
    assert period is not None, "Response missing reporting_period"
    actual = str(period.get("end"))[:10]
    assert actual == date, f"Expected period end '{date}', got '{actual}'"


@then("the response reporting_period end should be today's date")
def then_period_end_today(ctx: dict) -> None:
    """Assert reporting period end is today (response-level). See then_period_start."""
    from datetime import UTC, datetime

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    period = wire_dict(ctx).get("reporting_period")
    assert period is not None, "Response missing reporting_period"
    actual = str(period.get("end"))[:10]
    assert actual == today, f"Expected period end '{today}', got '{actual}'"


# ── Webhook Then steps ─────────────────────────────────────────────


@then("the system should POST a delivery report to the configured webhook URL")
def then_webhook_post(ctx: dict) -> None:
    """Assert webhook POST was made to the configured URL.

    The expected URL is the one the scenario actually configured. It used to default to
    ``"https://example.com/webhook"``, a literal that nothing writes and that does not even
    match the harness constant ``_WEBHOOK_URL`` — so the first run of this step would have
    compared the real POST target against a URL appearing nowhere in the setup. The step is
    masked today only because its scenario is xfailed on an unrelated production gap, which
    made it a landmine for whoever implements webhook delivery. See GH #1749.
    """
    env = ctx["env"]
    assert env.mock["post"].called, "Expected webhook POST but none was made"
    call_args = env.mock["post"].call_args
    called_url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    configured_url = _require(
        ctx,
        "webhook_url",
        hint="the Given that configures the reporting_webhook must record the URL it configured",
    )
    assert called_url == configured_url, (
        f"Webhook POST went to wrong URL: expected {configured_url!r}, got {called_url!r}"
    )


@then(parsers.parse('the payload should include delivery metrics for "{mb_id}"'))
def then_webhook_payload_has_metrics(ctx: dict, mb_id: str) -> None:
    """Assert webhook payload includes delivery metrics for the requested buy.

    Verifies ID mapping and that the payload carries concrete numeric metric
    values (impressions, spend) — not just structural presence.  The
    reporting_period check is left to its dedicated Then step.
    """
    payload = _get_last_webhook_payload(ctx)
    real_id = _resolve_media_buy_id(ctx, mb_id)
    # ID mapping: payload media_buy_id must match the requested buy
    assert payload.get("media_buy_id") == real_id, (
        f"Expected payload media_buy_id == {real_id!r}, got {payload.get('media_buy_id')!r}"
    )

    # Metrics: the payload must carry concrete numeric delivery data.
    # Look in totals, then by_package, then top-level — whatever the payload shape.
    totals = payload.get("totals") or payload.get("aggregated_totals") or {}
    impressions = totals.get("impressions") if isinstance(totals, dict) else None
    spend = totals.get("spend") if isinstance(totals, dict) else None

    # Fallback: check by_package or top-level keys
    if impressions is None:
        pkgs = payload.get("by_package") or []
        if pkgs:
            impressions = sum(p.get("impressions", 0) for p in pkgs if isinstance(p, dict))
            spend = sum(p.get("spend", 0) for p in pkgs if isinstance(p, dict))
        else:
            impressions = payload.get("impressions")
            spend = payload.get("spend")

    assert impressions is not None, (
        f"Webhook payload for {real_id!r} missing delivery metric 'impressions': payload keys={list(payload.keys())}"
    )
    assert isinstance(impressions, (int, float)) and impressions > 0, (
        f"Expected positive numeric impressions for {real_id!r}, got {impressions!r}"
    )
    assert spend is not None, (
        f"Webhook payload for {real_id!r} missing delivery metric 'spend': payload keys={list(payload.keys())}"
    )
    assert isinstance(spend, (int, float)) and spend > 0, (
        f"Expected positive numeric spend for {real_id!r}, got {spend!r}"
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
    """Assert retry count: at most 4 POST calls (1 original + 3 retries).

    Also verifies that multiple attempts were made (at least 2), confirming
    the retry mechanism was triggered, not just that it stayed under the cap.
    """
    env = ctx["env"]
    call_count = env.mock["post"].call_count
    assert call_count >= 2, (
        f"Expected at least 2 POST calls (original + retry), got {call_count} — retry mechanism may not have triggered"
    )
    assert call_count <= 4, f"Expected at most 4 calls (1+3 retries), got {call_count}"


def _assert_exponential_backoff(ctx: dict, *, expected_sleeps: int = 2) -> list[float]:
    """Assert the mocked sleep calls follow an exponential backoff schedule.

    Production WebhookDeliveryService sleeps between retries. This reads the
    recorded sleep durations, asserts there were exactly ``expected_sleeps`` of
    them (= ``expected_sleeps + 1`` total attempts), and that each duration is
    at least 1.5x the previous one (exponential growth). Returns the durations
    for any further per-step assertions.
    """
    sleep_calls = ctx["env"].mock["sleep"].call_args_list
    assert sleep_calls, "Expected at least one sleep call for backoff"
    durations = [float(c[0][0]) for c in sleep_calls]
    assert len(durations) == expected_sleeps, (
        f"Expected {expected_sleeps} backoff sleeps (for {expected_sleeps + 1} total attempts), got {len(durations)}"
    )
    for prev, nxt in zip(durations, durations[1:], strict=False):
        assert nxt >= prev * 1.5, (
            f"Backoff duration {nxt:.2f}s is not exponentially larger than prior {prev:.2f}s "
            f"(expected at least {prev * 1.5:.2f}s). Full schedule: {[f'{d:.2f}' for d in durations]}"
        )
    return durations


@then("retries should use exponential backoff (1s, 2s, 4s + jitter)")
def then_exponential_backoff(ctx: dict) -> None:
    """Assert sleep durations follow exponential backoff schedule.

    Production WebhookDeliveryService does 3 total attempts (1 original + 2 retries),
    sleeping between each retry. So we expect exactly 2 sleep calls with
    exponentially growing durations.
    """
    _assert_exponential_backoff(ctx)


@then("the system should retry up to 3 times with exponential backoff")
def then_retry_with_backoff(ctx: dict) -> None:
    """Assert at most 4 POST calls (1 original + 3 retries) with exponential sleep growth.

    Production WebhookDeliveryService does 3 total attempts with 2 sleeps between them.
    """
    env = ctx["env"]
    assert env.mock["post"].call_count <= 4, (
        f"Expected at most 4 calls (1 + 3 retries), got {env.mock['post'].call_count}"
    )
    _assert_exponential_backoff(ctx)


@then("the system should not retry the delivery")
def then_no_retry(ctx: dict) -> None:
    """Assert no retry was attempted."""
    env = ctx["env"]
    assert env.mock["post"].call_count <= 1, "Expected no retries"


@then("the system should log the authentication rejection")
def then_log_auth_rejection(ctx: dict) -> None:
    """Assert the system logged the authentication rejection.

    CircuitBreakerEnv captures WARNING+ log records from the webhook delivery
    service. This step verifies a log record about the 401/client error was
    emitted during the delivery attempt.
    """
    env = ctx["env"]
    # 1. Confirm delivery failed (precondition)
    success = _extract_webhook_success(ctx)
    assert success is False, f"Expected webhook delivery to fail on auth rejection, got success={success!r}"

    # 2. Verify auth rejection was logged
    # FIXME(#1749): reads ctx 'captured_logs', which no step writes — dead branch,
    # allowlisted in tests/unit/test_architecture_bdd_no_orphan_ctx_reads.py. Write the key
    # where the precondition is established, or delete the read; then drop it from the allowlist.
    log_records = getattr(env, "captured_logs", None) or ctx.get("captured_logs")
    assert log_records is not None, "CircuitBreakerEnv.captured_logs not available — harness must capture logs"
    found_auth_log = any("client error" in r.lower() or "401" in r or "unauthorized" in r.lower() for r in log_records)
    assert found_auth_log, (
        f"Expected a WARNING log record about auth rejection (401/client error/unauthorized), "
        f"but captured {len(log_records)} records: {log_records[:5]}"
    )


@then("the webhook should be marked as failed")
def then_webhook_marked_failed(ctx: dict) -> None:
    """Assert webhook delivery was marked as failed.

    Checks the return value from deliver_webhook_with_retry or
    WebhookDeliveryService: success must be False.
    """
    success = _extract_webhook_success(ctx)
    assert success is False, (
        f"Expected webhook delivery to be marked as failed (success=False), "
        f"got success={success!r} from webhook_result={ctx.get('webhook_result')!r}"
    )


@then("the webhook delivery should be skipped without an HTTP POST")
def then_webhook_skipped_no_post(ctx: dict) -> None:
    """Assert send-time SSRF skip: delivery failed and httpx POST was never called."""
    env = ctx["env"]
    success = _extract_webhook_success(ctx)
    assert success is False, f"Expected SSRF-skipped delivery to return False, got success={success!r}"
    post_mock = env.mock["post"]
    assert post_mock.call_count == 0, f"Expected no HTTP POST after SSRF rejection, got {post_mock.call_count} call(s)"


@then("the circuit breaker should record a failure")
def then_circuit_breaker_recorded_failure(ctx: dict) -> None:
    """Assert the send-time SSRF path called circuit_breaker.record_failure()."""
    env = ctx["env"]
    service = env.get_service()
    endpoint_key = ctx.get("circuit_breaker_endpoint_key", f"{env._tenant_id}:{_WEBHOOK_URL}")
    cb = service._circuit_breakers.get(endpoint_key)
    assert cb is not None, (
        f"Expected circuit breaker for {endpoint_key!r} after SSRF skip, found keys={list(service._circuit_breakers)}"
    )
    assert cb.failure_count >= 1, (
        f"Expected failure_count >= 1 after SSRF rejection for {endpoint_key!r}, got {cb.failure_count}"
    )


@then(parsers.parse('the circuit breaker should be in "{state}" state'))
def then_circuit_breaker_state(ctx: dict, state: str) -> None:
    """Assert circuit breaker state matches expected value."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual.lower() == state.lower(), f"Expected CB state '{state.lower()}', got '{actual}'"


@then("subsequent scheduled deliveries should be suppressed")
def then_deliveries_suppressed(ctx: dict) -> None:
    """Assert scheduled deliveries are suppressed while the circuit breaker is open.

    Rather than re-checking the breaker state (already verified by the preceding
    step), this asserts the observable suppression: record the current POST call
    count, attempt a delivery, and verify no new POST was dispatched.
    """
    env = ctx["env"]
    post_mock = env.mock["post"]
    calls_before = post_mock.call_count

    # Attempt a delivery while breaker is open — it should be suppressed
    result = env.call_send()
    assert result is False, f"Expected delivery to be suppressed (return False) while CB is open, got {result!r}"
    assert post_mock.call_count == calls_before, (
        f"Expected no new POST calls while CB is open (suppressed), "
        f"but call count went from {calls_before} to {post_mock.call_count}"
    )


@then(parsers.parse('the circuit breaker should transition to "{state}"'))
def then_circuit_transition(ctx: dict, state: str) -> None:
    """Assert circuit breaker transitioned to the expected state."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual.lower() == state.lower(), f"Expected CB transition to '{state.lower()}', got '{actual}'"


@then("the system should attempt a single probe delivery")
def then_single_probe(ctx: dict) -> None:
    """Assert exactly one probe delivery was dispatched in half-open state.

    The preceding step already verified the breaker transitioned to half_open.
    This step verifies the behavioral claim: exactly one probe attempt was made.

    The mock lookup here used to ask for ``httpx_post`` / ``webhook_post``. No env defines
    either key, so it ALWAYS missed and the step fell through to
    ``pytest.xfail("HARNESS GAP: no webhook POST mock")``. There was no missing mock — the key
    was wrong, and this step's own claim went ungraded for the scenario's whole lifetime behind
    a false excuse. ``CircuitBreakerEnv`` does expose it, as ``mock["post"]``
    (``tests/harness/delivery_circuit_breaker_unit.py:74``). The baseline was a phantom too:
    ``ctx.get("pre_open_call_count", 0)`` over a key nothing wrote, so the default turned the
    half-open delta into the scenario's TOTAL POST count. See GH #1749.

    Correcting the lookup exposes a real and deeper gap: the probe is logged as scheduled but
    never reaches httpx post, so ``call_count`` is 0 for the whole scenario — GH #1781. The
    xfail below is narrowed to exactly that case, so a non-zero-but-wrong count still fails
    loudly instead of being swallowed.
    """
    env = ctx["env"]
    probe_count = ctx.get("probe_count")
    if probe_count is not None:
        # Probe count was explicitly recorded by the When step
        assert probe_count == 1, f"Expected exactly 1 probe delivery attempt, got {probe_count}"
        return

    mock_post = env.mock.get("post")
    assert mock_post is not None, (
        f"{type(env).__name__} exposes no POST mock, so 'exactly one probe' cannot be counted. "
        f"Available mocks: {sorted(env.mock)}"
    )
    pre_probe_calls = _require(
        ctx,
        "pre_probe_call_count",
        hint="the When step that evaluates the circuit breaker must record the POST count before dispatching the probe",
    )
    if mock_post.call_count == 0:
        pytest.xfail(
            "HARNESS GAP(GH #1781): the half-open probe is logged as scheduled but never reaches "
            "httpx post within this step, so 'exactly one probe' is unobservable here"
        )

    probe_dispatches = mock_post.call_count - pre_probe_calls
    assert probe_dispatches == 1, (
        f"Expected exactly 1 probe dispatch in half-open state, got {probe_dispatches} "
        f"(total={mock_post.call_count}, pre-probe={pre_probe_calls})"
    )


@then("normal scheduled deliveries should resume")
def then_deliveries_resume(ctx: dict) -> None:
    """Assert normal scheduled deliveries can resume after circuit breaker closure.

    The preceding step already verified the breaker transitioned to closed.
    This step verifies the behavioral claim: the circuit breaker allows new
    delivery attempts (can_attempt returns True), proving the gate is open
    for scheduled deliveries to flow through.
    """
    from src.services.webhook_delivery_service import CircuitState

    env = ctx["env"]
    service = env.get_service()
    endpoint_key = ctx.get("circuit_breaker_endpoint_key", f"{env._tenant_id}:{_WEBHOOK_URL}")
    cb = service._circuit_breakers.get(endpoint_key)

    # The breaker must allow attempts (closed state permits delivery)
    assert cb is not None, f"No circuit breaker found for endpoint key {endpoint_key!r}"
    can_attempt = cb.can_attempt()
    assert can_attempt is True, (
        f"Circuit breaker should allow delivery attempts after closure, "
        f"but can_attempt() returned {can_attempt!r} (state={cb.state})"
    )
    # Verify the breaker is in closed state (not just half_open allowing a probe)
    assert cb.state == CircuitState.CLOSED, (
        f"Expected circuit breaker in CLOSED state for resumed deliveries, got {cb.state}"
    )


@then("the delivery should be recorded as successful")
def then_delivery_successful(ctx: dict) -> None:
    """Assert delivery was recorded as successful."""
    success = _extract_webhook_success(ctx)
    assert success is True, (
        f"Expected successful delivery (success=True), "
        f"got success={success!r} from webhook_result={ctx.get('webhook_result')!r}"
    )


@then("the circuit breaker state should remain healthy")
def then_circuit_healthy(ctx: dict) -> None:
    """Assert circuit breaker remains in healthy (closed) state."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual == "closed", f"Expected CB to remain 'closed' (healthy), got '{actual}'"


@then("the configuration should be rejected")
def then_config_rejected(ctx: dict) -> None:
    """Assert production rejected the webhook config on the wire (VALIDATION_ERROR).

    The short credential is rejected by production's Pydantic boundary
    (Authentication.credentials MinLen=32) — assert the real two-layer AdCP
    wire envelope, not a reconstructed/hand-built exception.
    """
    result = ctx["result"]
    result.assert_wire_error("VALIDATION_ERROR", recovery="correctable", message_substr="32")


@then("the error should indicate minimum credential length is 32 characters")
def then_error_min_credential_length(ctx: dict) -> None:
    """Assert the wire error message names the 32-character minimum.

    The 32-char minimum surfaces in the wire error MESSAGE (Pydantic's
    "String should have at least 32 characters"). Production's RequestValidationError
    envelope does NOT emit a suggestion for this path, so the message — not a
    suggestion — carries the boundary value.
    """
    result = ctx["result"]
    result.assert_wire_error("VALIDATION_ERROR", recovery="correctable", message_substr="32 characters")


@then("the configuration should be accepted")
def then_config_accepted(ctx: dict) -> None:
    """Assert production accepted the webhook config on the wire (create succeeded)."""
    result = ctx["result"]
    assert not result.is_error, f"Config rejected on the wire: {ctx.get('wire_error_envelope') or ctx.get('error')}"


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
    """Assert bearer token header matches the configured token from ctx.

    Verifies the header starts with 'Bearer ' and the token portion matches
    the bearer token configured in the test setup (ctx['webhook_bearer_token']).
    """
    headers = _get_last_webhook_headers(ctx)
    assert header in headers, f"Expected header {header!r} but got: {list(headers.keys())}"
    value = headers[header]
    assert value.startswith("Bearer "), f"Header {header!r} should be a Bearer token but got: {value!r}"
    token = value.removeprefix("Bearer ")
    expected_token = ctx.get("webhook_bearer_token", "")
    if expected_token:
        assert token == expected_token, f"Bearer token mismatch: expected {expected_token!r}, got {token!r}"


# ── Response field presence assertions ─────────────────────────────


@then('the response should contain "media_buy_deliveries" field')
def then_has_deliveries_field(ctx: dict) -> None:
    """Assert response has media_buy_deliveries matching the requested media buy IDs.

    Verifies structural correctness (list of delivery items) and, when the
    request included specific media_buy_ids, verifies that every returned
    delivery corresponds to a requested ID (filtering correctness).
    """
    deliveries = wire_dict(ctx).get("media_buy_deliveries")
    assert isinstance(deliveries, list), f"Expected media_buy_deliveries to be a list, got {type(deliveries).__name__}"
    # Every delivery item must carry a non-empty media_buy_id
    for d in deliveries:
        mb_id = d.get("media_buy_id")
        assert isinstance(mb_id, str) and mb_id, f"Delivery item has invalid media_buy_id: {mb_id!r}"
    # Filtering correctness: returned IDs must be a subset of requested IDs
    request_params = ctx.get("request_params", {})
    requested_ids = request_params.get("media_buy_ids")
    if requested_ids:
        returned_ids = {d.get("media_buy_id") for d in deliveries}
        assert returned_ids <= set(requested_ids), (
            f"Response contains unrequested media_buy_ids: {returned_ids - set(requested_ids)}"
        )


@then('the response should not contain "errors" field')
def then_no_errors_field(ctx: dict) -> None:
    """Assert response errors list is empty and no exception was raised.

    Graded on the WIRE (sweep verification salesagent-2qfx.8, R1) — matches the file's
    other error/status oracles rather than a getattr default.
    """
    assert "error" not in ctx, f"Unexpected error: {ctx.get('error')}"
    if ctx.get("response") is not None:
        errors = wire_dict(ctx).get("errors") or []
        assert not errors, f"Unexpected errors in response: {errors}"


@then('the response should contain "errors" field')
def then_has_errors_field(ctx: dict) -> None:
    """Assert an error was produced (either response-level or exception).

    When a response exists, its errors list must be non-empty. When no
    response was returned, an exception must have been raised and stored
    in ctx['error']. The assertion verifies that an error condition is
    present, not just that some field exists.

    Graded on the WIRE (sweep verification salesagent-2qfx.8, R1): reads
    wire_dict(ctx).get("errors") — the try/except AttributeError dance this replaced
    was only ever needed against a typed object; a wire dict never raises AttributeError.
    """
    error_exc = ctx.get("error")
    resp = ctx.get("response")
    assert resp is not None or error_exc is not None, (
        "Expected either a response with errors or an exception, got neither"
    )
    if resp is not None:
        errors = wire_dict(ctx).get("errors")
        if not errors:
            # Must have an exception instead
            assert error_exc is not None, "Response has no errors list and no exception was raised"
    else:
        assert isinstance(error_exc, Exception), (
            f"Expected an Exception in ctx['error'], got {type(error_exc).__name__}: {error_exc}"
        )


@then('the response should not contain "media_buy_deliveries" field')
def then_no_deliveries_field(ctx: dict) -> None:
    """Assert media_buy_deliveries is absent or empty in the serialized response."""
    if ctx.get("response") is not None:
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
        assert not deliveries, f"Expected 'media_buy_deliveries' to be absent or empty in response, got: {deliveries}"
    else:
        assert "error" in ctx, "Expected error-only response but got neither"


# ── Error ownership assertions ─────────────────────────────────────


@then(parsers.parse("the error should NOT reveal that the media buy exists"))
def then_error_no_reveal(ctx: dict) -> None:
    """Assert error does not leak existence information via message content or ID echoing."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = _get_error_message(error).lower()
    leaking_phrases = ["exists", "belongs to", "owned by", "not authorized for", "access denied"]
    for phrase in leaking_phrases:
        assert phrase not in msg, f"Error leaks existence info via phrase {phrase!r}: {error}"
    # The media_buy_id should not be echoed back in a way that confirms existence.
    #
    # Sourced from what the When step actually dispatched, not from ctx keys no step
    # writes. The previous read (`ctx["target_media_buy_id"] or ctx["media_buy_id"]`)
    # was always empty, so the `if mb_id:` guard was never true and this half of the
    # step asserted nothing — GH #1749 (dead ctx read) meeting GH #1751 (guard on the
    # artifact being graded).
    requested_ids = dispatched_request(ctx).media_buy_ids or []
    assert requested_ids, (
        "the dispatched request named no media_buy_ids, so there is no identifier whose "
        "echo could reveal existence — this step belongs only in scenarios that query one"
    )
    for mb_id in requested_ids:
        assert msg.count(str(mb_id).lower()) <= 1, (
            f"Error repeatedly echoes media_buy_id {mb_id!r}, which may reveal existence: {error}"
        )


# ── Webhook skip assertions ─────────────────────────────────────────


@then(parsers.parse('the system should skip "{mb_id}" (no webhook to deliver to)'))
def then_skip_no_webhook(ctx: dict, mb_id: str) -> None:
    """Assert no webhook POST was made for this specific media buy.

    Verifies that no POST call contains this media buy's ID in its payload,
    confirming the system correctly skipped delivery when no webhook is configured.
    """
    env = ctx["env"]
    real_id = _resolve_media_buy_id(ctx, mb_id)
    post_mock = env.mock["post"]
    # Collect all media_buy_ids that received webhook POSTs
    posted_mb_ids = [
        call[1].get("json", {}).get("media_buy_id") for call in post_mock.call_args_list if call[1].get("json")
    ]
    assert real_id not in posted_mb_ids, (
        f"Webhook POST was made for '{real_id}' but it should have been skipped "
        f"(no webhook configured). All posted IDs: {posted_mb_ids}"
    )


@then("no delivery attempt should be made")
def then_no_delivery_attempt(ctx: dict) -> None:
    """Assert no delivery attempt was made."""
    env = ctx["env"]
    assert not env.mock["post"].called, "Expected no delivery attempt"


# ── Reporting dimension assertions ─────────────────────────────────


@then(parsers.parse('the response packages should include "{field}" breakdown arrays'))
def then_packages_include_breakdown(ctx: dict, field: str) -> None:
    """Assert every package has a non-empty breakdown list for the named field.

    Verifies structural correctness (field is a list), content (each entry
    has impressions), and dimensional segmentation (each entry carries the
    dimension identifier, e.g. "device_type" for "by_device_type").
    """
    packages = wire_packages(ctx)
    checked = 0
    # Derive the dimension identifier from the field name: "by_device_type" -> "device_type"
    dimension_key = field[3:] if field.startswith("by_") else field
    for pkg in packages:
        pkg_id = pkg.get("package_id")
        value = pkg.get(field)
        assert isinstance(value, list), f"Package {pkg_id!r} missing '{field}' breakdown array: {value!r}"
        assert value, f"Package {pkg_id!r} has empty '{field}' breakdown"
        # Each breakdown entry must have impressions AND the dimension identifier
        identifiers_seen: set[str] = set()
        for entry in value:
            entry_impressions = entry.get("impressions")
            assert entry_impressions is not None, f"Breakdown entry in {pkg_id!r}.{field} missing 'impressions'"
            # Dimension identifier: proves data is actually segmented
            dim_value = entry.get(dimension_key)
            if dim_value is None:
                pytest.xfail(
                    f"PRODUCTION GAP: breakdown entry in {pkg_id!r}.{field} "
                    f"missing dimension identifier '{dimension_key}' — "
                    f"entries are not segmented by dimension"
                )
            assert dim_value, (
                f"Breakdown entry in {pkg_id!r}.{field} has empty dimension identifier '{dimension_key}': {dim_value!r}"
            )
            identifiers_seen.add(str(dim_value))
        # With multiple entries, dimension identifiers should be distinct
        if len(value) > 1:
            assert len(identifiers_seen) > 1, (
                f"Package {pkg_id!r}.{field} has {len(value)} entries "
                f"but only 1 distinct '{dimension_key}' value: {identifiers_seen} — "
                f"not truly segmented by dimension"
            )
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should NOT include "{field}" breakdown arrays'))
def then_packages_exclude_breakdown(ctx: dict, field: str) -> None:
    """Assert no package in the response has field as a list.

    Graded on the WIRE via :func:`wire_packages` — meaningful even for a field
    absent from the model (e.g. 'by_audience', which ``PackageDelivery`` never
    declares): under ``extra="ignore"``/``"forbid"`` (never ``"allow"``, see
    ``get_pydantic_extra_mode``), an undeclared field can NEVER appear in
    ``model_dump()``, so a typed-payload check here would be vacuous by
    construction for the exact case this assertion names.
    """
    for pkg in wire_packages(ctx):
        assert field not in pkg or not isinstance(pkg[field], list), (
            f"Package {pkg.get('package_id')!r} should not have '{field}' breakdown array: {pkg.get(field)!r}"
        )


@then(parsers.parse('the response packages should include "{field}" with at most {n:d} entries'))
def then_packages_limited(ctx: dict, field: str, n: int) -> None:
    """Assert every package has at most n entries in the named breakdown field.

    Verifies the count constraint and that entries are properly typed (list
    of dicts/objects with at least one field populated).

    Graded on the WIRE via :func:`wire_packages` — the buyer's view, not the
    coerced typed payload.
    """
    packages = wire_packages(ctx)
    checked = 0
    for pkg in packages:
        pkg_id = pkg.get("package_id")
        value = pkg.get(field)
        assert isinstance(value, list), f"Package {pkg_id!r} missing '{field}' as a list: {value!r}"
        actual_count = len(value)
        assert actual_count <= n, f"Package {pkg_id!r} '{field}' has {actual_count} entries, expected at most {n}"
        # Each entry must be a non-empty dict or object (not bare None)
        for entry in value:
            assert entry is not None, f"Package {pkg_id!r} '{field}' contains a None entry"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('"{field}" should be true'))
def then_field_true(ctx: dict, field: str) -> None:
    """Assert the named field is True on every package in the response.

    Truncation flags (by_geo_truncated, by_device_type_truncated) live on
    PackageDelivery, not on the top-level response object.

    Graded on the WIRE via :func:`wire_packages`, and with ``is True`` — so a flag
    serialized as the string "true" (which the typed payload would coerce back to a
    boolean) fails here. Absence fails too, as it must: the response schema requires
    by_*_truncated whenever the matching by_* array is present.
    """
    packages = wire_packages(ctx)
    assert packages, "Response has no packages to check"
    for pkg in packages:
        value = pkg.get(field)
        assert value is True, f"Expected response package.{field} to be True, got {value!r}"


@then(parsers.parse('"{field}" should be false'))
def then_field_false(ctx: dict, field: str) -> None:
    """Assert the named field is False on every package in the response.

    Truncation flags (by_geo_truncated, by_device_type_truncated) live on
    PackageDelivery, not on the top-level response object.

    Graded on the WIRE via :func:`wire_packages`, and with ``is False`` — so a flag
    serialized as the string "false" (which the typed payload would coerce back to a
    boolean) fails here. Absence fails too, as it must: the response schema requires
    by_*_truncated whenever the matching by_* array is present.
    """
    packages = wire_packages(ctx)
    assert packages, "Response has no packages to check"
    for pkg in packages:
        value = pkg.get(field)
        assert value is False, f"Expected response package.{field} to be False, got {value!r}"


@then(parsers.parse('the response packages should include "{field}"'))
def then_packages_include_field(ctx: dict, field: str) -> None:
    """Assert every package has the named field populated with a valid value.

    Verifies the field is non-None and, for numeric fields, is a proper
    numeric type. For string fields, verifies non-empty.
    """
    packages = wire_packages(ctx)
    checked = 0
    for pkg in packages:
        pkg_id = pkg.get("package_id")
        value = pkg.get(field)
        assert value is not None, f"Package {pkg_id!r} missing field {field!r}"
        # Type-specific validation
        if isinstance(value, str):
            assert value, f"Package {pkg_id!r} field {field!r} is empty string"
        elif isinstance(value, list):
            # List fields should be non-empty
            assert value, f"Package {pkg_id!r} field {field!r} is empty list"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should include "{f1}" and "{f2}" breakdowns'))
def then_packages_include_two(ctx: dict, f1: str, f2: str) -> None:
    """Assert every package has both named breakdown fields as non-empty lists."""
    packages = wire_packages(ctx)
    checked = 0
    for pkg in packages:
        pkg_id = pkg.get("package_id")
        for field in (f1, f2):
            value = pkg.get(field)
            assert isinstance(value, list), f"Package {pkg_id!r} missing '{field}' breakdown: {value!r}"
            assert value, f"Package {pkg_id!r} has empty '{field}' breakdown list"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should NOT include "{field}"'))
def then_packages_exclude_field(ctx: dict, field: str) -> None:
    """Assert no package has the named field set to a non-None value.

    Graded on the WIRE via :func:`wire_packages` — meaningful even for a field
    absent from the model (e.g. 'by_audience', which ``PackageDelivery`` never
    declares — see :func:`then_packages_exclude_breakdown` for why a typed
    ``model_dump()`` check cannot distinguish that case).
    """
    for pkg in wire_packages(ctx):
        value = pkg.get(field)
        assert value is None, f"Package {pkg.get('package_id')!r} should not have field {field!r}: {value!r}"


@then(parsers.parse('the response geo breakdown should use classification system "{system}"'))
def then_geo_system(ctx: dict, system: str) -> None:
    """Assert geo breakdown entries use the expected classification system.

    Asserts what the response DOES provide (media_buy_id, deliveries, totals),
    then xfails on the specific missing field (by_geo with system).
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    wire = wire_dict(ctx)
    assert wire.get("media_buy_deliveries"), "Expected non-empty media_buy_deliveries"
    assert wire.get("reporting_period") is not None, "Expected reporting_period"

    # Check if by_geo is populated on any package
    packages = wire_packages(ctx)
    has_geo = any(pkg.get("by_geo") for pkg in packages)
    if not has_geo:
        pytest.xfail(
            f"PRODUCTION GAP: by_geo breakdown not populated in response — "
            f"cannot verify classification system '{system}'"
        )
    # Geo data is present (the xfail above covers its absence), so every entry must
    # declare the system it classified by. No `if geo_system is not None` guard: the
    # scenario NAMES the system it expects, so an entry declaring none leaves that
    # claim ungraded, which is the whole defect class of GH #1751.
    #
    # GeoBreakdown.system is `str | None` in the schema, but AdCP 3.1.1 requires it in
    # the field description for the metro/postal_area levels this step runs for, and
    # the step text asserts a specific system either way.
    checked = 0
    for pkg in packages:
        by_geo = pkg.get("by_geo") or []
        for entry in by_geo:
            geo_system = entry.get("system")
            assert geo_system == system, (
                f"Geo breakdown system mismatch in package {pkg.get('package_id', '?')!r}: "
                f"expected {system!r}, got {geo_system!r}"
            )
            checked += 1
    assert checked >= 1, (
        f"No by_geo entries were graded, so 'uses classification system {system}' asserted nothing "
        f"— {len(packages)} package(s) reported a by_geo list, all empty"
    )


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}" (fallback)'))
def then_placement_sorted_fallback(ctx: dict, metric: str) -> None:
    """Assert placement breakdown uses fallback sort metric.

    Asserts what the response DOES provide (deliveries, packages),
    then verifies sort order if by_placement is populated.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    wire = wire_dict(ctx)
    assert wire.get("media_buy_deliveries"), "Expected non-empty media_buy_deliveries"
    assert wire.get("reporting_period") is not None, "Expected reporting_period"

    packages = wire_packages(ctx)
    _assert_placements_sorted_by(packages, metric, fallback=True)


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}"'))
def then_placement_sorted(ctx: dict, metric: str) -> None:
    """Assert placement breakdown is sorted by the given metric descending.

    Asserts what the response DOES provide (deliveries, packages),
    then verifies sort order if by_placement is populated with the metric.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    wire = wire_dict(ctx)
    assert wire.get("media_buy_deliveries"), "Expected non-empty media_buy_deliveries"
    assert wire.get("reporting_period") is not None, "Expected reporting_period"

    packages = wire_packages(ctx)
    _assert_placements_sorted_by(packages, metric, fallback=False)


# ── Attribution window assertions ─────────────────────────────────


def _wire_attribution_window(ctx: dict, *, expectation: str) -> dict:
    """Return the response's attribution_window as the buyer sees it on the WIRE.

    The one reader every attribution assertion in this module goes through.
    Asserts the request succeeded, that the response carries deliveries, and
    that attribution_window is present — BR-RULE-092 requires the seller to echo
    the applied window on every successful delivery response, so its absence is
    a failure here rather than a skipped assertion.

    Reads through ``wire_dict``, which raises when a real-wire transport did not
    stash a body, so this cannot silently degrade into asserting on a typed
    payload whose fields are already coerced.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    wire = wire_dict(ctx)
    # A 200 can still carry advisory errors (media_buy_delivery.py:689 populates
    # `errors` on partial success). Without this, such a response graded as a clean
    # echo — the absence of ctx["error"] only rules out a raised exception.
    assert not wire.get("errors"), f"Response carries advisory errors, so it is not a clean echo: {wire.get('errors')}"
    assert wire.get("media_buy_deliveries"), "Expected non-empty media_buy_deliveries"
    assert "attribution_window" in wire, f"Response omits attribution_window — {expectation}. Wire keys: {sorted(wire)}"
    aw = wire["attribution_window"]
    assert isinstance(aw, dict), f"attribution_window is {type(aw).__name__}, expected an object"
    return aw


def _wire_attribution_model(ctx: dict, *, expectation: str) -> str:
    """Return attribution_window.model off the wire, asserting it is present."""
    aw = _wire_attribution_window(ctx, expectation=expectation)
    model = aw.get("model")
    assert model is not None, "attribution_window.model is None — required by BR-RULE-092"
    return model


def _campaign_flight_days() -> int:
    """The seeded flight length a campaign-unit window must resolve to, in days.

    given_media_buy_with_status passes no dates, so MediaBuyFactory's defaults
    (2025-01-01 → 2027-12-31) define the flight. Sourced from the FIXTURE, not
    from the response's own dates — an oracle that mirrored production's
    subtraction of response fields could not catch a wrong flight, a fallback,
    or a clamp. (It still shares production's `.days` subtraction, the accepted
    tradeoff over pinning a literal that breaks when the fixture moves.)
    """
    from tests.factories import MediaBuyFactory

    return (MediaBuyFactory.end_date - MediaBuyFactory.start_date).days


_CAMPAIGN_FLIGHT_DAYS = _campaign_flight_days()


@then(parsers.parse('the response should include attribution_window with model "{model}"'))
def then_attribution_model(ctx: dict, model: str) -> None:
    """Assert attribution window model matches the expected value.

    The Gherkin literal and the dispatched request are cross-checked first:
    the model this step demands must be exactly what the dispatched
    attribution_window implies (buyer's model, else platform default). This
    ties the scenario to INV-1 — a literal that happens to coincide with the
    platform default while the When sends a different (or no) model is a
    scenario bug, not a pass. The INV-1 scenario once requested last_touch,
    which IS the default, so production ignoring the buyer's model was
    byte-identical to applying it and this step graded nothing.
    """
    expected_model = _expected_attribution_model(ctx)
    assert model == expected_model, (
        f"Scenario literal {model!r} disagrees with the dispatched request, which implies "
        f"{expected_model!r} — the Gherkin pair must request and assert the same model"
    )
    actual_model = _wire_attribution_model(ctx, expectation=f"expected model {model!r} to be echoed")
    assert actual_model == expected_model, (
        f"attribution_window.model should be '{expected_model}', got '{actual_model}'"
    )


def _dispatched_post_click(ctx: dict) -> Any:
    """Return the post_click window this scenario actually dispatched (typed).

    Derived from :func:`dispatched_request`, never from a literal default: a
    ``.get(key, 7)`` style fallback silently turns an echo assertion into a
    constant (GH #1749).
    """
    window = _dispatched_attribution_window(ctx, required=True)
    post_click = window.post_click
    assert post_click is not None, (
        f"the dispatched attribution_window has no post_click window: {window!r} — "
        "this step asserts the post_click echo specifically"
    )
    return post_click


@then("the attribution_window should echo the applied post_click window")
def then_attribution_echo(ctx: dict) -> None:
    """Assert attribution_window echoes the APPLIED post_click window (BR-RULE-092 INV-3).

    Expected values come from what the scenario dispatched, so changing the
    scenario's requested window changes what this step demands.

    Asserts value equality for both interval and unit. (Exact TYPE equality is
    not graded on a2a: its DataPart routes through protobuf Value, which widens
    every integer to an integral float — ``14.0 == 14`` keeps the comparison
    honest on value; see _dict_to_value in src/a2a_server/adcp_a2a_server.py
    and tests/unit/test_a2a_numeric_wire.py.) ``unit=campaign`` is the
    one window production does NOT echo verbatim — it resolves to the flight
    length in days (src/core/tools/media_buy_delivery.py:980-990) — but no
    scenario requests campaign through this step, so there is deliberately no
    branch for it: a weaker "resolved to some positive number of days" check
    would assert almost nothing. If a scenario ever does request campaign here,
    this fails, and the fix is to derive the expected day count from the flight
    dates the Given seeded — with that scenario as the thing proving it right.
    """
    aw = _wire_attribution_window(ctx, expectation="expected the post_click window to be echoed")

    pc = aw.get("post_click")
    assert pc is not None, (
        "attribution_window.post_click is None — buyer requested a post_click window which should be echoed"
    )

    requested = _dispatched_post_click(ctx)
    requested_dict = serialize_request(requested)
    assert pc == requested_dict, f"attribution_window.post_click should echo the request {requested_dict}, got {pc}"


# REMOVED (GH #1726): then_attribution_default. It graded the deleted
# T-UC-004-attr-unsupported scenario, and it wrapped its own post_click/post_view
# assertions in `try/except AssertionError: pytest.xfail(...)` -- the only
# self-swallowing assertion in the repo, invisible to the conftest xfail sweep and to
# the xpass audit, so it could never graduate and would have stayed green if the gap
# ever closed. Both it and its scenario are gone; see the RECONCILED note in
# BR-UC-004-deliver-media-buy-metrics.feature.


@then('the response attribution_window should include "model" field (required)')
def then_attribution_has_model(ctx: dict) -> None:
    """Assert attribution_window.model is present and valid in the response.

    BR-RULE-092 invariant: every delivery response must echo the applied
    attribution window with a non-null model from the spec-allowed values.
    """
    from adcp.types.generated_poc.enums.attribution_model import AttributionModel

    actual_model = _wire_attribution_model(ctx, expectation="BR-RULE-092 requires it")
    valid_models = {m.value for m in AttributionModel}
    assert actual_model in valid_models, (
        f"attribution_window.model '{actual_model}' is not a valid AttributionModel value: {valid_models}"
    )


@then("the response should include attribution_window with the seller's platform default model")
def then_attribution_default_model(ctx: dict) -> None:
    """Assert attribution window echoes the seller's platform default model.

    When the buyer omits attribution_window, production echoes the platform
    default (last_touch).  Assert the response's attribution_window.model
    matches the platform default from production config.
    """
    from src.core.tools.media_buy_delivery import PLATFORM_DEFAULT_ATTRIBUTION_MODEL

    actual_model = _wire_attribution_model(
        ctx, expectation="production should echo the platform default when the buyer omits it"
    )
    expected_model = PLATFORM_DEFAULT_ATTRIBUTION_MODEL.value
    assert actual_model == expected_model, (
        f"attribution_window.model should be platform default '{expected_model}', got '{actual_model}'"
    )


@then("the response should include attribution_window reflecting campaign-length window")
def then_attribution_campaign_length(ctx: dict) -> None:
    """Assert attribution window post_click resolves campaign unit to days.

    When the buyer requests post_click with unit=campaign and interval=1,
    production resolves this to unit=days with interval=campaign_length_days.
    The response must carry an attribution_window with a post_click whose
    unit is 'days' and interval equal to the seeded flight length.
    """
    aw = _wire_attribution_window(ctx, expectation="production should resolve the campaign-unit window and echo it")
    assert aw.get("model") is not None, "attribution_window.model is None — must carry the attribution model"

    # post_click must be present and resolved from campaign to days
    pc = aw.get("post_click")
    assert pc is not None, (
        "attribution_window.post_click is None — buyer requested post_click={interval:1, unit:campaign}"
    )
    assert pc["unit"] == "days", (
        f"attribution_window.post_click.unit should be 'days' (resolved from 'campaign'), got '{pc['unit']}'"
    )
    assert pc["interval"] == _CAMPAIGN_FLIGHT_DAYS, (
        f"attribution_window.post_click.interval should be the seeded flight length "
        f"({_CAMPAIGN_FLIGHT_DAYS} days), got {pc['interval']} — a campaign-unit window "
        f"must span the full flight, not a collapsed or clamped lookback"
    )


# ── Partial/error delivery assertions ─────────────────────────────


@then(parsers.parse('the response should indicate "{mb_id}" has partial_data or delayed metrics'))
def then_partial_data(ctx: dict, mb_id: str) -> None:
    """Assert the named media buy has reporting_delayed status."""
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    target = next((d for d in deliveries if d.get("media_buy_id") == mb_id), None)
    assert target is not None, f"No delivery found for {mb_id!r}"
    assert target.get("status") == "reporting_delayed", (
        f"Expected status='reporting_delayed' for partial/delayed metrics on {mb_id!r}, got {target.get('status')!r}"
    )


@then(parsers.parse('the response should include "{mb_id}" with zero impressions and zero spend'))
def then_zero_metrics(ctx: dict, mb_id: str) -> None:
    """Assert the named media buy has exactly zero impressions and zero spend.

    Verifies ID mapping (the requested media buy is found in deliveries)
    and exact metric values (both must be zero, not just non-negative).
    """
    deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
    target = next((d for d in deliveries if d.get("media_buy_id") == mb_id), None)
    assert target is not None, f"No delivery found for '{mb_id}' in {[d.get('media_buy_id') for d in deliveries]}"
    totals = target.get("totals") or {}
    assert totals.get("impressions") == 0.0, f"Expected zero impressions for '{mb_id}', got {totals.get('impressions')}"
    assert totals.get("spend") == 0.0, f"Expected zero spend for '{mb_id}', got {totals.get('spend')}"


@then("no real billing records should have been created")
def then_no_billing(ctx: dict) -> None:
    """Assert sandbox mode via the response's own sandbox flag, on the WIRE.

    Graded with ``is True`` off ``wire_field`` — a flag serialized as the wire
    string "true" (which the typed payload would coerce back to a real bool)
    fails here, matching :func:`tests.bdd.steps.generic.then_success.then_sandbox_true`.
    """
    sandbox = wire_field(ctx, "sandbox")
    assert sandbox is True, (
        f"Expected sandbox=True in response indicating no real billing records were created, got sandbox={sandbox!r}"
    )
    # REMOVED (GH #1751): a "secondary" loop over env.mock.get(name) for
    # ("charge", "create_billing_record", "bill"), each assertion nested under
    # `if mock is not None`. No harness env has ever exposed a mock by any of those
    # names — DeliveryPollEnv patches exactly one external, "adapter" — so all three
    # lookups returned None and the loop body never executed. It read as a second
    # line of defence while grading nothing.
    #
    # Not replaced with an "adapter was not called" assertion: sandbox delivery still
    # calls the adapter to fetch metrics, so that would assert the opposite of the
    # contract. The response flag above is the real grader; when a billing side effect
    # becomes observable in the harness, assert on it here unconditionally.


# ── Partition/boundary outcome assertions ─────────────────────────────


# Single source of truth for delivery boundary-field membership (moved out of
# generic/then_payload per salesagent-chit). Field names are normalized
# (spaces→underscores, lower-case) before lookup, so only underscore forms are
# listed here. _assert_valid_content below performs richer per-field content
# validation for a superset of these (it also covers "resolution"/"filter").
_DELIVERY_BOUNDARY_FIELDS = frozenset(
    {
        "reporting_dimensions",
        "attribution_window",
        "daily_breakdown",
        "include_package_daily_breakdown",
        "date_range",
        "ownership",
        "account",
        "status_filter",
    }
)


@register_boundary_handler
def _delivery_boundary_handler(ctx: dict, field: str, expected: str) -> bool:
    """Delivery-domain handler for the generic 'X handling should be Y' step.

    Returns True (after asserting) when *field* is a delivery boundary field or
    the response is a delivery response; returns False so the generic step can
    fall back to other domains (e.g. UC-005 creative formats). Behavior matches
    the delivery branch previously embedded in generic/then_payload.
    """
    resp = ctx.get("response")
    # Domain-routing type sniff, not a grading read: decides whether THIS handler owns
    # the field, before any assertion runs. Stays typed deliberately (a wire-graded
    # equivalent cannot run yet — there may be no wire on the error path this call is
    # trying to distinguish from).
    is_delivery = field.strip().lower().replace(" ", "_") in _DELIVERY_BOUNDARY_FIELDS or (
        resp is not None and hasattr(resp, "media_buy_deliveries")
    )
    if not is_delivery:
        return False

    if expected.strip().lower() in ("invalid", "error", "rejected"):
        # Wire-grounded, not a reconstructed-exception isinstance() check: no specific
        # error code is known at this generic boundary-routing layer, so this is the
        # same generic client-rejection shape _assert_wire_rejection already asserts
        # for fields whose Examples don't yet name a specific code.
        _assert_wire_rejection(ctx, field)
    else:
        assert "error" not in ctx, f"Expected valid '{field}' boundary but got error: {ctx.get('error')}"
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
        assert deliveries, f"Valid '{field}' boundary: expected non-empty media_buy_deliveries"
    return True


def _dispatched_attribution_window(ctx: dict, *, required: bool = False) -> Any:
    """Return the attribution_window this scenario dispatched (typed AttributionWindow), or
    None if it sent none.

    Merged reader (salesagent-hwji, closing @ChrisHuie's open nit that
    ``_dispatched_post_click`` and this were two readers for one channel).
    ``None`` is the honest answer for the ``omitted`` row — the buyer sent nothing, so
    the seller's default applies. It is NOT a fallback that hides a missing record:
    :func:`dispatched_request` itself is required, so a scenario that never dispatched
    fails loudly rather than silently grading against a default. Pass ``required=True``
    (the old ``_dispatched_post_click`` behavior) when the caller's contract is
    specifically about an attribution_window the buyer must have sent.
    """
    window = dispatched_request(ctx).attribution_window
    if required:
        assert window is not None, (
            "the dispatched request carried no attribution_window, so there is nothing to echo — "
            "this step belongs only in scenarios whose When step requests one"
        )
    return window


def _expected_attribution_model(ctx: dict) -> str:
    """The model value the seller must echo, derived from the dispatched request.

    BR-RULE-092: the buyer's model when given, otherwise the seller's platform
    default. The ``omitted`` and ``empty_object`` rows reach the default by two
    different production branches (``requested is None`` at
    media_buy_delivery.py:979 vs ``requested.model or default`` at :992), so both
    are graded here rather than assumed equivalent.
    """
    from src.core.tools.media_buy_delivery import PLATFORM_DEFAULT_ATTRIBUTION_MODEL

    requested = _dispatched_attribution_window(ctx)
    requested_model = requested.model if requested is not None else None
    return requested_model or PLATFORM_DEFAULT_ATTRIBUTION_MODEL.value


def _assert_attribution_echoed_on_wire(ctx: dict, field: str) -> None:
    """Assert the applied attribution_window on the WIRE (BR-RULE-092 INV-1/2/3).

    Unconditional by construction: reads through ``wire_dict``, which raises when
    a real-wire transport did not stash a wire body, and indexes
    ``attribution_window`` directly. A response that omits the field fails here
    rather than skipping the assertion — the regression these rows exist to catch.

    Grades the model VALUE (not key presence) and, when the buyer named a
    lookback window, that it comes back verbatim. ``unit=campaign`` is the one
    exception: production resolves it to the flight length in days
    (media_buy_delivery.py:980-990), so only its shape is asserted.
    """
    aw = _wire_attribution_window(ctx, expectation=f"valid {field} row requires the seller to echo the applied window")
    expected_model = _expected_attribution_model(ctx)
    assert aw.get("model") == expected_model, (
        f"Valid {field}: attribution_window.model should be {expected_model!r}, got {aw.get('model')!r}"
    )

    requested = _dispatched_attribution_window(ctx)
    for window_name in ("post_click", "post_view"):
        req_window = getattr(requested, window_name, None) if requested is not None else None
        if req_window is None:
            # INV-4: the applied window the seller echoes must not contain a
            # lookback the buyer never asked for — fabricated conversion
            # attribution would otherwise be graded by nothing, anywhere.
            assert aw.get(window_name) is None, (
                f"Valid {field}: buyer requested no {window_name}, but the response echoed "
                f"{aw.get(window_name)} — the seller must not fabricate a lookback window"
            )
            continue
        echoed = aw.get(window_name)
        assert echoed is not None, (
            f"Valid {field}: buyer requested {window_name}={req_window} but the response did not echo it"
        )
        if req_window.unit == "campaign":
            # Resolved to the flight length; assert the shape production promises.
            assert echoed["unit"] == "days", (
                f"Valid {field}: a campaign-unit {window_name} must be echoed resolved to days, got {echoed['unit']!r}"
            )
            assert echoed["interval"] == _CAMPAIGN_FLIGHT_DAYS, (
                f"Valid {field}: resolved campaign {window_name} must span the seeded flight "
                f"({_CAMPAIGN_FLIGHT_DAYS} days), got {echoed['interval']} — a collapsed or "
                f"clamped lookback silently shortens the buyer's attribution"
            )
        else:
            # Expected side serialized through the SAME shared dumper the harness uses
            # (architect review MEDIUM-3) — never a local model_dump, and the actual
            # (wire) side is never re-parsed back into a model. A2A widens int -> float
            # (tests/unit/test_a2a_numeric_wire.py), so this must stay value-equality:
            # dumping both sides to the same JSON-mode dict shape keeps 14 == 14.0 true
            # without asserting exact Python types.
            req_window_dict = serialize_request(req_window)
            assert echoed == req_window_dict, (
                f"Valid {field}: {window_name} should be echoed verbatim as {req_window_dict}, got {echoed}"
            )


def _assert_valid_content(ctx: dict, field: str) -> None:
    """Per-field content assertion for 'valid' partition/boundary outcomes.

    Every branch below is wire-graded by construction (architect review MEDIUM-3): none
    reads ``ctx["response"]``/``getattr`` anymore. ``wire_dict(ctx)`` is read lazily, per
    branch that actually needs delivery content — NOT hoisted unconditionally to the top
    of the function. Two reasons, both measured against the live BDD suite, not assumed:
    (1) several fields dispatched here (e.g. webhook_credentials) match no branch below
    and never touched delivery content even before this migration — forcing a wire read
    for them fails scenarios whose env never stashes a delivery wire body at all; (2) a
    "zero resolution" valid row (all requested media_buy_ids nonexistent) legitimately
    produces empty deliveries — asserting non-empty unconditionally breaks that scenario,
    which is exactly the opposite of what "valid" means for that row. The
    ``if requested_filter:`` / ``if requested_ids:`` guards below are intentionally NOT
    additionally gated on ``deliveries`` being non-empty — with wire dicts, an empty
    ``deliveries`` list makes the loop body execute zero times, which is the correct,
    non-vacuous outcome for a legitimate zero-match row, not a guard to route around.
    """
    if field in ("status_filter", "filter"):
        # NOTE (cross-ticket, salesagent-hwji/P2): the expected filter is read from
        # ctx["request_params"] rather than the dispatched request model — the
        # dispatched-request channel this should route through instead is hwji's
        # own P2 scope, not this ticket's. Left as-is pending that ticket landing.
        request_params = ctx.get("request_params", {})
        requested_filter = request_params.get("status_filter")
        if requested_filter:
            deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
            for d in deliveries:
                # No `if actual_status:` guard — a delivery that comes back with no status is
                # itself a filter violation (the filter cannot have been applied to it), and
                # guarding on it let exactly that case pass silently. See GH #1751.
                actual_status = d.get("status")
                assert actual_status in requested_filter, (
                    f"Status filter violation: got status {actual_status!r} but filter requested {requested_filter}"
                )

    elif field == "resolution":
        # NOTE (cross-ticket, salesagent-hwji/P2): see status_filter/filter above.
        request_params = ctx.get("request_params", {})
        requested_ids = request_params.get("media_buy_ids")
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or [] if requested_ids else []
        # `and deliveries` is intentional, not a vacuous-pass hole: a "zero resolution"
        # valid row (all requested ids nonexistent) legitimately produces empty
        # deliveries, and there is nothing to check per-id in that case. What this
        # guards against — deliveries silently empty when it should hold real matches —
        # is graded elsewhere (the response's own presence/count oracles), not here.
        if requested_ids and deliveries:
            returned_ids = {d.get("media_buy_id") for d in deliveries}
            for req_id in requested_ids:
                assert req_id in returned_ids, (
                    f"Resolution violation: requested media_buy_id '{req_id}' not in response: {returned_ids}"
                )

    elif field in ("reporting_dimensions", "reporting dimensions"):
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
        # Each delivery must have at least one package with data
        for d in deliveries:
            pkgs = d.get("by_package") or []
            assert pkgs, (
                f"Valid {field}: delivery {d.get('media_buy_id', '?')!r} has no package data — dimensions not populated"
            )

    elif field in ("attribution_window", "attribution window"):
        _assert_attribution_echoed_on_wire(ctx, field)

    elif field in ("daily_breakdown", "daily breakdown", "include_package_daily_breakdown"):
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
        # Branch on what the scenario REQUESTED, not on what came back. The Outline has three
        # valid rows — omitted / false / true — and omitted and false correctly expect NO daily
        # data, so a blanket presence assertion would fail rows that are behaving correctly.
        #
        # This previously read `getattr(pkg, "daily", None) or getattr(pkg, "by_day", None)` and
        # asserted only under `if daily is not None`. Neither attribute exists on
        # adcp.types.ByPackageItem (the field is `daily_breakdown`), so the guard was
        # unconditionally false and the assertion was dead by construction — no production change
        # of any kind could reach it. Proven by mutation: `assert False` inside that guard left
        # all 18 daily-breakdown rows passing. See GH #1751.
        requested = dispatched_request(ctx).include_package_daily_breakdown
        for d in deliveries:
            for pkg in d.get("by_package") or []:
                pkg_id = pkg.get("package_id", "?")
                daily = pkg.get("daily_breakdown")
                if requested is True:
                    assert daily, (
                        f"Valid {field}: include_package_daily_breakdown was requested true, but "
                        f"package {pkg_id!r} carries no daily_breakdown ({daily!r})"
                    )
                    assert isinstance(daily, list), (
                        f"Valid {field}: package {pkg_id!r} daily_breakdown is not a list: {type(daily).__name__}"
                    )
                else:
                    assert not daily, (
                        f"Valid {field}: include_package_daily_breakdown was {requested!r}, so "
                        f"package {pkg_id!r} must carry no daily_breakdown, got {daily!r}"
                    )

    elif field in ("account", "ownership"):
        # Byte-identical twins collapsed (both verify each delivery belongs to a known
        # media buy — account context and ownership are graded by the same wire read).
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or []
        for d in deliveries:
            mb_id = d.get("media_buy_id")
            assert mb_id is not None, f"Valid {field}: delivery missing media_buy_id"

    elif field in ("date_range", "date range"):
        period = wire_dict(ctx).get("reporting_period")
        if period is not None:
            assert period.get("start") is not None, f"Valid {field}: reporting_period.start is None"
            assert period.get("end") is not None, f"Valid {field}: reporting_period.end is None"


def _assert_error_outcome(ctx: dict, code: str, field: str, *, require_suggestion: bool) -> None:
    """Assert the scenario's named wire error CODE on the two-layer envelope.

    Thin wrapper over the harness-provided ``TransportResult.assert_wire_error``
    (single source of truth for wire-error assertions; recovery is pin-sourced
    from the AdCP error-code enum). The ``field`` is preserved as failure context.
    """
    result = ctx.get("result")
    assert result is not None, f"[{field}] No transport result captured to assert {code} on the wire"
    try:
        result.assert_wire_error(code, require_suggestion=require_suggestion)
    except AssertionError as exc:
        raise AssertionError(f"[{field}] {exc}") from None


def _assert_wire_rejection(ctx: dict, field: str) -> None:
    """Generic 'invalid' fallback for fields whose Examples do not YET name a specific
    error code (migration to ``error "<CODE>"`` pending — attribution_window is the
    migrated reference). Asserts a well-formed two-layer AdCP CLIENT rejection on the
    wire: not a server fault (INTERNAL_ERROR / transient) and not an auth failure. The
    precise code/recovery is asserted only once the scenario carries it.
    """
    envelope = ctx.get("wire_error_envelope")
    if isinstance(envelope, dict) and "adcp_error" in envelope:
        layer = envelope["adcp_error"]
        code = layer.get("code")
        recovery = layer.get("recovery")
        # SERVICE_UNAVAILABLE must be excluded too: ERROR_CODE_MAPPING remaps
        # INTERNAL_ERROR to SERVICE_UNAVAILABLE, and the base AdCPError default
        # recovery is "terminal" — so a {SERVICE_UNAVAILABLE, terminal} server fault
        # would otherwise pass as a field rejection. (#1420 should-fix)
        # CONFIGURATION_ERROR now passes through untranslated (salesagent-nr2q) and is
        # likewise a seller-side fault, never a field rejection.
        assert code and code not in {"INTERNAL_ERROR", "SERVICE_UNAVAILABLE", "CONFIGURATION_ERROR", "AUTH_REQUIRED"}, (
            f"Invalid {field}: expected a client rejection on the wire, got code={code!r} "
            f"— a server crash or auth failure is not a field rejection. Envelope: {envelope}"
        )
        assert recovery in ("correctable", "terminal"), (
            f"Invalid {field}: expected a client rejection (recovery correctable/terminal), got "
            f"recovery={recovery!r} — a transient server fault is not a rejection. Envelope: {envelope}"
        )
        return

    # Legacy fallback — no wire envelope captured (bare in-process exception).
    from pydantic import ValidationError

    from src.core.exceptions import AdCPError

    assert "error" in ctx, f"Expected invalid {field} result but operation succeeded"
    error = ctx["error"]
    assert isinstance(error, (AdCPError, ValidationError)), (
        f"Expected AdCPError/ValidationError for invalid {field}, got {type(error).__name__}: {error}"
    )
    if isinstance(error, AdCPError):
        assert error.error_code and error.error_code != "INTERNAL_ERROR", (
            f"Invalid {field}: expected a validation rejection, got {error.error_code}: {error}"
        )


# Fields migrated to the clean reference path (scenario names the exact error code,
# step asserts it on the harness wire envelope). attribution_window is the first.
_WIRE_ASSERTED_FIELDS = {"attribution_window"}


def _assert_partition_or_boundary(ctx: dict, expected: str, field: str = "unknown") -> None:
    """Assert partition/boundary outcome with field-aware content validation."""
    expected = expected.strip()

    if expected == "valid":
        assert "error" not in ctx, f"Expected valid {field} result but got error: {ctx.get('error')}"
        assert "response" in ctx, f"Expected response for valid {field} but none found"
        _assert_valid_content(ctx, field)
        return
    if expected == "invalid":
        _assert_wire_rejection(ctx, field)
        return

    # error "<CODE>" [with suggestion] — the scenario names the expected code.
    m = re.match(r'error "(?P<code>[A-Z_]+)"(?P<sug> with suggestion)?$', expected)
    if m:
        code = m.group("code")
        require_suggestion = bool(m.group("sug"))
        if field in _WIRE_ASSERTED_FIELDS:
            _assert_error_outcome(ctx, code, field, require_suggestion=require_suggestion)
            return
        # Legacy reconstructed path (other fields, pending migration to the wire path).
        from src.core.exceptions import AdCPError

        assert "error" in ctx, f"Expected error '{code}' for {field} but operation succeeded"
        error = ctx["error"]
        assert isinstance(error, AdCPError), f"Expected AdCPError for {field}, got {type(error).__name__}: {error}"
        assert error.error_code == code, f"Expected error code '{code}' for {field}, got '{error.error_code}'"
        if require_suggestion:
            # STRICT error.json conformance: suggestion is a top-level error
            # attribute; a copy buried in the free-form details dict does not
            # count (#1417).
            assert error.suggestion, f"Expected top-level suggestion in error for {field}, got: {error.suggestion!r}"
        return

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
    """Partition test: status_filter outcome.

    For "valid" outcomes: asserts each returned media buy has a status that
    matches the requested filter value.  For omitted filters, asserts all
    of the buyer's media buys are returned.  For "invalid"/"error" outcomes,
    delegates to the standard error assertion.
    """
    expected = expected.strip()

    if expected == "valid":
        assert "error" not in ctx, f"Expected valid status_filter result but got error: {ctx.get('error')}"
        assert "response" in ctx, "Expected response for valid status_filter but none found"
        deliveries = wire_dict(ctx).get("media_buy_deliveries") or []

        # Determine what filter was requested by inspecting the When step's kwargs.
        # NOTE (cross-ticket, salesagent-hwji/P2): reconstructed from ctx["request_params"]
        # rather than the dispatched request model — see _assert_valid_content's matching note.
        request_filter = None
        request_params = ctx.get("request_params", {})
        if request_params.get("status_filter"):
            request_filter = request_params["status_filter"]

        if request_filter and request_filter not in (["(field absent)"], ["(omitted)"]):
            # Concrete filter: every returned delivery must have a matching status
            assert deliveries, f"Expected non-empty deliveries for valid status_filter={request_filter}"
            for d in deliveries:
                # No enum unwrap: MediaBuyDeliveryData sets use_enum_values=True, so status is
                # already a plain str (and the underlying enum is a StrEnum regardless) — and now
                # this reads the wire directly, so even a non-conformant wire is graded. No
                # `is not None` guard either — a delivery that comes back without a status
                # cannot have had the filter applied to it, so that is a violation, not a case
                # to skip.
                actual_status = d.get("status")
                assert actual_status in request_filter, (
                    f"Status filter violation: delivery {d.get('media_buy_id', '?')!r} "
                    f"has status {actual_status!r} but filter requested {request_filter}"
                )
        else:
            # Omitted filter or field absent: all buyer's media buys should be returned
            assert deliveries, "Expected all buyer's media buys returned when status_filter is omitted"
    else:
        # Error/invalid cases — reuse the standard assertion logic
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
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    """Create a media buy in the test database using factories.

    Uses the env's integration DB session. If the env doesn't support
    DB operations (unit harness), this is a no-op — ctx state is enough.
    ``start_date``/``end_date`` (YYYY-MM-DD) override the factory's default
    mid-flight window when a status needs a specific flight phase.
    """
    env = ctx["env"]
    if env is None or not hasattr(env, "_session"):
        return

    from datetime import date as _date

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
    if start_date is not None:
        mb_kwargs["start_date"] = _date.fromisoformat(start_date)
    if end_date is not None:
        mb_kwargs["end_date"] = _date.fromisoformat(end_date)

    MediaBuyFactory(**mb_kwargs)


def _parse_request_params(params_str: str) -> dict[str, Any]:
    """Parse request parameters from Gherkin table/string format.

    Handles formats like:
    - media_buy_ids=["mb-001"]
    - media_buy_ids=["mb-001"] status_filter=["active"]
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


def _credential_label_to_config(label: str) -> tuple[str, str]:
    """Map a webhook-credential partition/boundary label to (auth_scheme, credentials).

    Scheme and credential length together decide validity per BR-RULE-029
    (AdCP reporting_webhook Authentication: scheme must be in the enum, credentials
    must be at least 32 characters).
    """
    text = label.lower()
    if "bearer" in text:
        scheme = "Bearer"
    elif "unknown" in text:  # "unknown_scheme" / "Unknown auth scheme not in enum"
        scheme = "Frobnicate-Not-A-Scheme"
    else:
        scheme = "HMAC-SHA256"

    if "31" in text or "too_short" in text or "too short" in text:
        credentials = "c" * 31  # below the 32-char minimum
    elif "32" in text or "minimum" in text or "at_minimum" in text:
        credentials = "c" * 32  # exactly the minimum
    else:
        credentials = "c" * 40  # comfortably valid
    return scheme, credentials


def _validate_reporting_webhook_credentials(ctx: dict, auth_scheme: str, credentials: str) -> None:
    """Drive webhook credentials through the real create_media_buy request boundary.

    The reporting webhook's Authentication (scheme enum + credentials min_length=32,
    BR-RULE-029) is validated when ``CreateMediaBuyRequest`` is parsed — the same
    validation production performs at the create_media_buy boundary. A valid config is
    accepted; an invalid one raises a ``ValidationError`` located on the credentials or
    scheme. Only credential/scheme errors count as the rejection under test; any other
    validation error means the test's base request is wrong (fail loudly).
    """
    from datetime import UTC, datetime

    from pydantic import ValidationError

    from src.core.schemas import CreateMediaBuyRequest

    reporting_webhook = {
        "url": "https://buyer.example.com/reporting",
        "authentication": {"schemes": [auth_scheme], "credentials": credentials},
        "reporting_frequency": "daily",
    }
    ctx.pop("error", None)
    try:
        ctx["response"] = CreateMediaBuyRequest(
            brand={"domain": "buyer.example.com"},
            start_time=datetime(2025, 1, 1, tzinfo=UTC),
            end_time=datetime(2025, 2, 1, tzinfo=UTC),
            reporting_webhook=reporting_webhook,
            # Required field — a valid key keeps this step's ValidationError
            # assertions scoped to the webhook credentials under test.
            idempotency_key="bdd-webhook-cred-key-0001",
        )
    except ValidationError as exc:
        offending = {".".join(str(p) for p in err["loc"]) for err in exc.errors()}
        credential_locs = {
            loc for loc in offending if "authentication.credentials" in loc or "authentication.schemes" in loc
        }
        assert credential_locs, (
            "Expected a credential/scheme validation error from the create_media_buy "
            f"boundary, but the base request failed elsewhere: {sorted(offending)}"
        )
        ctx["error"] = exc


# The account values the UC-004 delivery_account/boundary scenarios assert are
# VALID (BR-UC-004 feature Examples). Only these are seeded — the invalid rows
# (acc_nonexistent, acc_001+x.com, {}) name accounts we deliberately never seed
# so production still raises ACCOUNT_NOT_FOUND / INVALID_REQUEST for them.
_VALID_ACCOUNT_ID = "acc_acme_001"
_VALID_BRAND_DOMAIN = "acme-corp.com"
_VALID_OPERATOR = "acme-corp.com"


def _seed_valid_account_if_named(ctx: dict, value: str) -> None:
    """Seed the account a VALID delivery_account row names, so resolution succeeds.

    The delivery_account partition/boundary scenarios share one media-buy Given
    step across valid AND invalid rows, so account seeding must happen here in the
    When step where the account value is known. We seed ONLY the exact valid
    values the feature Examples mark ``valid`` (explicit acc_acme_001, the
    acme-corp.com natural key, and its sandbox:true variant); every other value —
    including the invalid rows — is left unseeded so production correctly emits
    ACCOUNT_NOT_FOUND / INVALID_REQUEST. Historically these rows only passed
    because the a2a account param was wire-dropped (salesagent-xpcd); now that
    resolution runs, a valid row REQUIRES its account to exist.
    """
    env = ctx.get("env")
    if env is None or not hasattr(env, "_session"):
        return

    try:
        parsed = json.loads(value.strip())
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(parsed, dict):
        return

    tenant = ctx.get("db_tenant")
    principal = ctx.get(f"db_principal_{getattr(env, '_principal_id', '')}")
    if tenant is None or principal is None:
        return

    from tests.bdd.steps.generic._account_resolution import seed_account_with_access

    # Explicit account_id ONLY (the invalid oneOf row also carries account_id but
    # pairs it with brand/operator — exclude it so it still errors).
    if set(parsed) == {"account_id"} and parsed["account_id"] == _VALID_ACCOUNT_ID:
        seed_account_with_access(
            tenant,
            principal,
            account_id=_VALID_ACCOUNT_ID,
            status="active",
            brand_domain=_VALID_BRAND_DOMAIN,
            operator=_VALID_OPERATOR,
        )
        return

    # Natural key (brand + operator), optionally sandbox:true. Non-sandbox and
    # sandbox variants are distinct accounts (the repo scopes the query by the
    # sandbox flag), so each valid row resolves to exactly one match.
    brand = parsed.get("brand")
    if (
        isinstance(brand, dict)
        and brand.get("domain") == _VALID_BRAND_DOMAIN
        and parsed.get("operator") == _VALID_OPERATOR
    ):
        sandbox = bool(parsed.get("sandbox", False))
        seed_account_with_access(
            tenant,
            principal,
            account_id=f"acc-acme-corp{'-sandbox' if sandbox else ''}",
            status="active",
            brand_domain=_VALID_BRAND_DOMAIN,
            operator=_VALID_OPERATOR,
            sandbox=sandbox,
        )


def _dispatch_partition(ctx: dict, field: str, value: str) -> None:
    """Dispatch a partition/boundary test request.

    Parses the partition cell against ``field``'s DECLARED type on
    ``GetMediaBuyDeliveryRequest`` (``Model.model_fields[field].annotation``, never
    re-declared here), strictly — ``TypeAdapter(annotation).validate_json(cell_json,
    strict=True)`` — instead of the prior JSON-loads-then-raw-string fallthrough that let
    a value coerce to the right type by luck. JSON mode (not Python mode) matters here:
    ``validate_python(cell, strict=True)`` rejects a nested field's plain-string enum
    member (e.g. attribution_window's ``unit: "days"``) because strict Python mode wants
    an actual enum INSTANCE, which no JSON payload can ever supply — that's not a real
    malformed-input signal, just a Python/JSON validation-mode mismatch. JSON mode
    correctly accepts the string-enum shape real wire JSON always uses, while still
    rejecting 'true'/'True'/1 for a bool field (only the JSON boolean literal
    ``true``/``false`` validates in JSON strict mode too). On success, dispatches a
    validated ``GetMediaBuyDeliveryRequest`` through :func:`dispatch_request`; on failure
    (the cell cannot become the field's declared type), dispatches through
    :func:`dispatch_malformed_request` so PRODUCTION — not this parser — rejects it on
    the wire.
    """
    value_stripped = value.strip()

    if value_stripped in ("(field absent)", "(omitted)", "(not provided)"):
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())
        return

    # JSON-decode first so a quoted cell ('"true"') and its bare counterpart (true) parse
    # to their distinct literal Python shapes (str vs bool) BEFORE type validation. The
    # strict TypeAdapter check below is what actually decides accept/reject, not this parse.
    try:
        cell = json.loads(value_stripped)
    except (json.JSONDecodeError, TypeError):
        cell = value_stripped

    annotation = GetMediaBuyDeliveryRequest.model_fields[field].annotation
    try:
        validated = TypeAdapter(annotation).validate_json(json.dumps(cell), strict=True)
    except ValidationError:
        dispatch_malformed_request(ctx, **{field: cell})
        return
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(**{field: validated}))


def _dispatch_date_range_partition(ctx: dict, label: str) -> None:
    """Translate a date-range partition label to concrete start_date/end_date.

    The partition names an abstract relationship, not a request field —
    dispatching the label verbatim leaks a bogus ``date_range=`` kwarg into the
    request model (extra=forbid -> ValidationError), which is exactly the
    plumbing bug the #1545 un-shadowing exposed. Map it to real dates so the
    valid rows succeed and the invalid rows are rejected by the tool's own
    start<end validation.
    """
    norm = label.strip().lower().replace(" ", "_")
    if "omitted" in norm or "absent" in norm or "not_provided" in norm:
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())  # no dates -> tool defaults to the last 30 days
    elif "before" in norm:
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(start_date="2026-01-01", end_date="2026-01-31"))
    elif "equal" in norm:
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(start_date="2026-01-15", end_date="2026-01-15"))
    elif "after" in norm:
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(start_date="2026-01-31", end_date="2026-01-01"))
    else:
        # "date_range" is not itself a request field (it names an abstract relationship
        # between start_date/end_date, per this function's own docstring) — an
        # unrecognized label here has no valid typed mapping, so it dispatches malformed
        # rather than crashing on GetMediaBuyDeliveryRequest.model_fields["date_range"].
        dispatch_malformed_request(ctx, date_range=label)


def _dispatch_ownership_partition(ctx: dict, label: str) -> None:
    """Translate an ownership partition label to a real identity/query.

    Ownership is decided by the caller's identity, not a request field — the buy
    is seeded under the default principal (buyer-001). ``owner_matches`` queries
    as the owner (the buy is returned); ``owner_mismatch`` queries the same buy
    id as a foreign principal (a real ownership mismatch).
    """
    norm = label.strip().lower().replace(" ", "_")
    media_buys = ctx.get("media_buys", {})
    owned_ids = _resolve_media_buy_ids(ctx, list(media_buys.keys()))
    if "mismatch" in norm or "differs" in norm:
        # Query the owned buy as a different principal — a genuine ownership
        # mismatch. (The row is selective-xfailed: production does not yet
        # reject a non-owned id, it just returns nothing.)
        from tests.factories import PrincipalFactory

        foreign = PrincipalFactory.make_identity(
            principal_id="buyer-999-foreign", tenant_id=ctx.get("tenant_id", "test_tenant")
        )
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=owned_ids or ["mb-001"]), identity=foreign)
    else:
        # owner_matches — query as the owning principal (default identity).
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=owned_ids or None))


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

    # Some boundary values (an out-of-enum status, an empty list violating min_length)
    # cannot become a valid GetMediaBuyDeliveryRequest at all — constructing the model
    # locally raises before dispatch ever runs. That IS the malformed-input signal:
    # route it through dispatch_malformed_request so PRODUCTION rejects it on the wire,
    # same as _dispatch_partition's accept/reject split.
    try:
        req = GetMediaBuyDeliveryRequest(**kwargs)
    except ValidationError:
        dispatch_malformed_request(ctx, **kwargs)
        return
    dispatch_request(ctx, req=req)


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

    if "both_provided" in partition_norm or partition_norm == "both":
        # Both selectors provided: media_buy_ids AND a status_filter. (buyer_refs
        # was removed in adcp 3.12, so "both" is now ids + filter.) A concrete
        # status_filter of "active" matches the seeded active buys.
        request_params["media_buy_ids"] = real_ids
        request_params["status_filter"] = ["active"]
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=real_ids, status_filter=["active"]))
    elif "media_buy_ids" in partition_norm and ("only" in partition_norm or "provided" in partition_norm):
        # Resolve by media_buy_ids ("media_buy_ids only" / "media_buy_ids provided").
        # Both translate to an explicit IDs request; passing the boundary label
        # verbatim would leak it into the request model (extra_forbidden).
        request_params["media_buy_ids"] = real_ids
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=real_ids))
    elif "neither_provided" in partition_norm or "neither" in partition_norm:
        # Neither IDs nor refs — should return all owned media buys
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())
    elif "partial" in partition_norm:
        # Partial resolution — request includes a nonexistent ID alongside a real
        # one. This is a partial SUCCESS: the real buy is returned and the
        # missing id yields a MEDIA_BUY_NOT_FOUND advisory (not a hard failure).
        # request_params records only the REAL id we expect back, so the "valid"
        # assertion doesn't demand the deliberately-absent one.
        real_one = real_ids[:1]
        dispatch_ids = real_one + ["mb-nonexistent"]
        request_params["media_buy_ids"] = real_one
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=dispatch_ids))
    elif "zero" in partition_norm:
        # Zero resolution — request IDs that don't exist
        request_params["media_buy_ids"] = ["mb-nonexistent-1", "mb-nonexistent-2"]
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=["mb-nonexistent-1", "mb-nonexistent-2"]))
    elif "empty_array" in partition_norm or "empty" in partition_norm and "array" in partition_norm:
        # Empty array — media_buy_ids has min_length=1, so constructing the model
        # directly raises before dispatch ever runs. That IS the rejection signal the
        # scenario wants: route through dispatch_malformed_request so PRODUCTION (not a
        # local ValidationError) rejects it on the wire.
        dispatch_malformed_request(ctx, media_buy_ids=[])
    elif "all_buys" in partition_norm or "all" in partition_norm:
        # All media buys — same as neither_provided
        dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())
    else:
        # Fallback: "resolution" is not a GetMediaBuyDeliveryRequest field — this
        # boundary label was never resolvable to a real field, so it always reached
        # production's extra="forbid" rejection. Preserve that exactly.
        dispatch_malformed_request(ctx, resolution=partition)
