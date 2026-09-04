"""Domain mixins — shared fluent API for integration and unit test environments.

Each mixin provides the domain-specific helper methods (set_*, call_*, get_*)
that are identical across integration and unit variants. Concrete env classes
inherit from both a base (BaseTestEnv or IntegrationEnv) and a mixin.

Mixins don't define ``__init__`` — concrete classes set up required state.
Mixins may call ``self._commit_factory_data()`` which is a no-op in unit mode.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypedDict
from unittest.mock import MagicMock, patch

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
    CircuitState,
    WebhookDeliveryService,
)
from tests.harness._realize import e2e_unsupported, realize_e2e
from tests.helpers.egress_hatches import egress_hatch_env
from tests.helpers.local_http_origin import (
    LocalOrigin,
    OriginRequest,
    OriginResponse,
    responds,
    run_local_origin,
)
from tests.helpers.test_tls_material import load_gen_test_tls, server_ssl_context


def _e2e_capture_url(env: Any) -> str:
    """E2E realization of :attr:`LocalOriginMixin.webhook_url` (#2098).

    In process the endpoint is a loopback origin on the runner. The Docker server
    cannot reach that, so under e2e the endpoint is the compose stack's
    long-lived ``webhook-capture`` service, addressed through the shared TLS
    front exactly as a production receiver would be.
    """
    from tests.e2e._webhook_capture import delivery_url_for

    return delivery_url_for(env._capture_key)


def _e2e_reject_next(env: Any, count: int, status: int = 418) -> None:
    """E2E realization of :meth:`LocalOriginMixin.reject_next`.

    Programs the capture service's per-key rejection run over its plain-HTTP
    control plane, the same side of the house as reading captures back.
    """
    from tests.e2e._webhook_capture import program_rejections

    program_rejections(env._capture_key, status=status, count=count)


# Long enough to outlast any scenario's retry schedule (3 attempts x a handful of
# deliveries), short enough to stay a number rather than a promise.
_E2E_UNBOUNDED_RUN = 1000


def _e2e_set_http_status(env: Any, code: int, text: str = "") -> None:
    """E2E realization of :meth:`LocalOriginMixin.set_http_status`.

    The capture service's control plane speaks a rejection RUN -- ``status`` for
    the next ``count`` deliveries, then 200 again -- so the two answers this
    method's callers actually ask for are both expressible:

    * a healthy endpoint (``2xx``) is a run of length ZERO;
    * a failing endpoint is a run long enough to outlast the scenario. The
      in-process meaning is "every attempt, forever"; over e2e that is a finite
      but generous run, because the control plane counts.

    ``text`` is dropped: the capture service answers a status, not a body, and no
    scenario asserts on that body over e2e.
    """
    from tests.e2e._webhook_capture import program_rejections

    program_rejections(env._capture_key, status=code, count=0 if 200 <= code < 300 else _E2E_UNBOUNDED_RUN)


def _e2e_set_http_sequence(env: Any, responses: list) -> None:
    """E2E realization of :meth:`LocalOriginMixin.set_http_sequence`.

    Only the shape the control plane can express: ``N`` copies of one failing
    status followed by a success, which is what every scenario using this step
    asks for ("fails on first attempt but succeeds on second"). A sequence with
    two DIFFERENT failure statuses, or one ending in a failure, is a genuinely
    different program and raises rather than being approximated -- an approximated
    endpoint would grade a scenario nobody wrote.
    """
    from tests.e2e._webhook_capture import program_rejections

    statuses = [item[0] if isinstance(item, tuple) else None for item in responses]
    if None in statuses:
        raise NotImplementedError(
            "set_http_sequence over e2e_rest accepts (status, text) pairs only; a full "
            "OriginResponse (hangs_up / malformed body / delay) has no control-plane "
            "expression on the capture service. See prebid/salesagent#2098."
        )
    failing = [code for code in statuses if not 200 <= code < 300]
    if len(set(failing)) > 1:
        raise NotImplementedError(
            f"set_http_sequence over e2e_rest expresses ONE failing status repeated, then "
            f"success; this sequence mixes {sorted(set(failing))}. See prebid/salesagent#2098."
        )
    if failing and not 200 <= statuses[-1] < 300:
        raise NotImplementedError(
            "set_http_sequence over e2e_rest expresses a run of failures FOLLOWED BY success; "
            "this sequence ends on a failure, which the control plane cannot hold open. "
            "See prebid/salesagent#2098."
        )
    program_rejections(env._capture_key, status=failing[0] if failing else 200, count=len(failing))


class _CapturedDelivery:
    """One delivery the compose stack's capture service received.

    The capture service stores the parsed JSON PAYLOAD and nothing else -- no
    headers, no raw bytes -- so this exposes ``.json()`` and refuses the rest BY
    NAME rather than returning an empty dict that a signature assertion would
    read as "no signature header". An assertion that cannot run over e2e must say
    so; one that quietly passes on absent evidence is worse than one that errors.
    """

    __slots__ = ("_payload",)

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    @property
    def headers(self) -> Any:
        raise AttributeError(
            "the webhook-capture service records the delivery PAYLOAD only, not its headers, "
            "so header assertions (signature, auth) cannot run over e2e_rest. Grade them "
            "in-process, or give the capture service header storage (prebid/salesagent#2098)."
        )

    @property
    def body(self) -> bytes:
        raise AttributeError(
            "the webhook-capture service records the PARSED payload, not the raw bytes on the "
            "wire, so byte-equality assertions (HMAC over the exact body) cannot run over "
            "e2e_rest. Grade them in-process (prebid/salesagent#2098)."
        )


def _e2e_delivered_requests(env: Any) -> list[_CapturedDelivery]:
    """E2E realization of :attr:`LocalOriginMixin.delivered_requests`."""
    from tests.e2e._webhook_capture import ReceivedView

    return [_CapturedDelivery(payload) for payload in ReceivedView(env._capture_key)]


def _e2e_last_delivery(env: Any) -> _CapturedDelivery:
    """E2E realization of :attr:`LocalOriginMixin.last_delivery`."""
    delivered = _e2e_delivered_requests(env)
    if not delivered:
        raise AssertionError("no webhook delivery reached the capture service")
    return delivered[-1]


def _e2e_delivery_attempts(env: Any) -> int:
    """E2E realization of :attr:`LocalOriginMixin.delivery_attempts`.

    Counts what the capture service actually received. A rejected delivery is
    still recorded, so this counts ATTEMPTS the server made — which is what
    "and no further attempt arrives" needs in order to mean anything.
    """
    from tests.e2e._webhook_capture import ReceivedView

    return len(ReceivedView(env._capture_key))


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
        return UpdateMediaBuySuccess.carrier(
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
        # Sentinel distinguishes "not provided" from "explicitly None".
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        raw_identity = extra.pop("identity", NO_IDENTITY_OVERRIDE)
        identity = self.identity if raw_identity is NO_IDENTITY_OVERRIDE else raw_identity  # type: ignore[attr-defined]

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


class LocalOriginMixin:
    """A REAL local HTTP origin, shared by every webhook-delivery env.

    The webhook envs used to patch the transport itself — ``requests.post`` in
    one env, ``httpx.Client`` in another — which made the tests a description of
    *how* delivery is currently implemented rather than of what delivery does.
    Migrating production onto ``src.core.security.outbound_http`` deletes both of
    those symbols, so every one of those tests would have had to be rewritten as
    part of the migration, which is exactly when a rewrite is least trustworthy.

    An origin that actually serves HTTP is neutral to that choice: it answered
    ``requests.post`` before the migration and answers
    ``src.core.security.outbound_http.send`` now, and the assertions — how many
    requests arrived, with which headers, carrying which bytes — mean the same
    thing under both. That is why this landed before the migration rather than
    with it.

    The origin serves real TLS off the generated CA/leaf (the primitive from
    #1757, reused here — never a second mechanism), so it no longer needs the
    scheme hatch, which the same issue deleted entirely; the private-range
    hatch stays open for the duration of the env because the origin
    necessarily listens on loopback, which the seam refuses by default —
    opening it here is the same statement the seam's own integration tests
    make with ``set_flags(private=True)``. ``SSL_CERT_FILE`` is scoped to the
    origin's lifetime only (patched and restored alongside it, not left
    ambient for the rest of the env), pointed at the COMBINED CA bundle (system
    + private) so any other outbound work inside the same scenario keeps
    trusting real public roots too.
    """

    _origin: LocalOrigin

    @property
    def origin(self) -> LocalOrigin:
        """The in-process local origin -- IN-PROCESS ONLY, and it says so.

        This was a bare class annotation, so every read under e2e raised an
        anonymous ``AttributeError: 'CircuitBreakerEnv' object has no attribute
        'origin'`` from wherever it happened to be reached. That is how the
        cascade in #1802 happened: one step read it, the env then failed to
        unbind the factory session, and 5 failures became 443.

        A named failure instead. It does not make the attribute REACHABLE over
        e2e -- programming the capture service's responses is
        prebid/salesagent#2098's work -- but it removes the anonymous form, and
        it tells the reader which accessor answers the same question on both
        transports.
        """
        if self.is_e2e:  # type: ignore[attr-defined]
            raise AttributeError(
                "env.origin is the IN-PROCESS local origin and does not exist under e2e_rest, "
                "where the endpoint is the compose stack's webhook-capture service. Read through "
                "a realize-aware accessor instead (delivery_attempts / delivered_requests / "
                "last_delivery), or program the endpoint through reject_next. Making the "
                "response-programming setters work over e2e is prebid/salesagent#2098."
            )
        return self._origin

    # -- Lifecycle ----------------------------------------------------------

    if TYPE_CHECKING:
        # Declared, not implemented. This mixin is only ever composed with
        # BaseTestEnv, which owns the cleanup registry; the requirement used to
        # be spelled as a bare ``_patchers: list`` annotation and this is the
        # same statement for the method that replaced it.
        def _guard(self, label: str, cleanup: Callable[[], None]) -> None: ...

    def _enter_pre(self) -> None:
        """Acquire the TLS origin BEFORE the base binds the DB and configures mocks.

        Pre, not post: ``CircuitBreakerEnv._configure_mocks`` programs
        ``self.origin``, so the origin has to exist by then.

        Every resource is registered with ``_guard`` on the line it is acquired,
        so a failure part-way through releases exactly what was started —
        no defensive ``getattr`` teardown, and nothing survives a failed enter.
        Release is LIFO, which reproduces the old hatches -> origin -> ssl order.
        """
        super()._enter_pre()  # type: ignore[misc]
        if self.is_e2e:  # type: ignore[attr-defined]
            # No local origin under e2e: it would listen on the runner's loopback,
            # which the Docker server cannot reach — that unreachability is the
            # whole defect #2098 exists to fix. The endpoint is the compose
            # stack's webhook-capture service instead, addressed by a fresh
            # per-scenario key so concurrent scenarios never share captures.
            from tests.e2e._webhook_capture import register_capture_key

            self._capture_key, _ = register_capture_key()
            return

        gen_test_tls = load_gen_test_tls()
        gen_test_tls.ensure_test_tls()
        self._ssl_cert_file = patch.dict(os.environ, {"SSL_CERT_FILE": str(gen_test_tls.COMBINED_CERT)})
        self._ssl_cert_file.start()
        self._guard("ssl_cert_file", self._ssl_cert_file.stop)

        self._origin_ctx = run_local_origin(ssl_context=server_ssl_context(gen_test_tls))
        self._origin = self._origin_ctx.__enter__()
        self._guard("local_origin", self._exit_origin)

        self._egress_hatches = patch.dict(os.environ, egress_hatch_env(private=True))
        self._egress_hatches.start()
        self._guard("egress_hatches", self._egress_hatches.stop)

    def _exit_origin(self) -> None:
        """Close the origin context, discarding ``__exit__``'s suppression verdict.

        A cleanup returns nothing: the registry is releasing resources, not
        handling the exception, and letting a context manager's bool leak into
        that position would silently mean "suppressed".
        """
        self._origin_ctx.__exit__(None, None, None)

    # -- The endpoint under test -------------------------------------------

    @property
    @realize_e2e(_e2e_capture_url)
    def webhook_url(self) -> str:
        """The endpoint every webhook test targets.

        In process, the running local origin. Over e2e, the compose stack's
        webhook-capture service — the only endpoint the Docker server can reach.
        """
        return f"{self.origin.base_url}/webhook"

    # -- Programming the endpoint to FAIL ------------------------------------

    @realize_e2e(_e2e_reject_next)
    def reject_next(self, count: int, status: int = 418) -> None:
        """Answer the next ``count`` deliveries with ``status``, then 200 again.

        The one way a scenario makes deliveries fail for real, on every
        transport. In process the origin answers a sequence whose last entry
        repeats; over e2e the capture service is programmed with the same run.

        ``status`` defaults to a terminal, non-retryable 4xx on purpose. A
        retryable 5xx (``_RETRYABLE_STATUSES``, src/core/security/egress/attempts.py)
        multiplies the request count by the attempt schedule and adds seconds of
        real backoff per failure, so "the endpoint received exactly 5 attempts"
        would stop being countable.
        """
        self.set_http_sequence([(status, "")] * count + [(200, "")])

    # -- Programming the endpoint ------------------------------------------

    @realize_e2e(_e2e_set_http_status)
    def set_http_status(self, code: int, text: str = "") -> None:
        """Answer every attempt with ``code`` and ``text`` as the body."""
        self.origin.respond_with(code, body=(text or f"Status {code}").encode())

    @realize_e2e(_e2e_set_http_sequence)
    def set_http_sequence(self, responses: list[tuple[int, str] | OriginResponse]) -> None:
        """Answer each attempt with the next entry; the last entry repeats.

        An entry is either ``(status_code, text)`` or a full ``OriginResponse``
        (``hangs_up()``, ``sends_malformed_body()``, ``responds(..., delay_seconds=...)``)
        so a sequence can express recovery from a genuine transport fault, not
        just from an unhappy status code.
        """
        self.origin.respond_in_sequence(
            [
                item if isinstance(item, OriginResponse) else responds(item[0], body=item[1].encode())
                for item in responses
            ]
        )

    def set_http_error(self) -> None:
        """Accept every attempt and then drop the connection without answering."""
        self.origin.close_without_responding()

    # -- Observing what actually arrived ------------------------------------

    @property
    @realize_e2e(_e2e_delivery_attempts)
    def delivery_attempts(self) -> int:
        """How many requests the endpoint actually received.

        Over e2e this is a fresh readback of the capture service on every access,
        so a count that has stopped growing is a live observation rather than a
        cached one.
        """
        return self.origin.hits

    @property
    @realize_e2e(_e2e_delivered_requests)
    def delivered_requests(self) -> list[OriginRequest]:
        """Every request the endpoint received, oldest first."""
        return self.origin.requests

    @property
    @realize_e2e(_e2e_last_delivery)
    def last_delivery(self) -> OriginRequest:
        """The most recent request the endpoint received."""
        return self.origin.last_request


class WebhookOutcomeRowsMixin:
    """Read back the ``webhook_delivery_log`` rows a sender concluded with.

    Shared by the two senders' envs (``CircuitBreakerEnv``,
    ``ProtocolWebhookEnv``) because "what did production write down about this
    delivery" is one question, and the whole point of the lane in #1802 is
    that both senders must answer it through ONE recorder. A per-env copy of
    this read is how the two answers would be allowed to drift.

    The read goes through :class:`~src.core.database.repositories.delivery.DeliveryRepository`
    rather than a raw ``select()``: the repository is the tenant-scoped data
    access layer, and grading the recorder against a query the grader wrote
    itself would only prove the grader and the writer agree about columns.
    """

    def make_media_buy(self, **overrides: Any) -> Any:
        """Create the ``MediaBuy`` row the delivery log's foreign key requires.

        ``webhook_delivery_log.media_buy_id`` references ``media_buys``, so a
        delivery-log assertion against a media buy that does not exist would
        grade nothing: the writers swallow the integrity error and log it,
        leaving zero rows and no exception.
        """
        from tests.factories import MediaBuyFactory

        tenant, principal = self.setup_default_data()  # type: ignore[attr-defined]
        return MediaBuyFactory(tenant=tenant, principal=principal, **overrides)

    def recorded_outcomes(
        self,
        media_buy_id: str,
        *,
        task_type: str,
        status: str | None = None,
    ) -> list[Any]:
        """The delivery-log rows a sender recorded for ``media_buy_id``.

        ``task_type`` is REQUIRED, not optional. ``media_buy_delivery.py``
        persists ``task_type="delivery_poll"`` counter rows that are also
        ``status="success"`` on the same ``media_buy_id``; a read that did not
        filter would let a grader for "the sender recorded a success" pass on a
        row no sender wrote.

        The senders commit through their own ``get_db_session()``, so the
        env-bound session must drop what it has cached or it answers from its
        identity map.
        """
        from src.core.database.repositories.delivery import DeliveryRepository

        session = self.get_session()  # type: ignore[attr-defined]
        session.expire_all()
        repo = DeliveryRepository(session, self._tenant_id)  # type: ignore[attr-defined]
        return repo.get_logs_by_webhook_id(media_buy_id, task_type=task_type, status=status)


class WebhookMixin(LocalOriginMixin):
    """Shared fluent API for webhook delivery testing."""

    _seq_counter: dict[str, int]

    def call_deliver(
        self,
        webhook_url: str | None = None,
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

        ``webhook_url`` defaults to the running local origin, so a test that
        does not care where delivery goes gets a destination that really
        answers. Passing an explicit URL is how a test targets somewhere the
        origin is NOT — e.g. a cloud-metadata address, which production's own
        URL policy refuses before any request leaves.
        """
        self._commit_factory_data()  # type: ignore[attr-defined]
        webhook_url = webhook_url if webhook_url is not None else self.webhook_url
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
            authentication_scheme="HMAC-SHA256" if signing_secret else None,
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


class CircuitBreakerMixin(LocalOriginMixin):
    """Shared fluent API for circuit breaker / webhook delivery service testing."""

    _service: WebhookDeliveryService | None

    def get_service(self) -> WebhookDeliveryService:
        """Return a WebhookDeliveryService instance (cached per env)."""
        if self._service is None:
            self._service = WebhookDeliveryService()
        return self._service

    def get_breaker(self, **kwargs: Any) -> CircuitBreaker:
        """Return a fresh CircuitBreaker instance with the given params."""
        return CircuitBreaker(**kwargs)

    def set_http_response(self, status_code: int) -> None:
        """Answer every attempt with the given status code."""
        self.set_http_status(status_code)

    def endpoint_key(self, tenant_id: str | None = None) -> str:
        """The per-endpoint circuit-breaker key production derives for this origin.

        Production keys breakers on ``f"{tenant_id}:{config.url}"``. The origin's
        port is only known at runtime, so a test cannot spell that key as a
        literal; deriving it here keeps the one place it is built.
        """
        return f"{tenant_id or self._tenant_id}:{self.webhook_url}"  # type: ignore[attr-defined]

    # -- Circuit-breaker seam ------------------------------------------------
    #
    # The harness owns exactly ONE private-state seam on the breaker, used for
    # SEEDING and for driving the OPEN->HALF_OPEN transition. Production exposes
    # a public READER (``get_circuit_breaker_state``) but no public setter, and
    # a test must be able to start from a given breaker state without spending
    # real failures to get there — so the write side lives here, in the harness,
    # and NOWHERE else. Every state READ that an assertion depends on goes
    # through :meth:`breaker_snapshot`, i.e. through the production public API.
    #
    # Enforced by tests/unit/test_architecture_bdd_wire_discipline.py's
    # ``test_no_private_circuit_breaker_state_in_steps`` (permanently empty
    # allowlist): no step under tests/bdd/steps/ may touch ``_circuit_breakers``.

    def _breaker_for(self, endpoint_key: str) -> CircuitBreaker:
        """The breaker production would use for *endpoint_key*, creating it if absent.

        THE single private-state touch. Everything else in this seam goes
        through it, so there is one line to audit rather than six.
        """
        service = self.get_service()
        if endpoint_key not in service._circuit_breakers:
            service._circuit_breakers[endpoint_key] = CircuitBreaker()
        return service._circuit_breakers[endpoint_key]

    def seed_breaker_failures(self, endpoint_key: str, n: int) -> None:
        """Record *n* consecutive failures, as production's own arithmetic would.

        Uses ``record_failure`` rather than assigning ``failure_count``: the
        breaker decides what n failures MEAN (including whether they open it),
        and a test that set the count directly would be asserting against its
        own arithmetic instead of production's.
        """
        breaker = self._breaker_for(endpoint_key)
        for _ in range(n):
            breaker.record_failure()

    def set_breaker_state(self, endpoint_key: str, state: str) -> None:
        """Force the breaker into *state* ('OPEN' | 'HALF_OPEN' | 'CLOSED').

        A Given that names a state is describing where the scenario STARTS, not
        something production just did — so this is a seed, not an assertion, and
        the state it sets is read back through :meth:`breaker_snapshot`.
        """
        self._breaker_for(endpoint_key).state = CircuitState[state.upper()]

    def elapse_breaker_timeout(self, endpoint_key: str, seconds: int = 61) -> None:
        """Age the last failure past the breaker's recovery timeout.

        Moves the CLOCK rather than the state: production decides what an
        elapsed timeout means (OPEN -> HALF_OPEN on the next ``can_attempt``),
        and a test that set HALF_OPEN directly would skip the transition it
        means to exercise.
        """
        self._breaker_for(endpoint_key).last_failure_time = datetime.now(UTC) - timedelta(seconds=seconds)

    def drive_breaker_transition(self, endpoint_key: str) -> None:
        """Drive the breaker's OPEN -> HALF_OPEN transition. Returns nothing.

        ``can_attempt()`` is not a pure read: it is where an OPEN breaker whose
        timeout has elapsed becomes HALF_OPEN, so a scenario that says "the
        timeout elapsed, now evaluate" must call it to get the transition. It is
        called here for that side effect alone.

        The verdict is deliberately DISCARDED, not returned. Asserting on it
        would grade the gate's own opinion of itself, which is unfalsifiable
        across a process boundary — under e2e_rest the breaker being consulted
        is the test process's, not the server's.

        Observe the CONSEQUENCE instead. :meth:`breaker_snapshot` is the better
        state read, because it goes through the production public API rather
        than the private dict — but it resolves the same test-process breaker,
        so it does not escape that boundary either. The only observation that
        does is what the endpoint actually saw: an admitted probe, a delivery
        count.
        """
        self._breaker_for(endpoint_key).can_attempt()

    def breaker_snapshot(self, endpoint_url: str | None = None) -> tuple[CircuitState, int]:
        """(state, failure_count) for *endpoint_url*, via the PRODUCTION public API.

        The ONLY read path. Delegates to
        :meth:`WebhookDeliveryService.get_circuit_breaker_state` so that what a
        test observes is what production exposes — a read of the private dict
        could report state production has no way to surface.
        """
        return self.get_service().get_circuit_breaker_state(endpoint_url or self.webhook_url)  # type: ignore[attr-defined]

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
        """Return the circuit-breaker state this env's tenant is in.

        Reads through the production public API only, which shapes what it can
        say: ``has_open_circuit_breaker`` answers "is ANY breaker under this
        tenant OPEN", and ``get_circuit_breaker_state`` answers per-URL. So this
        returns OPEN when any of the tenant's breakers is open, and otherwise the
        state of THIS env's own ``webhook_url``.

        That is narrower than the previous private-dict scan, which returned the
        worst state across every key under the tenant prefix: a HALF_OPEN breaker
        on some OTHER url now reads 'closed' here. Inert for every current caller
        (these envs drive a single origin), and stated rather than left for a
        reader to discover — production exposes no public "worst state for a
        tenant" reader, and inventing one to preserve a test-only scan would put
        test convenience into production.

        Callers: then_circuit_breaker_state, then_circuit_breaker_transition,
        then_circuit_healthy.

        Returns:
            State string: 'closed', 'open', or 'half_open'
        """
        service = self.get_service()
        if service.has_open_circuit_breaker(self._tenant_id):  # type: ignore[attr-defined]
            return CircuitState.OPEN.value
        state, _ = self.breaker_snapshot()
        return state.value

    @realize_e2e(
        e2e_unsupported(
            "the seam's BR-RULE-029 retry-schedule sleep count is process-local "
            "(env.mock['sleep']), not observable across the Docker HTTP boundary"
        )
    )
    def assert_no_retry_schedule_entered(self) -> None:
        """Prove a refusal happened pre-flight, before any retry/backoff attempt.

        In-process only — declared e2e-unsupported (see the decorator).
        """
        backoff_waits = self.mock["sleep"].call_count  # type: ignore[attr-defined]
        assert backoff_waits == 0, (
            f"Expected the refusal to happen before any connection attempt, but the seam's retry "
            f"schedule was entered {backoff_waits} time(s) — the destination was dialled, not refused"
        )

    @realize_e2e(
        e2e_unsupported(
            "get_service() constructs a fresh in-process WebhookDeliveryService under e2e_rest, "
            "disconnected from the live server's real circuit-breaker state — no wire surface"
        )
    )
    def assert_circuit_breaker_failure_recorded(self, endpoint_key: str) -> None:
        """Prove the circuit breaker recorded a failure for *endpoint_key*.

        In-process only — declared e2e-unsupported (see the decorator).

        Reads through the production public getter, which returns ``(CLOSED, 0)``
        for an endpoint it has NO breaker for. So "no breaker was ever created"
        and "a breaker exists with zero failures" are indistinguishable here,
        where the previous private-dict read could tell them apart. The assertion
        below is unaffected — it demands ``>= 1``, which both of those fail — but
        the diagnostic can no longer say which one happened.
        """
        _state, failure_count = self.breaker_snapshot(endpoint_key)
        assert failure_count >= 1, (
            f"Expected failure_count >= 1 after SSRF rejection for {endpoint_key!r}, got {failure_count} — "
            "the refusal did not reach the breaker, so a destination we cannot deliver to still looks healthy"
        )


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


class TMPSyncDelivery(TypedDict):
    """One request a stub TMP provider received.

    Typed rather than described in prose because a prose key list has no consumer:
    the mixin's docstring said the record was ``{"path", "body"}`` while
    ``record()`` returned four keys and the Then-steps read all four — including
    ``headers``, the only credential-bearing observable in the feature (#1197
    review). A renamed or dropped key is now a type error at the reader.
    """

    method: str
    path: str
    #: Lower-cased header names — the wire is case-insensitive, so the steps assert
    #: on one spelling.
    headers: dict[str, str]
    body: Any


class TMPSyncMixin:
    """The TMP package-sync observable, owned by the env instead of by each test.

    Package sync is transport-blind buyer-triggered behavior: *a buyer creates or
    updates a media buy, and every registered active/draining provider holds
    current package data*. Before this mixin, that observable had no seam, so
    each tier invented one — a process-wide ``httpx.Client`` patch plus a
    ``threading.Thread.start`` patch in the integration file, a second
    independent seed→dispatch→collect→assert implementation over a socket in the
    e2e file, and a third file asserting the thread was *constructed*. Three
    incompatible observables, two grading implementations that disagreed on which
    transports were covered (#1197 review).

    One implementation serves every transport because nothing here depends on
    which process the sync thread runs in:

    * **The collector is a real HTTP receiver**, recording the whole request as a
      :class:`TMPSyncDelivery` (method, path, headers, body). No client stubbing,
      so the URL construction, the auth header and the JSON body are production's,
      and the arrival IS the observable whether the thread ran in this process
      (a2a/mcp/rest) or in the server container (e2e_rest).
    * **Completion is the production registry**, via
      :func:`src.services.tmp_provider_sync.join_active_syncs` in-process and by
      polling the collector out-of-process — never by patching a stdlib
      constructor.

    Envs mixing this in get the seam whether or not a scenario uses it: with no
    provider registered the sync short-circuits, and ``__exit__`` still drains
    the threads, so no unrelated media-buy scenario leaves an unjoined daemon
    opening DB sessions after its own teardown.
    """

    # Set on first register_tmp_provider(); None means "this scenario never
    # registered a provider", which is the no-op case.
    _tmp_collector: dict[str, Any] | None = None
    _tmp_collector_ctx: Any = None

    #: The public host planted on the tenant so ``_resolve_seller_agent_url``
    #: yields a spec-valid https ``seller_agent.agent_url``. A constant so the
    #: Then-step can assert the exact URL production emitted.
    TMP_SELLER_AGENT_HOST = "tmp-sync-seller.publisher.example.com"

    @property
    def tmp_seller_agent_url(self) -> str:
        """The ``seller_agent.agent_url`` production must put on every package."""
        return f"https://{self.TMP_SELLER_AGENT_HOST}/mcp"

    def register_tmp_provider(self, *, auth_credentials: str | None = None, **fields: Any) -> str:
        """Register one active TMP provider pointed at this env's collector.

        Starts the collector on first call and replaces any provider rows the
        tenant already had, so the fan-out reaches exactly this endpoint.
        Returns the registered endpoint.

        ``auth_credentials`` is a first-class parameter because the credential is
        half of the cross-transport claim: a scenario registers a credentialed
        provider and asserts the ``Authorization`` header arrives, or registers an
        uncredentialed one and asserts it does not. It is written through the
        model's encrypting property, so the row is what production would store.
        """
        from tests.factories import plant_seller_agent_host, replace_tmp_providers

        collector = self._ensure_tmp_collector()
        tenant_id = self._tenant_id  # type: ignore[attr-defined]

        plant_seller_agent_host(self, tenant_id, self.TMP_SELLER_AGENT_HOST)
        fields.setdefault("name", "Package Sync Collector")
        fields.setdefault("endpoint", collector["endpoint"])
        fields.setdefault("timeout_ms", 2000)
        if auth_credentials is not None:
            # `auth_credentials` is the encrypting property; the factory writes
            # columns, so set it after construction to exercise the real path.
            fields.setdefault("auth_type", "bearer")
        provider = replace_tmp_providers(self, tenant_id, **fields)
        if auth_credentials is not None:
            provider.auth_credentials = auth_credentials
            self.get_session().commit()  # type: ignore[attr-defined]
        return str(fields["endpoint"])

    def tmp_sync_deliveries(self) -> list[TMPSyncDelivery]:
        """Every ``POST /packages/sync`` the collector has received, in order.

        Entries are :class:`TMPSyncDelivery`. The path is carried because "the
        server POSTed *something*" and "the server POSTed to /packages/sync" are
        different claims, and only the second one grades ``provider_url()``; the
        headers because the Bearer credential is the third thing that must be
        identical across transports.
        """
        if self._tmp_collector is None:
            return []
        origin = self._tmp_collector["origin"]
        return [
            TMPSyncDelivery(
                method=req.method,
                path=req.path,
                # Header names are case-insensitive on the wire; normalize so a
                # step asserts on one spelling.
                headers={name.lower(): value for name, value in dict(req.headers).items()},
                body=req.json(),
            )
            for req in origin.requests
            if str(req.path).endswith("/packages/sync")
        ]

    #: How long to keep watching AFTER the expected deliveries arrive, before a
    #: Then step asserts the exact count. Without a settle window, "exactly one"
    #: is unfalsifiable: a second, duplicate delivery in flight would simply not
    #: have landed yet (#1197 review).
    TMP_SYNC_SETTLE_SECONDS = 0.75

    def await_tmp_sync(self, count: int = 1, timeout: float = 30.0) -> TMPSyncDelivery:
        """Block until *count* package-sync deliveries have arrived; return the *count*-th.

        This is the LIVENESS signal, so it waits for "at least count" — the
        correctness signal is the Then step's exact ``len(...) == count``, which is
        what makes a double-fire fail. Reusing a ``>=`` wait as the assertion let a
        duplicate delivery pass green on every transport, including the REST
        double-fire that finding 5's placement argument exists to prevent.

        In-process, the production registry gives an exact completion signal, so
        the poll below normally returns on its first iteration. Out-of-process the
        thread is in the server container and polling is the only observation —
        hence one method with both, rather than a per-tier waiter.
        """
        import time

        if self._tmp_collector is None:
            raise AssertionError(
                "await_tmp_sync() called before register_tmp_provider() — there is no collector to wait on."
            )

        if not self.is_e2e:  # type: ignore[attr-defined]
            self.join_tmp_syncs(timeout=timeout)

        deadline = time.monotonic() + timeout
        while True:
            deliveries = self.tmp_sync_deliveries()
            if len(deliveries) >= count:
                # Let any duplicate that is already in flight land, so the caller's
                # exact-count assertion can see it.
                time.sleep(self.TMP_SYNC_SETTLE_SECONDS)
                return self.tmp_sync_deliveries()[count - 1]
            if time.monotonic() >= deadline:
                paths = [e["path"] for e in self._tmp_collector["received"]]
                raise AssertionError(
                    f"Expected {count} POST /packages/sync delivery(ies) within {timeout}s, "
                    f"got {len(deliveries)}. Captured paths: {paths}"
                )
            time.sleep(0.1)

    def settle_tmp_sync(self) -> None:
        """Wait out the settle window with no delivery expected.

        The counterpart to :meth:`await_tmp_sync` for a scenario asserting that
        NOTHING arrives: there is no arrival to wait for, so without a bounded wait
        "no delivery" would pass merely because the request had not landed yet.
        """
        import time

        time.sleep(self.TMP_SYNC_SETTLE_SECONDS)

    def join_tmp_syncs(self, timeout: float = 30.0) -> None:
        """Drain in-flight in-process syncs. No-op out-of-process (nothing local to join).

        Best-effort by design: this is the cleanup half of the seam, so a wedged
        thread is reported, not raised. The assertion belongs to
        :meth:`await_tmp_sync`, where a missing delivery is the actual failure —
        raising here would turn an unrelated media-buy scenario's slow teardown
        into that scenario's failure.
        """
        if self.is_e2e:  # type: ignore[attr-defined]
            return
        from src.services.tmp_provider_sync import join_active_syncs

        stragglers = join_active_syncs(timeout=timeout)
        if stragglers:
            logging.getLogger(__name__).warning(
                "TMP sync threads still running after %.0fs at env teardown: %s", timeout, stragglers
            )

    def _ensure_tmp_collector(self) -> dict[str, Any]:
        """Acquire the stub-provider origin once per env, lazily.

        A REAL TLS origin off the generated CA, not a hand-rolled receiver: #1802
        routed every outbound call through the egress seam, which requires https
        and refuses private addresses, so a plain-http loopback collector is no
        longer reachable by the code under test. Reusing
        ``run_local_origin`` + the generated CA + the private hatch is the same
        set of primitives ``LocalOriginMixin`` composes for the webhook envs —
        never a second mechanism.

        Acquired HERE rather than by composing ``LocalOriginMixin`` on the env:
        that mixin opens the private hatch for the whole env lifetime, and this
        env is shared with the ``@egress`` ingest-twin scenarios whose subject is
        the hatch POSTURE. Forcing ``private=True`` on every scenario in the env
        would decide, for those scenarios, the very thing they grade. Lazy means
        only a scenario that actually registers a TMP provider changes posture.

        Every resource is registered with ``_guard`` on the line it is acquired,
        so a failure part-way through releases exactly what was started.
        """
        if self._tmp_collector is not None:
            return self._tmp_collector

        if self.is_e2e:  # type: ignore[attr-defined]
            # The container cannot reach the runner's loopback, so the collector
            # has to be the compose stack's own capture service — which IS
            # reachable and IS covered by the generated CA
            # (``webhooks.adcp.test`` behind the shared tls-proxy, under
            # ``*.adcp.test``). What it cannot do is grade this feature's claim:
            # it records the JSON body ONLY (``store.append(key, payload)`` in
            # ``tests/e2e/webhook_capture_service.py``), while these scenarios
            # assert the method, the ``/packages/sync`` path that grades
            # ``provider_url()``, and the ``Authorization`` header that is the
            # one credential this feature transmits to a third party. Recording
            # those on a service shared with the webhook suites is
            # prebid/salesagent#2098's work, not this merge's. Declared, not
            # silently skipped.
            from tests.harness._realize import E2EUnsupportedSetup

            raise E2EUnsupportedSetup(
                "TMP package-sync needs a container-reachable collector serving TLS the "
                "generated CA covers; the compose capture service is the only such endpoint "
                "and programming it is prebid/salesagent#2098. The same scenarios grade the "
                "fan-out on impl/a2a/mcp/rest."
            )

        from tests.helpers.egress_hatches import egress_hatch_env
        from tests.helpers.local_http_origin import run_local_origin
        from tests.helpers.test_tls_material import load_gen_test_tls, server_ssl_context

        gen_test_tls = load_gen_test_tls()
        gen_test_tls.ensure_test_tls()
        ssl_cert_file = patch.dict(os.environ, {"SSL_CERT_FILE": str(gen_test_tls.COMBINED_CERT)})
        ssl_cert_file.start()
        self._guard("tmp_ssl_cert_file", ssl_cert_file.stop)  # type: ignore[attr-defined]

        origin_ctx = run_local_origin(ssl_context=server_ssl_context(gen_test_tls))
        origin = origin_ctx.__enter__()
        # No guard of its own: the drain registered at entry closes it, AFTER
        # joining the in-flight syncs, so no thread is mid-POST when the socket
        # goes away. A separate guard here would be released newest-first — i.e.
        # BEFORE the drain — which is the wrong order.
        self._tmp_collector_ctx = origin_ctx

        # The origin necessarily listens on loopback, which the seam refuses by
        # default — the same statement the seam's own integration tests make with
        # ``set_flags(private=True)``.
        hatches = patch.dict(os.environ, egress_hatch_env(private=True))
        hatches.start()
        self._guard("tmp_egress_hatches", hatches.stop)  # type: ignore[attr-defined]

        self._tmp_collector = {"endpoint": f"{origin.base_url}/tmp", "origin": origin}
        return self._tmp_collector

    def _enter_post(self) -> None:
        """Register the sync drain for EVERY scenario in this env.

        Not in ``_ensure_tmp_collector``: ``fire_tmp_sync`` starts its thread on
        any successful media-buy write that carries a tenant, whether or not a
        provider is registered — so a plain UC-002 create in this env spawns one
        too. Draining only the scenarios that registered a collector would leave
        those threads to open DB sessions after their test's scope, which is the
        defect this mixin's docstring claims to prevent (and which registering
        the drain in the lazy path reintroduced).

        ``_enter_post``, not a hand-rolled ``__exit__``: the body runs inside
        ``BaseTestEnv.__enter__``'s unwind guard, which is what
        ``test_harness_base::test_harness_envs_define_no_enter_exit`` requires.
        """
        super()._enter_post()  # type: ignore[misc]
        self._guard("tmp_sync_drain", self._teardown_tmp_sync)  # type: ignore[attr-defined]

    def _teardown_tmp_sync(self) -> None:
        """Join in-flight syncs, drop the provider rows, then close the origin.

        One cleanup owns the whole sequence because the order matters: joining
        first means no thread is still POSTing when the origin socket closes
        (which would surface as a connection error in the sync's fan-out log),
        and dropping the rows before the socket goes away stops a later scenario
        sharing an e2e database from fanning out to a dead port.

        Every step is conditional on having got that far, so this is also the
        release path for a scenario whose ``__enter__`` failed midway — and for
        one that never registered a provider, where it is just the join.
        """
        try:
            self.join_tmp_syncs(timeout=30.0)
        finally:
            try:
                if self._tmp_collector is not None:
                    from tests.factories import delete_tmp_providers

                    delete_tmp_providers(self, self._tenant_id)  # type: ignore[attr-defined]
            finally:
                ctx, self._tmp_collector_ctx = self._tmp_collector_ctx, None
                self._tmp_collector = None
                if ctx is not None:
                    ctx.__exit__(None, None, None)
