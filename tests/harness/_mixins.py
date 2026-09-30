"""Domain mixins — shared fluent API for integration and unit test environments.

Each mixin provides the domain-specific helper methods (set_*, call_*, get_*)
that are identical across integration and unit variants. Concrete env classes
inherit from both a base (BaseTestEnv or IntegrationEnv) and a mixin.

Mixins don't define ``__init__`` — concrete classes set up required state.
Mixins may call ``self._commit_factory_data()`` which is a no-op in unit mode.
"""

from __future__ import annotations

import itertools
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
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
from tests.helpers.tls_material import load_gen_test_tls, server_ssl_context


def _e2e_capture_url(env: Any) -> str:
    """E2E realization of :attr:`LocalOriginMixin.webhook_url` (#2098).

    In process the endpoint is a loopback origin on the runner. The Docker server
    cannot reach that, so under e2e the endpoint is the compose stack's
    long-lived ``webhook-capture`` service, addressed through the shared TLS
    front exactly as a production receiver would be.

    ISSUING the address is what this does, so it records that — see
    :meth:`~tests.harness._base.BaseTestEnv.record_capture_key_handed_out`, which
    names both issuing paths and what went wrong while only the other one recorded.
    :func:`_deliver_via_live_server` reads that fact to decide whether the media buy
    gets a ``reporting_webhook`` at all, and this is the path every DELIVERY env
    takes.
    """
    from tests.e2e._webhook_capture import delivery_url_for

    env.record_capture_key_handed_out()
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

    Presents a ``received_raw`` capture in the shape the in-process ``OriginRequest``
    has — ``.headers``, ``.body``, ``.json()`` — so a Then reads a delivery the same
    way whatever transport produced it, which is what lets the SAME assertion grade
    both.

    It used to expose ``.json()`` alone and raise BY NAME on the other two, on the
    stated grounds that "the capture service stores the parsed JSON PAYLOAD and
    nothing else". That was never true of the service: ``_CaptureStore`` has always
    kept ``received_raw`` (path, headers verbatim, body bytes) beside ``received``,
    and ``captured_delivery`` has always known how to read it. Only the READER this
    class was built on was parsed-only. The refusal was the honest response to that
    belief — an assertion that cannot run must say so rather than pass on absent
    evidence — but it made three graduated header-grading e2e_rest legs
    unobservable, which is the same silence one layer up.
    """

    __slots__ = ("_wire",)

    def __init__(self, wire: Any) -> None:
        self._wire = wire

    def json(self) -> dict:
        return json.loads(self._wire.content) if self._wire.content else {}

    @property
    def headers(self) -> Any:
        return self._wire.headers

    @property
    def body(self) -> bytes:
        return bytes(self._wire.content)


def _e2e_delivered_requests(env: Any) -> list[_CapturedDelivery]:
    """E2E realization of :attr:`LocalOriginMixin.delivered_requests`."""
    from tests.e2e._webhook_capture import ReceivedView

    return [_CapturedDelivery(wire) for wire in ReceivedView(env._capture_key).raw()]


def _e2e_last_delivery(env: Any) -> _CapturedDelivery:
    """E2E realization of :attr:`LocalOriginMixin.last_delivery`."""
    delivered = _e2e_delivered_requests(env)
    if not delivered:
        raise AssertionError("no webhook delivery reached the capture service")
    return delivered[-1]


def _e2e_raw_captures(env: Any) -> list[dict]:
    """Every wire entry the capture service holds for this env's key, in arrival order.

    The SERVICE DICTS, deliberately, not ``ReceivedView.raw()``: that returns
    ``CapturedWebhook`` values, which model the request a receiving socket saw (url,
    headers, content) and drop the service's own bookkeeping — including the
    ``received_at`` stamp the retry schedule is reconstructed from.
    """
    from tests.e2e._webhook_capture import captures

    return captures(env._capture_key).get("received_raw") or []


def _e2e_captures_since_trigger(env: Any) -> list[dict]:
    """The wire entries that arrived AFTER the scenario's delivery trigger.

    The server's own background sweep (DELIVERY_WEBHOOK_INTERVAL=5) keeps delivering the
    same row for as long as the scenario runs, so an absolute capture count conflates the
    sender's retry behaviour with the scenario's wall-clock duration. The watermark is set
    by :func:`_e2e_deliver_webhook` immediately before it asks the server to deliver; with
    no watermark (a scenario that never triggered) this is the whole list, which is the
    right reading for the legs that assert nothing was ever sent.
    """
    captures = _e2e_raw_captures(env)
    return captures[getattr(env, "_delivery_trigger_watermark", 0) :]


def _e2e_attempts_at_one_delivery(env: Any) -> list[dict]:
    """The capture entries that are ATTEMPTS AT THE SAME DELIVERY, in arrival order.

    The watermark in :func:`_e2e_deliver_webhook` excludes everything the background sweep
    delivered BEFORE the trigger, but the sweep keeps running during the window too:
    DELIVERY_WEBHOOK_INTERVAL=5 against a retry ladder that is ~4s wide means a second,
    independent delivery of the same row can land in the middle of the first one's retries.
    Counting the window naively conflates the two, which is how a correct 3-attempt ladder
    (``max_attempts=3`` at webhook_delivery_service.py:753, and the seam counts TOTAL
    attempts, not retries after the first) reads as four.

    THE BYTES ARE THE DISCRIMINATOR, and they are exact rather than heuristic. A retry
    re-sends the SAME body -- it must, because the RFC 9421 signature covers
    ``content-digest`` over those bytes, so a body that changed between attempts would not
    verify. A separate delivery builds a fresh envelope with its own timestamp. So attempts
    at one delivery are byte-identical and two deliveries never are.

    Returns the run belonging to the delivery the trigger caused: the first body seen in
    the window. An empty window yields an empty list, which is the right reading for the
    legs asserting nothing was ever sent.
    """
    window = _e2e_captures_since_trigger(env)
    if not window:
        return []
    first_body = window[0].get("body_b64")
    return [entry for entry in window if entry.get("body_b64") == first_body]


def _e2e_delivery_attempts(env: Any) -> int:
    """E2E realization of :attr:`LocalOriginMixin.delivery_attempts`.

    Counts the attempts at ONE delivery since the trigger. A rejected delivery is still
    recorded, so this counts ATTEMPTS the server made -- which is what "and no further
    attempt arrives" needs in order to mean anything -- while the per-delivery grouping
    keeps a concurrent background sweep from inflating the number.
    """
    return len(_e2e_attempts_at_one_delivery(env))


#: How much wall-clock slop a receipt gap is allowed on top of the scheduled wait, to
#: absorb TLS, the proxy hop and the sender's own serialization. Deliberately smaller than
#: the 1s doubling step, so it can never let one rung of the schedule pass for the next.
_RECEIPT_GAP_SLOP_SECONDS = 0.75


def _e2e_assert_retry_backoff_schedule(env: Any, *, expected_sleeps: int = 2, grade_jitter_draws: bool = False) -> None:
    """E2E realization of :meth:`assert_retry_backoff_schedule`, read off the WIRE.

    This was declared ``e2e_unsupported`` on the ground that the waits happen in another
    process "and the capture service records no receipt times to reconstruct them from".
    The first half is true and unfixable; the second half was a missing capability, and it
    is now present: every capture carries a monotonic ``received_at``, so the GAPS between
    consecutive receipts of the same key are the waits, measured on the wire instead of
    reconstructed from a patched clock.

    What the wire can carry, and therefore what this grades: the DOUBLING. Each gap is held
    against its own rung of ``BR_RULE_029_BASE_DELAYS`` — the same schedule constant the
    in-process leg and the webhook integration tests grade, imported rather than restated —
    with the live jitter range as the upper bound. A delay that stopped doubling, or one
    that jittered outside ``uniform(0, 1)``, lands outside its window.

    THE GAPS ARE TAKEN WITHIN ONE DELIVERY, not across the window. The background sweep
    runs every 5s against a ~4s ladder, so a second delivery of the same row lands among the
    first one's retries; :func:`_e2e_attempts_at_one_delivery` separates them by body bytes,
    which retries share by signature necessity. Without that, a correct 3-attempt ladder
    (``max_attempts=3``, TOTAL attempts) presents as four receipts and the count is wrong
    for a reason that has nothing to do with backoff.

    WHY THE SHARED ``assert_backoff_schedule`` IS NOT CALLED ON THESE NUMBERS. It grades
    SLEEP DURATIONS, and its ``jitter=None`` window is the half-open ``[base, base + 1)``.
    These are RECEIPT DELTAS: each carries the sleep plus TLS, the proxy hop and the
    sender's own serialization, so a correct wait can legitimately exceed ``base + 1``.
    Feeding wire timings to an assertion written for clock readings would trade a real
    regression signal for flakiness. The schedule itself is still shared; only the window
    differs, and it differs because the measurement does.

    What it cannot grade: the DRAW COUNT. ``grade_jitter_draws`` asks that production called
    ``random.uniform(0, 1)`` once per wait, which is white-box on the sender's process and
    has no wire projection. The jitter is still BOUNDED here — a gap outside
    ``[rung, rung + 1 + slop)`` fails — so the parameter narrows nothing further on this
    transport. It is accepted rather than refused so the in-process leg keeps grading the
    draws it can see. Stated here rather than left as a silent difference.
    """
    from tests.helpers.backoff_assertions import BR_RULE_029_BASE_DELAYS

    receipts = [entry.get("received_at") for entry in _e2e_attempts_at_one_delivery(env)]
    missing = [index for index, value in enumerate(receipts) if not isinstance(value, int | float)]
    assert not missing, (
        "the capture service must stamp every entry with a monotonic received_at for the "
        f"retry schedule to be observable at all; entries {missing} carry none. The service "
        "bind-mounts the repo (docker-compose.e2e.yml: `.:/app`), so a container started "
        "before this field existed is still running the old module -- restart "
        "webhook-capture rather than relaxing the assertion."
    )
    assert len(receipts) == expected_sleeps + 1, (
        f"expected {expected_sleeps + 1} attempts at this delivery ({expected_sleeps} waits "
        f"between them), got {len(receipts)}. Fewer means the retry schedule was never "
        "entered or was cut short; more means it did not stop where max_attempts says."
    )

    # ``pairwise``, not ``zip(receipts, receipts[1:])``: the pairing is deliberately offset
    # by one, so ``strict=True`` on that zip raises on every well-formed input.
    gaps = [later - earlier for earlier, later in itertools.pairwise(receipts)]
    assert len(gaps) <= len(BR_RULE_029_BASE_DELAYS), (
        f"observed {len(gaps)} waits but BR-RULE-029 defines only "
        f"{len(BR_RULE_029_BASE_DELAYS)} ({list(BR_RULE_029_BASE_DELAYS)}): "
        f"{[round(gap, 3) for gap in gaps]}. More attempts than the ladder allows is a "
        "retry loop that does not terminate where the rule says it does."
    )
    for index, gap in enumerate(gaps):
        rung = BR_RULE_029_BASE_DELAYS[index]
        ceiling = rung + 1.0 + _RECEIPT_GAP_SLOP_SECONDS  # + jitter uniform(0, 1), + wire slop
        assert rung <= gap < ceiling, (
            f"retry {index + 1} landed {gap:.3f}s after the previous attempt; BR-RULE-029 puts "
            f"it on the {rung:.0f}s rung of the exponential schedule plus jitter in [0, 1), so "
            f"the wire window is [{rung:.0f}, {ceiling:.2f}). A gap below the rung means the "
            "wait was skipped or shortened; above it means the schedule stopped doubling, the "
            "jitter range grew, or a background sweep delivery landed inside the ladder."
        )


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


#: The statuses ``get_media_buy_delivery`` will report on, read off production's own
#: ``status_filter`` (``src/core/tools/media_buy_delivery.py:158``) rather than restated as a
#: harness opinion: a row outside this set is declined with an advisory
#: ``MEDIA_BUY_NOT_FOUND`` and no webhook is ever sent.
_DELIVERABLE_STATUSES = frozenset({"active", "completed"})


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

    UNCONDITIONAL MEANS THE STATUS TOO, and that is what this function stopped doing.
    ``get_media_buy_delivery`` selects on ``status_filter=[active, completed]``
    (``src/core/tools/media_buy_delivery.py:158``), so a row outside that set is one the
    live server declines with an advisory ``MEDIA_BUY_NOT_FOUND`` and zero POSTs. The
    existence guard below used to return early on any row it found, and UC-004's Given
    ``_ensure_delivery_log_parent`` creates ``mb_001`` through ``make_media_buy`` with
    ``MediaBuyFactory``'s default ``pending_approval`` -- so on e2e_rest the two seeders
    silently disagreed about the same row and this one deferred. The scenario then graded
    an absence of deliveries that had nothing to do with retry behaviour.

    Two seeders for one row is the defect; one owner of its DELIVERABILITY is the fix. A
    row that already exists is upgraded rather than left, because every caller of this
    function is asking the live server to report on it.
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
    elif media_buy.status not in _DELIVERABLE_STATUSES:
        media_buy.status = "active"
    set_adapter_test_behavior(env, env._tenant_id, manual_approval_required=False)
    env._commit_factory_data()
    return media_buy


def _registered_webhook_url(env: Any) -> str:
    """The destination this scenario actually REGISTERED, as production would read it.

    A real ``create_media_buy`` writes the buyer's registered URL into both places —
    the ``push_notification_configs`` row and ``raw_request["reporting_webhook"]`` —
    so the two agree by construction. The harness has to reproduce that agreement
    rather than assume it: a Given may point the registration somewhere OTHER than
    this env's own endpoint, and ``the outbound webhook URL is blocked by SSRF
    validation`` does exactly that (it swaps the active row for one naming a
    cloud-metadata address).

    Reading the env's own endpoint here instead made the two disagree, and the
    disagreement was invisible in the direction that mattered: the live server reads
    ``raw_request``, so it delivered happily to the capture origin while the row said
    the destination was blocked — and the scenario asserting "skipped without a POST"
    was left grading a delivery that had in fact been made.

    Falls back to the env's own endpoint when nothing is registered, which is the case
    for the scenarios whose Given establishes the destination through the env rather
    than through a config row.
    """
    from sqlalchemy import select

    from src.core.database.models import PushNotificationConfig

    registered = env._session.scalars(
        select(PushNotificationConfig).filter_by(
            tenant_id=env._tenant_id, principal_id=env._principal_id, is_active=True
        )
    ).all()
    return str(registered[0].url) if registered else str(env.webhook_destination())


def _attach_reporting_webhook(env: Any, media_buy: Any) -> None:
    """Give *media_buy* the callback the scenario registered. CONDITIONAL.

    The delivery path reads ``MediaBuy.raw_request["reporting_webhook"]`` — that is what
    a real ``create_media_buy`` writes — so this is the row-level realization of a Given
    that says "this media buy has a reporting webhook". It is the half that must NOT run
    when no Given established a destination; see :func:`_deliver_via_live_server`.

    The URL comes from :func:`_registered_webhook_url`, so what the media buy says and
    what the registration says can never diverge.
    """
    raw_request = dict(media_buy.raw_request or {})
    raw_request["reporting_webhook"] = {"url": _registered_webhook_url(env), "frequency": "daily"}
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
    # The TRIGGER WINDOW opens here, and everything the scenario counts is read relative
    # to it. ``run_all_tests.sh`` exports DELIVERY_WEBHOOK_INTERVAL=5, so the server's own
    # background sweep is delivering the same row every five seconds; a raw capture count
    # therefore measures "how long the scenario took" as much as "how many attempts the
    # sender made", and the retry gaps read off those captures would include a sweep
    # interval sitting between two real retries. Watermarking is the smaller of the two
    # available fixes (the other being a drain) and it is the one that keeps the captures
    # the OTHER Thens read intact.
    env._delivery_trigger_watermark = len(_e2e_raw_captures(env))
    triggered = trigger_delivery_webhook_via_admin(
        env.e2e_config.base_url, tenant_id=env._tenant_id, media_buy_id=media_buy_id
    )
    return triggered, {"success": triggered}


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


def _fetch_from_live_server(
    env: Any, path: str, *, headers: dict[str, str], body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One anonymous fetch of a per-tenant document from the LIVE server.

    Anonymous because every document this serves — the JWKS, the capabilities —
    describes the SELLER, not the caller, and is resolved from the origin header
    rather than from a credential.

    *headers* is passed in rather than read from ``env.trust_root_request_headers()``
    here, because :func:`_realize_publishable_origin_on_server` is itself what that
    accessor is built on: defaulting would make the origin realization call itself.

    *body* selects the VERB, because the two documents are reached differently and
    that difference is production's. ``/.well-known/jwks.json`` is a static document
    served by a GET route. The capabilities document is an AdCP TOOL
    (``get_adcp_capabilities``), and #1721's registry gives every tool exactly one
    REST shape — ``RestBinding("POST", "/capabilities")`` — having deleted the bodyless
    GET variant on the ground that a buyer could not send ``protocols`` / ``context`` /
    ``ext`` through it. So a caller with a body POSTs, and one without GETs.
    """
    url = f"{env.e2e_config.base_url}{path}"
    if body is None:
        response = httpx.get(url, headers=headers, timeout=30.0)
    else:
        response = httpx.post(url, headers=headers, json=body, timeout=30.0)
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
        _fetch_from_live_server(env, JWKS_PATH, headers=_publishable_origin_headers(host))
        env.__dict__["_verified_publishable_origin"] = host
    return host


def _fetch_served_jwks_over_http(env: Any) -> dict[str, Any]:
    """E2E realization: GET the JWKS the LIVE server publishes for this tenant's origin."""
    from src.core.agent_identity import JWKS_PATH

    return _fetch_from_live_server(env, JWKS_PATH, headers=env.trust_root_request_headers())


def _fetch_advertised_webhook_signing(env: Any) -> Any:
    """E2E realization: the ``webhook_signing`` block the LIVE server ADVERTISES.

    Not the in-process re-derivation. ``signing_key_backed`` decides ``supported`` by
    DECRYPTING the private half, and over e2e the row is encrypted under the server
    container's KEK — the runner holds a different one, so an in-process derivation
    reports ``supported=False`` for a key that demonstrably signs, and the tag
    assertion would fail against a posture nobody advertises. Reading the served
    document is also the stronger claim: it is what a receiver statically validates the
    on-wire ``tag=`` against.

    POSTed with an empty body, which is the only shape this document has since #1721:
    the registry binds ``get_adcp_capabilities`` to ``POST /api/v1/capabilities`` and the
    bodyless GET was deleted with the rest of the second-shape routes, so the GET this
    used to make now 404s and the scenario failed on the fetch instead of on the tag.
    Empty rather than a filtered request because the caller wants the tenant's whole
    advertisement, and every field of the DTO is optional.
    """
    from src.core.signing.posture import WebhookSigningPosture
    from tests.harness.capabilities import CapabilitiesEnv

    served = (
        _fetch_from_live_server(
            env, CapabilitiesEnv.REST_ENDPOINT, headers=env.trust_root_request_headers(), body={}
        ).get("webhook_signing")
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

    Produces the adapter contract, ``AdapterUpdateResult``, echoing the
    media_buy_id from the call and claiming no affected packages, which mirrors
    what the mock adapter's own ``update_media_buy`` returns. Used by
    MediaBuyDualEnv to wire the update-path adapter mock.

    It passes no ``implementation_date``, and ``AdapterUpdateResult``'s
    ``extra="forbid"`` is what makes that a check rather than a convention. The
    field is not an adapter's to report: ``media_buy_update`` mints it itself
    from ``_applied_instant()`` on the branch that applied the change. An
    adapter kept passing it after the carrier split precisely because nothing
    refused it, which is the case the carrier's own docstring cites.
    """
    from src.adapters.base import AdapterUpdateResult

    def _update_response(*args: Any, **kwargs: Any) -> AdapterUpdateResult:
        # The tool calls adapter.update_media_buy with every argument by
        # keyword, so media_buy_id arrives in kwargs.
        media_buy_id = kwargs.get("media_buy_id") or (args[0] if args else "")
        return AdapterUpdateResult(media_buy_id=media_buy_id, affected_packages=[])

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

    def webhook_destination(self) -> str:
        """Where this env's outbound deliveries are addressed: its OWN endpoint.

        Overrides :meth:`~tests.harness._base.BaseTestEnv.webhook_destination`, whose
        answer is right only for an env that merely CARRIES a webhook URL as data. An
        env that RUNS an endpoint has exactly one honest answer, and it is the endpoint
        it runs — otherwise "where production was told to deliver" and "where this test
        reads deliveries back" are two different addresses, and a delivery that
        genuinely happened is indistinguishable from one that never did.

        That was the live defect. The base answers from ``webhook_capture_key``
        (``harness-<uuid>``) while this mixin's endpoint is ``_capture_key``
        (``register_capture_key()``), so over e2e ``_attach_reporting_webhook`` wrote
        the FORMER into ``MediaBuy.raw_request["reporting_webhook"]`` while every Given
        registered, and every Then read, the LATTER. The live server dutifully
        delivered — to a capture bucket no assertion looks at. It also lost the
        registration on the way: ``_send_report_for_media_buy`` matches
        ``DBPushNotificationConfig.url`` against the URL it was handed, so the
        mismatched address missed the stored row carrying the bearer/HMAC credential
        and fell through to an anonymous, unsigned delivery.

        In process this collapses to the local origin the test already asserts against,
        which is what the Givens have always written into the config row — so the two
        transports now name the endpoint the same way, from one place.
        """
        return self.webhook_url

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

    @staticmethod
    def delivered_result(request: Any) -> dict[str, Any]:
        """The delivery REPORT inside one webhook POST, not the envelope around it.

        AdCP 3.1.1 L3/webhooks.mdx :217 puts the report under ``result`` and says it "is not
        valid as the top-level POST body by itself", so a test reading ``notification_type``
        or ``sequence_number`` off the body finds nothing. One accessor, so no case
        re-decides where a report field lives.
        """
        body = request.json()
        result = body.get("result")
        assert isinstance(result, dict), (
            f"the webhook envelope carries no `result` object: got {type(result).__name__} "
            f"at `result`, envelope keys {sorted(body)}"
        )
        return result

    def call_send_enhanced(
        self,
        result: dict[str, Any] | None = None,
        *,
        tenant_id: str = "t1",
        principal_id: str = "p1",
        media_buy_id: str = "mb_001",
        notification_type: str | None = "scheduled",
        sequence_number: int = 1,
    ) -> bool:
        """Call ``_send_webhook_enhanced`` with a task context built from these values.

        The sender takes the delivery REPORT and a typed context, and builds the AdCP
        envelope per registration. Constructing that context is seven fields; doing it at
        each of sixteen call sites is the copy-paste shape the duplication ratchet refuses,
        and it is also how a site ends up naming the wrong sequence number without saying so.
        """
        from src.core.webhooks.delivery import WebhookTaskContext

        service = self.get_service()
        return service._send_webhook_enhanced(
            ctx=WebhookTaskContext(
                task_id=media_buy_id,
                task_type="delivery_report",
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                sequence_number=sequence_number,
                notification_type=notification_type,
            ),
            result=result if result is not None else {"test": "data"},
        )

    # ── The outbound delivery seam ────────────────────────────────────
    #
    # Two halves, realized per transport HERE: who MAKES a delivery
    # (:meth:`deliver_webhook`) and how a test READS it back
    # (:attr:`LocalOriginMixin.delivery_attempts` / ``delivered_requests`` /
    # ``last_delivery``). The third — where it is ADDRESSED — is
    # ``BaseTestEnv.webhook_destination``, because envs that only REGISTER a webhook
    # need it too. No step definition learns that a transport exists; the defect this
    # removes is a delivery that happened in the test process while the leg claimed to
    # grade the live server (salesagent-n78j0.1.4).
    #
    # The READ half is INHERITED, never re-declared here. This class used to carry a
    # second pair (``deliveries()`` / ``last_delivery()``) reading ``mock["post"]``,
    # a patched socket; #1802 deleted that socket and replaced it with a REAL local
    # origin, so the mock-backed pair both shadowed the inherited readers through the
    # MRO and read an attribute that no longer exists. One reader of the endpoint,
    # and it is the one that observes what the endpoint actually received.

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
        """Answer every attempt with the given status code.

        A name kept for the callers that predate the local origin; the endpoint
        that answers is :meth:`LocalOriginMixin.set_http_status`'s, so there is
        exactly ONE thing that decides what a delivery gets back. The old body
        programmed ``mock["post"]``, a patched socket that #1802 deleted — a
        stubbed socket cannot record what a REAL receiver saw.
        """
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

    #: Why every breaker seam below is declared unrealizable over e2e_rest, once.
    #:
    #: TWO independent reasons, and either alone is disqualifying:
    #:
    #: 1. WRONG PROCESS. ``_breaker_for`` reaches ``get_service()._circuit_breakers``
    #:    on the RUNNER's in-process ``WebhookDeliveryService``. The breaker that could
    #:    matter lives in the server container, so a Given that seeds here arranges
    #:    nothing the server will consult, and ``breaker_snapshot`` reads back the very
    #:    object the test just poked. The Then that reads it was already declared
    #:    unsupported (``assert_circuit_breaker_failure_recorded``); the GIVENS were not,
    #:    so the scenarios kept running and driving real deliveries against an endpoint
    #:    programmed to fail.
    #:
    #: 2. WRONG SENDER. The e2e delivery path is
    #:    ``DeliveryWebhookScheduler -> ProtocolWebhookService``, which contains ZERO
    #:    circuit-breaker references; the breaker belongs to ``WebhookDeliveryService``,
    #:    the sender the in-process legs drive. So "subsequent scheduled deliveries
    #:    should be suppressed" is not merely unobservable over e2e — it is not true of
    #:    the path e2e exercises.
    #:
    #: NOT A COVERAGE LOSS BEING HIDDEN: AdCP 3.1.1 does not require a circuit breaker
    #: at all. ``docs/building/by-layer/L3/webhooks.mdx`` :528 is its ONLY mention and is
    #: descriptive ("suppressed fires under a tripped circuit breaker" as one thing a
    #: buyer might be diagnosing); nothing in ``dist/compliance/3.1.1/`` grades it. The
    #: spec's buyer-visible observable is ``webhook_activity[]`` on ``get_media_buys``
    #: (``include_webhook_activity``, ``core/webhook-activity-record.json``), which this
    #: repo does not implement — see the handoff. When it lands, these Thens should be
    #: rewritten against it and these declarations removed, because that surface IS
    #: readable on every transport.
    _BREAKER_IS_PROCESS_LOCAL = (
        "the circuit breaker is process-local to the runner's own WebhookDeliveryService "
        "(get_service()._circuit_breakers), and the live server's delivery-report path "
        "(ProtocolWebhookService) has no breaker at all — so this seeds and reads state no "
        "server behaviour depends on. AdCP 3.1.1 mandates no breaker; the spec's observable "
        "is webhook_activity[] on get_media_buys, which is not implemented here"
    )

    @realize_e2e(e2e_unsupported(_BREAKER_IS_PROCESS_LOCAL))
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

    @realize_e2e(e2e_unsupported(_BREAKER_IS_PROCESS_LOCAL))
    def set_breaker_state(self, endpoint_key: str, state: str) -> None:
        """Force the breaker into *state* ('OPEN' | 'HALF_OPEN' | 'CLOSED').

        A Given that names a state is describing where the scenario STARTS, not
        something production just did — so this is a seed, not an assertion, and
        the state it sets is read back through :meth:`breaker_snapshot`.
        """
        self._breaker_for(endpoint_key).state = CircuitState[state.upper()]

    @realize_e2e(e2e_unsupported(_BREAKER_IS_PROCESS_LOCAL))
    def elapse_breaker_timeout(self, endpoint_key: str, seconds: int = 61) -> None:
        """Age the last failure past the breaker's recovery timeout.

        Moves the CLOCK rather than the state: production decides what an
        elapsed timeout means (OPEN -> HALF_OPEN on the next ``can_attempt``),
        and a test that set HALF_OPEN directly would skip the transition it
        means to exercise.
        """
        self._breaker_for(endpoint_key).last_failure_time = datetime.now(UTC) - timedelta(seconds=seconds)

    @realize_e2e(e2e_unsupported(_BREAKER_IS_PROCESS_LOCAL))
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

    @realize_e2e(e2e_unsupported(_BREAKER_IS_PROCESS_LOCAL))
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

    @realize_e2e(_e2e_assert_retry_backoff_schedule)
    def assert_retry_backoff_schedule(self, *, expected_sleeps: int = 2, grade_jitter_draws: bool = False) -> None:
        """Grade the retry schedule production actually waited: 1s/2s/4s + jitter.

        The COUNTERPART of :meth:`assert_no_retry_schedule_entered` — one says the
        schedule was never entered, this one says what it looked like — so both read
        the same patched clock and both are declared e2e-unsupported for the same
        reason. The magnitudes themselves are graded by the shared
        :func:`tests.helpers.backoff_assertions.assert_backoff_schedule`, which the
        webhook integration tests grade the same sentence with.

        Args:
            expected_sleeps: how many waits the scenario's attempt count implies
                (3 attempts = 2 waits). Exact, not a floor: a schedule with an extra
                wait is as wrong as one with a missing wait.
            grade_jitter_draws: also require one ``random.uniform(0, 1)`` draw per
                wait. Only the scenario whose text names jitter asks for this;
                deleting ``+ random.uniform(0, 1)`` from the delay is what it catches.
        """
        from tests.helpers.backoff_assertions import assert_backoff_schedule

        sleep_calls = self.mock["sleep"].call_args_list  # type: ignore[attr-defined]
        assert sleep_calls, "Expected at least one sleep call for backoff"
        durations = [float(call.args[0]) for call in sleep_calls]
        assert len(durations) == expected_sleeps, (
            f"Expected {expected_sleeps} backoff sleeps (for {expected_sleeps + 1} total attempts), "
            f"got {len(durations)}"
        )
        # The env pins random.uniform to a constant, so the pinned offset is part of
        # the expected delay rather than a window to tolerate.
        assert_backoff_schedule(durations, jitter=float(self.mock["random"].return_value))  # type: ignore[attr-defined]

        if not grade_jitter_draws:
            return
        jitter_draws = self.mock["random"].call_args_list  # type: ignore[attr-defined]
        assert len(jitter_draws) == len(durations), (
            f"Expected one jitter draw per backoff sleep ({len(durations)}), got {len(jitter_draws)} — "
            "BR-RULE-029 requires randomisation so retries do not thunder"
        )
        for index, call in enumerate(jitter_draws):
            assert call.args == (0, 1), (
                f"Jitter draw {index + 1} was random.uniform{call.args}, expected random.uniform(0, 1)"
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


class AccountListDispatchMixin:
    """The ``list_accounts`` verb's four transport methods, shared by both account envs.

    ``AccountListEnv`` dispatches only this verb; ``AccountSyncEnv`` dispatches it
    as its SECOND verb, behind a request-type discriminator (the ``MediaBuyDualEnv``
    pattern). They share these methods rather than each owning a copy, because a
    second copy is how the two envs would drift into grading the same production
    call two different ways.

    The alternative — a step calling ``_list_accounts_impl`` directly because the
    sync env "doesn't dispatch list" — is what this mixin exists to delete: such a
    bypass grades ``_impl`` on EVERY transport, so the a2a/mcp/rest/e2e legs of a
    scenario never touch the wire they claim to.
    """

    LIST_REST_ENDPOINT = "/api/v1/accounts"

    @staticmethod
    def is_list_request(kwargs: dict[str, Any]) -> bool:
        """Whether this dispatch is the list verb rather than the env's primary one.

        Discriminates on the request TYPE, so one uniform rule covers every call
        site; a ``sync_accounts`` dispatch never carries a ``ListAccountsRequest``,
        so there is no ambiguity.
        """
        from src.core.schemas.account import ListAccountsRequest

        return isinstance(kwargs.get("req"), ListAccountsRequest)

    def _call_list_impl(self, **kwargs: Any) -> Any:
        from src.core.tools.accounts import _list_accounts_impl

        self._commit_factory_data()  # type: ignore[attr-defined]
        kwargs.setdefault("req", None)
        kwargs.setdefault("identity", self.identity)  # type: ignore[attr-defined]
        return _list_accounts_impl(**kwargs)

    def _call_list_a2a(self, **kwargs: Any) -> Any:
        from src.core.schemas.account import ListAccountsResponse

        return self._run_a2a_handler("list_accounts", ListAccountsResponse, **kwargs)  # type: ignore[attr-defined]

    def _call_list_mcp(self, **kwargs: Any) -> Any:
        from src.core.schemas.account import ListAccountsResponse

        return self._run_mcp_client("list_accounts", ListAccountsResponse, **kwargs)  # type: ignore[attr-defined]

    def _parse_list_rest_response(self, data: dict[str, Any]) -> Any:
        # revive, not the constructor: a served document carries the context the boundary
        # stamped and AdcpResponse refuses that field on construction.
        from src.core.schemas.account import ListAccountsResponse

        return ListAccountsResponse.revive(data)
