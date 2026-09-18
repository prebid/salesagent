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

import httpx
import pytest
from adcp.types import AuthenticationScheme
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import (
    error_envelope_or_none,
    payload_or_none,
    require_payload,
    wire_advisory_errors,
    wire_entry,
    wire_field,
)
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.bdd.steps.generic.then_error import _get_error_message
from tests.bdd.steps.generic.then_payload import register_boundary_handler
from tests.factories.webhook import ReportingWebhookRequestFactory
from tests.harness._mixins import LocalOriginMixin
from tests.helpers import locate_envelope_error
from tests.helpers.egress_hatches import UNDIALLED_PUBLIC_HTTPS_ORIGIN
from tests.helpers.hmac_assertions import assert_signature_verifies_over_wire_body
from tests.helpers.signing import verify_as_conformant_receiver
from tests.helpers.webhook_wire import CapturedWebhook, signature_input_label, signature_input_params

# ── Helpers ──────────────────────────────────────────────────────────


def _parse_json_list(text: str) -> list[str]:
    """Parse a JSON-like list string from Gherkin, e.g., '["mb-001", "mb-002"]'."""
    return json.loads(text)


def _webhook_deliveries(ctx: dict) -> list[Any]:
    """Every webhook request the local origin actually received, oldest first."""
    return ctx["env"].delivered_requests


def _last_webhook_delivery(ctx: dict) -> Any:
    """The most recent request the endpoint received. Fails loudly when there was none.

    ONE read path for every delivery assertion in this module. The env owns WHO made the
    delivery and HOW it is read back per transport (``LocalOriginMixin.delivered_requests``
    — a REAL local origin in process, the compose stack's capture service over e2e); this
    step layer only knows that a delivery has one shape and never learns which transport
    produced it.
    """
    deliveries = _webhook_deliveries(ctx)
    assert deliveries, "No webhook POST was made"
    return deliveries[-1]


def _sent_payload(delivery: Any) -> dict[str, Any]:
    """The body of one delivery, parsed from the BYTES that arrived.

    Reading the bytes rather than a dict a client was asked to serialize is what
    lets a payload assertion be about what the receiver got (#1441). Tolerates an
    empty body — a reader that scans EVERY delivery for one media_buy_id
    (``then_skip_no_webhook``) must not die on a bodyless one.
    """
    if not delivery.body:
        return {}
    return json.loads(delivery.body)


def _captured_delivery(ctx: dict) -> CapturedWebhook:
    """The last delivery in the shape every RFC 9421 grader in this repo consumes.

    ``tests/helpers/webhook_wire.CapturedWebhook`` is what
    ``verify_as_conformant_receiver`` / ``signature_input_label`` take, so the signature
    assertions below run against the same object ``tests/e2e/test_webhook_signature_e2e.py``
    and ``tests/integration/test_notification_proof_challenge.py`` verify. One grader shape,
    one verifier — a second one here would let this surface be graded more weakly.

    Nothing is RECONSTRUCTED: the headers and the body bytes come straight off the request
    the endpoint received, and the ``@target-uri`` is :func:`_webhook_url` — the very string
    the scenario registered as the destination and production read back out of
    ``PushNotificationConfig.url``, not a URL rebuilt from wire fragments. A signature
    covers ``@target-uri`` and the body, so either being reconstructed would mean verifying
    a message that never went out.
    """
    delivery = _last_webhook_delivery(ctx)
    return CapturedWebhook(
        url=_webhook_url(ctx["env"]),
        headers=httpx.Headers(delivery.headers.items()),
        content=delivery.body,
    )


def _get_last_webhook_payload(ctx: dict) -> dict[str, Any]:
    """The JSON body of the most recent webhook delivery, as it crossed the socket.

    This is the ENVELOPE (``core/mcp-webhook-payload.json``). Delivery-report fields are
    not here -- read them through :func:`_webhook_result` -- because AdCP 3.1.1
    ``L3/webhooks.mdx`` :217 puts the report under ``result`` and says it "is not valid as
    the top-level POST body by itself".
    """
    delivery = _last_webhook_delivery(ctx)
    payload = _sent_payload(delivery)
    assert payload, f"Webhook POST had no JSON payload: {delivery.body!r}"
    return payload


def _webhook_result(body: dict[str, Any]) -> dict[str, Any]:
    """The delivery report carried by one webhook envelope.

    THE one place the two layers are separated, so no step re-decides where a report field
    lives. A step that read the envelope for ``notification_type`` or ``media_buy_id``
    found neither and said the payload was missing them -- which is what fourteen scenarios
    could not tell you while they were ledgered.
    """
    result = body.get("result")
    assert isinstance(result, dict), (
        f"the webhook envelope carries no `result` object, so there is no delivery report to "
        f"grade: got {type(result).__name__} at `result`, envelope keys {sorted(body)}"
    )
    return result


def _get_last_webhook_result(ctx: dict) -> dict[str, Any]:
    """The delivery report inside the most recent webhook POST."""
    return _webhook_result(_get_last_webhook_payload(ctx))


def _delivery_entries(result: dict[str, Any]) -> list[dict[str, Any]]:
    """``media_buy_deliveries[]`` off one delivery report, which the pin makes required."""
    entries = result.get("media_buy_deliveries")
    assert isinstance(entries, list) and entries, (
        f"the delivery report carries no media_buy_deliveries[]: got {entries!r}"
    )
    return entries


def _get_last_webhook_headers(ctx: dict) -> Any:
    """Headers of the most recent webhook delivery, as the endpoint received them.

    CASE-INSENSITIVELY, and that is a decision rather than an accident: the origin keeps
    the raw ``http.client.HTTPMessage``, so ``X-AdCP-Timestamp`` / ``X-AdCP-Signature`` /
    ``X-ADCP-Signature`` lookups that were exact-match dict reads no longer depend on the
    casing the sender happened to choose. That is HTTP-correct — RFC 9110 makes field names
    case-insensitive — and it stops these assertions grading the sender's FORMATTING
    instead of the wire contract (salesagent-n78j0.1.4).
    """
    return _last_webhook_delivery(ctx).headers


def _collect_all_packages(resp: Any) -> list[Any]:
    """Collect all packages across all deliveries in a response."""
    return [pkg for d in resp.media_buy_deliveries for pkg in d.by_package]


def _extract_webhook_success(ctx: dict) -> bool:
    """Extract the boolean success flag from ctx['webhook_result'].

    Handles both shapes: bare ``bool`` (from call_send) and
    ``tuple[bool, dict]`` (from call_deliver).
    """
    raw = ctx.get("webhook_result")
    if isinstance(raw, tuple):
        return raw[0]
    return bool(raw)


def _assert_placements_sorted_by(packages: list[Any], metric: str, *, fallback: bool) -> None:
    """Assert by_placement entries are sorted by the given metric descending.

    THE ABSENCE OF THE SUBJECT IS NOT AN EXCUSE. Both guards here used to xfail: one
    when the entries lacked the metric, one when NO package carried by_placement at
    all -- so a seller that stopped emitting placement breakdowns entirely, which is
    precisely the regression this step exists to catch, turned it green-by-xfail
    instead of red (the archetype).

    The pinned 3.1 get-media-buy-delivery-response.json makes by_placement conditional
    -- "Available when the buyer requests placement breakdown via reporting_dimensions
    and the seller supports it" -- so its absence is legal IN GENERAL. It is not legal
    HERE: a scenario that asks whether the breakdown is SORTED has already asserted the
    breakdown exists, and if this seller does not support it the scenario does not
    belong, rather than belonging and excusing itself.
    """
    checked = False
    for pkg in packages:
        placements = getattr(pkg, "by_placement", None) or []
        if not placements or not isinstance(placements, list):
            continue
        # Need at least 2 placements to verify sort order
        if isinstance(placements[0], dict):
            first_val = placements[0].get(metric)
        else:
            first_val = getattr(placements[0], metric, None)
        suffix = " (fallback)" if fallback else ""
        assert first_val is not None, (
            f"by_placement entries lack metric {metric!r}{suffix}; a sort assertion needs the value it sorts by"
        )
        values = []
        for p in placements:
            val = p.get(metric) if isinstance(p, dict) else getattr(p, metric, None)
            if val is not None:
                values.append(val)
        assert values == sorted(values, reverse=True), (
            f"Placement breakdown not sorted by '{metric}' descending: {values}"
        )
        checked = True
    assert checked, (
        "no package carried by_placement data, so the sort claim graded nothing. A seller "
        "that stopped emitting placement breakdowns entirely would reach this line."
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


@given(
    parsers.parse(
        'package "{pkg_id}" uses pricing_model "{pricing_model}" with rate {rate:g} and currency "{currency}"'
    )
)
def given_package_pricing(ctx: dict, pkg_id: str, pricing_model: str, rate: float, currency: str) -> None:
    """The most recently seeded buy carries ONE package bought on these terms.

    Rewrites the buy's persisted request to name *pkg_id* and stores the matching
    ``MediaPackage`` row, both through the fixture machinery: the request package comes
    from ``request_package`` (bound to ``PackageRequest``), the option is named by
    the option row's own ``pricing_option_id``, and its ``pricing_info`` is the same projection
    production writes. Nothing here restates a shape.

    Fixed-rate terms. The auction Given below is separate because auction pricing carries
    a bid and the rate the buyer is owed is derived, not agreed.
    """
    _seed_package_terms(ctx, pkg_id, pricing_model=pricing_model, rate=rate, currency=currency, is_fixed=True)


@given(
    parsers.parse(
        'package "{pkg_id}" is bought at auction on pricing_model "{pricing_model}" '
        'with bid_price {bid_price:g} and currency "{currency}"'
    )
)
def given_package_auction_pricing(ctx: dict, pkg_id: str, pricing_model: str, bid_price: float, currency: str) -> None:
    """The most recently seeded buy carries ONE package bought at AUCTION.

    An auction package has a bid, not an agreed rate: ``get-media-buy-delivery-response.json``
    says of ``by_package[].rate`` that "For auction-based pricing, this represents the
    effective rate based on actual delivery" — so the rate the buyer is owed is computed
    from what delivered, and no rate is stored for it up front.
    """
    _seed_package_terms(
        ctx, pkg_id, pricing_model=pricing_model, rate=None, currency=currency, is_fixed=False, bid_price=bid_price
    )


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


def _assert_media_buy_unseeded(ctx: dict, mb_id: str) -> None:
    """The scenario must not have seeded the media buy it calls nonexistent.

    Absence is the default -- a UC-004 scenario creates every media buy it wants
    through a Given, so anything unseeded does not exist. The falsifiable half is
    the other direction: a scenario that seeds ``mb-001`` and then declares it
    absent is testing nothing, and used to say so into a ctx list no step read.
    """
    seeded = ctx.get("media_buys", {})
    assert mb_id not in seeded, (
        f"Step claims no media buy exists with id {mb_id!r}, but this scenario seeded it. Seeded ids: {sorted(seeded)}"
    )


@given(parsers.parse('no media buy exists with id "{mb_id}"'))
def given_no_media_buy(ctx: dict, mb_id: str) -> None:
    """Ensure no media buy with this ID exists."""
    _assert_media_buy_unseeded(ctx, mb_id)


@given(parsers.parse('no media buy exists with id "{mb_id1}" or "{mb_id2}"'))
def given_no_media_buys(ctx: dict, mb_id1: str, mb_id2: str) -> None:
    """Ensure neither media buy exists."""
    _assert_media_buy_unseeded(ctx, mb_id1)
    _assert_media_buy_unseeded(ctx, mb_id2)


@given(parsers.parse('the principal "{principal_id}" has no media buys'))
def given_principal_no_buys(ctx: dict, principal_id: str) -> None:
    """Principal exists but has no media buys."""
    ctx["media_buys"] = {}


# REMOVED with the scenario it served: `no principal "<id>" exists in the tenant
# database`. The harness seeds exactly one principal -- the authenticated one -- so any
# other id is absent by construction and the step could only assert that the scenario did
# not name THAT one. Naming an absent id changed nothing about the request, so ext-b went
# out as the real authenticated principal and graded a successful read while claiming to
# grade a refusal. It now uses the generic `a presented credential that resolves to no
# principal`, which the resolver actually rejects (AUTH_INVALID).


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


@given(parsers.parse('the ad server adapter reports {impressions:d} impressions and {spend:g} spend for "{pkg_id}"'))
def given_adapter_package_metrics(ctx: dict, impressions: int, spend: float, pkg_id: str) -> None:
    """The adapter reports exactly these metrics for *pkg_id* of the seeded buy.

    Per-package, because an effective rate is a ratio of what THIS package delivered; a
    buy-level total would not pin it.
    """
    mb_id = next(reversed(ctx.get("media_buys", {})), None)
    assert mb_id, "adapter metrics need a media buy — the scenario must seed one first"
    ctx["env"].set_adapter_response(
        media_buy_id=mb_id,
        impressions=impressions,
        spend=spend,
        packages=[{"package_id": pkg_id, "impressions": impressions, "spend": spend}],
    )


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


# The URL a buyer *declares* in a create_media_buy request. It is never fetched
# by these scenarios — only validated — so it stays a stable literal. It IS
# validated at ingest now (the seam's validate_url runs inside
# _create_media_buy_impl), so the literal must pass egress policy under every
# hatch posture: an https public-unicast IP literal resolves nothing (no DNS
# dependency) and is refused by no gate, unlike the previous
# ``buyer.example.com``, which NXDOMAINs and is therefore refused even with
# both hatches open.
_DECLARED_WEBHOOK_URL = f"{UNDIALLED_PUBLIC_HTTPS_ORIGIN}/webhook"

# A destination the egress seam refuses under EVERY hatch posture. The cloud
# metadata address is blocked by the SDK's address policy even with
# ADCP_OUTBOUND_ALLOW_PRIVATE on, which the delivery envs turn on for their
# loopback origin — a private-range or plaintext-http cause would be disarmed
# there and the "blocked" scenario would silently deliver.
BLOCKED_WEBHOOK_URL = "https://169.254.169.254/webhook"


def _webhook_url(env: Any) -> str:
    """The endpoint a delivery for this scenario would actually be POSTed to.

    Envs that DELIVER (``WebhookEnv``, ``CircuitBreakerEnv``) run a real local
    origin, and every webhook config row — plus every circuit-breaker key
    derived from one — has to name it, or production would POST somewhere the
    scenario cannot observe. Its port is assigned by the kernel, so it cannot be
    a module constant.

    Envs that merely CARRY a webhook config through a create or poll request
    (``DeliveryPollEnv``, ``MediaBuyCreateEnv``) never fetch it: for them the URL
    is data being validated, and the declared literal is the honest value.

    Realized per transport by the env itself (``LocalOriginMixin.webhook_url``), never by
    a literal in this module: over e2e the destination is the compose stack's capture
    origin, so a hard-coded in-process URL would name an endpoint no delivery ever touched
    — and a symbol called IN_PROCESS_* sitting in ``tests/bdd/steps/`` IS the step layer
    naming a transport (salesagent-n78j0.1.4).
    """
    return env.webhook_url if isinstance(env, LocalOriginMixin) else _DECLARED_WEBHOOK_URL


def _circuit_breaker_endpoint_key(ctx: dict, webhook_url: str | None = None) -> str:
    """The key production files this scenario's circuit breaker under.

    ``WebhookDeliveryService`` keys breakers ``f"{tenant_id}:{webhook_url}"``, so a step
    that names one must name the SAME url production delivered to — :func:`_webhook_url`,
    realized per transport.

    *webhook_url* overrides the destination for the two Givens that CREATE the breaker
    from a url the scenario itself configured. Readers pass nothing and pick up whichever
    key those Givens stashed, falling back to the env's own key when no Given ran.

    Only ever used to NAME a breaker. Seeding and reading go through the env's breaker
    accessors — no step reaches into ``WebhookDeliveryService._circuit_breakers``, which
    is process-local and therefore unfalsifiable across a process boundary
    (``test_architecture_bdd_wire_discipline.py`` Check C, allowlist permanently empty).
    """
    stashed = ctx.get("circuit_breaker_endpoint_key")
    if webhook_url is None and stashed:
        return str(stashed)
    env = ctx["env"]
    if webhook_url is None:
        # The env derives it, because the origin's port is only known at runtime
        # (:meth:`LocalOriginMixin.endpoint_key`) — one place it is built.
        return str(env.endpoint_key())
    return f"{env._tenant_id}:{webhook_url}"


def _set_active_webhook(ctx: dict, mb_id: str) -> None:
    """Shared: configure an active webhook for a media buy.

    Persists PushNotificationConfig to the DB whenever the env has a bound
    session. That includes E2E: BaseTestEnv binds ``_session`` to the LIVE
    server's database when ``e2e_config`` is present (``_base.py`` :1198-1212), so
    the row this writes is one the running server reads — the same mechanism
    ``_persist_simulation_config`` uses for delivery-poll responses.
    """
    env = ctx["env"]
    ctx.setdefault("webhook_config", {})[mb_id] = {
        "url": _webhook_url(env),
        "active": True,
    }
    if getattr(env, "_session", None) is not None:
        _persist_webhook_config_if_needed(ctx, env)
        _ensure_delivery_log_parent(env)


def _ensure_delivery_log_parent(env: Any) -> None:
    """Give ``webhook_delivery_log.media_buy_id`` the ``media_buys`` row it references.

    The senders SWALLOW the integrity error and log it, so without the parent row the
    insert fails silently, leaves zero rows, and every delivery-log assertion grades
    nothing -- which is how ``assert_rejection_logged`` came to pass while scanning a
    ForeignKeyViolation message for the very status code it was supposed to be grading.

    LAZY, and from this Given rather than from the env's ``__enter__``. Seeding at enter
    pre-created the tenant, and the 18 tests in
    ``tests/integration/test_delivery_service_behavioral.py`` that build their own with a
    raw ``TenantFactory(tenant_id="t1")`` then died on a duplicate key. The Given runs
    after the test's own setup, which is exactly why it belongs here.

    ``deliver_webhook``'s default id, because that is what the senders stamp on the row --
    the scenario's own "mb-001" spelling never reaches the database.
    """
    if not hasattr(env, "make_media_buy"):
        return
    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    if env._session.scalars(select(MediaBuy).filter_by(media_buy_id="mb_001")).first() is None:
        env.make_media_buy(media_buy_id="mb_001")


def _canonical_scheme(scheme: str) -> AuthenticationScheme:
    """The pinned AdCP ``AuthenticationScheme`` member a Gherkin token names.

    The SDK enum is the SINGLE SOURCE. Its constructor is both the membership
    test and the refusal, and the member IS the spec spelling (a ``StrEnum``), so
    ``authentication_type`` is canonical by construction rather than by a local
    lookup that can drift. There used to be such a lookup here, mapping four
    Gherkin spellings onto two DB values; the drift it permitted is exactly how
    ``authentication_type="hmac"`` -- a fifth spelling matching nothing
    production compares against -- reached the DB (GH #1894).
    A local table is not re-introduced: with none, a fifth spelling has nowhere
    to live.
    """
    try:
        return AuthenticationScheme(scheme)
    except ValueError as exc:
        canonical = ", ".join(f"{member!s}" for member in AuthenticationScheme)
        raise ValueError(
            f"Gherkin names webhook authentication scheme {scheme!r}, which is not a pinned "
            f"AdCP AuthenticationScheme. Canonical spellings: {canonical}. The pinned enum is "
            "case-SENSITIVE; a scenario must name the scheme exactly as the spec spells it."
        ) from exc


def _credential_in_ctx(scheme: AuthenticationScheme, ctx: dict) -> str | None:
    """The credential a scheme signs/authenticates with, matched EXHAUSTIVELY.

    Mirrors the exhaustive destructuring production does at
    ``src/core/security/webhook_egress.py``. The predecessor was a binary
    if/else on one member, so Bearer was "whatever is not HMAC-SHA256" and a
    third canonical member would have silently read the bearer token. Falling
    out of this match is a visible gap instead.
    """
    match scheme:
        case AuthenticationScheme.HMAC_SHA256:
            return ctx.get("webhook_secret")
        case AuthenticationScheme.Bearer:
            return ctx.get("webhook_bearer_token")
    raise AssertionError(
        f"No credential source wired for AuthenticationScheme {scheme!r}. A new member was "
        "added to the pinned enum -- give it its ctx key here rather than letting it inherit "
        "another scheme's credential."
    )


def _auth_scheme_to_db_fields(scheme: str | None, ctx: dict) -> dict[str, Any]:
    """Translate a Gherkin auth scheme to the PushNotificationConfig DB columns.

    ONE pair of columns for every scheme: ``authentication_type`` plus
    ``authentication_token``, which is where AdCP 3.1.1 puts the credential
    (``push_notification_config.authentication.credentials``) — and the one
    selector the spec defines (security.mdx @ v3.1.1 :1424, "Mode selection is a
    switch, not both"). The HMAC arm used to seed a separate ``webhook_secret``
    column instead, which production never wrote and which #1291 C1 retired: a
    second selector is precisely the "signed two ways" shape :1425 forbids.

    Three outcomes, and nothing else:

    1. No scheme named -> ``{}``. The scenario wants an unauthenticated webhook.
    2. A canonical scheme with its credential -> both columns.
    3. A scheme that is not a pinned ``AuthenticationScheme`` member -> RAISE.

    A canonical scheme whose credential is not in ``ctx`` YET returns ``{}`` on
    purpose, and that is NOT outcome 1 in disguise: scenarios name the scheme on
    one line and supply the credential on the next
    (BR-UC-004-deliver-media-buy-metrics.feature:252-253), and
    ``given_webhook_auth_scheme`` persists the row on the first line, so mid-setup
    absence is legitimate and the row is updated in place when the credential
    arrives. The end-state invariant -- a scenario that claims auth and never
    supplied a credential -- is enforced once setup is COMPLETE, in
    ``_wire_webhook_db``. Raising here instead would redden the two scenarios
    this translation exists to serve.
    """
    if scheme is None:
        return {}
    canonical = _canonical_scheme(scheme)
    credential = _credential_in_ctx(canonical, ctx)
    if not credential:
        return {}
    return {"authentication_type": canonical, "authentication_token": credential}


def _persist_webhook_config_if_needed(ctx: dict, env: Any) -> None:
    """Idempotently create or update the PushNotificationConfig DB row.

    Reads ``ctx['webhook_config']`` and ``ctx['webhook_secret']`` /
    ``ctx['webhook_bearer_token']`` so subsequent Given-steps that set the
    secret/token can re-run persistence and pick up the new values.
    Sister-task ```` ensured the
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
            PushNotificationConfig.url == _webhook_url(env),
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
        url=_webhook_url(env),
        is_active=True,
        **auth_fields,
    )


@given(parsers.parse('a media buy "{mb_id}" with an active reporting_webhook configured'))
@given(parsers.parse('a media buy "{mb_id}" with an active reporting_webhook'))
@given(parsers.parse('a media buy "{mb_id}" with webhook delivery configured'))
def given_webhook_configured(ctx: dict, mb_id: str) -> None:
    """Media buy has an active webhook endpoint configured.

    Three sentences, one precondition. They were three functions with the same
    body plus a ``webhook_variant`` flag ("active" / "delivery") that no step
    read -- so the variant was a distinction the scenarios could see in their
    Gherkin and the harness could not act on. One owner, three spellings.
    """
    _set_active_webhook(ctx, mb_id)


@given(parsers.parse('a media buy "{mb_id}" without a reporting_webhook configured'))
def given_no_webhook(ctx: dict, mb_id: str) -> None:
    """Media buy has no webhook configured."""
    ctx.setdefault("webhook_config", {})[mb_id] = {"active": False}


@given(parsers.parse('the reporting_frequency is "{frequency}"'))
def given_reporting_frequency(ctx: dict, frequency: str) -> None:
    """The scenario's reporting webhook fires at this frequency.

    Nothing on this path reaches a per-scenario frequency switch, so what the step
    establishes is that the frequency the scenario names is one the pinned
    ``ReportingWebhook.reporting_frequency`` enum admits. That is not ceremony:
    ``tests/factories/webhook.py`` records the live bug where a webhook config
    spelled this field ``frequency``, the model dropped the unknown key, and a
    scenario that "configures daily reporting" configured nothing. The top-level
    ctx flag this replaced was read by no step.
    """
    from adcp.types import ReportingFrequency

    allowed = {m.value for m in ReportingFrequency}
    assert frequency in allowed, (
        f"reporting_frequency {frequency!r} is not in the pinned ReportingFrequency enum: {sorted(allowed)}"
    )


@given(parsers.parse('a media buy "{mb_id}" with webhook authentication scheme "{scheme}"'))
def given_webhook_auth_scheme(ctx: dict, mb_id: str, scheme: str) -> None:
    """Configure webhook with specific auth scheme.

    Also creates the ``PushNotificationConfig`` DB row so a subsequent
    ``given_shared_secret_valid`` / ``given_bearer_token_valid`` step can
    update the same row in-place with the auth credentials.
    """
    env = ctx["env"]
    wh = ctx.setdefault("webhook_config", {}).setdefault(mb_id, {})
    wh["auth_scheme"] = scheme
    wh["active"] = True
    wh["url"] = _webhook_url(env)
    if getattr(env, "_session", None) is not None:
        _persist_webhook_config_if_needed(ctx, env)


@given("the reporting_webhook registers no authentication block")
def given_webhook_no_authentication(ctx: dict) -> None:
    """The RFC 9421 selector: :1424 makes the ABSENCE of the block the mode switch.

    Stated rather than assumed. The registration this scenario needs is not "some
    webhook" but specifically one carrying NEITHER ``authentication_type`` NOR
    ``authentication_token`` — with either set, ``_headers_for``
    (``src/core/security/webhook_egress.py``) takes the legacy arm and the whole scenario
    grades HMAC while claiming to grade 9421. So the
    persisted row is read back and checked, which also makes this Given fail loudly if
    a preceding Given ever starts seeding a default credential.
    """
    from sqlalchemy import select

    from src.core.database.models import PushNotificationConfig

    configs = ctx.get("webhook_config", {})
    assert configs, "a preceding Given must have registered a reporting_webhook"
    for cfg in configs.values():
        cfg.pop("auth_scheme", None)
    ctx.pop("webhook_secret", None)
    ctx.pop("webhook_bearer_token", None)

    env = ctx["env"]
    session = getattr(env, "_session", None)
    assert session is not None, "the 9421 arm reads the persisted registration, so it needs the integration env"
    _persist_webhook_config_if_needed(ctx, env)
    row = session.scalars(
        select(PushNotificationConfig).where(
            PushNotificationConfig.tenant_id == env._tenant_id,
            PushNotificationConfig.principal_id == env._principal_id,
            PushNotificationConfig.url == _webhook_url(env),
        )
    ).first()
    assert row is not None, f"no PushNotificationConfig was registered for {_webhook_url(env)}"
    assert (row.authentication_type, row.authentication_token) == (None, None), (
        "this registration must carry no authentication block — it is the ABSENCE that selects "
        f"RFC 9421 (:1424) — but it carries type={row.authentication_type!r} token set: "
        f"{row.authentication_token is not None}"
    )


@given("the tenant publishes an RFC 9421 webhook signing key")
def given_tenant_publishes_signing_key(ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt IN to signing material for THIS scenario.

    Deliberately a per-scenario step and not an env default: every other UC-004 webhook
    scenario must keep its current posture byte-for-byte, and
    ``@T-UC-004-webhook-notification-type`` asserts exactly that by requiring no
    ``Signature`` header on a delivery where this step never ran.

    Both halves matter and the env owns both: an ACTIVE key row, and an origin the key
    can be PUBLISHED from. Without the publishable origin ``origin_is_publishable``
    is false, ``delivery_signer_for_tenant`` (``src/core/signing/outbound.py``) returns
    ``None``, ``_headers_for`` has no signer to hand ``send``, the delivery goes out
    unsigned — and a
    sibling that skipped this would pass vacuously on headers that were simply absent.

    *monkeypatch* is requested like any other fixture — pytest-bdd step functions can
    depend on additional fixtures beyond ``ctx`` — and threaded through to the env's
    provisioner, which needs it to configure the deployment KEK
    (``provision_signing_key`` refuses to mint without one).
    """
    env = ctx["env"]
    ctx["webhook_signing_kid"] = env.provision_webhook_signing_key(monkeypatch)


@given("the shared secret is a valid 32+ character string")
def given_shared_secret_valid(ctx: dict) -> None:
    """A valid shared secret for HMAC."""
    secret = "a" * 32
    # ONE key. ``then_hmac_computation`` reproduces the signature from
    # ``ctx['webhook_secret']`` -- the same value production uses to generate the
    # header. A second ``signing_secret`` mirror used to be written here "so both
    # keys stay in lockstep"; nothing ever read it, so it was a copy that could
    # only ever drift.
    ctx["webhook_secret"] = secret
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
    """Point this scenario's webhook at an address the egress seam always refuses.

    The send-time gate is the seam (``src.core.security.outbound_http``): it
    resolves, validates and pins inside the same call that opens the socket, so
    a delivery to a refused address raises ``OutboundRequestBlocked`` before any
    POST and ``_deliver_with_backoff`` turns that into ``record_failure()``.
    There is no longer an application-level gate above it to program, so this
    Given states the CAUSE — a blocked destination — rather than mocking a
    verdict.

    The cause has to be one no configuration disarms. The delivery envs run a
    real loopback origin and therefore open BOTH egress hatches
    (``LocalOriginMixin``), which disarms the private-range and the plaintext-
    scheme refusals; the cloud-metadata address is refused by the SDK's address
    policy regardless of ``allow_private``, so it is the one cause that means
    the same thing under every posture this scenario can run in.
    """
    env = ctx["env"]
    for cfg in ctx.setdefault("webhook_config", {}).values():
        cfg["url"] = BLOCKED_WEBHOOK_URL
    # Rewrite the endpoint production will actually read: the preceding Given
    # persisted a config naming the local origin, and set_db_webhooks replaces
    # the active set (deactivating that row) rather than adding to it — leaving
    # it active would deliver to the origin and grade nothing.
    env.set_db_webhooks([env.make_webhook_config(url=BLOCKED_WEBHOOK_URL)])
    # Production keys the breaker on the URL it was asked to deliver to, so the
    # Then step has to look under the blocked URL, not the origin's.
    ctx["circuit_breaker_endpoint_key"] = f"{env._tenant_id}:{BLOCKED_WEBHOOK_URL}"


@given("the webhook endpoint is unreachable (connection timeout)")
def given_webhook_unreachable(ctx: dict) -> None:
    """Make the webhook endpoint accept the connection and then drop it.

    The client raises its own transport error, which is what
    :class:`WebhookDeliveryService` catches as ``httpx.RequestError`` and
    retries with backoff. Nothing here names the exception — the endpoint
    misbehaves and the client decides what that was.
    """
    ctx["env"].set_http_error()


@given(parsers.parse("the webhook endpoint returns {status_code:d} Unauthorized"))
def given_webhook_unauthorized(ctx: dict, status_code: int) -> None:
    """Configure webhook endpoint to return auth error."""
    env = ctx["env"]
    env.set_http_status(status_code, "Unauthorized")


@given(parsers.parse("the webhook endpoint has failed {n:d} consecutive delivery attempts"))
def given_webhook_failed_n_times(ctx: dict, n: int) -> None:
    """Trigger n consecutive delivery failures on the circuit breaker."""
    env = ctx["env"]
    configured_url = next(iter(ctx.get("webhook_config", {}).values()), {}).get("url")
    endpoint_key = _circuit_breaker_endpoint_key(ctx, configured_url)
    env.seed_breaker_failures(endpoint_key, n)
    ctx["circuit_breaker_endpoint_key"] = endpoint_key


@given(parsers.parse('a media buy "{mb_id}" with circuit breaker in "{state}" state'))
def given_circuit_breaker_state(ctx: dict, mb_id: str, state: str) -> None:
    """Start the scenario with the breaker already in *state* (a seed, not an effect)."""
    env = ctx["env"]
    configured_url = ctx.get("webhook_config", {}).get(mb_id, {}).get("url")
    endpoint_key = _circuit_breaker_endpoint_key(ctx, configured_url)
    env.set_breaker_state(endpoint_key, state)
    ctx["circuit_breaker_endpoint_key"] = endpoint_key


@given("the circuit breaker timeout (60s) has elapsed")
def given_circuit_breaker_timeout(ctx: dict) -> None:
    """Age the last failure past the breaker's recovery timeout.

    Moves the clock, not the state: production decides that an elapsed timeout
    means OPEN -> HALF_OPEN on the next ``can_attempt``, and setting the state
    here would skip the very transition the scenario exercises.
    """
    env = ctx["env"]
    endpoint_key = _circuit_breaker_endpoint_key(ctx)
    env.elapse_breaker_timeout(endpoint_key)


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


def _assert_wire_carries_dimension(dimension: str) -> None:
    """The named reporting dimension must be one the delivery wire can carry.

    Derived from ``PackageDelivery`` rather than a literal list, so the vocabulary
    cannot drift from what a package breakdown can actually hold: a ``by_<dim>``
    field IS the dimension's wire surface.

    This is all a seller-capability sentence can establish here, and it is why the
    positive and negative sentences check the same thing: production has NO
    per-seller reporting-capability gate (there is no column, no adapter flag and
    no capabilities field for it), so a dimension is never "unsupported" by a
    seller -- it is either expressible on the wire or misspelled. Each of these
    steps used to record a ctx flag instead, and no step anywhere read one.
    """
    from src.core.schemas.delivery import PackageDelivery

    carried = {
        name[3:] for name in PackageDelivery.model_fields if name.startswith("by_") and not name.endswith("_truncated")
    }
    assert dimension in carried, (
        f"Step names reporting dimension {dimension!r}, which no PackageDelivery "
        f"breakdown field can carry. Known dimensions: {sorted(carried)}"
    )


def _assert_wire_carries_metric(metric: str) -> None:
    """The named delivery metric must be a field a breakdown entry can carry.

    Same derivation and same reason as ``_assert_wire_carries_dimension``:
    ``PlacementBreakdown`` is the pinned per-entry metric surface, and production
    has no per-seller metric capability gate for either sentence to configure.
    """
    from src.core.schemas.delivery import PlacementBreakdown

    assert metric in PlacementBreakdown.model_fields, (
        f"Step names delivery metric {metric!r}, which is not a field on "
        f"PlacementBreakdown -- no breakdown entry could ever report it."
    )


def _assert_attribution_window_has_no_seller_toggle() -> None:
    """attribution_window is buyer-configurable, with no seller-side opt-out.

    Both "the seller supports configurable attribution windows" and its negation
    reduce to this one production fact, so both establish it: the buyer sends the
    window on the request and nothing on the seller side can refuse it. What the
    two scenarios then grade is production's actual echo behaviour, not a
    precondition either sentence could set.
    """
    from src.core.schemas.delivery import GetMediaBuyDeliveryRequest

    assert "attribution_window" in GetMediaBuyDeliveryRequest.model_fields, (
        "Step claims attribution windows are configurable, but the delivery "
        "request carries no attribution_window field."
    )
    toggles = [f for f in GetMediaBuyDeliveryRequest.model_fields if "attribution" in f and f != "attribution_window"]
    assert not toggles, (
        f"The request now carries an attribution capability toggle ({toggles}) -- "
        "the supports/does-NOT-support sentences can finally differ, so they must "
        "stop sharing this body."
    )


@given(parsers.parse('the seller supports reporting dimension "{dimension}"'))
def given_seller_supports_dimension(ctx: dict, dimension: str) -> None:
    """Seller supports a specific reporting dimension."""
    _assert_wire_carries_dimension(dimension)


@given(parsers.parse('the seller does NOT support reporting dimension "{dimension}"'))
def given_seller_no_dimension(ctx: dict, dimension: str) -> None:
    """Seller does not support a specific reporting dimension."""
    _assert_wire_carries_dimension(dimension)


@given(parsers.parse('the seller supports reporting dimensions "{dim1}" and "{dim2}"'))
def given_seller_supports_dimensions(ctx: dict, dim1: str, dim2: str) -> None:
    """Seller supports multiple reporting dimensions.

    Also configures the adapter with simulated breakdown data so that
    multi-dimension requests (BR-RULE-091 INV-1) return non-empty arrays.
    """
    _assert_wire_carries_dimension(dim1)
    _assert_wire_carries_dimension(dim2)
    env = ctx["env"]
    for mb_id in ctx.get("media_buys", {}):
        env.set_adapter_response(media_buy_id=mb_id)


@given(parsers.parse('the seller does NOT support "{capability}"'))
def given_seller_no_capability(ctx: dict, capability: str) -> None:
    """Seller does not support a capability (used for reporting dimensions)."""
    _assert_wire_carries_dimension(capability)


@given("the seller supports configurable attribution windows")
def given_seller_supports_attribution(ctx: dict) -> None:
    """Seller supports configurable attribution windows."""
    _assert_attribution_window_has_no_seller_toggle()


@given("the seller does NOT support configurable attribution windows")
def given_seller_no_attribution(ctx: dict) -> None:
    """Seller does not support configurable attribution windows."""
    _assert_attribution_window_has_no_seller_toggle()


@given(parsers.parse('the seller does NOT report metric "{metric}"'))
def given_seller_no_metric(ctx: dict, metric: str) -> None:
    """Seller does not report a specific metric."""
    _assert_wire_carries_metric(metric)


@given(parsers.parse('the seller reports metric "{metric}"'))
def given_seller_reports_metric(ctx: dict, metric: str) -> None:
    """Seller reports a specific metric."""
    _assert_wire_carries_metric(metric)


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
    """Request delivery metrics by media_buy_ids, recording the ids it sent.

    ``ctx["requested_media_buy_ids"]`` is the request as sent, and the non-disclosure
    Then needs it: it used to look for ``target_media_buy_id`` or ``media_buy_id``,
    neither of which any step writes (salesagent-b9hi1.1).
    """
    media_buy_ids = _parse_json_list(ids_json)
    ctx["requested_media_buy_ids"] = media_buy_ids
    dispatch_request(ctx, media_buy_ids=media_buy_ids)


@when("the Buyer Agent requests delivery metrics without media_buy_ids")
def when_request_no_identifiers(ctx: dict) -> None:
    """Request delivery metrics without any identifiers."""
    dispatch_request(ctx)


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
    dispatch_request(ctx, **kwargs)


@when(parsers.parse("the Buyer Agent requests delivery metrics with media_buy_ids {ids_json}"))
def when_request_with_media_buy_ids(ctx: dict, ids_json: str) -> None:
    """Request with explicit media_buy_ids list."""
    if ids_json == "[]":
        dispatch_request(ctx, media_buy_ids=[])
    else:
        media_buy_ids = _parse_json_list(ids_json)
        dispatch_request(ctx, media_buy_ids=media_buy_ids)


@when(parsers.re(r'the Buyer Agent requests delivery metrics with status_filter "(?P<filter_value>[^"]+)"'))
def when_request_with_status_filter(ctx: dict, filter_value: str) -> None:
    """Request with status_filter string.

    Records the requested filter in ctx["request_params"] so then_filter_result
    can reconstruct it. The "(field absent)" / "(omitted)" sentinels mean "send
    no status_filter at all" — dispatching the literal would resolve to an empty
    filter and drop every buy.

    An Examples cell that SPELLS an array ("[]") means that array, not a
    one-element list holding its literal text. Wrapping it produced
    ``status_filter=["[]"]``, which the pinned MediaBuyStatus enum rejects as an
    unknown value — the very rejection the ``unknown_value`` sibling row already
    grades — so the ``empty_array`` row passed without ever exercising the
    minItems obligation it is named for (pinned v3.1.1 StatusFilter: "List
    should have at least 1 item after validation, not 0").
    """
    if filter_value in ("(field absent)", "(omitted)"):
        ctx.setdefault("request_params", {})["status_filter"] = [filter_value]
        dispatch_request(ctx)
        return

    status_filter = _parse_json_list(filter_value) if filter_value.startswith("[") else [filter_value]
    ctx.setdefault("request_params", {})["status_filter"] = status_filter
    dispatch_request(ctx, status_filter=status_filter)


@when(parsers.re(r"the Buyer Agent requests delivery metrics with status_filter (?P<filter_json>\[.+?\])"))
def when_request_with_status_filter_list(ctx: dict, filter_json: str) -> None:
    """Request with status_filter list."""
    status_filter = _parse_json_list(filter_json)
    ctx.setdefault("request_params", {})["status_filter"] = status_filter
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

    Dispatches as the principal the env points at. A scenario that authenticated as a
    named principal switched the env there already (``authenticate_env_as``), so the
    credential presents that principal's token, and a principal with no row presents
    none and is refused by the resolver.
    """
    media_buys = ctx.get("media_buys", {})
    mb_ids = list(media_buys.keys()) or None
    kwargs: dict = {}
    if mb_ids:
        kwargs["media_buy_ids"] = mb_ids
    dispatch_request(ctx, **kwargs)


# "the Buyer Agent sends a delivery metrics request without authentication" is bound by
# steps/generic/given_auth.py::when_dispatch_without_credential, together with UC-011's
# list_accounts spelling. Both were functions here and there with byte-identical bodies:
# presenting no credential is tool-agnostic, because the tool is the env's declaration.


# ── Webhook When steps ─────────────────────────────────────────────


def _deliver_for_label(ctx: dict, mb_id: str) -> tuple[bool, dict[str, Any]]:
    """Ask the env to deliver for the Gherkin LABEL *mb_id*, resolved to its real id.

    THE RESOLUTION IS NOT OPTIONAL, and it is here so no caller can forget it. Gherkin
    labels ("mb-001") are not database ids: ``_resolve_media_buy_id`` maps them through
    ``ctx["media_buy_labels"]`` to whatever the Given actually created. A When that passed
    the raw label reached the e2e action with an id no row has, and that action SEEDS the
    row it is asked about (:func:`tests.harness._mixins._seed_media_buy_for_delivery`) —
    so it fabricated a SECOND media buy at the literal id "mb-001" alongside the Given's
    real one, delivered for the fabricated one, and the Thens graded that. Same scenario,
    two media buys, and the one under test was not the one measured
    (salesagent-n78j0.1.4).
    """
    return ctx["env"].deliver_webhook(media_buy_id=_resolve_media_buy_id(ctx, mb_id))


@when(parsers.parse('the webhook scheduler fires for "{mb_id}"'))
def when_webhook_fires(ctx: dict, mb_id: str) -> None:
    """Webhook scheduler fires for a media buy."""
    try:
        ctx["webhook_result"] = _deliver_for_label(ctx, mb_id)
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
    try:
        result = _call_webhook_service(ctx, mb_id=mb_id, report_type=report_type)
        ctx["webhook_result"] = result
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the system delivers three consecutive webhook reports for "{mb_id}"'))
def when_deliver_three_reports(ctx: dict, mb_id: str) -> None:
    """Deliver three consecutive webhook reports."""
    ctx["webhook_reports"] = []
    for _ in range(3):
        try:
            result = _deliver_for_label(ctx, mb_id)
            ctx["webhook_reports"].append(result)
        except Exception as exc:
            ctx["error"] = exc
            break


@when("the system attempts to deliver a webhook report")
def when_attempt_webhook(ctx: dict) -> None:
    """System attempts webhook delivery."""
    env = ctx["env"]
    try:
        ctx["webhook_result"] = env.deliver_webhook()
    except Exception as exc:
        ctx["error"] = exc


@when("the system evaluates the circuit breaker state")
def when_evaluate_circuit_breaker(ctx: dict) -> None:
    """Evaluate circuit breaker state.

    Calls cb.can_attempt() directly to trigger timeout-based state transitions
    (OPEN → HALF_OPEN), then attempts delivery via call_send().
    """
    env = ctx["env"]
    endpoint_key = _circuit_breaker_endpoint_key(ctx)
    env.drive_breaker_transition(endpoint_key)
    try:
        env.call_send()
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse("the system delivers {n:d} successful probe reports"))
def when_deliver_probe_reports(ctx: dict, n: int) -> None:
    """Deliver n probe reports for real, so the CLOSED transition is production's.

    This used to call production's ``record_success()`` directly, n times. That
    was not a tautology — the Then still graded the breaker's threshold
    arithmetic, and mutating ``success_threshold`` reddens it — but it skipped
    the delivery layer this step's own text names, so the scenario could not see
    production forget to record a success at all. Deleting
    ``record_success()`` from ``_deliver_to_config`` left it green.

    The Given has already stood up a real origin returning 200 and left the
    breaker HALF_OPEN, where ``can_attempt()`` allows the probe through.

    Counts through ``env.delivery_attempts``, NOT ``env.origin.hits``.
    ``origin`` is the in-process local origin, which does not exist under
    e2e_rest -- the compose stack's webhook-capture service is the endpoint
    there, and ``__enter__`` deliberately never sets the attribute. Reading it
    directly raised ``AttributeError`` in env SETUP on the live stack, and
    because the env then failed to unbind the factory session, every following
    scenario on that xdist worker errored with "Factory TenantFactory session
    already bound" -- 5 real failures cascading into 443. ``delivery_attempts``
    is the realize-aware accessor that answers the same question on both, which
    is what it exists for.
    """
    env = ctx["env"]
    attempts_before = env.delivery_attempts

    for _ in range(n):
        assert env.call_send(), "a probe report against a 200 origin must be delivered"

    delivered = env.delivery_attempts - attempts_before
    assert delivered == n, f"the scenario says {n} reports were delivered; the endpoint saw {delivered}"
    # Nothing is left in ctx: the count this step establishes is graded HERE, by the
    # assertion above, and no Then reads it. A `ctx["probe_count"] = n` stood here and was
    # read by nothing on either side of this merge -- a claim that cannot be wrong
    # (test_architecture_bdd_ctx_keys.py).


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
    from tests.bdd.steps.generic.given_media_buy import harness_create_request_kwargs

    secret = ctx.get("webhook_secret", "")
    kwargs = harness_create_request_kwargs(ctx)
    kwargs["reporting_webhook"] = ReportingWebhookRequestFactory.payload(
        url=_DECLARED_WEBHOOK_URL,
        reporting_frequency="daily",
        authentication={"schemes": ["Bearer"], "credentials": secret},
    )
    # Dispatch the flat body (no typed construction) so a short credential reaches
    # the production transport boundary instead of being rejected in test code.
    dispatch_request(ctx, **kwargs)


@when(parsers.parse('the webhook scheduler evaluates "{mb_id}"'))
def when_webhook_evaluates(ctx: dict, mb_id: str) -> None:
    """Ask the system under test to deliver for *mb_id*, and let PRODUCTION decide.

    This step used to be test-local bookkeeping — it read ``ctx["webhook_config"]``
    and set ``ctx["webhook_skipped"]``, calling no production code on any transport.
    The Thens behind it ("the system should skip", "no delivery attempt should be
    made") were therefore true because NOTHING RAN, not because production declined:
    an assertion change to such a Then cannot mean anything (salesagent-n78j0.1.4).

    The evaluation IS a delivery request: production's own skip lives at
    ``WebhookDeliveryService._send_webhook_enhanced``, which looks up this
    principal's active ``PushNotificationConfig`` rows and returns False without a
    POST when there are none. Driving :meth:`deliver_webhook` is what puts that
    branch under the scenario — the same seam every other delivery When uses, so the
    step still names no transport.
    """
    # Recorded so the Then can pin that the verdict it grades belongs to the media buy
    # the scenario named, rather than to whatever the step happened to ask about.
    ctx["webhook_evaluated_media_buy"] = _resolve_media_buy_id(ctx, mb_id)
    try:
        ctx["webhook_result"] = _deliver_for_label(ctx, mb_id)
    except Exception as exc:
        ctx["error"] = exc


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
    """Shared: request delivery for a single media buy, recording the id it sent."""
    ctx["requested_media_buy_ids"] = [mb_id]
    dispatch_request(ctx, media_buy_ids=[mb_id])


@when(parsers.parse('the Buyer Agent requests delivery metrics for "{mb_id}"'))
@when(parsers.parse('the Buyer Agent queries delivery metrics for media buy "{mb_id}"'))
@when(parsers.re(r'the Buyer Agent requests delivery metrics for media_buy_ids \["(?P<mb_id>[^"]+)"\]$'))
def when_request_single_mb(ctx: dict, mb_id: str) -> None:
    """Request delivery metrics for a single media buy.

    Three spellings of one dispatch -- "requests ... for", "queries ... for media
    buy", and the quoted ``media_buy_ids [...]`` form. They were three functions
    with the same body, each also stashing a flag naming its own spelling
    (``query_variant``, ``id_format``) that no step read: the wire call is
    identical, so there was nothing for a Then to tell apart.
    """
    _request_single_mb(ctx, mb_id)


@when(
    parsers.re(
        r'the Buyer Agent requests delivery metrics for "(?P<mb_id>[^"]+)" '
        r"with attribution_window (?P<aw_json>\{.+\})"
    )
)
def when_request_with_attribution(ctx: dict, mb_id: str, aw_json: str) -> None:
    """Request with attribution window, recording what was sent for the echo Then.

    ``ctx["request_attribution"]`` is the REQUEST AS SENT, and it is written here
    because this is the only step that knows it. ``then_attribution_echo`` read that
    key with hardcoded fallbacks (``7`` / ``"days"``) and nothing wrote it, so it
    compared the response against constants that happened to match this scenario's
    Examples — a seller that ignored the buyer's window entirely and always answered
    7 days would have passed (salesagent-b9hi1.1).
    """
    aw = json.loads(aw_json)
    ctx["request_attribution"] = aw
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
    _seed_valid_account_if_named(ctx, value)
    _dispatch_partition(ctx, "account", value)


# NOTE: the partition status_filter step is identical to
# when_request_with_status_filter above (same regex + body); the single
# definition there serves both the alternative and partition scenarios.
@when(parsers.re(r'the Buyer Agent requests delivery metrics at status_filter boundary "(?P<boundary_value>[^"]+)"'))
def when_boundary_status_filter(ctx: dict, boundary_value: str) -> None:
    """Boundary test: status_filter value."""
    dispatch_request(ctx, status_filter=[boundary_value])


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
    """Boundary test: ownership — swap identity, same as the partition outline.

    This used to send the Gherkin label as a literal ``ownership=`` kwarg. That
    is not a request field, so FastMCP's TypeAdapter rejected it as unrecognized
    before _get_media_buy_delivery_impl ever ran — matching "invalid" for the
    mismatch row no matter what production does about cross-principal access
    (debt item B3). Ownership is decided by the caller's IDENTITY, so the
    boundary now routes through the same reconciled dispatcher as its partition
    twin.
    """
    _dispatch_ownership_partition(ctx, boundary_point)


# The two sampling When steps are deleted with the scenarios that bound them
# (2026-09-15). They dispatched `sampling_method`, which AdCP 3.1.1 declares nowhere,
# so nothing they sent could be graded against the pin. BR-UC-004 records the full
# reasoning where the outlines stood.


@when("the Buyer Agent queries delivery metrics for a non-existent media buy")
def when_query_nonexistent(ctx: dict) -> None:
    """Query delivery metrics for a non-existent media buy."""
    dispatch_request(ctx, media_buy_ids=["mb-nonexistent"])


@when(
    parsers.re(
        r'the Buyer Agent requests delivery metrics for "(?P<mb_id>[^"]+)" '
        r"without (?P<field>\w+)"
    )
)
def when_request_without_field(ctx: dict, mb_id: str, field: str) -> None:
    """Request without a specific optional field (attribution_window etc).

    Also owns the "without attribution_window" sentence, which used to have its
    own ``parsers.parse`` step shadowing this regex with an identical body plus
    an ``omitted_fields`` list no step read.

    Omitting a field is only meaningful if the request HAS one to omit, so the
    named field must be real: "without frobnicator" would otherwise dispatch a
    perfectly ordinary request and grade whatever came back.
    """
    from src.core.schemas.delivery import GetMediaBuyDeliveryRequest

    assert field in GetMediaBuyDeliveryRequest.model_fields, (
        f"Step omits {field!r} from the delivery request, but the request carries "
        f"no such field: {sorted(GetMediaBuyDeliveryRequest.model_fields)}"
    )
    assert field not in (ctx.get("request_kwargs") or {}), (
        f"Step claims the request goes out without {field!r}, but a prior step put it there."
    )
    _request_single_mb(ctx, mb_id)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — delivery-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.re(r'the response should include delivery data for "(?P<mb_id1>[^"]+)" and "(?P<mb_id2>[^"]+)"'))
def then_includes_delivery_data_both(ctx: dict, mb_id1: str, mb_id2: str) -> None:
    """Assert response includes delivery data for both media buys."""
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id1 in mb_ids, f"Expected delivery data for '{mb_id1}', got: {mb_ids}"
    assert mb_id2 in mb_ids, f"Expected delivery data for '{mb_id2}', got: {mb_ids}"


@then(parsers.re(r'the response should include delivery data for "(?P<mb_id>[^"]+)"$'))
def then_includes_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert response includes delivery data for the given media buy."""
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_id in mb_ids, f"Expected delivery data for '{mb_id}', got: {mb_ids}"


@then(parsers.parse('the response should include delivery data for "{mb_id}" only'))
def then_includes_delivery_data_only(ctx: dict, mb_id: str) -> None:
    """Assert response includes delivery data for ONLY the given media buy."""
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    mb_ids = [d.media_buy_id for d in deliveries]
    assert mb_ids == [mb_id], f"Expected only '{mb_id}', got: {mb_ids}"


@then(parsers.parse('the response should NOT include delivery data for "{mb_id}"'))
def then_excludes_delivery_data(ctx: dict, mb_id: str) -> None:
    """Assert the response arrived and carries no delivery row for *mb_id*.

    ``require_payload``, not ``payload_or_none``: every scenario binding this
    sentence asserts ``the response is compliant with the get_media_buy_delivery
    spec`` first, so a dispatch that produced no payload is a failed request, not
    an omission. The previous ``if resp is None: return`` graded that failure as
    a pass — silent omission and total failure are the two outcomes this step
    exists to tell apart (BR-RULE-030 INV-5: non-owned media buys are omitted
    from a SUCCESSFUL partial result, never turned into a rejection).

    One definition, both spellings: ``parsers.parse`` matches case-insensitively,
    so this binds the "should NOT" sentence of
    @T-UC-004-identify-mixed-ownership and the "should not" sentence of
    @T-UC-004-status-filter-multi alike. The verbatim second copy that used to
    sit here (``then_no_delivery_data``) competed for both of them, and which one
    ran was decided by plugin registration order.
    """
    resp = require_payload(ctx)
    mb_ids = [d.media_buy_id for d in (getattr(resp, "media_buy_deliveries", None) or [])]
    assert mb_id not in mb_ids, f"Expected no delivery data for '{mb_id}', got: {mb_ids}"


@then("the response should have an empty media_buy_deliveries array")
def then_empty_deliveries(ctx: dict) -> None:
    """Assert response has empty media_buy_deliveries."""
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert len(deliveries) == 0, f"Expected empty deliveries, got {len(deliveries)}"


@then("the delivery data should include impressions, spend, and clicks")
def then_has_metrics(ctx: dict) -> None:
    """Assert delivery totals carry internally-consistent metric values.

    Asserts type correctness and cross-field consistency rather than
    hardcoded mock-adapter values.
    """
    resp = require_payload(ctx)
    d = resp.media_buy_deliveries[0]
    totals = d.totals
    # Type correctness: impressions and spend must be numeric
    assert isinstance(totals.impressions, (int, float)), (
        f"impressions must be numeric, got {type(totals.impressions).__name__}"
    )
    assert isinstance(totals.spend, (int, float)), f"spend must be numeric, got {type(totals.spend).__name__}"
    # clicks is present (may be None or numeric per schema)
    assert totals.clicks is None or isinstance(totals.clicks, (int, float)), (
        f"clicks must be numeric or None, got {type(totals.clicks).__name__}"
    )
    # Cross-field consistency: nonzero spend implies nonzero impressions
    if totals.spend > 0:
        assert totals.impressions > 0, f"Nonzero spend ({totals.spend}) with zero impressions"
    # Aggregation: package-level impressions must sum to totals
    packages = d.by_package
    pkg_impressions = sum(p.impressions for p in packages)
    assert totals.impressions == pkg_impressions, (
        f"Totals impressions ({totals.impressions}) != sum of package impressions ({pkg_impressions})"
    )


@then("the delivery data should include package-level breakdowns")
def then_has_packages(ctx: dict) -> None:
    """Assert delivery data includes package-level breakdowns with distinct IDs.

    Verifies structural correctness: packages exist, have distinct IDs,
    and their impressions roll up to the media-buy totals.
    """
    resp = require_payload(ctx)
    d = resp.media_buy_deliveries[0]
    packages = d.by_package
    assert isinstance(packages, list), f"by_package must be a list, got {type(packages).__name__}"
    assert packages, "by_package list is empty"
    # Every package must have a non-empty package_id
    ids = [p.package_id for p in packages]
    for pid in ids:
        assert isinstance(pid, str) and pid, f"package_id must be a non-empty string, got {pid!r}"
    # Package IDs must be unique
    assert len(ids) == len(set(ids)), f"Duplicate package_ids: {ids}"
    # Package impressions must sum to media-buy totals (rollup invariant)
    pkg_impressions = sum(p.impressions for p in packages)
    assert pkg_impressions == d.totals.impressions, (
        f"Package impressions ({pkg_impressions}) != media-buy total ({d.totals.impressions})"
    )


@then("the response should include the reporting period start and end dates")
def then_has_reporting_period(ctx: dict) -> None:
    """Assert response includes reporting period.

    reporting_period is on the response object (GetMediaBuyDeliveryResponse),
    not on individual MediaBuyDeliveryData entries.
    """
    resp = require_payload(ctx)
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    assert period.start is not None, "Reporting period start is None"
    assert period.end is not None, "Reporting period end is None"


@then(parsers.parse('the response should include the media buy status "{status}"'))
def then_has_mb_status(ctx: dict, status: str) -> None:
    """Assert response includes the expected media buy status."""
    resp = require_payload(ctx)
    d = resp.media_buy_deliveries[0]
    assert d.status == status, f"Expected status '{status}', got '{d.status}'"


@then(parsers.parse('buyers MAY treat the legacy alias "{legacy_status}" as equivalent to "{status}"'))
def then_legacy_status_alias(ctx: dict, legacy_status: str, status: str) -> None:
    """Buyer-side compatibility note, not a seller behavior: confirms the
    response actually carries the NEW v3.1 canonical status name (the
    precondition for "buyers who used to check for the legacy name MAY treat
    the new one as equivalent" to be meaningful at all) rather than silently
    also emitting the retired legacy value.
    """
    resp = require_payload(ctx)
    d = resp.media_buy_deliveries[0]
    assert d.status == status, f"Expected canonical status {status!r}, got {d.status!r}"
    assert d.status != legacy_status, (
        f"response carries the retired legacy status {legacy_status!r} instead of canonical {status!r}"
    )


@then("the response should include aggregated totals across both media buys")
def then_has_aggregated_totals(ctx: dict) -> None:
    """Assert aggregated totals equal the sum of per-delivery totals."""
    resp = require_payload(ctx)
    agg = resp.aggregated_totals
    deliveries = resp.media_buy_deliveries
    # Aggregated impressions must equal the sum of individual delivery impressions
    individual_impressions = sum(d.totals.impressions for d in deliveries)
    assert agg.impressions == individual_impressions, (
        f"aggregated_totals.impressions ({agg.impressions}) != sum of individual impressions ({individual_impressions})"
    )
    # Aggregated spend must equal the sum of individual delivery spend
    individual_spend = sum(d.totals.spend for d in deliveries)
    assert agg.spend == individual_spend, (
        f"aggregated_totals.spend ({agg.spend}) != sum of individual spend ({individual_spend})"
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
    resp = require_payload(ctx)
    agg = resp.aggregated_totals
    roas = getattr(agg, "roas", None)
    assert roas is not None, "aggregated_totals.roas is missing — production does not compute roas"
    conversion_values = [getattr(d.totals, "conversion_value", None) for d in resp.media_buy_deliveries]
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
    resp = require_payload(ctx)
    agg = resp.aggregated_totals
    cpa = getattr(agg, "cost_per_acquisition", None)
    assert cpa is not None, (
        "aggregated_totals.cost_per_acquisition is missing — production does not compute cost_per_acquisition"
    )
    conversions = [getattr(d.totals, "conversions", None) for d in resp.media_buy_deliveries]
    assert all(c is not None for c in conversions), (
        f"per-delivery totals.conversions missing (cpa input must be reported per buy): {conversions}"
    )
    assert cpa == pytest.approx(25.0), (
        f"aggregated_totals.cost_per_acquisition ({cpa}) != 25.0 (Given seeds 2 buys x spend 250.0 / 2 x conversions 10.0)"
    )


@then(parsers.parse('the aggregated_totals should include "media_buy_count" equal to {count:d}'))
def then_aggregated_media_buy_count(ctx: dict, count: int) -> None:
    """Assert aggregated_totals.media_buy_count matches the scenario's buy count."""
    resp = require_payload(ctx)
    agg = resp.aggregated_totals
    assert agg.media_buy_count == count, (
        f"aggregated_totals.media_buy_count ({agg.media_buy_count}) != expected ({count})"
    )


@then("the aggregated impressions should equal the sum of individual impressions")
def then_aggregated_impressions(ctx: dict) -> None:
    """Assert aggregated impressions equal sum of individual values."""
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    individual_sum = sum(getattr(getattr(d, "totals", None), "impressions", 0.0) for d in deliveries)
    agg = getattr(resp, "aggregated_totals", None)
    assert agg is not None, "Missing aggregated_totals"
    agg_impressions = getattr(agg, "impressions", 0.0)
    assert agg_impressions == individual_sum, f"Aggregated impressions {agg_impressions} != sum {individual_sum}"


@then("the aggregated spend should equal the sum of individual spend")
def then_aggregated_spend(ctx: dict) -> None:
    """Assert aggregated spend equals sum of individual values."""
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    individual_sum = sum(getattr(getattr(d, "totals", None), "spend", 0.0) for d in deliveries)
    agg = getattr(resp, "aggregated_totals", None)
    assert agg is not None, "Missing aggregated_totals"
    agg_spend = getattr(agg, "spend", 0.0)
    assert agg_spend == individual_sum, f"Aggregated spend {agg_spend} != sum {individual_sum}"


@then(parsers.parse('the response should not include an error for "{mb_id}"'))
def then_no_error_for_mb(ctx: dict, mb_id: str) -> None:
    """No entry in the response's ``errors[]`` names *mb_id*.

    The buyer-facing side of the advisory contract its twin
    (``the response errors include code ... for media buy ...``) grades: the delivery
    verb answers about EVERY id it was asked about, naming an unresolved one in
    ``errors[]`` — "Task-specific errors and warnings (e.g., missing delivery data,
    reporting platform issues)", ``get-media-buy-delivery-response.json`` — and saying
    nothing there about the ones it delivered.

    Read BY VALUE off the wire array (through the sanctioned reader), not off
    ``ctx["error"]``: the sibling step promotes the entry it matched into ``ctx``, so a
    ctx-presence check would report a failure for THIS id whenever another id in the
    same response had one.
    """
    named = [err for err in wire_advisory_errors(ctx) if (err.get("details") or {}).get("media_buy_id") == mb_id]
    assert not named, f"Response errors[] names media buy {mb_id!r}: {named}"


@then(parsers.re(r'the response errors include code "(?P<code>[^"]+)" for media buy "(?P<mb_id>[^"]+)"$'))
def then_response_errors_include_for_mb(ctx: dict, code: str, mb_id: str) -> None:
    """A TASK-LEVEL error in a SUCCESSFUL delivery response names *code* for *mb_id*.

    The delivery verb reports a per-buy failure INSIDE a completed response rather than
    refusing the call: get-media-buy-delivery-response.json declares ``errors[]`` as
    "Task-specific errors and warnings (e.g., missing delivery data, reporting platform
    issues)", and an unavailable ad server is a reporting platform issue. A request for
    five buys where one platform is down still answers for the other four.

    So this is NOT ``the response contains error code`` with a different spelling. That
    primitive grades the ENVELOPE — ``adcp_error`` — and a response like this one
    deliberately has none. Asserting envelope failure here is what the scenario used to
    do, and it only ever passed because ``the operation should fail`` promoted this very
    object into ``ctx["error"]`` and ``the error code should be`` then read it back off
    the reconstruction rather than off the wire.

    The item is identified by ``details.media_buy_id``, which is where the delivery verb
    puts it -- the buy has no entry in ``media_buy_deliveries`` at all when its platform
    is unreachable, so a per-delivery lookup (``the response should not include an error
    for``) would find nothing and pass vacuously.

    Sets ``ctx["error"]`` to the MATCHED error so the per-error Thens that follow grade
    that object. That is the same promotion ``then_operation_fails`` performs, made
    explicit and narrowed to the error this step actually found.
    """
    resp = payload_or_none(ctx)
    assert resp is not None, (
        f"No response payload to read errors[] from -- the dispatch produced nothing. ctx: {list(ctx)}"
    )
    errors = list(getattr(resp, "errors", None) or [])
    assert errors, (
        f"Response carries an EMPTY errors[], so it reports no task-level failure at all. "
        f"Expected {code!r} for media buy {mb_id!r}."
    )
    for err in errors:
        details = getattr(err, "details", None) or {}
        if getattr(err, "code", None) == code and details.get("media_buy_id") == mb_id:
            ctx["error"] = err
            return
    raise AssertionError(
        f"No errors[] entry with code {code!r} for media buy {mb_id!r}. Carried: "
        f"{[(getattr(e, 'code', None), (getattr(e, 'details', None) or {}).get('media_buy_id')) for e in errors]}"
    )


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
    actually exercised. ``status`` is normalized off the enum's ``.value`` since
    MediaBuyDeliveryStatus is an Enum (not a str-enum), so identity-compares
    against the plain wire string would otherwise fail.
    """
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", None) or []
    assert deliveries, (
        f"Filter '{status}' returned no media buys — the scenario must seed a buy "
        f"for this status or the assertion passes vacuously."
    )
    for d in deliveries:
        raw = getattr(d, "status", None)
        actual = getattr(raw, "value", raw)  # Enum -> wire string; str passthrough
        assert actual == status, f"Expected status '{status}', got '{actual}' for {d.media_buy_id}"


# ── Reporting period assertions ────────────────────────────────────


@then(parsers.parse('the response reporting_period start should be "{date}"'))
def then_period_start(ctx: dict, date: str) -> None:
    """Assert reporting period start date (response-level, not per-delivery)."""
    resp = require_payload(ctx)
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.start)[:10]
    assert actual == date, f"Expected period start '{date}', got '{actual}'"


@then(parsers.parse('the response reporting_period end should be "{date}"'))
def then_period_end(ctx: dict, date: str) -> None:
    """Assert reporting period end date (response-level, not per-delivery)."""
    resp = require_payload(ctx)
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.end)[:10]
    assert actual == date, f"Expected period end '{date}', got '{actual}'"


@then("the response reporting_period end should be today's date")
def then_period_end_today(ctx: dict) -> None:
    """Assert reporting period end is today (response-level)."""
    from datetime import UTC, datetime

    resp = require_payload(ctx)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    period = getattr(resp, "reporting_period", None)
    assert period is not None, "Response missing reporting_period"
    actual = str(period.end)[:10]
    assert actual == today, f"Expected period end '{today}', got '{actual}'"


# ── Webhook Then steps ─────────────────────────────────────────────


@then("the system should POST a delivery report to the configured webhook URL")
def then_webhook_post(ctx: dict) -> None:
    """Assert the configured endpoint actually received a POST.

    The configured endpoint IS the env's local origin, and the origin only
    records requests that genuinely reached it — so "went to the configured URL"
    is not a separate assertion here, it is the precondition for there being
    anything to observe at all.
    """
    env = ctx["env"]
    deliveries = _webhook_deliveries(ctx)
    assert deliveries, f"Expected webhook POST to {_webhook_url(env)} but none was made"
    assert deliveries[-1].method == "POST", f"Expected the delivery report to be POSTed, got {deliveries[-1].method}"


@then(parsers.parse('the payload should include delivery metrics for "{mb_id}"'))
def then_webhook_payload_has_metrics(ctx: dict, mb_id: str) -> None:
    """Assert webhook payload includes delivery metrics for the requested buy.

    Verifies ID mapping and that the payload carries concrete numeric metric
    values (impressions, spend) — not just structural presence.  The
    reporting_period check is left to its dedicated Then step.
    """
    result = _get_last_webhook_result(ctx)
    real_id = _resolve_media_buy_id(ctx, mb_id)
    entries = _delivery_entries(result)

    # ID mapping, at the nesting media-buy-delivery-webhook-result.json declares it:
    # media_buy_id is a property of a media_buy_deliveries[] ENTRY, never of the report.
    entry = next((e for e in entries if e.get("media_buy_id") == real_id), None)
    assert entry is not None, (
        f"no media_buy_deliveries[] entry for {real_id!r}: got {[e.get('media_buy_id') for e in entries]!r}"
    )

    # Metrics live in the entry's `totals`. There is no fallback chain: the pin gives one
    # location, and an "or wherever else it might be" search passes against a shape the
    # buyer's own schema validation would reject.
    totals = entry.get("totals")
    assert isinstance(totals, dict), f"delivery entry for {real_id!r} carries no totals object: got {totals!r}"
    impressions = totals.get("impressions")
    spend = totals.get("spend")

    assert isinstance(impressions, (int, float)) and impressions > 0, (
        f"Expected positive numeric totals.impressions for {real_id!r}, got {impressions!r}"
    )
    assert isinstance(spend, (int, float)) and spend > 0, (
        f"Expected positive numeric totals.spend for {real_id!r}, got {spend!r}"
    )


@then("the payload should include the reporting_period")
def then_webhook_payload_has_period(ctx: dict) -> None:
    """Assert webhook payload includes a reporting_period with start and end."""
    payload = _get_last_webhook_result(ctx)
    period = payload.get("reporting_period")
    assert period is not None, f"Webhook payload missing 'reporting_period': {list(payload.keys())}"
    assert period.get("start") is not None and period.get("end") is not None, (
        f"reporting_period must have non-None start and end: {period}"
    )


@then(parsers.parse('the payload notification_type should be "{ntype}"'))
def then_notification_type(ctx: dict, ntype: str) -> None:
    """Assert notification type matches expected value."""
    payload = _get_last_webhook_result(ctx)
    assert payload.get("notification_type") == ntype, (
        f"Expected notification_type={ntype!r}, got {payload.get('notification_type')!r}"
    )


@then(parsers.re(r"the payload (?P<next_expected>.+) include next_expected_at"))
def then_next_expected(ctx: dict, next_expected: str) -> None:
    """Assert next_expected_at is present or absent based on 'should'/'should not'."""
    payload = _get_last_webhook_result(ctx)
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
    """Assert sequence numbers are strictly increasing across consecutive deliveries.

    A MULTI-call read: it needs the whole stream the endpoint received, not just the
    last one. [5/5 routed — salesagent-n78j0.1.4]
    """
    deliveries = _webhook_deliveries(ctx)
    assert len(deliveries) >= 2, f"Expected at least 2 webhook deliveries for sequence check, got {len(deliveries)}"
    # Read at the REPORT layer: ``sequence_number`` is a field of ``result``, not of the
    # envelope, so an envelope-level read is None for every delivery and grades nothing.
    seq_nums = [_webhook_result(_sent_payload(delivery)).get("sequence_number") for delivery in deliveries]
    for i in range(1, len(seq_nums)):
        assert seq_nums[i] is not None, f"POST call {i} payload missing sequence_number"
        assert seq_nums[i] > seq_nums[i - 1], (
            f"sequence_number not ascending at index {i}: {seq_nums[i - 1]} -> {seq_nums[i]}"
        )


@then("the first sequence_number should be >= 1")
def then_first_sequence(ctx: dict) -> None:
    """Assert the FIRST delivery of the stream has sequence_number >= 1.

    A MULTI-call read for the same reason as its sibling: "first" is only meaningful
    against the whole stream. [5/5 routed — salesagent-n78j0.1.4]
    """
    deliveries = _webhook_deliveries(ctx)
    assert deliveries, "No webhook deliveries were made"
    first_payload = _webhook_result(_sent_payload(deliveries[0]))
    seq = first_payload.get("sequence_number")
    assert seq is not None, f"First webhook delivery payload missing sequence_number: {list(first_payload.keys())}"
    assert seq >= 1, f"Expected sequence_number >= 1, got {seq}"


@then('the payload should not include "aggregated_totals" field')
def then_no_aggregated_in_payload(ctx: dict) -> None:
    """Assert the delivery REPORT excludes aggregated_totals (polling-only field).

    Graded on ``result``, not on the envelope. The envelope's field set is fixed by
    ``core/mcp-webhook-payload.json``, so ``aggregated_totals`` is structurally impossible
    there and an assertion against the POST body could not fail whatever production did --
    which is exactly how this read while the scenario was ledgered.

    The result layer is where the field could actually appear:
    ``media-buy-delivery-webhook-result.json`` sets ``additionalProperties: true`` and does
    not declare it, so an emitted value is schema-VALID and no schema check can catch it.
    The obligation is prose only -- ``L3/webhooks.mdx`` :253, "API-only for
    get_media_buy_delivery responses and must not be emitted in reporting webhook result
    payloads" -- which is why it needs a named-field assertion here.
    """
    result = _get_last_webhook_result(ctx)
    assert "aggregated_totals" not in result, (
        f"the delivery report carries 'aggregated_totals', which L3/webhooks.mdx :253 forbids "
        f"in a reporting webhook result: got keys {sorted(result)}"
    )


@then("the system should retry up to 3 times")
def then_retry_3_times(ctx: dict) -> None:
    """Assert retry count: at most 4 POST calls (1 original + 3 retries).

    Also verifies that multiple attempts were made (at least 2), confirming
    the retry mechanism was triggered, not just that it stayed under the cap.
    """
    env = ctx["env"]
    call_count = env.delivery_attempts
    assert call_count >= 2, (
        f"Expected at least 2 POST calls (original + retry), got {call_count} — retry mechanism may not have triggered"
    )
    assert call_count <= 4, f"Expected at most 4 calls (1+3 retries), got {call_count}"


@then("retries should use exponential backoff (1s, 2s, 4s + jitter)")
def then_exponential_backoff(ctx: dict) -> None:
    """Assert sleep durations are 1s/2s/4s and that jitter is actually drawn.

    Production does 3 total attempts (1 original + 2 retries), sleeping between
    each retry, so we expect exactly 2 sleeps graded against the [1s, 2s] prefix.

    The env pins ``random.uniform`` for determinism, so the jitter half of this
    step is graded by the draw itself: production must ask for a jitter value in
    [0, 1) per retry. Deleting ``+ random.uniform(0, 1)`` from the delay makes
    this step red, which is the whole point of grading the step text.

    Read through ``env.assert_retry_backoff_schedule()``, the sibling of
    ``assert_no_retry_schedule_entered``, because the durations come off the
    RUNNER's patched clock: under e2e_rest the sender is the live server (the
    e2e realization of ``deliver_webhook`` drives the deployment's own delivery),
    whose waits happen in another process. The env declares that, rather than
    this step reaching into the patched clock itself, so the one unobservable
    assertion is named where it lives and the scenario's wire-observable Thens
    above still grade the live server.
    """
    ctx["env"].assert_retry_backoff_schedule(grade_jitter_draws=True)


@then("the system should retry up to 3 times with exponential backoff")
def then_retry_with_backoff(ctx: dict) -> None:
    """Assert at most 4 POST calls (1 original + 3 retries) on the 1s/2s/4s schedule.

    Production does 3 total attempts with 2 sleeps between them. This step text
    claims no jitter, so jitter is not graded here — the sibling 5xx scenario
    that does claim it grades it.
    """
    env = ctx["env"]
    assert env.delivery_attempts <= 4, f"Expected at most 4 calls (1 + 3 retries), got {env.delivery_attempts}"
    env.assert_retry_backoff_schedule()


@then("the system should not retry the delivery")
def then_no_retry(ctx: dict) -> None:
    """Assert no retry was attempted."""
    env = ctx["env"]
    assert env.delivery_attempts <= 1, (
        f"Expected no retries, but the endpoint received {env.delivery_attempts} requests"
    )


@then("the system should log the authentication rejection")
def then_log_auth_rejection(ctx: dict) -> None:
    """Assert the system logged the authentication rejection.

    Two facts, both wire- or database-observable on EVERY transport including
    e2e_rest: the delivery failed, and the sender RECORDED the rejection with the
    status code it received.

    The record is read out of ``webhook_delivery_log`` rather than off this
    process's logger. Under e2e_rest the sender is the live server, so a log line
    is emitted inside the container and never reaches the runner's handler --
    which is why the log form needed an e2e escape hatch. The row does not: it is
    written by whichever process delivered, into the database both share.
    """
    env = ctx["env"]
    # 1. Confirm delivery failed (precondition)
    success = _extract_webhook_success(ctx)
    assert success is False, f"Expected webhook delivery to fail on auth rejection, got success={success!r}"

    # 2. The sender attributed the rejection, with the status the receiver sent.
    env.assert_rejection_logged(http_status=401)


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
    """Assert the refusal happened BEFORE any connection, not after a failed one.

    The two outcome assertions are wire-observable on every transport, e2e_rest
    included, and are checked unconditionally first:

    * ``delivery_attempts`` is the hit count of the REAL local origin this env
      runs (``LocalOriginMixin``), and this scenario deliberately points the
      config somewhere the origin is not (the cloud-metadata address). It
      therefore reads 0 whether production refused the destination or dialled it,
      so on its own it states only "the origin was not the destination".
    * ``success is False`` is the same on both sides of the gate too:
      ``OutboundRequestBlocked`` and a dead socket are both ``OutboundError`` and
      take the identical ``record_failure(); return False`` branch in
      ``WebhookDeliveryService._deliver_with_backoff``.

    The discriminator is the retry SCHEDULE, checked last via
    ``env.assert_no_retry_schedule_entered()``: process-local
    (``env.mock["sleep"]``), so it is declared e2e-unsupported there (see
    ``CircuitBreakerMixin`` in ``tests/harness/_mixins.py``) rather than
    silently dropping the whole scenario from e2e_rest.
    """
    env = ctx["env"]
    success = _extract_webhook_success(ctx)
    assert success is False, f"Expected SSRF-skipped delivery to return False, got success={success!r}"
    assert env.delivery_attempts == 0, (
        f"Expected no HTTP POST after SSRF rejection, the origin received {env.delivery_attempts} request(s)"
    )
    env.assert_no_retry_schedule_entered()


@then("the circuit breaker should record a failure")
def then_circuit_breaker_recorded_failure(ctx: dict) -> None:
    """Assert the send-time SSRF path called circuit_breaker.record_failure().

    Read through the production public API (``get_circuit_breaker_state``, via
    the env's ``breaker_snapshot``), never off service internals.

    Declared e2e-unsupported by
    ``CircuitBreakerMixin.assert_circuit_breaker_failure_recorded`` for a
    TOPOLOGY reason that the public read does not change: under e2e_rest
    ``get_service()`` builds a fresh in-process service that the live server's
    deliveries never touch, so there is no breaker state to observe from here at
    all.
    """
    env = ctx["env"]
    endpoint_key = _circuit_breaker_endpoint_key(ctx)
    env.assert_circuit_breaker_failure_recorded(endpoint_key)


@then("subsequent scheduled deliveries should be suppressed")
def then_deliveries_suppressed(ctx: dict) -> None:
    """Assert scheduled deliveries are suppressed while the circuit breaker is open.

    Rather than re-checking the breaker state (already verified by the preceding
    step), this asserts the observable suppression: record the current POST call
    count, attempt a delivery, and verify no new POST was dispatched.
    """
    env = ctx["env"]
    calls_before = env.delivery_attempts

    # Attempt a delivery while breaker is open — it should be suppressed
    result = env.call_send()
    assert result is False, f"Expected delivery to be suppressed (return False) while CB is open, got {result!r}"
    assert env.delivery_attempts == calls_before, (
        f"Expected no new POST calls while CB is open (suppressed), "
        f"but the endpoint went from {calls_before} to {env.delivery_attempts} requests"
    )


@then(parsers.parse('the circuit breaker should transition to "{state}"'))
def then_circuit_transition(ctx: dict, state: str) -> None:
    """Assert circuit breaker transitioned to the expected state."""
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual.lower() == state.lower(), f"Expected CB transition to '{state.lower()}', got '{actual}'"


@then("the system should attempt a single probe delivery")
def then_single_probe(ctx: dict) -> None:
    """Assert exactly one probe delivery REACHED THE ENDPOINT in half-open state.

    "A probe delivery was attempted" is a claim about a DELIVERY, so it is graded
    on the endpoint, the same way ``then_deliveries_resume`` below grades its own.

    What stood here was a three-way branch whose last two branches could not run. It
    looked for ``env.mock["httpx_post"]`` or ``env.mock["webhook_post"]``; a
    ``CircuitBreakerEnv`` holds neither, because it delivers over real HTTP to a
    real origin rather than through a POST mock. So the second branch was dead, and
    the third branch — a ``pytest.xfail("HARNESS GAP")`` guarded by
    ``ctx["cb_can_attempt"]``, a key no step in this module writes — was dead
    twice over. A dormant xfail on a branch that cannot execute grades nothing
    while reading, in the report, exactly like a scenario that does.

    ``env.delivery_attempts`` is the realize-aware accessor, so the count is a
    live readback of whichever endpoint this transport actually uses.

    The probe is made HERE, not read from a prior step, because this scenario's
    When is "the system evaluates the circuit breaker state" — an evaluation, not
    a delivery. Grading a delivery claim therefore means performing one, exactly
    as ``then_deliveries_resume`` below does for its own claim. The preceding
    Then has already asserted the breaker reached HALF_OPEN.
    """
    env = ctx["env"]

    attempts_before = env.delivery_attempts
    admitted = env.call_send()

    delivered = env.delivery_attempts - attempts_before
    assert delivered == 1, (
        f"a half-open breaker admits exactly ONE probe; the endpoint saw {delivered}. "
        "Zero means the breaker is still suppressing and never left OPEN in practice, "
        "whatever it reports about its own state; more than one means it is not gating."
    )
    assert admitted is True, (
        "the probe reached the endpoint but the sender reported failure — a probe that "
        "arrives and is then recorded as a failure keeps the breaker open forever"
    )


@then("normal scheduled deliveries should resume")
def then_deliveries_resume(ctx: dict) -> None:
    """Assert normal scheduled deliveries RESUME — by making one happen.

    "Deliveries resume" is a claim about DELIVERIES, so it is graded on a
    delivery: a report is sent and the endpoint must actually receive it. What
    stood here before asserted ``cb.can_attempt() is True`` on the private
    breaker object — a gate's internal opinion of itself, which stays true even
    when nothing can get through, and which is unfalsifiable across any process
    boundary.

    This is gradeable because the scenario now registers a webhook (the
    ``active reporting_webhook`` Given). Without it ``_send_webhook_enhanced``
    finds no PushNotificationConfig and returns False before the breaker is ever
    consulted, so every step in the scenario could only ever grade test doubles.

    The CLOSED state is still asserted, because the scenario text names it — but
    it is read through the production public API (``breaker_snapshot``) rather
    than off the private dict, and it is now the SECONDARY claim. An attempt that
    reached the origin is the primary one.
    """
    from src.services.webhook_delivery_service import CircuitState

    env = ctx["env"]

    # No arrange here, deliberately: the scenario's own Given ("the webhook
    # endpoint has recovered and returns 200") owns the endpoint. A Then that
    # forced the endpoint healthy would grade a condition it created, and would
    # silently mask a scenario that arranged a failing endpoint on purpose.
    attempts_before = env.delivery_attempts
    delivered = env.call_send()

    assert delivered is True, (
        "a delivery attempted after the breaker closed did not succeed — deliveries have not resumed"
    )
    assert env.delivery_attempts == attempts_before + 1, (
        f"the endpoint received {env.delivery_attempts - attempts_before} request(s) for one resumed "
        "delivery — the breaker is still gating traffic, whatever its state says"
    )

    state, failure_count = env.breaker_snapshot()
    assert state == CircuitState.CLOSED, (
        f"Expected circuit breaker in CLOSED state for resumed deliveries, got {state} — a HALF_OPEN "
        "breaker lets a single probe through, which is not resumed scheduled delivery"
    )
    # NOT claimed as an "effect of the probes": this scenario seeds a FRESH
    # breaker at HALF_OPEN, which is born with failure_count 0 and never records
    # a failure, so this cannot currently fail. It is kept as a pin — successful
    # probes must not ADD failures — and it is deliberately the weakest of the
    # four assertions here, not the grade.
    assert failure_count == 0, (
        f"the breaker carries {failure_count} recorded failure(s) after successful recovery "
        "probes — successful probes must not add to the failure tally"
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
    """Assert the breaker is CLOSED *and* carries no recorded failure.

    "Healthy" is two facts, and the state alone is the weaker one: the default
    failure_threshold is 5, so a delivery that succeeded but was ALSO counted as
    a failure still reports CLOSED. The failure count is what says the retried
    delivery was recorded as the single success it was.

    Both reads go through the production public API
    (``WebhookDeliveryService.get_circuit_breaker_state``). Neither can tell "no
    breaker exists" from "a breaker with zero failures" — stated at
    ``breaker_snapshot`` and unchanged here — so this does NOT grade that a
    success was recorded at all; that half is prebid/salesagent#2060's, and it
    needs a wire surface (``reporting_delayed``) this scenario does not touch.
    """
    env = ctx["env"]
    actual = env.get_breaker_state()
    assert actual == "closed", f"Expected CB to remain 'closed' (healthy), got '{actual}'"
    _state, failure_count = env.breaker_snapshot()
    assert failure_count == 0, (
        f"Expected the successful (retried) delivery to leave no recorded failure, got "
        f"failure_count={failure_count} — a delivery that succeeded is being counted against the endpoint"
    )


@then("the configuration should be rejected")
def then_config_rejected(ctx: dict) -> None:
    """Assert production rejected the webhook config on the wire (INVALID_REQUEST).

    The short credential is rejected by production's Pydantic boundary
    (Authentication.credentials MinLen=32) — assert the real two-layer AdCP
    wire envelope, not a reconstructed/hand-built exception.

    Graded STRUCTURALLY on the pin's own channel: `issues[].keyword` carries the
    JSON Schema keyword that rejected the payload, never a substring of the
    buyer-facing sentence, which is derived from the code through CODE_TABLE.

    This read `details.validation_errors[].type` and pydantic's own error token
    (`string_too_short`). The wire now carries `issues[].keyword` = `minLength`,
    which is what core/error.json asks for -- "drawn from the JSON Schema
    vocabulary ... Matches the keyword names emitted by JSON Schema validators".
    """
    # INVALID_REQUEST, and this step's OWN evidence is why: it asserts
    # issues[].keyword == "minLength", a JSON Schema keyword. A rejection carrying a JSON
    # Schema keyword is by definition a "violates schema constraints" rejection, which
    # 3.1/enums/error-code.json assigns to INVALID_REQUEST. VALIDATION_ERROR is for
    # business rules "beyond schema validation" -- there is no such rule here, only
    # Authentication.credentials MinLen=32.
    ctx["result"].assert_wire_error("INVALID_REQUEST", recovery="correctable", issues=[{"keyword": "minLength"}])


@then("the error should indicate minimum credential length is 32 characters")
def then_error_min_credential_length(ctx: dict) -> None:
    """Assert the wire envelope names the 32-character minimum on the credentials field.

    The boundary VALUE used to live ONLY inside pydantic's authored sentence
    ("String should have at least 32 characters"), because the old projection kept
    loc/msg/type and dropped pydantic's ``ctx``. So this step had to read prose for
    a number.

    It is structural now: ``issues[].keyword_value`` carries the constraint the
    keyword refers to -- in JSON Schema terms, the keyword's value in the schema.
    Pinned to the credentials pointer specifically, as before.
    """
    # Same code as the rejection step above, for the same reason: the constraint that
    # failed is MinLen=32, reported on the wire as issues[].keyword = minLength, so it is a
    # schema-constraint rejection -> INVALID_REQUEST.
    issues = ctx["result"].wire_error_issues("INVALID_REQUEST", recovery="correctable")
    credentials_entry = next(
        (i for i in issues if "credentials" in str(i.get("pointer", ""))),
        None,
    )
    assert credentials_entry is not None, f"no issue names the credentials field; issues were {issues!r}"
    assert credentials_entry.get("keyword") == "minLength", (
        f"expected a minLength constraint on credentials, got {credentials_entry!r}"
    )
    # The NUMBER, read as a number's own field rather than found inside a sentence.
    assert credentials_entry.get("keyword_value") == "32", (
        f"expected the 32-character minimum on the credentials issue, got {credentials_entry!r}"
    )


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


# Both spellings the feature file has carried for this obligation are bound to the ONE
# assertion, so the grade cannot depend on which wording a scenario line uses.
@then(parsers.parse('the request should include header "{header}" with a UNIX timestamp'))
@then(parsers.parse('the request should include header "{header}" with unix timestamp'))
def then_timestamp_header(ctx: dict, header: str) -> None:
    """Assert the replay-binding timestamp is the AdCP-legacy UNIX seconds form.

    Per AdCP 3.1.1 (docs/building/by-layer/L3/webhooks.mdx:404-418):
    ``X-ADCP-Timestamp: <unix timestamp in seconds>``, an exact ASCII integer.

    ``adcp.signing.webhook_hmac`` — the verifier a buyer runs against this scheme —
    signs and checks ``f"{timestamp}.{body}"`` with ``timestamp = str(int(time.time()))``
    and applies its skew window to that integer. An ISO string (which this scenario
    asked for before #1291 C1 routed the sender through the SDK) is not accepted by
    any conformant legacy verifier, so the seconds form is the contract.
    """
    headers = _get_last_webhook_headers(ctx)
    assert header in headers, f"Expected header {header!r} but got: {list(headers.keys())}"
    value = headers[header]
    assert value.isdigit(), f"Header {header!r} is not a UNIX-seconds integer: {value!r}"
    # Sanity: seconds, not milliseconds — a ms value would silently blow every
    # receiver's skew window.
    assert 1_000_000_000 < int(value) < 10_000_000_000, f"Header {header!r} is not in seconds: {value!r}"


@then('the HMAC should be computed over "timestamp.payload" concatenation')
def then_hmac_computation(ctx: dict) -> None:
    """Assert the HMAC verifies against the RAW bytes the origin actually received.

    Delegates to ``tests.helpers.hmac_assertions``, which 13 non-BDD callers
    already use. This step used to recompute the HMAC inline, and the copy had
    drifted in three ways that all favour a false pass:

    * it read ``X-ADCP-Signature``/``X-ADCP-Timestamp`` while the helper and the
      senders use ``X-AdCP-``, which is the exact regression the helper's own
      docstring names;
    * it stripped ``sha256=`` and compared bare hex, so a signature sent WITHOUT
      the prefix verified anyway;
    * it read the headers with ``.get(..., "")``, so a delivery that went out
      entirely unsigned failed on 'header present' rather than reporting that
      nothing can attribute the request to us (#1894).

    The recompute-over-wire-bytes property this step exists for is the helper's
    property too: it signs ``f"{timestamp}." + request.body``, never a fresh dump
    of the parsed payload — a recompute from the parsed dict can silently agree with a
    sender that signed one serialization and transmitted another, which is the defect
    PR #1802 fixed and the whole point of the obligation (#1441). Being byte-based it is
    also canonicalization-agnostic. [5/5 routed — salesagent-n78j0.1.4]
    """
    delivery = _last_webhook_delivery(ctx)

    signing_secret: str = ctx.get("webhook_secret", "")
    assert signing_secret, "Test setup must store webhook_secret in ctx['webhook_secret']"

    assert_signature_verifies_over_wire_body(delivery, signing_secret)


# ── RFC 9421 delivery-signature assertions ────────────────────────


@then(parsers.parse('the request should include header "{header}"'))
def then_header_present(ctx: dict, header: str) -> None:
    """The named header reached the socket, with a non-empty value.

    Case-insensitively, through :class:`httpx.Headers`: HTTP header names are
    case-insensitive and the sender is free to change its casing, so a case-sensitive
    dict lookup would grade the sender's formatting rather than the wire contract.
    """
    captured = _captured_delivery(ctx)
    assert header in captured.headers, (
        f"expected header {header!r} on the outbound delivery; the wire carried {sorted(captured.headers.keys())}"
    )
    assert captured.headers[header].strip(), f"header {header!r} was sent empty, so it carries no contract"


@then(parsers.parse('the request should not include header "{header}"'))
def then_header_absent(ctx: dict, header: str) -> None:
    """The named header did NOT reach the socket.

    security.mdx @ v3.1.1 :1425 — *"Sellers MUST NOT sign the same webhook both ways."*
    The mode switch is exclusive, so on the 9421 arm the legacy HMAC headers are not
    merely unnecessary, they are forbidden; and on an arm with no key material the 9421
    headers must be absent rather than present-but-unresolvable.
    """
    captured = _captured_delivery(ctx)
    assert header not in captured.headers, (
        f"header {header!r} was sent with value {captured.headers[header]!r}; this delivery's "
        "authentication mode forbids it"
    )


@then("the Signature-Input tag should equal the advertised webhook_signing profile")
def then_signature_tag_matches_advertised_profile(ctx: dict) -> None:
    """The ``tag=`` on the wire is the profile our capabilities document advertises.

    Two things a receiver does statically: read ``webhook_signing.profile`` off our
    capabilities and compare it against the ``tag=`` parameter of the delivery it
    receives (the ``profile`` field's own contract — "MUST match the ``tag=``
    parameter … so receivers can statically validate the declared profile against the
    on-wire signature"). Comparing the wire against the ADVERTISEMENT rather than
    against an SDK constant is what makes this one decision instead of two: a literal
    here would stay green while the two sides drifted apart.
    """
    captured = _captured_delivery(ctx)
    advertised = ctx["env"].advertised_webhook_signing()
    assert advertised.profile is not None, (
        "this tenant advertises no webhook_signing.profile, so a receiver has nothing to validate "
        "the tag against — the delivery's signature is unverifiable by static comparison"
    )
    tag = signature_input_params(captured).get("tag")
    assert tag == advertised.profile, (
        f"the delivery is signed under tag {tag!r} while we advertise profile "
        f"{advertised.profile!r}; a receiver validating one against the other rejects every delivery"
    )


@then(parsers.parse('the covered components should include "{component}"'))
def then_covered_components_include(ctx: dict, component: str) -> None:
    """*component* is in the signature's covered-component list, not merely in a header.

    Parsed as a structured field rather than substring-matched: ``Content-Digest`` can
    be present as a header while the signature does not COVER it, and that is precisely
    the shape where the body is unprotected — the digest is then an unsigned claim an
    attacker rewrites alongside the body.
    """
    captured = _captured_delivery(ctx)
    covered = signature_input_label(captured).components
    assert component in covered, (
        f"the signature covers {list(covered)}, which does not include {component!r} — the "
        "signature does not protect what this scenario says it protects"
    )


@then("the signature should verify against the tenant's published JWKS")
def then_signature_verifies_against_published_jwks(ctx: dict) -> None:
    """A conformant receiver, given only what we publish, accepts this delivery.

    The one shared verifier (``tests/helpers/signing.py::verify_as_conformant_receiver``,
    the single legal home per
    ``tests/unit/test_guards_no_duplicate_conformant_receiver_verifier.py``) run over the
    JWKS the tenant PUBLISHES — not over the key row we minted. Verifying against the
    row would still pass if the publication hop were broken, and a signature only
    verifiable against a key we never published is not verifiable at all.

    The signer identity is compared to the kid the Given provisioned, so a delivery
    signed under some other key cannot pass by merely being well-formed.
    """
    captured = _captured_delivery(ctx)
    env = ctx["env"]
    expected_kid = ctx.get("webhook_signing_kid")
    assert expected_kid, "the signing-key Given must record the kid it provisioned"

    verified = verify_as_conformant_receiver(captured, env.published_jwks())

    assert verified.key_id == expected_kid, (
        f"the delivery verified under key {verified.key_id!r}, but this tenant publishes and "
        f"should have signed with {expected_kid!r}"
    )


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
    resp = require_payload(ctx)
    deliveries = resp.media_buy_deliveries
    assert isinstance(deliveries, list), f"Expected media_buy_deliveries to be a list, got {type(deliveries).__name__}"
    # Every delivery item must carry a non-empty media_buy_id
    for d in deliveries:
        assert isinstance(d.media_buy_id, str) and d.media_buy_id, (
            f"Delivery item has invalid media_buy_id: {d.media_buy_id!r}"
        )
    # Filtering correctness: returned IDs must be a subset of requested IDs
    request_params = ctx.get("request_params", {})
    requested_ids = request_params.get("media_buy_ids")
    if requested_ids:
        returned_ids = {d.media_buy_id for d in deliveries}
        assert returned_ids <= set(requested_ids), (
            f"Response contains unrequested media_buy_ids: {returned_ids - set(requested_ids)}"
        )


@then('the response should not contain "errors" field')
def then_no_errors_field(ctx: dict) -> None:
    """Assert response errors list is empty and no exception was raised."""
    assert "error" not in ctx, f"Unexpected error: {ctx.get('error')}"
    resp = payload_or_none(ctx)
    if resp is not None:
        errors = getattr(resp, "errors", None) or []
        assert not errors, f"Unexpected errors in response: {errors}"


@then('the response should contain "errors" field')
def then_has_errors_field(ctx: dict) -> None:
    """Assert an error was produced (either response-level or exception).

    When a response exists, its errors list must be non-empty. When no
    response was returned, an exception must have been raised and stored
    in ctx['error']. The assertion verifies that an error condition is
    present, not just that some field exists.
    """
    error_exc = ctx.get("error")
    resp = payload_or_none(ctx)
    assert resp is not None or error_exc is not None, (
        "Expected either a response with errors or an exception, got neither"
    )
    if resp is not None:
        try:
            errors = resp.errors
        except AttributeError:
            errors = []
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
    resp = payload_or_none(ctx)
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
    """Assert the refusal leaks no existence information — in the message OR by echoing.

    BOTH halves run. The id-echo half used to be gated on
    ``ctx.get("target_media_buy_id") or ctx.get("media_buy_id")``, and NO step writes
    either key, so ``mb_id`` was always ``""`` and the half was skipped every time it
    ran — the scenario named a security obligation and graded half of it
    (salesagent-b9hi1.1). It now reads the request as sent, and FAILS if that is
    missing rather than skipping.

    @T-UC-004-ext-d is the non-disclosure scenario: a non-owner asking about someone
    else's media buy must get media_buy_not_found, worded so it cannot be told apart
    from a buy that never existed. Echoing the requested id ONCE is normal — "no media
    buy found for X" repeats what the buyer sent and reveals nothing — so the bound is
    on repetition, which is the shape that starts to read like confirmation.
    """
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = _get_error_message(error).lower()
    leaking_phrases = ["exists", "belongs to", "owned by", "not authorized for", "access denied"]
    for phrase in leaking_phrases:
        assert phrase not in msg, f"Error leaks existence info via phrase {phrase!r}: {error}"

    requested = ctx.get("requested_media_buy_ids")
    assert requested, (
        "No requested_media_buy_ids recorded — the When that dispatched must record what it "
        "sent, or the id-echo half of this non-disclosure check grades nothing"
    )
    for label in requested:
        mb_id = _resolve_media_buy_id(ctx, label)
        assert msg.count(mb_id.lower()) <= 1, (
            f"Error repeatedly echoes media_buy_id {mb_id!r}, which may reveal existence: {error}"
        )


# ── Webhook skip assertions ─────────────────────────────────────────


def _declined_delivery_outcome(ctx: dict) -> bool:
    """Production's OWN verdict on the delivery this scenario asked for.

    The observable for "the system considered this and declined". Receiver absence is
    NOT that observable: nothing arriving is true for a dozen uninteresting reasons —
    the request was never made, the address was never handed out, the receiver was
    never asked — and that is exactly how this scenario stayed vacuous through three
    review cycles (salesagent-n78j0.1.4). ``WebhookDeliveryService._send_webhook_enhanced``
    (``src/services/webhook_delivery_service.py`` :348-355) selects this principal's
    ACTIVE ``PushNotificationConfig`` rows and returns ``False`` WITHOUT a POST when
    there are none, so the returned flag is production reporting its own skip.

    Read strictly, because every lax reading of it is a way back into the bug:

    * ``ctx["webhook_result"]`` is INDEXED, not ``.get()``-ed — a ``When`` that never
      assigned it must raise ``KeyError``, not quietly become ``None``;
    * ``ctx`` must carry no ``"error"`` — the delivery Whens store exceptions there, so
      an assertion that tolerated a missing result would go GREEN when production BLEW
      UP, which is worse than vacuous;
    * the flag is returned to the caller UNCOERCED so the caller can pin ``is False``.
      ``bool(...)`` here would let ``None`` / ``0`` / ``[]`` pass for a decline.
    """
    assert "error" not in ctx, (
        f"the delivery request raised instead of returning a verdict, so there is no decline to grade: {ctx['error']!r}"
    )
    raw = ctx["webhook_result"]
    return raw[0] if isinstance(raw, tuple) else raw


@then(parsers.parse('the system should skip "{mb_id}" (no webhook to deliver to)'))
def then_skip_no_webhook(ctx: dict, mb_id: str) -> None:
    """Production was asked to deliver for THIS media buy and declined.

    Two things pinned together, because either alone is satisfiable by an accident: the
    request the ``When`` actually made names the media buy the scenario names, and
    production's verdict on it was ``False``. A verdict without the id would be green if
    the step evaluated some other buy; an id without the verdict would be green if
    production had happily delivered.
    """
    real_id = _resolve_media_buy_id(ctx, mb_id)
    evaluated = ctx["webhook_evaluated_media_buy"]
    outcome = _declined_delivery_outcome(ctx)
    assert (evaluated, outcome) == (real_id, False), (
        f"expected the system to be asked about {real_id!r} and to DECLINE (False); it was asked "
        f"about {evaluated!r} and answered {outcome!r}"
    )
    # And the endpoint agrees: no delivery that arrived carried this media buy. The
    # verdict alone would be green if production had reported a skip while a POST went
    # out anyway, so the receiver-side claim is kept as the second half of the pin.
    # Read at the ENTRY layer: media_buy_id is a property of a media_buy_deliveries[]
    # entry, never of the envelope, so an envelope-level read is None for every delivery
    # and the `not in` below would be green whatever production sent.
    posted_mb_ids = [
        entry.get("media_buy_id")
        for delivery in _webhook_deliveries(ctx)
        for entry in _delivery_entries(_webhook_result(_sent_payload(delivery)))
    ]
    assert real_id not in posted_mb_ids, (
        f"Webhook POST was made for '{real_id}' but it should have been skipped "
        f"(no webhook configured). All posted IDs: {posted_mb_ids}"
    )


@then("no delivery attempt should be made")
def then_no_delivery_attempt(ctx: dict) -> None:
    """No POST was attempted — production returned its verdict without sending.

    Production's OWN verdict is the PRIMARY claim, and it is the one that cannot be
    satisfied by an accident: nothing arriving at a receiver is true for a dozen
    uninteresting reasons (the request was never made, the address was never handed out,
    the receiver was never asked), which is how this scenario stayed vacuous through
    three review cycles (salesagent-n78j0.1.4).

    ``is False`` and not ``not outcome``: the falsy set includes ``None``, which is what
    a crashed ``When`` leaves behind.

    The endpoint's own count is asserted as the SECONDARY claim. It is not redundant —
    a verdict of False while a POST went out anyway is exactly the divergence neither
    half catches alone — and it is answerable because the delivery envs run a real
    origin that records every request that reaches it.
    """
    outcome = _declined_delivery_outcome(ctx)
    assert outcome is False, (
        f"expected production to report that it made no delivery attempt (False); it returned "
        f"{outcome!r}, so a POST was attempted or the verdict is not production's"
    )
    env = ctx["env"]
    assert env.delivery_attempts == 0, f"Expected no delivery attempt, endpoint received {env.delivery_attempts}"


# ── Reporting dimension assertions ─────────────────────────────────


@then(parsers.parse('the response packages should include "{field}" breakdown arrays'))
def then_packages_include_breakdown(ctx: dict, field: str) -> None:
    """Assert every package has a non-empty breakdown list for the named field.

    Verifies structural correctness (field is a list), content (each entry
    has impressions), and dimensional segmentation (each entry carries the
    dimension identifier, e.g. "device_type" for "by_device_type").
    """
    resp = require_payload(ctx)
    packages = _collect_all_packages(resp)
    checked = 0
    # Derive the dimension identifier from the field name: "by_device_type" -> "device_type"
    dimension_key = field[3:] if field.startswith("by_") else field
    for pkg in packages:
        value = getattr(pkg, field)
        assert isinstance(value, list), f"Package {pkg.package_id!r} missing '{field}' breakdown array: {value!r}"
        assert value, f"Package {pkg.package_id!r} has empty '{field}' breakdown"
        # Each breakdown entry must have impressions AND the dimension identifier
        identifiers_seen: set[str] = set()
        for entry in value:
            entry_impressions = (
                entry.get("impressions") if isinstance(entry, dict) else getattr(entry, "impressions", None)
            )
            assert entry_impressions is not None, f"Breakdown entry in {pkg.package_id!r}.{field} missing 'impressions'"
            # Dimension identifier: proves data is actually segmented
            dim_value = entry.get(dimension_key) if isinstance(entry, dict) else getattr(entry, dimension_key, None)
            # A breakdown entry with no dimension identifier is not a breakdown. The xfail
            # that stood here excused the one condition that makes the whole claim false --
            # unsegmented rows -- while the assert two lines down already refuses an EMPTY
            # identifier. Absent and empty are the same defect.
            assert dim_value is not None, (
                f"Breakdown entry in {pkg.package_id!r}.{field} is missing dimension "
                f"identifier {dimension_key!r}: entries are not segmented by dimension"
            )
            assert dim_value, (
                f"Breakdown entry in {pkg.package_id!r}.{field} has empty "
                f"dimension identifier '{dimension_key}': {dim_value!r}"
            )
            identifiers_seen.add(str(dim_value))
        # With multiple entries, dimension identifiers should be distinct
        if len(value) > 1:
            assert len(identifiers_seen) > 1, (
                f"Package {pkg.package_id!r}.{field} has {len(value)} entries "
                f"but only 1 distinct '{dimension_key}' value: {identifiers_seen} — "
                f"not truly segmented by dimension"
            )
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should NOT include "{field}" breakdown arrays'))
def then_packages_exclude_breakdown(ctx: dict, field: str) -> None:
    """Assert no package in the response has field as a list.

    Uses ``model_dump()`` to check the serialised dict so the assertion is
    meaningful even for fields that are absent from the model (e.g. 'by_audience'
    which PackageDelivery never defines — a ``getattr`` check would always pass
    vacuously).
    """
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", []) or []
    packages = [pkg for d in deliveries for pkg in (getattr(d, "by_package", None) or [])]
    for pkg in packages:
        dumped = pkg.model_dump()
        assert field not in dumped or not isinstance(dumped[field], list), (
            f"Package {pkg.package_id!r} should not have '{field}' breakdown array: {dumped.get(field)!r}"
        )


@then(parsers.parse('the response packages should include "{field}" with at most {n:d} entries'))
def then_packages_limited(ctx: dict, field: str, n: int) -> None:
    """Assert every package has at most n entries in the named breakdown field.

    Verifies the count constraint and that entries are properly typed (list
    of dicts/objects with at least one field populated).
    """
    resp = require_payload(ctx)
    packages = _collect_all_packages(resp)
    checked = 0
    for pkg in packages:
        value = getattr(pkg, field)
        assert isinstance(value, list), f"Package {pkg.package_id!r} missing '{field}' as a list: {value!r}"
        actual_count = len(value)
        assert actual_count <= n, (
            f"Package {pkg.package_id!r} '{field}' has {actual_count} entries, expected at most {n}"
        )
        # Each entry must be a non-empty dict or object (not bare None)
        for entry in value:
            assert entry is not None, f"Package {pkg.package_id!r} '{field}' contains a None entry"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('"{field}" should be true'))
def then_field_true(ctx: dict, field: str) -> None:
    """Assert the named field is True on every package in the response.

    Truncation flags (by_geo_truncated, by_device_type_truncated) live on
    PackageDelivery, not on the top-level response object.
    """
    resp = require_payload(ctx)
    packages = _collect_all_packages(resp)
    assert packages, "Response has no packages to check"
    for pkg in packages:
        value = getattr(pkg, field, None)
        assert value is True, f"Expected response package.{field} to be True, got {value!r}"


@then(parsers.parse('"{field}" should be false'))
def then_field_false(ctx: dict, field: str) -> None:
    """Assert the named field is False on every package in the response.

    Truncation flags (by_geo_truncated, by_device_type_truncated) live on
    PackageDelivery, not on the top-level response object.
    """
    resp = require_payload(ctx)
    packages = _collect_all_packages(resp)
    assert packages, "Response has no packages to check"
    for pkg in packages:
        value = getattr(pkg, field, None)
        assert value is False, f"Expected response package.{field} to be False, got {value!r}"


@then(parsers.parse('the response packages should include "{field}"'))
def then_packages_include_field(ctx: dict, field: str) -> None:
    """Assert every package has the named field populated with a valid value.

    Verifies the field is non-None and, for numeric fields, is a proper
    numeric type. For string fields, verifies non-empty.
    """
    resp = require_payload(ctx)
    packages = _collect_all_packages(resp)
    checked = 0
    for pkg in packages:
        value = getattr(pkg, field)
        assert value is not None, f"Package {pkg.package_id!r} missing field {field!r}"
        # Type-specific validation
        if isinstance(value, str):
            assert value, f"Package {pkg.package_id!r} field {field!r} is empty string"
        elif isinstance(value, list):
            # List fields should be non-empty
            assert value, f"Package {pkg.package_id!r} field {field!r} is empty list"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


def _wire_package(ctx: dict, pkg_id: str) -> dict:
    """The ``by_package`` entry for *pkg_id*, off the serialized wire body.

    Two hops, because ``by_package`` sits inside a list element and ``wire_lookup``'s
    dotted path deliberately does not index lists. The delivery is located by the
    sanctioned ``wire_entry`` primitive with the count pinned, as its docstring requires
    for an index-located entry; the package is then found by value within it. Sole
    implementation, so the three commercial-field Thens cannot drift apart.
    """
    deliveries = wire_field(ctx, "media_buy_deliveries")
    assert len(deliveries) == 1, f"expected one delivery to read {pkg_id!r} from, got {len(deliveries)}"
    delivery = wire_entry(ctx, "media_buy_deliveries", index=0)
    packages = delivery.get("by_package") or []
    named = [pkg for pkg in packages if pkg.get("package_id") == pkg_id]
    assert named, f"no by_package entry for {pkg_id!r}; wire carried {[p.get('package_id') for p in packages]}"
    return named[0]


@then(parsers.parse('the response packages should include pricing_model "{expected}" for "{pkg_id}"'))
def then_package_pricing_model(ctx: dict, expected: str, pkg_id: str) -> None:
    """The package's pricing_model on the wire is *expected*.

    Required on every ``by_package`` entry by get-media-buy-delivery-response.json and
    typed as the ``enums/pricing-model.json`` enum — read off the wire, not the typed
    payload, because a serialized enum member is what the buyer actually receives.
    """
    actual = _wire_package(ctx, pkg_id).get("pricing_model")
    assert actual == expected, f"package {pkg_id!r} pricing_model is {actual!r}, expected {expected!r}"


@then(parsers.parse('the response packages should include rate {expected:g} for "{pkg_id}"'))
def then_package_rate(ctx: dict, expected: float, pkg_id: str) -> None:
    """The package's rate on the wire is *expected*.

    get-media-buy-delivery-response.json: "For fixed-rate pricing, this is the agreed rate
    ... For auction-based pricing, this represents the effective rate based on actual
    delivery." One step grades both readings — which value is owed is the scenario's
    business, and it says so by the number it passes.
    """
    actual = _wire_package(ctx, pkg_id).get("rate")
    assert actual == expected, f"package {pkg_id!r} rate is {actual!r}, expected {expected!r}"


@then(parsers.parse('the response packages should include currency "{expected}" for "{pkg_id}"'))
def then_package_currency(ctx: dict, expected: str, pkg_id: str) -> None:
    """The package's currency on the wire is *expected* (required on every entry)."""
    actual = _wire_package(ctx, pkg_id).get("currency")
    assert actual == expected, f"package {pkg_id!r} currency is {actual!r}, expected {expected!r}"


@then(parsers.parse('the response packages should include "{f1}" and "{f2}" breakdowns'))
def then_packages_include_two(ctx: dict, f1: str, f2: str) -> None:
    """Assert every package has both named breakdown fields as non-empty lists."""
    resp = require_payload(ctx)
    packages = _collect_all_packages(resp)
    checked = 0
    for pkg in packages:
        for field in (f1, f2):
            value = getattr(pkg, field, None)
            assert isinstance(value, list), f"Package {pkg.package_id!r} missing '{field}' breakdown: {value!r}"
            assert value, f"Package {pkg.package_id!r} has empty '{field}' breakdown list"
        checked += 1
    assert checked >= 1, "Response has no packages to check"


@then(parsers.parse('the response packages should NOT include "{field}"'))
def then_packages_exclude_field(ctx: dict, field: str) -> None:
    """Assert no package has the named field set to a non-None value.

    Uses ``model_dump()`` so the assertion is meaningful even for fields that
    are absent from the model (e.g. 'by_audience' which PackageDelivery never
    defines — a ``getattr`` check would always pass vacuously).
    """
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", []) or []
    packages = [pkg for d in deliveries for pkg in (getattr(d, "by_package", None) or [])]
    for pkg in packages:
        dumped = pkg.model_dump()
        value = dumped.get(field)
        assert value is None, f"Package {pkg.package_id!r} should not have field {field!r}: {value!r}"


@then(parsers.parse('the response geo breakdown should use classification system "{system}"'))
def then_geo_system(ctx: dict, system: str) -> None:
    """Assert geo breakdown entries use the expected classification system.

    Asserts what the response DOES provide (media_buy_id, deliveries, totals),
    then xfails on the specific missing field (by_geo with system).
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"
    assert resp.reporting_period is not None, "Expected reporting_period"

    # The subject must be there for the claim to mean anything. The pinned 3.1
    # get-media-buy-delivery-response.json makes by_geo conditional ("Available when the
    # buyer requests geo breakdown via reporting_dimensions and the seller supports it"),
    # so absence is legal in general — but a scenario asking which CLASSIFICATION SYSTEM
    # the geo rows use has already asserted the rows exist. The xfail that stood here
    # excused exactly the case the step is for.
    packages = _collect_all_packages(resp)
    has_geo = any(getattr(pkg, "by_geo", None) for pkg in packages)
    assert has_geo, f"no package carried by_geo, so the classification-system claim ({system!r}) graded nothing"
    # If geo data is present, verify system field
    for pkg in packages:
        by_geo = getattr(pkg, "by_geo", None) or []
        for entry in by_geo:
            geo_system = entry.get("system") if isinstance(entry, dict) else getattr(entry, "system", None)
            if geo_system is not None:
                assert geo_system == system, f"Geo breakdown system mismatch: expected '{system}', got '{geo_system}'"


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}" (fallback)'))
def then_placement_sorted_fallback(ctx: dict, metric: str) -> None:
    """Assert placement breakdown uses fallback sort metric.

    Asserts what the response DOES provide (deliveries, packages),
    then verifies sort order if by_placement is populated.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"
    assert resp.reporting_period is not None, "Expected reporting_period"

    packages = _collect_all_packages(resp)
    _assert_placements_sorted_by(packages, metric, fallback=True)


@then(parsers.parse('the response placement breakdown should be sorted by "{metric}"'))
def then_placement_sorted(ctx: dict, metric: str) -> None:
    """Assert placement breakdown is sorted by the given metric descending.

    Asserts what the response DOES provide (deliveries, packages),
    then verifies sort order if by_placement is populated with the metric.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"
    assert resp.reporting_period is not None, "Expected reporting_period"

    packages = _collect_all_packages(resp)
    _assert_placements_sorted_by(packages, metric, fallback=False)


# ── Attribution window assertions ─────────────────────────────────


@then(parsers.parse('the response should include attribution_window with model "{model}"'))
def then_attribution_model(ctx: dict, model: str) -> None:
    """Assert attribution window model matches the expected value.

    Verifies the response carries an attribution_window whose model field
    equals the expected model string.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"
    assert resp.reporting_period is not None, "Expected reporting_period"

    aw = getattr(resp, "attribution_window", None)
    assert aw is not None, f"Response missing attribution_window — expected model '{model}' to be echoed"
    assert aw.model is not None, "attribution_window.model is None"
    actual_model = aw.model.value if hasattr(aw.model, "value") else str(aw.model)
    assert actual_model == model, f"attribution_window.model should be '{model}', got '{actual_model}'"


@then("the attribution_window should echo the applied post_click window")
def then_attribution_echo(ctx: dict) -> None:
    """Assert attribution_window echoes the post_click window THE BUYER SENT.

    Reads the request as dispatched, with NO default. It used to read
    ``ctx["request_attribution"]`` — a key no step wrote — and fall back to
    ``interval=7`` / ``unit="days"``, so it compared the response against two
    constants. They happened to equal this scenario's Examples, which is why it
    passed; a seller that ignored the requested window entirely and always answered
    7 days would have passed identically, and the docstring claimed the opposite of
    what the code did (salesagent-b9hi1.1).

    The absent-key branch now FAILS instead of defaulting. A Then that cannot find
    what the buyer asked for cannot grade an echo, and saying so is the whole repair:
    the defect was not a weak comparison, it was a comparison against a constant.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"

    aw = getattr(resp, "attribution_window", None)
    assert aw is not None, "Response missing attribution_window — expected post_click echo"

    pc = aw.post_click
    assert pc is not None, (
        "attribution_window.post_click is None — buyer requested a post_click window which should be echoed"
    )

    # The request as sent, written by the When that sent it. Required, not defaulted.
    requested = ctx.get("request_attribution")
    assert requested is not None, (
        "No request_attribution recorded — the When that sends attribution_window must "
        "record it, or this step is comparing the response against nothing"
    )
    requested_pc = requested.get("post_click")
    assert requested_pc is not None, (
        f"The request carried no post_click window, so there is no echo to grade: {requested!r}"
    )
    req_interval = requested_pc["interval"]
    req_unit = requested_pc["unit"]

    assert pc.interval == req_interval, (
        f"attribution_window.post_click.interval should echo the requested {req_interval}, got {pc.interval}"
    )
    pc_unit = pc.unit.value if hasattr(pc.unit, "value") else str(pc.unit)
    assert pc_unit == req_unit, (
        f"attribution_window.post_click.unit should echo the requested {req_unit!r}, got {pc_unit!r}"
    )


@then("the response should include attribution_window with the seller's platform default")
def then_attribution_default(ctx: dict) -> None:
    """Assert attribution window uses the seller's platform default.

    When the seller does NOT support configurable attribution, the response
    should contain only the platform default model without buyer-requested
    post_click/post_view windows.
    """
    from src.core.tools.media_buy_delivery import PLATFORM_DEFAULT_ATTRIBUTION_MODEL

    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"
    assert resp.reporting_period is not None, "Expected reporting_period in response"

    # Attribution window must be present
    aw = getattr(resp, "attribution_window", None)
    assert aw is not None, (
        "Response missing attribution_window — production should always "
        "echo an attribution window even for unsupported sellers"
    )
    assert aw.model is not None, "attribution_window.model is None — must carry the model"

    # Model should be the platform default
    actual_model = aw.model.value if hasattr(aw.model, "value") else str(aw.model)
    expected_model = (
        PLATFORM_DEFAULT_ATTRIBUTION_MODEL.value
        if hasattr(PLATFORM_DEFAULT_ATTRIBUTION_MODEL, "value")
        else str(PLATFORM_DEFAULT_ATTRIBUTION_MODEL)
    )
    assert actual_model == expected_model, (
        f"attribution_window.model should be platform default '{expected_model}', got '{actual_model}'"
    )

    # When seller does not support configurable windows, post_click/post_view
    # should be None — the buyer's requested window must be discarded.
    pc = getattr(aw, "post_click", "MISSING")
    pv = getattr(aw, "post_view", "MISSING")
    if pc != "MISSING" or pv != "MISSING":
        # This was `try: assert ... except AssertionError: pytest.xfail(...)` — the purest
        # form of the defect this epic names: catch the failure and excuse it, so the step
        # cannot fail in either direction. A seller that echoes the buyer's attribution
        # window when it does not support configurable attribution is reporting a setting
        # it will not honour, which is the thing worth failing on.
        assert pc is None, (
            f"attribution_window.post_click should be None for a seller that does not "
            f"support configurable attribution (the buyer's request is discarded, not "
            f"echoed), got {pc!r}"
        )
        assert pv is None, (
            f"attribution_window.post_view should be None for a seller that does not "
            f"support configurable attribution (the buyer's request is discarded, not "
            f"echoed), got {pv!r}"
        )


@then('the response attribution_window should include "model" field (required)')
def then_attribution_has_model(ctx: dict) -> None:
    """Assert attribution_window.model is present and valid in the response.

    BR-RULE-092 invariant: every delivery response must echo the applied
    attribution window with a non-null model from the spec-allowed values.
    """
    from adcp.types.generated_poc.enums.attribution_model import AttributionModel

    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"

    aw = getattr(resp, "attribution_window", None)
    assert aw is not None, "Response missing attribution_window — BR-RULE-092 requires it"
    assert aw.model is not None, "attribution_window.model is None — required by spec (BR-RULE-092)"
    # Model must be one of the spec-allowed values
    valid_models = {m.value for m in AttributionModel}
    actual_model = aw.model.value if hasattr(aw.model, "value") else str(aw.model)
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

    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"
    assert resp.reporting_period is not None, "Expected reporting_period in response"

    aw = getattr(resp, "attribution_window", None)
    assert aw is not None, (
        "Response missing attribution_window — production should echo the platform default when buyer omits it"
    )
    assert aw.model is not None, "attribution_window.model is None — must carry the platform default"
    actual_model = aw.model.value if hasattr(aw.model, "value") else str(aw.model)
    expected_model = (
        PLATFORM_DEFAULT_ATTRIBUTION_MODEL.value
        if hasattr(PLATFORM_DEFAULT_ATTRIBUTION_MODEL, "value")
        else str(PLATFORM_DEFAULT_ATTRIBUTION_MODEL)
    )
    assert actual_model == expected_model, (
        f"attribution_window.model should be platform default '{expected_model}', got '{actual_model}'"
    )


@then("the response should include attribution_window reflecting campaign-length window")
def then_attribution_campaign_length(ctx: dict) -> None:
    """Assert attribution window post_click resolves campaign unit to days.

    When the buyer requests post_click with unit=campaign and interval=1,
    production resolves this to unit=days with interval=campaign_length_days.
    The response must carry an attribution_window with a post_click whose
    unit is 'days' and interval >= 1.
    """
    assert "error" not in ctx, f"Expected valid response but got error: {ctx.get('error')}"
    resp = require_payload(ctx)

    # Response-level structural assertions
    assert resp.media_buy_deliveries, "Expected non-empty media_buy_deliveries"
    assert resp.reporting_period is not None, "Expected reporting_period in response"

    # Attribution window assertions
    aw = getattr(resp, "attribution_window", None)
    assert aw is not None, (
        "Response missing attribution_window — production should resolve "
        "campaign-unit window and echo it in the response"
    )
    assert aw.model is not None, "attribution_window.model is None — must carry the attribution model"

    # post_click must be present and resolved from campaign to days
    pc = aw.post_click
    assert pc is not None, (
        "attribution_window.post_click is None — buyer requested post_click={interval:1, unit:campaign}"
    )
    pc_unit = pc.unit.value if hasattr(pc.unit, "value") else str(pc.unit)
    assert pc_unit == "days", (
        f"attribution_window.post_click.unit should be 'days' (resolved from 'campaign'), got '{pc_unit}'"
    )
    assert pc.interval >= 1, (
        f"attribution_window.post_click.interval should be >= 1 (campaign length in days), got {pc.interval}"
    )


# ── Partial/error delivery assertions ─────────────────────────────


@then(parsers.parse('the response should indicate "{mb_id}" has partial_data or delayed metrics'))
def then_partial_data(ctx: dict, mb_id: str) -> None:
    """Assert the named media buy has reporting_delayed status."""
    resp = require_payload(ctx)
    deliveries = getattr(resp, "media_buy_deliveries", []) or []
    target = next((d for d in deliveries if d.media_buy_id == mb_id), None)
    assert target is not None, f"No delivery found for {mb_id!r}"
    assert target.status == "reporting_delayed", (
        f"Expected status='reporting_delayed' for partial/delayed metrics on {mb_id!r}, got {target.status!r}"
    )


@then(parsers.parse('the response should include "{mb_id}" with zero impressions and zero spend'))
def then_zero_metrics(ctx: dict, mb_id: str) -> None:
    """Assert the named media buy has exactly zero impressions and zero spend.

    Verifies ID mapping (the requested media buy is found in deliveries)
    and exact metric values (both must be zero, not just non-negative).
    """
    resp = require_payload(ctx)
    deliveries = resp.media_buy_deliveries
    target = next((d for d in deliveries if d.media_buy_id == mb_id), None)
    assert target is not None, f"No delivery found for '{mb_id}' in {[d.media_buy_id for d in deliveries]}"
    assert target.totals.impressions == 0.0, f"Expected zero impressions for '{mb_id}', got {target.totals.impressions}"
    assert target.totals.spend == 0.0, f"Expected zero spend for '{mb_id}', got {target.totals.spend}"


@then("no real billing records should have been created")
def then_no_billing(ctx: dict) -> None:
    """Assert sandbox mode — verify via response flag and absence of billing adapter calls."""
    resp = require_payload(ctx)
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


# Single source of truth for delivery boundary-field membership (moved out of
# generic/then_payload per ). Field names are normalized
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
    resp = payload_or_none(ctx)
    is_delivery = field.strip().lower().replace(" ", "_") in _DELIVERY_BOUNDARY_FIELDS or (
        resp is not None and hasattr(resp, "media_buy_deliveries")
    )
    if not is_delivery:
        return False

    if expected.strip().lower() in ("invalid", "error", "rejected"):
        # The rejection is graded on the WIRE: a code the buyer actually received,
        # not the class of a reconstructed exception. The
        # in-process branch survives only for a request that never reached the wire
        # -- a pydantic failure while BUILDING it -- which is a genuinely different
        # outcome, not a lenient fallback for the same one.
        from pydantic import ValidationError as PydanticValidationError

        result = ctx.get("result")
        wire_code = result.wire_error_code() if result is not None else None
        if wire_code is not None:
            result.assert_wire_error(wire_code)
            return True
        error = ctx.get("error")
        assert error is not None, f"Expected '{field}' boundary to be rejected as invalid, but no error in ctx"
        assert isinstance(error, PydanticValidationError), (
            f"no wire error envelope was captured for the '{field}' boundary, so the only remaining "
            f"acceptable outcome is a request that failed to build — got {type(error).__name__}: {error}"
        )
    else:
        assert "error" not in ctx, f"Expected valid '{field}' boundary but got error: {ctx.get('error')}"
        assert resp is not None, f"Expected delivery response for valid '{field}' boundary"
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert deliveries, f"Valid '{field}' boundary: expected non-empty media_buy_deliveries"
    return True


def _assert_valid_content(ctx: dict, field: str) -> None:
    """Per-field content assertion for 'valid' partition/boundary outcomes."""
    resp = require_payload(ctx)

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
        assert deliveries, f"Valid {field}: expected non-empty deliveries"
        # Each delivery must have at least one package with data
        for d in deliveries:
            pkgs = getattr(d, "by_package", None) or []
            assert pkgs, (
                f"Valid {field}: delivery {getattr(d, 'media_buy_id', '?')!r} "
                f"has no package data — dimensions not populated"
            )

    elif field in ("attribution_window", "attribution window"):
        resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else {}
        if isinstance(resp_dict, dict):
            aw = resp_dict.get("attribution_window")
            if aw is not None:
                assert "model" in aw, f"Valid {field}: attribution_window missing 'model'"

    elif field in ("daily_breakdown", "daily breakdown", "include_package_daily_breakdown"):
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert deliveries, f"Valid {field}: expected non-empty deliveries"
        # Verify daily breakdown data is structurally present
        for d in deliveries:
            pkgs = getattr(d, "by_package", None) or []
            for pkg in pkgs:
                daily = getattr(pkg, "daily", None) or getattr(pkg, "by_day", None)
                if daily is not None:
                    assert isinstance(daily, list), (
                        f"Valid {field}: package {getattr(pkg, 'package_id', '?')!r} "
                        f"daily field is not a list: {type(daily).__name__}"
                    )

    elif field == "account":
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert deliveries, f"Valid {field}: expected non-empty deliveries"
        # Verify account context is present in response when account was provided
        for d in deliveries:
            mb_id = getattr(d, "media_buy_id", None)
            assert mb_id is not None, f"Valid {field}: delivery missing media_buy_id"

    elif field in ("date_range", "date range"):
        period = getattr(resp, "reporting_period", None)
        if period is not None:
            start = getattr(period, "start", None)
            end = getattr(period, "end", None)
            assert start is not None, f"Valid {field}: reporting_period.start is None"
            assert end is not None, f"Valid {field}: reporting_period.end is None"

    elif field == "ownership":
        deliveries = getattr(resp, "media_buy_deliveries", None) or []
        assert deliveries, f"Valid {field}: expected non-empty deliveries"
        # Verify each delivery belongs to a known media buy
        for d in deliveries:
            mb_id = getattr(d, "media_buy_id", None)
            assert mb_id is not None, f"Valid {field}: delivery missing media_buy_id"


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
    # Read through the sanctioned readers rather than digging the envelope by hand.
    # error_envelope_or_none() is the ctx-side guarded accessor: on a wire transport it
    # hands back the REAL captured envelope, and on IMPL — which declares has_wire=False
    # — the dispatcher's own synthesized stand-in, so these assertions grade every
    # transport instead of pushing the wireless one into the failed-to-build branch
    # below. locate_envelope_error() is the single locator for the payload-layer error
    # object (errors[0]); the two-layer invariant guarantees it agrees with adcp_error,
    # so the payload layer is the position to read.
    envelope = error_envelope_or_none(ctx)
    error_object = locate_envelope_error(envelope)
    if error_object is not None:
        code = error_object.get("code")
        recovery = error_object.get("recovery")
        # SERVICE_UNAVAILABLE must be excluded too: it is a server fault that would otherwise
        # pass as a field rejection. (#1420 should-fix) CONFIGURATION_ERROR now
        # passes through untranslated and is likewise a
        # seller-side fault, never a field rejection. AUTH_MISSING/AUTH_INVALID
        # (the v3.1.1 split) replace the deprecated AUTH_REQUIRED
        # alias for auth failures — excluding only the literal "AUTH_REQUIRED"
        # string let an auth failure (e.g. resolve_principal_or_raise's
        # "principal not found" -> AUTH_INVALID) masquerade as a legitimate
        # client field rejection once the split landed.
        # The three codes below were added when the code rewriters were deleted. They used to
        # arrive here as SERVICE_UNAVAILABLE — already excluded — because a collapse rewrote
        # every non-standard code before the wire. With the collapse gone they arrive as
        # themselves, and each would otherwise pass as a legitimate "client field rejection":
        #   PARTIAL_FAILURE       correctable, but a SERVER-side partial failure
        #   MEDIA_BUY_REJECTED    terminal, a SELLER decision, not a field being invalid
        #   INVENTORY_UNAVAILABLE correctable, but about availability rather than the field
        # The recovery check below already catches the transient server faults
        # (ACTIVATION_WORKFLOW_FAILED, AD_SERVER_CREATE_FAILED, AD_SERVER_UPDATE_FAILED,
        # WORKFLOW_CREATION_FAILED), and ACTIVATION_ERROR / ACTIVATION_FAILED / ADAPTER_ERROR /
        # CREATIVE_SYNC_FAILED are absent from CODE_TABLE entirely, so no raise site can emit
        # them. Without these three the assertion would silently WEAKEN as a result of this step.
        assert code and code not in {
            "INTERNAL_ERROR",
            "SERVICE_UNAVAILABLE",
            "CONFIGURATION_ERROR",
            "AUTH_REQUIRED",
            "AUTH_MISSING",
            "AUTH_INVALID",
            "PARTIAL_FAILURE",
            "MEDIA_BUY_REJECTED",
            "INVENTORY_UNAVAILABLE",
        }, (
            f"Invalid {field}: expected a client rejection on the wire, got code={code!r} "
            f"— a server crash or auth failure is not a field rejection. Envelope: {envelope}"
        )
        assert recovery in ("correctable", "terminal"), (
            f"Invalid {field}: expected a client rejection (recovery correctable/terminal), got "
            f"recovery={recovery!r} — a transient server fault is not a rejection. Envelope: {envelope}"
        )
        return

    # No wire envelope. Exactly ONE outcome is still acceptable: the request never
    # reached the wire because it FAILED TO BUILD. That is a real, different outcome,
    # not a lenient fallback -- _validate_reporting_webhook_credentials (:3113) drives
    # the reporting-webhook Authentication rules through CreateMediaBuyRequest PARSING
    # on purpose, so those scenarios have no dispatch by construction.
    #
    # What is NOT accepted any more is the old `isinstance(error, (AdCPSalesAgentError,
    # ValidationError))`: admitting AdCPSalesAgentError there let a scenario that DID dispatch,
    # and produced no wire bytes, pass on the strength of a reconstructed exception
    # .
    from pydantic import ValidationError as PydanticValidationError

    error = ctx.get("error")
    assert error is not None, f"Expected invalid {field} result but operation succeeded"
    assert isinstance(error, PydanticValidationError), (
        f"Invalid {field}: no wire error envelope was captured, so the only remaining "
        f"acceptable outcome is a request that failed to build — got "
        f"{type(error).__name__}: {error}"
    )


def _assert_partition_or_boundary(ctx: dict, expected: str, field: str = "unknown") -> None:
    """Assert partition/boundary outcome with field-aware content validation."""
    expected = expected.strip()

    if expected == "valid":
        assert "error" not in ctx, f"Expected valid {field} result but got error: {ctx.get('error')}"
        # Two kinds of When feed this assertion. Most DISPATCH and are graded on
        # the payload that came back. The webhook-credentials scenarios instead
        # CONSTRUCT a CreateMediaBuyRequest locally and are graded on whether
        # construction raised — there is no dispatch and so no payload, and
        # demanding one would fail them for the wrong reason.
        if ctx.get("constructed_request") is not None:
            return
        require_payload(ctx)  # raises if the dispatch produced no payload for this field
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
        # EVERY field goes through the wire path. _WIRE_ASSERTED_FIELDS used to name
        # the one field ("attribution_window") that had been migrated, sending all the
        # others down a reconstructed-exception branch -- a ratcheting allowlist living
        # inside a test, where the unmigrated majority graded a rebuilt object instead
        # of the buyer's envelope. Deleted rather than shrunk: measured first that no
        # BR-UC-004 scenario names a code absent from CODE_TABLE, so nothing here
        # depended on the lenient branch.
        _assert_error_outcome(ctx, code, field, require_suggestion=require_suggestion)
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
        require_payload(ctx)  # raises if the dispatch produced no payload
        resp = require_payload(ctx)
        deliveries = getattr(resp, "media_buy_deliveries", None) or []

        # Determine what filter was requested by inspecting the When step's kwargs.
        # dispatch_request passes status_filter to call_impl; we reconstruct
        # from the call_impl request or from the response itself.
        request_filter = None
        request_params = ctx.get("request_params", {})
        if request_params.get("status_filter"):
            request_filter = request_params["status_filter"]

        if request_filter and request_filter not in (["(field absent)"], ["(omitted)"]):
            # Concrete filter: every returned delivery must have a matching status
            assert deliveries, f"Expected non-empty deliveries for valid status_filter={request_filter}"
            for d in deliveries:
                actual_status = getattr(d, "status", None)
                if actual_status is not None:
                    status_str = actual_status.value if hasattr(actual_status, "value") else str(actual_status)
                    assert status_str in request_filter, (
                        f"Status filter violation: delivery {getattr(d, 'media_buy_id', '?')!r} "
                        f"has status '{status_str}' but filter requested {request_filter}"
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


def _seed_package_terms(
    ctx: dict,
    pkg_id: str,
    *,
    pricing_model: str,
    rate: float | None,
    currency: str,
    is_fixed: bool,
    bid_price: float | None = None,
) -> None:
    """Give the buy seeded by the preceding Given exactly one package on these terms.

    Both halves of a package's identity move together, which is the whole point: the
    persisted request names *pkg_id* and the option, and the ``MediaPackage`` row carries
    the matching ``pricing_info``. The delivery path joins them by ``package_id``, so
    seeding one without the other grades nothing.
    """
    env = ctx["env"]
    if env is None or not hasattr(env, "_session"):
        return

    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import attributes

    from src.core.database.models import MediaBuy
    from src.core.helpers.pricing_helpers import pricing_info_for
    from tests.factories import MediaPackageFactory, PricingOptionFactory
    from tests.factories.media_buy import request_package

    option = PricingOptionFactory.build(
        pricing_model=pricing_model,
        currency=currency,
        is_fixed=is_fixed,
        rate=Decimal(str(rate)) if rate is not None else None,
    )
    package = request_package(package_id=pkg_id, pricing_option_id=option.pricing_option_id)
    if bid_price is not None:
        package["bid_price"] = bid_price

    mb_id = next(reversed(ctx.get("media_buys", {})), None)
    assert mb_id, "package terms need a media buy — the scenario must seed one first"
    session = env._session
    buy = session.scalars(select(MediaBuy).filter_by(media_buy_id=mb_id)).first()
    assert buy is not None, f"media buy {mb_id!r} was not seeded into the database"

    buy.raw_request = {**(buy.raw_request or {}), "packages": [package]}
    attributes.flag_modified(buy, "raw_request")
    MediaPackageFactory(
        media_buy=buy,
        package_id=pkg_id,
        package_config={**package, "pricing_info": pricing_info_for(option, bid_price=bid_price)},
    )
    session.commit()


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
    if "lowercase" in text:
        # The exact spelling the free-form A2A push-config endpoint stored. The
        # pinned AuthenticationScheme is case-SENSITIVE, so this is not a member
        # and the document is invalid — the casing is refused, not folded.
        scheme = "hmac-sha256"
    elif "basic" in text:
        # Not an AdCP scheme at all. Reachable the same way, and equally invalid:
        # the pinned enum is exactly ["Bearer", "HMAC-SHA256"].
        scheme = "Basic"
    elif "bearer" in text:
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

    FIXME(#2109): this grades a Pydantic constructor in the test process, not
    production — nothing is dispatched, so the a2a/mcp/rest axis carries no
    information for the 14 auth-scheme and credential rows that route here.

    WHY THE OBVIOUS FIX DOES NOT WORK, measured under salesagent-prkv.65 so the
    next person does not spend the attempt again: converting this to
    ``harness_create_request_kwargs(ctx)`` + a raw ``dispatch_request`` — the
    template that fixed every other site in that ticket, and which
    ``when_validate_webhook_config`` above already uses successfully — turns all
    42 of these tests (14 rows x 3 transports) RED, valid rows included. The two
    scenario outlines are routed to ``DeliveryPollEnv``, whose verb is
    ``get_media_buy_delivery``; a create_media_buy-shaped kwargs bag dispatched
    there comes back INVALID_REQUEST no matter what the credentials say.

    So this is NOT a step-definition migration. It needs the
    ``T-UC-004-partition-credentials`` / ``T-UC-004-boundary-credentials`` rows
    routed to a create-capable env (an ENV_ROUTES change plus product/pricing
    seeding), or the rows re-homed next to the ``T-UC-004-webhook-creds-*``
    scenarios, which already dispatch a real create. That is a scenario-routing
    decision, not a harness one.

    Note the graduation note in conftest.py for these two tags says they pass "on
    all transports" — they do, but only because this constructor check is
    transport-independent. That is the gap #2109 names, not evidence of coverage.
    """
    from datetime import UTC, datetime

    from pydantic import ValidationError

    from src.core.schemas import CreateMediaBuyRequest

    reporting_webhook = ReportingWebhookRequestFactory.payload(
        url="https://buyer.example.com/reporting",
        authentication={"schemes": [auth_scheme], "credentials": credentials},
        reporting_frequency="daily",
    )
    ctx.pop("error", None)
    try:
        # NOT ctx["response"]: this is the constructed REQUEST, not a response.
        # It shared the key with dispatch responses, so a Then could read a
        # request believing it had a response — the ambiguity lane gra7.5 removes.
        ctx["constructed_request"] = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
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
    because the a2a account param was wire-dropped ; now that
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

    from tests.helpers.account_seeding import seed_account_with_access

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
        dispatch_request(ctx)  # no dates -> tool defaults to the last 30 days
    elif "before" in norm:
        dispatch_request(ctx, start_date="2026-01-01", end_date="2026-01-31")
    elif "equal" in norm:
        dispatch_request(ctx, start_date="2026-01-15", end_date="2026-01-15")
    elif "after" in norm:
        dispatch_request(ctx, start_date="2026-01-31", end_date="2026-01-01")
    else:
        _dispatch_partition(ctx, "date_range", label)


def _dispatch_ownership_partition(ctx: dict, label: str) -> None:
    """Translate an ownership partition label to a real identity/query.

    Ownership is decided by the caller's identity, not a request field — the buy
    is seeded under the default principal (buyer-001). ``owner_matches`` queries
    as the owner (the buy is returned); ``owner_mismatch`` queries the same buy
    id as a foreign principal (a real ownership mismatch).

    Serves the BOUNDARY outline too, whose Examples spell the same two cases as
    "principal matches owner" / "principal differs from owner" — hence
    ``differs`` alongside ``mismatch``. Both outlines assert the same obligation,
    so they must exercise the same identity swap.
    """
    norm = label.strip().lower().replace(" ", "_")
    media_buys = ctx.get("media_buys", {})
    owned_ids = _resolve_media_buy_ids(ctx, list(media_buys.keys()))
    if "mismatch" in norm or "differs" in norm:
        # Query the owned buy as a REAL second principal: seed its row, re-point the env at
        # it, and let the resolver build its identity from the token that row carries.
        #
        # What this replaced passed ``identity=`` as a request kwarg -- a fabricated
        # ResolvedIdentity injected past the resolver. It never tested ownership on any
        # transport. a2a and mcp rejected ``identity`` as an unrecognized argument, so the
        # "expected invalid" assertion passed on the argument being unknown rather than on
        # the buy being someone else's; rest dropped it, dispatched as the OWNER, and
        # "failed" for succeeding. The comment above this table already named that exact
        # trap for the boundary outline ("a table of which transport rejects an unknown
        # argument") and routed both outlines through this helper to fix it -- while the
        # helper kept the injection, so the trap moved rather than closed.
        from tests.bdd.steps.generic._auth import authenticate_env_as
        from tests.factories.principal import PrincipalFactory

        env = ctx["env"]
        foreign = PrincipalFactory(tenant=ctx["tenant"], principal_id="buyer-999-foreign")
        env._commit_factory_data()
        authenticate_env_as(ctx, foreign.principal_id)
        dispatch_request(ctx, media_buy_ids=owned_ids or ["mb-001"])
    else:
        # owner_matches — query as the owning principal (default identity).
        dispatch_request(ctx, media_buy_ids=owned_ids or None)


# ── Restored helpers (from pre-merge 89a6c4bb) ──────────────────────


def _generate_unique_id(label: str) -> str:
    """Generate a unique media_buy_id from a Gherkin label."""
    import uuid

    from tests.factories.mint import mint

    return mint(f"{label}-{uuid.uuid4().hex[:8]}")


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

        # Same translation as _auth_scheme_to_db_fields, through the same columns:
        # the scheme names itself in authentication_type and the credential lands
        # in authentication_token, whichever scheme it is.
        auth_fields = _auth_scheme_to_db_fields(
            scheme,
            {"webhook_secret": secret, "webhook_bearer_token": bearer},
        )

        # END-STATE INVARIANT, enforced HERE and nowhere earlier. This runs from
        # the dispatch helper at the When step, so every Given has already run:
        # a scheme still without its credential was never going to get one. Left
        # tolerant, the config below would be built unauthenticated while the
        # Gherkin claims signing, the Then steps would grade that unauthenticated
        # config, and the scenario would pass green while measuring nothing.
        # Deliberately not a `require_credential` flag on the translation helper:
        # a strictness switch lets a future caller take the lax branch by
        # accident, which is the defect class this lane removes.
        if scheme is not None and not auth_fields:
            raise AssertionError(
                f"Scenario configured webhook authentication scheme {scheme} but no credential "
                "reached ctx by dispatch time -- add the Given step that supplies it. Building "
                "the config unauthenticated would let the scenario grade signing it never had."
            )

        configs.append(
            env.make_webhook_config(
                url=url,
                auth_type=auth_fields.get("authentication_type"),
                auth_token=auth_fields.get("authentication_token"),
            )
        )
    if configs:
        env.set_db_webhooks(configs)


def _call_webhook_service(
    ctx: dict,
    mb_id: str | None = None,
    report_type: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Ask the system under test to make ONE delivery, through the env's own seam.

    ``report_type`` (the Gherkin word) is passed straight through as
    ``notification_type``: which production flags it maps to is the env's business,
    not this step layer's — the same reason the transport is.
    """
    if mb_id is None:
        # Pick the first label from ctx, then resolve to real ID
        label = next(iter(ctx.get("media_buys", {})), None) or next(iter(ctx.get("webhook_config", {})), None)
        assert label, "No media buy in ctx or webhook_config — a Given step must create one first"
        mb_id = _resolve_media_buy_id(ctx, label)
    else:
        mb_id = _resolve_media_buy_id(ctx, mb_id)
    _wire_webhook_db(ctx)
    env = ctx["env"]
    return env.deliver_webhook(media_buy_id=mb_id, notification_type=report_type)


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
    resp = payload_or_none(ctx)
    error = ctx.get("error")
    assert resp is not None or error is not None, "Neither error nor response in ctx — test setup failed"
    # If a general error occurred, check it's not about this specific mb_id
    if error is not None:
        error_msg = _get_error_message(error).lower()
        assert real_id.lower() not in error_msg, f"Error mentions '{mb_id}' (real_id={real_id}): {error}"
    # If response exists, check response-level errors list and per-delivery errors
    if resp is not None:
        # Check response-level errors array (e.g. resp.errors)
        resp_errors = getattr(resp, "errors", None)
        if resp_errors:
            for err in resp_errors:
                err_str = _get_error_message(err).lower()
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
    resp = payload_or_none(ctx)
    assert resp is not None, "No response in ctx — When step must dispatch so a payload exists"
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
    wh["auth_scheme"] = AuthenticationScheme.HMAC_SHA256

    try:
        WebhookVerifier(webhook_secret=secret)
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
        dispatch_request(ctx, media_buy_ids=real_ids, status_filter=["active"])
    elif "media_buy_ids" in partition_norm and ("only" in partition_norm or "provided" in partition_norm):
        # Resolve by media_buy_ids ("media_buy_ids only" / "media_buy_ids provided").
        # Both translate to an explicit IDs request; passing the boundary label
        # verbatim would leak it into the request model (extra_forbidden).
        request_params["media_buy_ids"] = real_ids
        dispatch_request(ctx, media_buy_ids=real_ids)
    elif "neither_provided" in partition_norm or "neither" in partition_norm:
        # Neither IDs nor refs — should return all owned media buys
        dispatch_request(ctx)
    elif "partial" in partition_norm:
        # Partial resolution — request includes a nonexistent ID alongside a real
        # one. This is a partial SUCCESS: the real buy is returned and the
        # missing id yields a MEDIA_BUY_NOT_FOUND advisory (not a hard failure).
        # request_params records only the REAL id we expect back, so the "valid"
        # assertion doesn't demand the deliberately-absent one.
        real_one = real_ids[:1]
        dispatch_ids = real_one + ["mb-nonexistent"]
        request_params["media_buy_ids"] = real_one
        dispatch_request(ctx, media_buy_ids=dispatch_ids)
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
