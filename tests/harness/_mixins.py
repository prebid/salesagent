"""Domain mixins — shared fluent API for integration and unit test environments.

Each mixin provides the domain-specific helper methods (set_*, call_*, get_*)
that are identical across integration and unit variants. Concrete env classes
inherit from both a base (BaseTestEnv or IntegrationEnv) and a mixin.

Mixins don't define ``__init__`` — concrete classes set up required state.
Mixins may call ``self._commit_factory_data()`` which is a no-op in unit mode.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime
from time import sleep
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx

from src.adapters.mock_ad_server import simulate_breakdowns
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetProductsResponse,
    ReportingPeriod,
)
from src.core.schemas import GetProductsRequest as GetProductsRequestGenerated
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.core.tools.products import _get_products_impl
from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry
from src.services.webhook_delivery_service import (
    CircuitBreaker,
    WebhookDeliveryService,
)
from tests.harness._realize import e2e_unsupported, realize_e2e
from tests.helpers.webhook_wire import CapturedWebhook, stub_outbound_webhooks

# Patch target for send-time SSRF gate in CircuitBreakerEnv (unit + integration).
OUTBOUND_SSRF_VALIDATE_TARGET = "src.core.webhook_validator.WebhookURLValidator.validate_outbound_webhook_url"
# Shared EXTERNAL_PATCHES fragment — both CircuitBreakerEnv variants merge this.
SSRF_EXTERNAL_PATCH: dict[str, str] = {"ssrf": OUTBOUND_SSRF_VALIDATE_TARGET}

# Patch target for the send-time gate as the DELIVERY paths reach it. One constant
# for both WebhookEnv variants, mirroring the circuit-breaker pair above.
#
# It names the OWNER (src.core.webhook_validator), not the importer: every sender
# now reaches the gate through reject_unsafe_outbound_webhook_url, so patching a
# symbol on an importing module intercepts nothing. That is exactly how these two
# envs came to patch a method the delivery path had stopped calling
# (salesagent-og9k.5 / og9k.8).
WEBHOOK_VALIDATE_TARGET = "src.core.webhook_validator.WebhookURLValidator.validate_outbound_webhook_url"
WEBHOOK_VALIDATE_EXTERNAL_PATCH: dict[str, str] = {"validate": WEBHOOK_VALIDATE_TARGET}

#: Long enough for the SERVER's retry ladder on the wire, short enough that a
#: missing delivery is a failure rather than a hang. Only the e2e branch waits —
#: in process the POST has already happened by the time a reader runs.
_WIRE_DELIVERY_TIMEOUT_SECONDS = 45.0
_WIRE_POLL_INTERVAL_SECONDS = 0.5


def _captured_from_mock_call(call: Any) -> CapturedWebhook:
    """One recorded ``mock["post"]`` call as the receiving socket would have seen it.

    ``mock["post"]`` IS the socket (:func:`tests.helpers.webhook_wire.stub_outbound_webhooks`
    calls it with ``(url, headers=..., content=...)``), so this is a re-shaping of real
    wire bytes, never a reconstruction from a dict a client was asked to serialize —
    which is what lets a signature assertion be about what the receiver got (#1441).
    """
    args, kwargs = call
    return CapturedWebhook(
        url=str(args[0] if args else kwargs.get("url", "")),
        headers=httpx.Headers(kwargs.get("headers") or {}),
        content=kwargs.get("content") or b"",
    )


#: What a reader of the e2e stand-in socket is told, and what to read instead. One
#: string so every refused accessor names the SAME fix.
_E2E_SOCKET_BLIND_MESSAGE = (
    "this scenario runs on e2e; the delivery left the server container, so the "
    "in-process mock can never observe it — read deliveries through env.deliveries() / "
    "env.last_delivery()"
)


def _refuses_to_grade(accessor: str) -> Any:
    """A read-only property whose GETTER refuses, naming *accessor* and the fix.

    A plain instance attribute cannot do this: ``MagicMock`` resolves unknown
    attribute reads through ``__getattr__`` and stores its call bookkeeping in
    ``__dict__`` under ``_mock_*`` names, so assigning ``called = ...`` on an
    instance is simply overwritten bookkeeping. Python resolves DATA DESCRIPTORS on
    the TYPE before the instance ``__dict__``, so a ``property`` on a ``MagicMock``
    SUBCLASS is the one placement that intercepts the read. Verified empirically
    before this was built on.
    """

    def _get(self: Any) -> Any:
        raise AssertionError(f"{type(self).__name__}.{accessor} is not readable: {_E2E_SOCKET_BLIND_MESSAGE}")

    return property(_get)


class _E2EBlindWebhookSocket(MagicMock):
    """The e2e stand-in for the outbound webhook socket: still SETTABLE, never READABLE.

    ``_no_in_process_webhook_socket`` used to hand out a bare ``MagicMock`` and justify
    it as "a reader that still consults it fails on its own assertion instead of a
    KeyError". That reasoning holds for a PRESENCE assertion (``assert mock.called``
    fails loudly, correctly) and is FALSE for an ABSENCE one: over e2e the mock exists,
    is nameable, and is never populated, so ``assert not mock.called`` was green BY
    CONSTRUCTION — unfailable. Two readers of the same object, opposite honesty, and
    nothing marking the difference (salesagent-n78j0.1.4 blocker 2).

    So the difference is marked HERE, by the object, instead of by every reader
    remembering it. Configuration keeps working — ``return_value`` / ``side_effect``
    are untouched, which preserves the original reason this mock is created at all
    (setup that configures a response code still has something to configure). Every
    GRADING accessor refuses. The ``assert_*`` call helpers come along for free:
    ``assert_not_called`` reads ``call_count``, ``assert_called_with`` reads
    ``call_args``, ``assert_has_calls`` reads ``mock_calls`` — each lands on a refusal
    below rather than on a vacuous pass.

    Aliasing does not escape it (``post_mock = env.mock["post"]`` then
    ``post_mock.call_count``): the refusal is on ATTRIBUTE ACCESS, not on the dict
    lookup.

    Installed ONLY by :func:`_no_in_process_webhook_socket`, i.e. only on e2e. In
    process the very same accessors are legitimate — ``deliveries()``'s in-process
    realization reads ``call_args_list`` to reshape real wire bytes — and keep working
    unchanged, because in process the object is a plain ``MagicMock``. This is
    CLAUDE.md's no-quiet-failures rule turned on the harness itself.
    """

    called = _refuses_to_grade("called")
    call_count = _refuses_to_grade("call_count")
    call_args = _refuses_to_grade("call_args")
    call_args_list = _refuses_to_grade("call_args_list")
    mock_calls = _refuses_to_grade("mock_calls")
    method_calls = _refuses_to_grade("method_calls")


def _no_in_process_webhook_socket(env: Any) -> None:
    """E2E: leave the process's HTTP clients ALONE — the socket that matters is the server's.

    :func:`tests.helpers.webhook_wire.stub_outbound_webhooks` patches ``requests`` and
    ``httpx`` PROCESS-WIDE. Over e2e that catches far more than a webhook: it also
    intercepts the runner's own calls to the live stack — driving an admin route,
    reading the capture service back — and answers each of them with the stub's
    ``{"status":"received"}``. Measured, not theorised: the graduating leg failed with
    the signing-key admin route "reporting" that body as its rendered page.

    ``mock["post"]`` is still created, so setup that configures a response code has
    something to configure — but it is a :class:`_E2EBlindWebhookSocket`, which REFUSES
    every read that would grade a delivery it cannot have seen.
    """
    env.mock["post"] = _E2EBlindWebhookSocket()
    env.set_http_response(200)


def _seed_media_buy_for_delivery(env: Any, media_buy_id: str) -> Any:
    """The media buy the LIVE server is asked to report on. UNCONDITIONAL.

    Every delivery scenario needs the server to KNOW the buy — including the ones whose
    whole premise is that it carries no webhook. Splitting this out from
    :func:`_attach_reporting_webhook` is not tidying: while the two were one helper and
    the pair was made conditional, the no-config scenario's admin trigger answered
    ``HTTP 404 'Media buy not found'`` and the leg failed for a reason that had nothing
    to do with what it grades (salesagent-n78j0.1.4).

    Seeded through the same factories the in-process Givens use. An ``AdapterConfig``
    comes with it because the report the server builds is a REAL delivery poll: without
    a mock adapter row the poll returns errors and the server declines to send, which
    would read as a signing failure 45 seconds later.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy, Principal, Tenant
    from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
    from tests.factories.core import set_adapter_test_behavior

    session = env._session
    tenant = session.scalars(select(Tenant).filter_by(tenant_id=env._tenant_id)).first()
    if tenant is None:
        tenant = TenantFactory(tenant_id=env._tenant_id)
    principal = session.scalars(
        select(Principal).filter_by(tenant_id=env._tenant_id, principal_id=env._principal_id)
    ).first()
    if principal is None:
        principal = PrincipalFactory(tenant=tenant, principal_id=env._principal_id)

    media_buy = session.scalars(select(MediaBuy).filter_by(tenant_id=env._tenant_id, media_buy_id=media_buy_id)).first()
    if media_buy is None:
        media_buy = MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id=media_buy_id, status="active")
    set_adapter_test_behavior(env, env._tenant_id, manual_approval_required=False)
    env._commit_factory_data()
    return media_buy


def _attach_reporting_webhook(env: Any, media_buy: Any) -> None:
    """Give *media_buy* the callback this env's capture origin answers. CONDITIONAL.

    The delivery path reads ``MediaBuy.raw_request["reporting_webhook"]`` — that is what
    a real ``create_media_buy`` writes — so this is the row-level realization of a Given
    that says "this media buy has a reporting webhook". It is the half that must NOT run
    when no Given established a destination; see :func:`_deliver_via_live_server`.
    """
    raw_request = dict(media_buy.raw_request or {})
    raw_request["reporting_webhook"] = {"url": env.webhook_destination(), "frequency": "daily"}
    media_buy.raw_request = raw_request
    env._commit_factory_data()


def _deliver_via_live_server(
    env: Any,
    media_buy_id: str = "mb_001",
    notification_type: str | None = None,
    **kwargs: Any,
) -> tuple[bool, dict[str, Any]]:
    """E2E realization: the LIVE SERVER makes the delivery, the test only asks it to.

    The in-process branch calls ``WebhookDeliveryService`` inside the test process,
    which over e2e would post from the runner against its own patched socket while the
    server sent nothing — transport bypass, and the reason a whole cluster of UC-004
    webhook tags sat in the e2e_rest ledger (salesagent-n78j0.1.4).

    The trigger is a PRODUCTION route driven over real HTTP —
    ``/admin/tenant/<id>/media-buy/<id>/trigger-delivery-webhook`` — the same
    admin-over-HTTP shape ``provision_signing_key_via_admin`` uses. It runs
    ``DeliveryWebhookScheduler.trigger_report_for_media_buy_by_id`` INSIDE the server
    container, so the report is built, signed and posted by the deployment under test.
    Deliberately this route rather than a ``create_media_buy`` with a
    ``push_notification_config`` (the shape ``tests/e2e/test_webhook_signature_e2e.py``
    uses): that fires a TASK-status notification, while every scenario here says "the
    system delivers a webhook REPORT".

    THE ROUTE IS NOT THE ONLY SENDER, and an earlier version of this docstring claimed it
    was ("it is synchronous, so a missing delivery is a delivery defect rather than a
    scheduler race"). MEASURED, not reasoned: with this call replaced by a hardcoded
    ``return True`` — registration performed, route never driven — the graduated
    ``T-UC-004-webhook-9421`` e2e_rest leg still PASSED, in 3.1s against 0.6s on the
    unmutated tree (bdd_e2e ``innet_210826_0257`` vs ``innet_210826_0240``, both
    542 passed / 1 failed / 2033 xfailed / 18 xpassed / 2 skipped). ``run_all_tests.sh``
    exports ``DELIVERY_WEBHOOK_INTERVAL=5``, so the server's OWN background delivery
    scheduler picks the row up within about five seconds of
    :func:`_attach_reporting_webhook` writing it. Both senders are the deployment under
    test, so what the receiver captures is still a real server-made delivery — but the
    route is a way to ask PROMPTLY, not the reason a delivery exists, and a scenario
    cannot conclude "the trigger did it" from the fact that something arrived
    (salesagent-n78j0.1.4).

    ``notification_type`` has no server-side selector on this route (the scheduler
    emits ``scheduled``), so it is accepted and ignored here — the scenarios that grade
    it stay parked in the e2e_rest ledger.

    The WEBHOOK ATTACHMENT is conditional and that condition is the whole point: an
    ACTION that unconditionally creates the precondition its own scenario declares makes
    the Given decorative — a scenario asserting "no webhook is configured" would have one
    configured by the very step that is supposed to find none, and a scenario whose Given
    was deleted would still pass. So :func:`_attach_reporting_webhook` runs only when a
    Given actually established a destination
    (:attr:`BaseTestEnv.webhook_capture_key_was_handed_out`); otherwise the live server is
    driven exactly as it stands and gets to decline on its own terms.

    The MEDIA BUY ITSELF is unconditional, and the split matters: the route resolves the
    buy before it considers a webhook, so skipping the seed too made the no-config leg
    fail with ``HTTP 404 'Media buy not found'`` — a setup defect wearing the costume of
    the behaviour under test.
    """
    from tests.e2e._signing_e2e import trigger_delivery_webhook_via_admin

    media_buy = _seed_media_buy_for_delivery(env, media_buy_id)
    if env.webhook_capture_key_was_handed_out:
        _attach_reporting_webhook(env, media_buy)
    triggered = trigger_delivery_webhook_via_admin(
        env.e2e_config.base_url, tenant_id=env._tenant_id, media_buy_id=media_buy_id
    )
    return triggered, {"success": triggered}


def _await_captured_deliveries(env: Any, *, at_least: int = 1) -> list[CapturedWebhook]:
    """E2E realization of "the deliveries this scenario made", read off the receiver.

    Polls, because delivery on the wire runs behind the sender's retry ladder — unlike
    the in-process branch, where the POST has already happened by the time a reader
    runs. Returns whatever arrived when the window closes rather than asserting: a
    reader that expects NO delivery needs an empty list, not an AssertionError.

    ``at_least=0`` is an ABSENCE read and waits for NOTHING. Waiting the full arrival
    window to prove nothing arrived is a design fault, not a timeout to tune: it took
    ``T-UC-004-webhook-no-config`` from 0.2s to 47.9s — 45 of those seconds spent
    polling a capture key freshly minted for a scenario whose ``When`` asks the server
    for no delivery at all (salesagent-n78j0.1.4). Nor is answering immediately
    premature where a delivery WAS requested: the e2e trigger
    (:func:`tests.e2e._signing_e2e.trigger_delivery_webhook_via_admin`) is synchronous —
    the admin route runs the scheduler's send inline and only then reports its outcome —
    so by the time any ``Then`` runs, an attempt that was going to be made has been made
    and the receiver has already answered it.

    REFUSES to answer at all when this env never handed its capture URL to anything.
    :func:`tests.e2e._webhook_capture.captures` already carries this argument one layer
    out, for the unreachable-receiver case (":87-91": *"'zero captures' below would be
    true of every leg and the calling module would grade nothing"*); this is the same
    argument one layer in. An address nobody was ever given collects nothing whatever
    production does, so an absence claim read against it is green by construction —
    the exact vacuity that made ``T-UC-004-webhook-no-config`` look fixed
    (salesagent-n78j0.1.4).
    """
    from tests.e2e._webhook_capture import captured_deliveries

    assert env.webhook_capture_key_was_handed_out, (
        "this scenario is asking whether deliveries arrived at an address nobody was ever "
        f"given: capture key {env.webhook_capture_key!r} was minted by this very read and "
        "registered as a destination nowhere, so the receiver holds nothing for it and could "
        "hold nothing for it whatever production did. Absence here is green by construction, "
        "not a finding. Hand the address out first — a Given that establishes the webhook "
        "destination (env.webhook_destination()) — or grade the claim somewhere it can be "
        "observed (salesagent-n78j0.1.4)."
    )
    deliveries = captured_deliveries(env.webhook_capture_key)
    elapsed = 0.0
    while at_least > 0 and elapsed < _WIRE_DELIVERY_TIMEOUT_SECONDS and len(deliveries) < at_least:
        sleep(_WIRE_POLL_INTERVAL_SECONDS)
        elapsed += _WIRE_POLL_INTERVAL_SECONDS
        deliveries = captured_deliveries(env.webhook_capture_key)
    return deliveries


def _provision_signing_key_on_server(env: Any, monkeypatch: Any, *, alg: str = "ed25519") -> str:
    """E2E realization: mint the key INSIDE the server container, through the admin route.

    *monkeypatch* is accepted and unused — it configures the RUNNER's deployment KEK,
    and a key minted under that one is a key the server cannot open. ``docker-compose.e2e.yml``
    gives the server its own ``ADCP_SIGNING_DEV_KEK``; the admin route mints under it, so
    the row the server later signs with is one it can actually decrypt. That is exactly
    why ``tests/e2e/test_webhook_signature_e2e.py`` refuses a runner-minted key, and why
    the scenario's own feature comment records that no key was ever provisioned for the
    e2e leg (BR-UC-004 :301).
    """
    from tests.e2e._signing_e2e import provision_signing_key_via_admin

    env.make_origin_publishable()
    return provision_signing_key_via_admin(env.e2e_config.base_url, tenant_id=env._tenant_id, alg=alg)


def _get_from_live_server(env: Any, path: str, *, headers: dict[str, str]) -> dict[str, Any]:
    """One anonymous GET of a per-tenant document from the LIVE server.

    Anonymous because every document this serves — the JWKS, the capabilities —
    describes the SELLER, not the caller, and is resolved from the origin header
    rather than from a credential.

    *headers* is passed in rather than read from ``env.trust_root_request_headers()``
    here, because :func:`_realize_publishable_origin_on_server` is itself what that
    accessor is built on: defaulting would make the origin realization call itself.
    """
    response = httpx.get(f"{env.e2e_config.base_url}{path}", headers=headers, timeout=30.0)
    assert response.status_code == 200, (
        f"the live server must serve {path!r} for origin {headers!r}; got HTTP "
        f"{response.status_code}: {response.text[:300]!r}"
    )
    return dict(response.json())


def _publishable_origin_headers(host: str) -> dict[str, str]:
    """Headers that address a per-tenant document GET at *host*.

    ``Apx-Incoming-Host`` rather than ``Host``: :func:`route_landing_page` gives the
    proxy header precedence, and over e2e the request crosses nginx, which is free to
    rewrite ``Host``.
    """
    return {"Apx-Incoming-Host": host}


def _write_publishable_origin(env: Any) -> str:
    """Put a PUBLISHABLE origin on this env's tenant row; return the host it publishes at.

    The half of "publishable" that is a stored value, shared by both branches of
    :meth:`CircuitBreakerMixin.make_origin_publishable` so the in-process and e2e legs
    cannot drift on what they wrote. ``_get_protocol_for_domain`` derives ``http`` for a
    single-label host, which ``origin_is_publishable`` then refuses, so a DOTTED
    ``virtual_host`` is the requirement.
    """
    from sqlalchemy import select

    from src.core.database.models import Tenant

    session = env._session
    tenant = session.scalars(select(Tenant).filter_by(tenant_id=env._tenant_id)).first()
    assert tenant is not None, (
        f"no tenant row for {env._tenant_id!r} — provision the tenant before its publishable origin"
    )
    if "." not in (tenant.virtual_host or ""):
        tenant.virtual_host = f"{env._tenant_id}.example.com"
    env._commit_factory_data()
    return str(tenant.virtual_host)


def _realize_publishable_origin_on_server(env: Any) -> str:
    """E2E realization: the SERVER must have this tenant at this host, and route it.

    In process, writing a dotted ``virtual_host`` IS the whole intent: the same process
    that wrote the row resolves the host and builds the document. Over e2e the row is
    written by the RUNNER into the live server's database and every document is resolved
    INSIDE the server container, behind nginx — so "the runner wrote it" and "the
    deployment can find it" are separate facts, and the second is the one every
    trust-root fetch depends on. The e2e branch therefore writes the same row through the
    same helper and then makes the SERVER answer for it: an anonymous GET of
    ``/.well-known/jwks.json`` addressed at that origin, which 404s when no tenant
    resolves from that Host (``src/routes/well_known.py`` ``_serve`` :117-129) and 200s
    with an empty key set when one does. Deliberately the JWKS and not the capabilities
    document: it answers BEFORE any key exists, which is what
    ``_provision_signing_key_on_server`` needs, since it makes the origin publishable
    first and mints the key second.

    WHAT THE 200 DOES NOT PROVE, corrected after review: not publishability. An earlier
    version of this docstring said a host the deployment does not route "makes
    ``_rfc9421_sender`` drop its signing arm and send the webhook UNSIGNED". It does not.
    ``origin_is_publishable`` (``src/core/signing/posture.py`` :504) is exactly
    ``origin is not None and origin.startswith("https://")``, and the origin comes from
    ``_get_protocol_for_domain`` (``src/core/domain_config.py`` :36-46), a PURE function
    of the host STRING — a dotted host yields https whether the deployment routes it or
    not. And this module's own docstring records that it "never consults
    ``origin_is_publishable``" and serves at any Host. So the GET is evidence about
    ROUTING and DB reachability, which is real and is why it is kept; publishability is
    decided by the dotted host :func:`_write_publishable_origin` writes, one line up. In
    an epic about retiring comments that overstate their evidence, the previous sentence
    was itself one (salesagent-n78j0.1.4).

    Verified once per host per env — the answer cannot change while the row does not, and
    ``trust_root_request_headers`` calls this on every trust-root fetch.
    """
    from src.core.agent_identity import JWKS_PATH

    host = _write_publishable_origin(env)
    if env.__dict__.get("_verified_publishable_origin") != host:
        _get_from_live_server(env, JWKS_PATH, headers=_publishable_origin_headers(host))
        env.__dict__["_verified_publishable_origin"] = host
    return host


def _fetch_served_jwks_over_http(env: Any) -> dict[str, Any]:
    """E2E realization: GET the JWKS the LIVE server publishes for this tenant's origin."""
    from src.core.agent_identity import JWKS_PATH

    return _get_from_live_server(env, JWKS_PATH, headers=env.trust_root_request_headers())


def _fetch_advertised_webhook_signing(env: Any) -> Any:
    """E2E realization: the ``webhook_signing`` block the LIVE server ADVERTISES.

    Not the in-process re-derivation. ``signing_key_backed`` decides ``supported`` by
    DECRYPTING the private half, and over e2e the row is encrypted under the server
    container's KEK — the runner holds a different one, so an in-process derivation
    reports ``supported=False`` for a key that demonstrably signs, and the tag
    assertion would fail against a posture nobody advertises. Reading the served
    document is also the stronger claim: it is what a receiver statically validates the
    on-wire ``tag=`` against.
    """
    from src.core.signing.posture import WebhookSigningPosture

    served = (
        _get_from_live_server(env, "/api/v1/capabilities", headers=env.trust_root_request_headers()).get(
            "webhook_signing"
        )
        or {}
    )
    return WebhookSigningPosture.model_validate(served)


def _persist_simulation_config(env: Any, resp: AdapterGetMediaBuyDeliveryResponse) -> Any:
    """E2E realization of a delivery-poll adapter response (#1418).

    Persists the same ``AdapterGetMediaBuyDeliveryResponse`` the in-process
    branch would inject on the MagicMock as a ``DeliverySimulationConfig`` row in
    the live server's DB, where the server's Mock adapter reads it. Uses the
    env's server-bound session (rebound to the server engine in e2e mode) via
    the tenant-scoped repository, then commits so the HTTP request sees it.

    The repository ``upsert`` writes only the simulation-config row (never a
    tenant row), so the seeded tenant from the discovery-path / Given step is
    left intact.
    """
    from src.core.database.repositories.delivery_simulation import (
        DeliverySimulationConfigRepository,
    )

    repo = DeliverySimulationConfigRepository(env._session, env._tenant_id)
    row = repo.upsert(resp.media_buy_id, resp.model_dump(mode="json"))
    env._commit_factory_data()
    return row


def make_adapter_update_side_effect() -> Any:
    """Return a side_effect for a mocked ``adapter.update_media_buy``.

    Produces an ``UpdateMediaBuySuccess`` echoing the media_buy_id from the
    call and a resolved ``implementation_date``, mirroring the mock adapter's
    own ``update_media_buy`` return (mock_ad_server.update_media_buy). Used by
    MediaBuyDualEnv to wire the update-path adapter mock.
    """
    from src.core.schemas._base import UpdateMediaBuySuccess

    def _update_response(*args: Any, **kwargs: Any) -> UpdateMediaBuySuccess:
        media_buy_id = kwargs.get("media_buy_id") or (args[0] if args else "")
        today = kwargs.get("today") or datetime.now(UTC)
        return UpdateMediaBuySuccess(
            media_buy_id=media_buy_id,
            affected_packages=[],
            implementation_date=today,
        )

    return _update_response


class DeliveryPollMixin:
    """Shared fluent API for delivery poll testing.

    Requires concrete class to set ``self._adapter_responses: dict`` in __init__.
    """

    _adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse]

    def _configure_adapter_mock(self) -> None:
        """Wire adapter mock with side_effect lookup. Call from _configure_mocks."""
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = self._adapter_lookup
        self.mock["adapter"].return_value = mock_adapter  # type: ignore[attr-defined]

    def _adapter_lookup(self, *args: Any, **kwargs: Any) -> AdapterGetMediaBuyDeliveryResponse:
        """Look up configured adapter response by media_buy_id.

        Raises KeyError for unregistered IDs when other IDs are registered,
        preventing tests from silently succeeding with wrong data.
        """
        mb_id = kwargs.get("media_buy_id") or (args[0] if args else None)
        if mb_id and mb_id in self._adapter_responses:
            return self._adapter_responses[mb_id]
        if self._adapter_responses:
            raise KeyError(
                f"No adapter response registered for media_buy_id={mb_id!r}. "
                f"Registered: {list(self._adapter_responses.keys())}. "
                f"Call env.set_adapter_response({mb_id!r}, ...) first."
            )
        return self._make_default_adapter_response()

    @staticmethod
    def _build_adapter_delivery(
        media_buy_id: str,
        impressions: int,
        spend: float,
        package_id: str,
        clicks: int | None,
        packages: list[dict[str, Any]] | None,
        conversions: float | None = None,
        conversion_value: float | None = None,
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Normalize set_adapter_response params into the delivery intent.

        Shared by both transports: the in-process branch injects this object on
        the MagicMock; the e2e branch persists its wire dump. Single source of
        the packages-list-vs-scalars + totals-auto-sum logic.
        """
        if packages is not None:
            by_package = [
                AdapterPackageDelivery(
                    package_id=p["package_id"],
                    impressions=p.get("impressions", 0),
                    spend=p.get("spend", 0.0),
                )
                for p in packages
            ]
            total_impressions = float(sum(p.get("impressions", 0) for p in packages))
            total_spend = float(sum(p.get("spend", 0.0) for p in packages))
            totals = DeliveryTotals(impressions=total_impressions, spend=total_spend)
        else:
            simulated_geo, simulated_device_type = simulate_breakdowns(float(impressions), float(spend))
            by_package = [
                AdapterPackageDelivery(
                    package_id=package_id,
                    impressions=impressions,
                    spend=spend,
                    by_geo=simulated_geo,
                    by_device_type=simulated_device_type,
                )
            ]
            totals = DeliveryTotals(impressions=float(impressions), spend=spend)

        if clicks is not None:
            totals.clicks = float(clicks)
        if conversions is not None:
            totals.conversions = float(conversions)
        if conversion_value is not None:
            totals.conversion_value = float(conversion_value)

        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            totals=totals,
            by_package=by_package,
            currency="USD",
        )

    def set_adapter_response(
        self,
        media_buy_id: str = "mb_001",
        impressions: int = 5000,
        spend: float = 250.0,
        package_id: str = "pkg_001",
        clicks: int | None = None,
        packages: list[dict[str, Any]] | None = None,
        conversions: float | None = None,
        conversion_value: float | None = None,
    ) -> None:
        """Configure adapter to return specific delivery data for a media buy.

        For single-package responses, use the scalar parameters (backward compatible).
        For multi-package responses, pass ``packages`` — a list of dicts with
        ``package_id``, ``impressions``, and ``spend`` keys. Totals are auto-computed
        as the sum of per-package values. ``conversions`` / ``conversion_value``
        are totals-level (spec-optional metrics; omitted when None).

        In-process: injects the response on the adapter MagicMock. E2E: persists
        a ``DeliverySimulationConfig`` row the live server's Mock adapter reads.
        """
        resp = self._build_adapter_delivery(
            media_buy_id, impressions, spend, package_id, clicks, packages, conversions, conversion_value
        )
        self._realize_adapter_response(resp)

    @realize_e2e(_persist_simulation_config)
    def _realize_adapter_response(self, resp: AdapterGetMediaBuyDeliveryResponse) -> None:
        """In-process realization: register the response on the adapter mock."""
        self._adapter_responses[resp.media_buy_id] = resp

    @realize_e2e(
        e2e_unsupported(
            "adapter fault-injection has no server surface; needs an ADCP_TESTING fault-injection control (#1418)"
        )
    )
    def set_adapter_error(self, exception: Exception) -> None:
        """Make the adapter raise the given exception on get_media_buy_delivery."""
        self.mock["adapter"].return_value.get_media_buy_delivery.side_effect = exception  # type: ignore[attr-defined]

    def call_impl(
        self,
        media_buy_ids: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        status_filter: list[str] | None = None,
        **extra: Any,
    ) -> GetMediaBuyDeliveryResponse:
        """Call _get_media_buy_delivery_impl with the given parameters."""
        self._commit_factory_data()  # type: ignore[attr-defined]

        # Pop identity — it's injected by call_via for transport dispatch
        # but is not a GetMediaBuyDeliveryRequest field.
        # Use sentinel to distinguish "not provided" from "explicitly None".
        _no_identity = object()
        raw_identity = extra.pop("identity", _no_identity)
        identity = self.identity if raw_identity is _no_identity else raw_identity  # type: ignore[attr-defined]

        kwargs: dict[str, Any] = {}
        if media_buy_ids is not None:
            kwargs["media_buy_ids"] = media_buy_ids
        if start_date is not None:
            kwargs["start_date"] = start_date
        if end_date is not None:
            kwargs["end_date"] = end_date
        if status_filter is not None:
            kwargs["status_filter"] = status_filter
        kwargs.update(extra)

        req = GetMediaBuyDeliveryRequest(**kwargs)
        return _get_media_buy_delivery_impl(req, identity)

    @staticmethod
    def _make_default_adapter_response() -> AdapterGetMediaBuyDeliveryResponse:
        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_001",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=5000.0, spend=250.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_001", impressions=5000, spend=250.0)],
            currency="USD",
        )


class WebhookMixin:
    """Shared fluent API for webhook delivery testing."""

    _seq_counter: dict[str, int]

    def set_http_status(self, code: int, text: str = "") -> None:
        """Configure requests.post to return a single response with the given status."""
        mock_response = MagicMock()
        mock_response.status_code = code
        mock_response.text = text or f"Status {code}"
        self.mock["post"].return_value = mock_response  # type: ignore[attr-defined]
        self.mock["post"].side_effect = None  # type: ignore[attr-defined]

    def set_http_sequence(self, responses: list[tuple[int, str]]) -> None:
        """Configure requests.post to return a sequence of responses.

        Args:
            responses: List of (status_code, text) tuples.
        """
        mocks = []
        for code, text in responses:
            r = MagicMock()
            r.status_code = code
            r.text = text
            mocks.append(r)
        self.mock["post"].side_effect = mocks  # type: ignore[attr-defined]

    def set_http_error(self, exception: Exception) -> None:
        """Make requests.post raise the given exception."""
        self.mock["post"].side_effect = exception  # type: ignore[attr-defined]

    def set_url_invalid(self, error_msg: str = "Invalid URL") -> None:
        """Make URL validation fail, short-circuiting delivery."""
        self.mock["validate"].return_value = (False, error_msg)  # type: ignore[attr-defined]

    def call_deliver(
        self,
        webhook_url: str = "https://example.com/webhook",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        signing_secret: str | None = None,
        max_retries: int = 3,
        timeout: int = 10,
        event_type: str | None = None,
        tenant_id: str | None = None,
        object_id: str | None = None,
        media_buy_id: str | None = None,
        notification_type: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Call deliver_webhook_with_retry with the given parameters.

        When ``payload`` is omitted, a structured default payload is built that
        includes ``media_buy_id``, a monotonically increasing ``sequence_number``
        (per media_buy_id), ``reporting_period``, and optionally
        ``notification_type`` / ``next_expected_at``.  This mirrors the payload
        shape that ``WebhookDeliveryService`` produces so that BDD Then steps can
        assert on payload fields without requiring the full service stack.
        """
        self._commit_factory_data()  # type: ignore[attr-defined]
        mb = media_buy_id or "mb_001"

        # Per-media-buy sequence counter (simulates WebhookDeliveryService behaviour)
        if not hasattr(self, "_seq_counter"):
            self._seq_counter = {}  # type: ignore[assignment]
        self._seq_counter[mb] = self._seq_counter.get(mb, 0) + 1  # type: ignore[index]
        seq: int = self._seq_counter[mb]  # type: ignore[index]

        if payload is None:
            payload = {
                "event": "delivery.update",
                "media_buy_id": mb,
                "sequence_number": seq,
                "reporting_period": {
                    "start": "2025-01-01T00:00:00+00:00",
                    "end": "2025-12-31T23:59:59+00:00",
                },
            }
            if notification_type is not None:
                payload["notification_type"] = notification_type
                if notification_type != "final":
                    payload["next_expected_at"] = "2025-01-08T00:00:00+00:00"
        if headers is None:
            headers = {"Content-Type": "application/json"}
        delivery = WebhookDelivery(
            webhook_url=webhook_url,
            payload=payload,
            headers=headers,
            signing_secret=signing_secret,
            max_retries=max_retries,
            timeout=timeout,
            event_type=event_type,
            tenant_id=tenant_id,
            object_id=object_id,
        )
        return deliver_webhook_with_retry(delivery)

    def call_impl(self, **kwargs: Any) -> Any:
        """Alias for call_deliver to satisfy BaseTestEnv interface."""
        return self.call_deliver(**kwargs)


class CircuitBreakerMixin:
    """Shared fluent API for circuit breaker / webhook delivery service testing."""

    _service: WebhookDeliveryService | None
    _wire: ExitStack | None = None

    # ── The outbound delivery seam ────────────────────────────────────
    #
    # Two halves, realized per transport HERE: who MAKES a delivery
    # (:meth:`deliver_webhook`) and how a test READS it back (:meth:`deliveries` /
    # :meth:`last_delivery`). The third — where it is ADDRESSED — is
    # ``BaseTestEnv.webhook_destination``, because envs that only REGISTER a webhook
    # need it too. No step definition learns that a transport exists; the defect this
    # removes is a delivery that happened in the test process while the leg claimed to
    # grade the live server (salesagent-n78j0.1.4).

    @realize_e2e(_deliver_via_live_server)
    def deliver_webhook(
        self,
        media_buy_id: str = "mb_001",
        notification_type: str | None = None,
        **kwargs: Any,
    ) -> tuple[bool, dict[str, Any]]:
        """Make ONE outbound delivery through the code under test.

        In process that is :meth:`call_deliver` (the production
        ``WebhookDeliveryService``); over e2e the live server is driven to send it
        (:func:`_deliver_via_live_server`). Returns ``(success, info)`` either way.
        """
        return self.call_deliver(media_buy_id=media_buy_id, notification_type=notification_type, **kwargs)

    @realize_e2e(_await_captured_deliveries)
    def deliveries(self, *, at_least: int = 1) -> list[CapturedWebhook]:
        """Every delivery this scenario made, as the receiver saw it.

        ORDER IS PER-BRANCH AND THE DIFFERENCE IS REAL, so this no longer claims "in send
        order" for both. In process the list is ``call_args_list``, which IS send order.
        On the wire it is ``captured_deliveries``, documented as ARRIVAL order
        (``tests/e2e/_webhook_capture.py`` :118-120) — the receiver records what reaches
        it, and nothing in the env can promise a retry ladder or concurrent sends arrive
        in the order they left. A caller that needs send order must read a field that
        carries it (``sequence_number``) rather than trusting the index; the sequence
        ``Then``s do exactly that (salesagent-n78j0.1.4).

        ``at_least`` is the number a caller needs before the answer is meaningful —
        the multi-call readers (sequence ordering) need more than one. It is only
        consulted on the wire, where deliveries arrive asynchronously; in process the
        calls have already been recorded.

        ``at_least=0`` is how an ABSENCE claim asks ("no delivery carried this id",
        "no delivery attempt at all"): there is nothing to wait FOR, so the wire branch
        answers immediately instead of burning the arrival window to prove a negative
        (:func:`_await_captured_deliveries`).
        """
        return [_captured_from_mock_call(call) for call in self.mock["post"].call_args_list]  # type: ignore[attr-defined]

    def last_delivery(self) -> CapturedWebhook:
        """The most recent outbound delivery. Fails loudly when there was none."""
        deliveries = self.deliveries()
        assert deliveries, "no outbound webhook delivery was made, so there is nothing to read"
        return deliveries[-1]

    @realize_e2e(_no_in_process_webhook_socket)
    def install_webhook_wire(self) -> None:
        """Replace the outbound webhook socket and expose it as ``mock["post"]``.

        Shared by the unit and integration envs: after #1291 C1 every AdCP sender
        delivers through ``adcp.webhooks.WebhookSender``, so there is no
        module-level ``httpx.Client`` left to patch — the stub sits under whichever
        client the sender uses and hands the mock the real URL, headers and bytes.

        Over e2e there is no in-process socket to replace, and replacing one is
        actively harmful — see :func:`_no_in_process_webhook_socket`.
        """
        self.mock["post"] = MagicMock()  # type: ignore[attr-defined]
        self.set_http_response(200)
        self._wire = ExitStack()
        self._wire.enter_context(stub_outbound_webhooks(self.mock["post"]))  # type: ignore[attr-defined]

    def close_webhook_wire(self) -> None:
        """Undo :meth:`install_webhook_wire`. Idempotent."""
        if self._wire is not None:
            self._wire.close()
            self._wire = None

    @staticmethod
    def webhook_auth_fields(
        auth_type: str | None, auth_token: str | None, secret: str | None
    ) -> tuple[str | None, str | None]:
        """Fold a legacy HMAC ``secret`` onto the spec's ONE selector.

        security.mdx @ v3.1.1 :1424 — the buyer's ``authentication`` block selects
        the mode. #1291 C1 retired the second selector (the ``webhook_secret``
        column, which production never wrote), so a test asking for an HMAC secret
        gets an HMAC-SHA256 registration.
        """
        if secret:
            return "HMAC-SHA256", secret
        return auth_type, auth_token

    @realize_e2e(_realize_publishable_origin_on_server)
    def make_origin_publishable(self) -> str:
        """Give this tenant a PUBLISHABLE origin, and return the host it publishes at.

        One of the two halves :func:`src.core.signing.posture.origin_is_publishable`
        needs (the other is an active key). In process the stored dotted
        ``virtual_host`` IS the whole intent; over e2e the deployment has to actually
        route it, which is what :func:`_realize_publishable_origin_on_server` makes the
        server answer for. The host is returned either way because every trust-root
        fetch has to address the tenant by it.
        """
        return _write_publishable_origin(self)

    def trust_root_request_headers(self) -> dict[str, str]:
        """Headers that address a trust-root GET at THIS tenant's published origin.

        Naming the origin explicitly is what keeps the fetched document this tenant's
        rather than whichever tenant the proxy's own hostname happens to resolve to;
        see :func:`_publishable_origin_headers` for why it is the proxy header.
        """
        return _publishable_origin_headers(self.make_origin_publishable())

    @realize_e2e(_provision_signing_key_on_server)
    def provision_webhook_signing_key(self, monkeypatch: Any, *, alg: str = "ed25519") -> str:
        """Opt this scenario's tenant into RFC 9421 webhook signing. Returns the ``kid``.

        Per-scenario opt-in, never an env default (#1291 z6nr.31): every other UC-004
        webhook scenario must keep its current unsigned/HMAC posture byte-for-byte, and
        ``@T-UC-004-webhook-notification-type`` asserts exactly that.

        Both halves are prerequisites for :func:`src.core.signing.posture.origin_is_publishable`
        and neither alone is enough: an ACTIVE ``SigningKey`` row (through the SAME
        production provisioner ``tests/e2e/_signing_e2e.py`` uses,
        :func:`tests.helpers.signing.provision_key`, never a hand-built row) plus a
        PUBLISHABLE origin (a dotted ``virtual_host`` — ``_get_protocol_for_domain``
        derives ``http`` for a single-label host, which ``origin_is_publishable`` then
        refuses). Without the origin half, ``_rfc9421_sender`` drops the arm and the
        delivery goes out UNSIGNED — a sibling that skipped this would pass vacuously on
        headers that were simply never sent.

        *monkeypatch* is the caller's own fixture (a step function can request it like
        any other), threaded through to :func:`tests.helpers.signing.deployment_kek` —
        ``provision_signing_key`` refuses to mint without a configured KEK, and
        ``deployment_kek`` has no teardown of its own; it relies entirely on
        ``monkeypatch``'s automatic per-test undo, so the env stays scoped to this one
        scenario.
        """
        from tests.helpers.signing import deployment_kek, provision_key, signing_key_repo

        self.make_origin_publishable()

        repo = signing_key_repo(self, self._tenant_id)  # type: ignore[attr-defined]
        # A random suffix, not a deterministic one: this scenario runs once PER
        # TRANSPORT (a2a/mcp/rest) against the SAME tenant_id, and
        # _resolve_signing_provider (provider.py) caches resolved key material for
        # 60s keyed by (tenant_id, kid) — a deterministic kid would let one
        # transport's run resolve a STALE PEM cached under another transport's
        # identical kid, verifying against the wrong key entirely.
        kid = f"{self._tenant_id}-webhook-signing-{uuid4().hex[:8]}"  # type: ignore[attr-defined]
        with deployment_kek(monkeypatch):
            row = provision_key(repo, self._tenant_id, kid, alg=alg)  # type: ignore[attr-defined]
        self._commit_factory_data()  # type: ignore[attr-defined]
        return row.kid

    @realize_e2e(_fetch_served_jwks_over_http)
    def published_jwks(self) -> dict[str, Any]:
        """The JWKS document this tenant SERVES right now, fetched over the wire.

        The DOCUMENT, not the selector behind it. This used to call ``build_jwks``
        against ``publishable_at`` in process, which grades the publication SELECTOR:
        a route that never served, served the wrong tenant's key set, or 404'd would
        leave every caller green (salesagent-n78j0.1.4). ``/.well-known/jwks.json`` is
        addressed at this tenant's own published origin so the answer is the one a real
        receiver walking our discovery chain would get.
        """
        from src.core.agent_identity import JWKS_PATH

        response = self.get_rest_client().get(JWKS_PATH, headers=self.trust_root_request_headers())  # type: ignore[attr-defined]
        assert response.status_code == 200, (
            f"the app must publish a JWKS at {JWKS_PATH} for this tenant's origin; got HTTP "
            f"{response.status_code}: {response.text[:300]!r}"
        )
        return dict(response.json())

    @realize_e2e(_fetch_advertised_webhook_signing)
    def advertised_webhook_signing(self) -> Any:
        """This tenant's CURRENT ``webhook_signing`` posture — the same object ``_rfc9421_sender`` reads.

        Derived through production's own :func:`src.core.signing.posture.webhook_signing_posture`
        rather than re-typed here, so the Then step compares the wire against the SAME
        advertisement production would compute — a literal profile string would stay
        green while the two sides drifted apart.

        Over e2e the derivation has to happen where the KEY CAN BE OPENED — inside the
        server — so the e2e branch reads the served capabilities document instead
        (:func:`_fetch_advertised_webhook_signing`).
        """
        from src.core.signing.posture import webhook_signing_posture
        from tests.helpers.signing import signing_key_repo

        repo = signing_key_repo(self, self._tenant_id)  # type: ignore[attr-defined]
        origin = repo.canonical_origin()
        assert origin is not None, f"no tenant row for {self._tenant_id!r}"
        return webhook_signing_posture(repo, now=datetime.now(UTC), origin=origin)

    def get_service(self) -> WebhookDeliveryService:
        """Return a WebhookDeliveryService instance (cached per env)."""
        if self._service is None:
            self._service = WebhookDeliveryService()
        return self._service

    def get_breaker(self, **kwargs: Any) -> CircuitBreaker:
        """Return a fresh CircuitBreaker instance with the given params."""
        return CircuitBreaker(**kwargs)

    def set_http_response(self, status_code: int) -> None:
        """Answer every outbound webhook with *status_code*.

        ``mock["post"]`` IS the socket: :func:`tests.helpers.webhook_wire.stub_outbound_webhooks`
        calls it with ``(url, headers=..., content=...)`` for every delivery, so it
        carries real wire bytes while keeping ``call_args`` / ``call_count`` /
        ``side_effect`` available to the suites.
        """
        mock_response = MagicMock()
        mock_response.status_code = status_code
        self.mock["post"].return_value = mock_response  # type: ignore[attr-defined]

    def set_http_status(self, code: int, text: str = "") -> None:
        """Alias for set_http_response — BDD steps use this name consistently."""
        self.set_http_response(code)

    def set_http_sequence(self, responses: list[tuple[int, str]]) -> None:
        """Answer the Nth outbound webhook with the Nth ``(status, text)``."""
        mocks = []
        for code, text in responses:
            r = MagicMock()
            r.status_code = code
            r.text = text
            mocks.append(r)
        self.mock["post"].side_effect = mocks  # type: ignore[attr-defined]

    def set_url_invalid(self, error_msg: str = "Invalid URL") -> None:
        """Make send-time SSRF validation fail (skip delivery / record failure).

        Default harness config passes the SSRF mock so fixture hostnames do not
        NXDOMAIN-fail; scenarios that grade the outbound reject branch must call
        this hook explicitly.
        """
        self.mock["ssrf"].return_value = (False, error_msg)  # type: ignore[attr-defined]

    def set_url_valid(self) -> None:
        """Allow fixture hostnames through send-time SSRF (default harness path)."""
        self.mock["ssrf"].return_value = (True, "")  # type: ignore[attr-defined]

    def _configure_ssrf_default(self) -> None:
        """Default: allow fixture hostnames through send-time SSRF (DNS covered elsewhere).

        Scenarios that grade the reject branch call set_url_invalid(). Both
        CircuitBreakerEnv variants must call this from ``_configure_mocks``.
        """
        self.set_url_valid()

    def call_send(
        self,
        media_buy_id: str = "mb_001",
        tenant_id: str | None = None,
        principal_id: str | None = None,
        reporting_period_start: datetime | None = None,
        reporting_period_end: datetime | None = None,
        impressions: float = 1000.0,
        spend: float = 100.0,
        **extra: Any,
    ) -> bool:
        """Call service.send_delivery_webhook with sensible defaults."""
        self._commit_factory_data()  # type: ignore[attr-defined]
        service = self.get_service()
        return service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id=tenant_id or self._tenant_id,  # type: ignore[attr-defined]
            principal_id=principal_id or self._principal_id,  # type: ignore[attr-defined]
            reporting_period_start=reporting_period_start or datetime(2025, 1, 1, tzinfo=UTC),
            reporting_period_end=reporting_period_end or datetime(2025, 12, 31, tzinfo=UTC),
            impressions=impressions,
            spend=spend,
            **extra,
        )

    def call_deliver(
        self,
        media_buy_id: str = "mb_001",
        notification_type: str | None = None,
        **kwargs: Any,
    ) -> tuple[bool, dict[str, Any]]:
        """Deliver via the production WebhookDeliveryService.

        BDD scenarios that exercise webhook authentication (HMAC, bearer) and
        retry/backoff timing must use the real production code path —
        ``WebhookDeliveryService.send_delivery_webhook`` — because
        ``deliver_webhook_with_retry`` (the legacy path used by
        :class:`tests.harness.delivery_webhook.WebhookEnv`) emits a different
        signature header name and has different retry timing.

        ``notification_type`` is translated to the production flags:

        * ``"final"``    -> ``is_final=True``
        * ``"adjusted"`` -> ``is_adjusted=True``
        * any other value (``None``, ``"scheduled"``, ``"delayed"``) leaves
          both flags False, which yields a ``"scheduled"`` payload from
          production. ``"delayed"`` is a spec-defined value that production
          does not yet emit; tests that assert on it document a production
          gap rather than a harness gap.

        Returns ``(success, info_dict)`` to keep the call shape compatible
        with :meth:`WebhookMixin.call_deliver`.
        """
        is_final = notification_type == "final"
        is_adjusted = notification_type == "adjusted"
        # Set a non-zero interval so production includes ``next_expected_at``
        # in the payload for non-final notifications. The exact value does not
        # matter — assertions check presence, not the value.
        next_expected_interval_seconds = None if is_final else 86400.0

        success = self.call_send(
            media_buy_id=media_buy_id,
            is_final=is_final,
            is_adjusted=is_adjusted,
            next_expected_interval_seconds=next_expected_interval_seconds,
            **kwargs,
        )
        return success, {"success": success}

    def call_impl(self, **kwargs: Any) -> bool:
        """Alias for call_send to satisfy BaseTestEnv interface."""
        return self.call_send(**kwargs)

    def get_breaker_state(self) -> str:
        """Return circuit breaker state for this tenant's endpoints.

        Scans all circuit breakers keyed to this tenant and returns the
        worst observed state: 'open' > 'half_open' > 'closed'.

        Returns:
            State string: 'closed', 'open', or 'half_open'
        """
        from src.services.webhook_delivery_service import CircuitState

        service = self.get_service()
        tenant_prefix = f"{self._tenant_id}:"  # type: ignore[attr-defined]
        worst = CircuitState.CLOSED
        for key, cb in service._circuit_breakers.items():
            if key.startswith(tenant_prefix):
                if cb.state == CircuitState.OPEN:
                    return CircuitState.OPEN.value
                if cb.state == CircuitState.HALF_OPEN:
                    worst = CircuitState.HALF_OPEN
        return worst.value


class ProductMixin:
    """Shared fluent API for _get_products_impl testing.

    Requires concrete class to define EXTERNAL_PATCHES with these keys:
        "policy_service", "dynamic_variants", "ranking_factory",
        "dynamic_pricing", "resolve_property_list"

    And ASYNC_PATCHES containing at least:
        {"dynamic_variants", "resolve_property_list"}

    Fluent API:
        set_policy_approved()            -- policy check returns approved
        set_policy_blocked(reason)       -- policy check returns blocked
        set_dynamic_variants(variants)   -- configure dynamic variant generation
        set_property_list(ids)           -- configure property list resolver
        set_ranking_disabled()           -- disable AI ranking
        call_impl(brief, **kw)           -- call _get_products_impl
    """

    def set_policy_approved(self) -> None:
        """Configure PolicyCheckService to approve the brief.

        Note: Policy checks are only invoked when the tenant dict has
        ``advertising_policy.enabled = True`` AND ``gemini_api_key`` set.
        By default the harness identity has neither, so this is a no-op
        unless the test explicitly configures the tenant.
        """
        from unittest.mock import AsyncMock

        mock_result = MagicMock(status="approved", reason=None, restrictions=[])
        mock_instance = MagicMock()
        mock_instance.check_brief_compliance = AsyncMock(return_value=mock_result)
        self.mock["policy_service"].return_value = mock_instance  # type: ignore[attr-defined]

    def set_policy_blocked(self, reason: str = "Policy violation") -> None:
        """Configure PolicyCheckService to block the brief."""
        from unittest.mock import AsyncMock

        from src.services.policy_check_service import PolicyStatus

        mock_result = MagicMock(status=PolicyStatus.BLOCKED, reason=reason, restrictions=[])
        mock_instance = MagicMock()
        mock_instance.check_brief_compliance = AsyncMock(return_value=mock_result)
        self.mock["policy_service"].return_value = mock_instance  # type: ignore[attr-defined]

    def set_dynamic_variants(self, variants: list[Any] | None = None) -> None:
        """Configure generate_variants_for_brief to return specific variants.

        Args:
            variants: List of Product model instances to return. Defaults to [].
        """
        self.mock["dynamic_variants"].return_value = variants or []  # type: ignore[attr-defined]

    def set_property_list(self, property_ids: list[str] | None = None) -> None:
        """Configure resolve_property_list to return specific property IDs.

        Args:
            property_ids: List of property identifier strings. Defaults to [].
        """
        self.mock["resolve_property_list"].return_value = property_ids or []  # type: ignore[attr-defined]

    def set_ranking_disabled(self) -> None:
        """Disable AI ranking by making the factory report AI as not enabled."""
        mock_factory = MagicMock()
        mock_factory.is_ai_enabled.return_value = False
        self.mock["ranking_factory"].return_value = mock_factory  # type: ignore[attr-defined]

    def _configure_product_mocks(self) -> None:
        """Wire default happy-path mocks for product testing.

        Call from _configure_mocks() in concrete classes.

        Defaults:
        - PolicyCheckService: not invoked (no gemini_api_key in tenant dict)
        - Dynamic variants: returns [] (already AsyncMock via ASYNC_PATCHES)
        - DynamicPricingService: pass-through in unit mode, real in integration mode
        - Property list resolver: returns [] (already AsyncMock via ASYNC_PATCHES)
        - Ranking factory: AI not enabled
        """
        # Dynamic variants: returns empty list (AsyncMock from ASYNC_PATCHES)
        self.mock["dynamic_variants"].return_value = []  # type: ignore[attr-defined]

        # DynamicPricingService: configure pass-through mock in unit mode only.
        # In integration mode (ProductEnv from product.py), dynamic_pricing is NOT
        # in EXTERNAL_PATCHES, so self.mock won't have it — runs against real DB.
        if "dynamic_pricing" in self.mock:  # type: ignore[attr-defined]
            mock_pricing_instance = MagicMock()
            mock_pricing_instance.enrich_products_with_pricing.side_effect = lambda products, **kw: products
            self.mock["dynamic_pricing"].return_value = mock_pricing_instance  # type: ignore[attr-defined]

        # Ranking factory: AI not enabled
        self.set_ranking_disabled()

        # Property list resolver: returns [] (AsyncMock from ASYNC_PATCHES)
        self.mock["resolve_property_list"].return_value = []  # type: ignore[attr-defined]

    async def call_impl(  # type: ignore[override]
        self,
        brief: str = "test brief",
        brand: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        property_list: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        **extra: Any,
    ) -> GetProductsResponse:
        """Call _get_products_impl with the given parameters.

        Args:
            brief: Search brief text.
            brand: Brand reference dict (defaults to {"domain": "test.com"}).
            filters: ProductFilters dict.
            property_list: PropertyListReference dict.
            context: ContextObject dict.
            **extra: Additional kwargs forwarded to request construction.

        Returns:
            GetProductsResponse from the impl function.
        """
        self._commit_factory_data()  # type: ignore[attr-defined]

        # Pop identity — injected by call_via for transport dispatch
        # but not a GetProductsRequest field.
        identity = extra.pop("identity", None) or self.identity  # type: ignore[attr-defined]

        if brand is None:
            brand = {"domain": "test.com"}

        req = GetProductsRequestGenerated(
            brief=brief,
            brand=brand,
            filters=filters,
            property_list=property_list,
            context=context,
            **extra,
        )
        return await _get_products_impl(req, identity)
