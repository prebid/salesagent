"""SSRF gate for protocol push / reporting webhook URLs.

Pins that ProtocolWebhookService refuses unsafe URLs before any outbound POST,
mirrors application-level WebhookURLValidator usage in webhook_delivery, and
covers registration wiring for the two surfaces that accept a webhook: the
``push_notification_config`` and ``reporting_webhook`` arguments on
create_media_buy and sync_creatives.

Wire-level VALIDATION_ERROR / recovery=correctable + suggestion for
create_media_buy and sync_creatives is graded by transport-blind BDD scenarios
(BR-UC-002-ext-webhook-ssrf, BR-UC-006-ext-webhook-ssrf).

The A2A-native ``TaskPushNotificationConfig`` endpoints used to translate the
same gate to InvalidParamsError, and three cases here graded that. They are
retired with their subject: this agent advertises ``push_notifications=False``
and declines all four ``tasks/pushNotificationConfig/*`` methods, because AdCP
3.1.1 L3/webhooks.mdx :308 makes the A2A-native channel a separate registration
mechanism with a separate (A2A ``Task``) envelope, and this seller implements
only the AdCP channel. The obligation those cases stood for -- an SSRF URL is
refused before any push config is persisted -- is graded below at the two
surfaces that still accept one.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import httpx
import pytest
from adcp import create_mcp_webhook_payload
from adcp.types import ReportingWebhook
from adcp.webhooks import GeneratedTaskStatus

from src.core.database.models import PushNotificationConfig
from src.core.exceptions import AdCPUrlNotAllowedError
from src.core.resolved_identity import AccountIdentity
from src.core.schemas import CreateMediaBuyRequest
from src.core.security import outbound_http
from src.core.tools.creatives._sync import _sync_creatives_impl
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.webhook_validator import reject_unsafe_webhook_registration_url
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.factories import WebhookTaskContextFactory
from tests.factories.webhook import PushNotificationConfigRequestFactory
from tests.helpers.adcp_factories import create_test_media_buy_request_dict, valid_reporting_webhook
from tests.helpers.creative_test_helpers import sync_creatives_request
from tests.helpers.egress_hatches import egress_hatch_env
from tests.helpers.local_http_origin import LocalOrigin, run_local_origin
from tests.helpers.tls_material import load_gen_test_tls, server_ssl_context
from tests.helpers.unit_identity import fabricated_account_identity

# No WEBHOOK_SSRF_SUGGESTION* import: origin/main narrowed the two dev/strict
# wordings to one constant, and the merged webhook_validator exports NEITHER --
# suggestion is a read-only property off CODE_TABLE keyed by the error code,
# never a per-raise-site or per-class override (ADR-010). The assertion that
# constant carried is preserved below against the resolved property.
_METADATA_URL = "http://169.254.169.254/latest/meta-data/"

# What a delivery carries when the case is only about the destination. Written
# once because all five send-path cases below pass the same pair and none of
# them is about the payload: ``task_type`` deliberately stays outside the
# delivery-report pair so no case touches the database.
# The real envelope, built by the SDK exactly as ``notify`` builds it. It used to be a
# two-key dict, which only reached the sender's Mapping passthrough -- a branch that no
# longer exists, because there is one envelope and the sender takes it typed.
_PAYLOAD = create_mcp_webhook_payload(
    task_id="t1",
    status=GeneratedTaskStatus.completed,
    task_type="update_media_buy",
    result={},
)
# The delivery's task identity, typed. `send_notification` used to take a loose
# four-key dict and rebuild a context from it downstream; the rebuild reset
# sequence_number to 1 and notification_type to None, and those were the values
# persisted. Naming the fields here is the point of the change these tests follow.
_TASK = WebhookTaskContextFactory(tenant_id="t1")


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Delegating transport that logs the URL of every hop httpx dispatches."""

    def __init__(self, inner: httpx.AsyncBaseTransport, dispatched: list[str]) -> None:
        self._inner = inner
        self._dispatched = dispatched

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._dispatched.append(str(request.url))
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@contextlib.contextmanager
def _egress_hatches(*, private: bool) -> Iterator[None]:
    """Pin the private-range outbound escape hatch for the block.

    A refusal case that leaves it ambient is graded by whichever gate the
    surrounding shell happened to branch, so a test meaning "production posture"
    would silently grade nothing. Same spelling as ``LocalOriginMixin`` and the
    seam's own suite. There is no ``insecure`` hatch anymore (salesagent-e6h0):
    the scheme gate is unconditional in production.
    """
    with patch.dict(os.environ, egress_hatch_env(private=private)):
        yield


@contextlib.contextmanager
def _dispatched_hops() -> Iterator[list[str]]:
    """Record every HTTP hop the egress seam actually puts on the wire.

    WRAPS the real ``outbound_http._async_transport`` rather than replacing
    it: the real thin builder still calls ``EgressPolicy.resolve_for_dial``
    (SDK resolve + validate + the shared address predicate) before returning
    anything, so nothing here can turn a refusal into a pass — a refused URL
    raises inside the real call and never reaches the wrapper at all, which
    is why an empty list is direct evidence that nothing left the process.
    Every redirect hop httpx follows is dispatched through the same client
    transport, so a followed redirect shows up as a second entry naming
    where it went.

    Re-pointed from the pre-salesagent-tbrk.1 ``build_async_ip_pinned_
    transport`` patch target: that SDK builder is no longer called directly
    by ``guarded_async_client``/``asend`` (see ``_async_transport``), so
    patching it would silently record nothing rather than fail loudly.
    """
    dispatched: list[str] = []
    real_builder = outbound_http._async_transport

    def build(url: str, *, field: str | None, allow_private: bool) -> httpx.AsyncBaseTransport:
        return _RecordingTransport(real_builder(url, field=field, allow_private=allow_private), dispatched)

    with patch.object(outbound_http, "_async_transport", build):
        yield dispatched


@contextlib.contextmanager
def _tls_origin(monkeypatch: pytest.MonkeyPatch, *, listen_host: str = "127.0.0.1") -> Iterator[LocalOrigin]:
    """A real origin, served over real TLS and trusted by this process.

    Written once because THREE accept-arm cases below need exactly this and none
    of them is about TLS setup — the duplicated four-line preamble is the
    copy-paste-with-variable-substitution shape the duplication ratchet refuses.

    Why real TLS rather than a hatch: the seam requires ``https`` unconditionally
    now (salesagent-e6h0 deleted ``ADCP_OUTBOUND_ALLOW_INSECURE``), so the only
    origin a unit test can actually be dialled at has to EARN that scheme. The
    material is the shared generated CA/leaf (``scripts/dev/gen_test_tls.py``),
    whose SAN set already covers both ``127.0.0.1`` and ``localhost``, so no
    second copy of it is minted here.
    """
    gen_test_tls = load_gen_test_tls()
    gen_test_tls.ensure_test_tls()
    monkeypatch.setenv("SSL_CERT_FILE", str(gen_test_tls.COMBINED_CERT))
    with run_local_origin(listen_host=listen_host, ssl_context=server_ssl_context(gen_test_tls)) as origin:
        yield origin


def _config(url: str) -> PushNotificationConfig:
    return PushNotificationConfig(
        id="pnc-ssrf-test",
        tenant_id="t1",
        principal_id="p1",
        url=url,
        authentication_type=None,
        authentication_token=None,
        is_active=True,
    )


def _reporting_webhook(url: str) -> ReportingWebhook:
    return ReportingWebhook.model_validate(valid_reporting_webhook(url))


def _identity() -> AccountIdentity:
    """The authenticated caller these cases dispatch as.

    An ``AccountIdentity``, which is what ``_create_media_buy_impl`` declares: its DTO puts
    ``account`` in ``/required``, so the boundary always resolves one, and the
    ``account=None`` identity a bare ``make_identity()`` returns is a caller the controller
    can never receive.

    The database is mocked in this module, so the caller is fabricated and the one tenant
    fact these cases depend on stands in for the row: ``human_review_required=False``,
    because a seller that queues for review never reaches the adapter and the refusal under
    test would be graded against the wrong branch. The impl reads that field off
    ``identity.tenant`` in production too.

    The three other arguments this call once carried are gone with their subjects:
    ``protocol`` and ``testing_context`` (commit a1b79d22d removed the testing-hook channel
    and took the transport off the identity) and ``auto_create_media_buys``, which stopped
    being a tenant field when the tenant became typed (f3c46a970).
    """
    return fabricated_account_identity(principal_id="principal_1", human_review_required=False)


def _minimal_create_request(**overrides):
    data = create_test_media_buy_request_dict(
        product_ids=["prod_1"],
        total_budget=5000.0,
        pricing_option_id="cpm_usd_fixed",
        idempotency_key="unit-ssrf-create-key-0001",
        **overrides,
    )
    return CreateMediaBuyRequest(**data)


# RETIRED WITH THE SUBJECT THEY GRADED (merge of the RFC 9421 signing lane into
# the #1802 egress seam) -- recorded rather than dropped in silence:
#
#   * ``_running_service()`` / ``_send()``. Both named things this service no
#     longer has. ``ProtocolWebhookService`` owns NO connection state now, so
#     there is no ``close()`` to call and no long-lived client whose construction
#     had to happen inside a capture block; and ``send_notification`` takes a
#     typed ``task: WebhookTaskContext`` rather than the loose ``metadata`` dict
#     ``_send`` built. Their replacement is ``_config(url)`` + ``_PAYLOAD`` +
#     ``_TASK`` passed directly.
#
#   * the ``constructed_http_clients()`` legs, which asserted
#     ``client.timeout.connect == 10.0`` and ``client.follow_redirects is False``
#     on the client the SERVICE constructed. That client is deleted (#1802): a
#     pooled client is trusted completely by ``adcp``'s ``WebhookSender`` and
#     cannot carry a per-destination pin, so each delivery now builds and discards
#     a transport inside ``outbound_http.asend``. Neither property is assertable
#     HERE any more without mocking the seam, and neither is unowned: the timeout
#     is ``_DELIVERY_TIMEOUT_SECONDS`` handed to ``adeliver_webhook``, and the
#     redirect refusal is ``guarded_async_client``/``asend``'s unconditional
#     ``follow_redirects=False`` -- both graded in the seam's own suites. What
#     survives here, and is strictly stronger than the client-attribute check, is
#     ``test_send_notification_does_not_follow_redirect_to_metadata``'s dispatch
#     log: it counts the hops that were actually put on the wire.
#
#   * the backoff stub that patched ``protocol_webhook_service.asyncio.sleep``. The
#     module imports no ``asyncio`` at all now -- the retry ladder moved into the
#     seam's ``Attempts`` -- so that patch target does not exist and would raise.


@pytest.mark.asyncio
async def test_send_notification_rejects_metadata_url_without_post() -> None:
    """A cloud-metadata destination fails closed, with nothing put on the wire.

    Graded with the private-range hatch OPEN, which is what makes the case
    about the metadata blocklist and nothing else — a plain ``http://``
    link-local URL is refused by the seam's scheme rule unconditionally now
    (salesagent-e6h0), so this case would say nothing about the address if the
    hatch were closed instead. ``adcp.signing`` refuses ``169.254.169.254``
    unconditionally, hatch or not — the property
    ``tests/integration/test_outbound_http.py::test_cloud_metadata_stays_refused_with_the_private_hatch_open``
    grades directly.
    """
    service = ProtocolWebhookService()

    with _egress_hatches(private=True), _dispatched_hops() as hops:
        sent = await service.send_notification(_config(_METADATA_URL), payload=_PAYLOAD, task=_TASK)

    assert sent is False
    assert hops == [], f"a request was dispatched towards a cloud-metadata address: {hops}"


@pytest.mark.asyncio
async def test_send_notification_rejects_localhost_without_post() -> None:
    """Under production posture a loopback destination is refused, unreached.

    The endpoint is a REAL origin that is genuinely listening on ``localhost``,
    so "no POST" is read off the server's own hit count rather than off a mock:
    zero hits is a fact about a socket nobody connected to. Opening the hatches
    is the whole difference between this and
    :func:`test_send_notification_posts_when_url_is_public`, which reaches the
    same kind of origin and counts one hit.
    """
    service = ProtocolWebhookService()

    with run_local_origin(listen_host="localhost") as origin:
        origin.respond_with(200)

        with _egress_hatches(private=False), _dispatched_hops() as hops:
            sent = await service.send_notification(_config(f"{origin.base_url}/webhook"), payload=_PAYLOAD, task=_TASK)

        assert sent is False
        assert origin.hits == 0, f"the loopback endpoint was reached anyway: {origin.requests}"
        assert hops == [], f"a request was dispatched towards a reserved address: {hops}"


@pytest.mark.asyncio
async def test_send_notification_posts_when_url_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """A destination the seam permits is really POSTed to — body and headers included.

    Asserted against the bytes the origin received rather than against the
    arguments a transport mock was handed: the latter reads back the object the
    caller passed and proves nothing crossed a socket. The origin is served over
    real TLS (see :func:`_tls_origin`) standing in for "public": the seam requires
    https unconditionally now, so the only origin a unit test can really run has to
    earn that scheme, not merely be waved through by a hatch.
    What the case grades is that a destination the gate ALLOWS is dialled and served.
    """
    service = ProtocolWebhookService()

    with _tls_origin(monkeypatch) as origin:
        origin.respond_with(200)

        with _egress_hatches(private=True):
            sent = await service.send_notification(_config(f"{origin.base_url}/webhook"), payload=_PAYLOAD, task=_TASK)

        assert sent is True
        assert origin.hits == 1, f"the endpoint served {origin.hits} requests for one notification"
        request = origin.last_request
        assert request.method == "POST"
        assert request.path == "/webhook"
        # Whole-document equality against the ENVELOPE OBJECT, which is stronger than
        # the ``{**payload, "idempotency_key": <whatever arrived>}`` shape this case
        # used to state. The per-event idempotency key is a field of
        # ``McpWebhookPayload`` itself now, minted once by ``create_mcp_webhook_payload``
        # when the envelope is built, so the sender no longer merges a second key in
        # beside the body it serializes -- and equality against the module-level
        # ``_PAYLOAD`` therefore also pins that the key on the wire is the one the
        # envelope was born with, not a fresh one per attempt. The receiver dedups on
        # exactly that.
        body = request.json()
        assert body == _PAYLOAD.model_dump(mode="json", exclude_none=True)
        assert str(body.get("idempotency_key")).startswith("whk_"), (
            f"the receiver has nothing to dedup this event on: {body.get('idempotency_key')!r}"
        )

        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["User-Agent"] == "AdCP-Sales-Agent/1.0"


@pytest.mark.asyncio
async def test_send_notification_does_not_follow_redirect_to_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 302 towards link-local metadata is returned, never chased.

    The dispatch log is the proof, not the return value: were the redirect
    followed, the pinned transport would refuse the wrong-host connect and the
    call would STILL come back ``False``, so ``sent is False`` alone cannot tell
    a refused redirect from a followed one. Every hop httpx follows is dispatched
    through the client's transport, so a chased 302 appears as a second entry
    naming ``169.254.169.254``.

    The hit count carries the other half: a 302 is terminal to the seam, so the
    buyer's endpoint is asked exactly once. The origin is served over real TLS
    (see :func:`_tls_origin`) since the seam requires https unconditionally now.
    """
    service = ProtocolWebhookService()

    with _tls_origin(monkeypatch) as origin:
        origin.redirect_to(_METADATA_URL, status=302)
        webhook_url = f"{origin.base_url}/webhook"

        with _egress_hatches(private=True), _dispatched_hops() as hops:
            sent = await service.send_notification(_config(webhook_url), payload=_PAYLOAD, task=_TASK)

        assert sent is False
        assert origin.hits == 1, f"the 302 was retried: {origin.paths}"
        assert hops == [webhook_url], f"the redirect was followed: {hops}"


@pytest.mark.parametrize(
    ("url", "rule"),
    [
        ("https://metadata.google.internal/computeMetadata/v1/", "hostname blocklist"),
        ("http://metadata.google.internal/computeMetadata/v1/", "scheme"),
    ],
)
def test_reject_unsafe_webhook_registration_url_raises_validation_error(url: str, rule: str) -> None:
    """A blocklisted host is refused at registration — on BOTH rules that can refuse it.

    The two branches each pinned ONE refusal, on URLs that no longer mean the
    same thing. salesagent-e6h0 deleted the scheme hatch, so ``https`` is
    required unconditionally: origin/main's ``https://`` URL keeps the scheme
    fine and is therefore refused by the HOSTNAME blocklist, while this branch's
    ``http://`` URL is refused one rule earlier, by the SCHEME gate. They are two
    distinct blocked cases, so both are kept — dropping either would stop grading
    a rule that fails closed today. (Verified against the merged gate: the two
    URLs log "hostname is on the blocklist" and "scheme is not https"
    respectively.)

    The pinned class is ``AdCPUrlNotAllowedError``, the NARROWER of the two
    branches' expectations: it is a subclass of ``AdCPValidationError`` carrying
    the same published VALIDATION_ERROR code, so this also satisfies
    origin/main's ``AdCPValidationError`` expectation while additionally
    discriminating the URL refusal BY TYPE — which is what the A2A boundary does
    to select ``InvalidParamsError`` rather than re-labelling it INTERNAL_ERROR.

    ``suggestion`` is no longer compared against a ``webhook_validator``
    constant (origin/main's ``WEBHOOK_SSRF_SUGGESTION``, which the merged module
    does not export): it resolves from CODE_TABLE by error code now (ADR-010).
    What that comparison was FOR is preserved directly — the buyer gets a
    non-empty actionable sentence, and neither it nor the message names the
    refused host, which is the Security Considerations property ("never disclose
    internal service names, hostnames, or IP addresses") the gate cites as the
    reason it discards the computed cause.
    """
    # Posture pinned explicitly rather than left ambient: a refusal case that
    # inherits whichever hatch the surrounding shell happened to branch is graded by
    # a gate the case did not choose. Same spelling as the send-path cases above.
    with _egress_hatches(private=False), pytest.raises(AdCPUrlNotAllowedError) as exc_info:
        reject_unsafe_webhook_registration_url(url, field="reporting_webhook.url")

    assert exc_info.value.field == "reporting_webhook.url", rule
    assert exc_info.value.recovery == "correctable", rule
    assert exc_info.value.suggestion.strip() != "", f"{rule}: buyer got no actionable suggestion"
    assert "metadata.google.internal" not in exc_info.value.suggestion, rule
    assert "metadata.google.internal" not in exc_info.value.message, rule


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_reject_unsafe_webhook_registration_url_noop_on_blank(blank: str | None) -> None:
    """Blank / missing URL is not a rejection — callers extract-then-call unconditionally."""
    reject_unsafe_webhook_registration_url(blank, field="push_notification_config.url")


def test_sanitize_webhook_url_for_log_strips_credentials_query_and_fragment() -> None:
    from src.core.webhook_validator import (
        UNPARSEABLE_WEBHOOK_URL_FOR_LOG,
        sanitize_webhook_url_for_log,
        webhook_url_for_log,
    )

    dirty = "https://user:pass@buyer.example.com:8443/hook?token=abc#frag"
    assert sanitize_webhook_url_for_log(dirty) == "https://buyer.example.com/hook"
    assert webhook_url_for_log(dirty) == "https://buyer.example.com/hook"
    assert sanitize_webhook_url_for_log(None) is None
    assert sanitize_webhook_url_for_log("not-a-url") is None
    assert webhook_url_for_log(None) == UNPARSEABLE_WEBHOOK_URL_FOR_LOG
    assert webhook_url_for_log("not-a-url") == UNPARSEABLE_WEBHOOK_URL_FOR_LOG


def test_reject_unsafe_webhook_registration_url_allows_public() -> None:
    # Registration skips DNS — fixture hostnames must not NXDOMAIN-fail.
    reject_unsafe_webhook_registration_url("https://buyer.example.com/hook", field="push_notification_config.url")


def test_reject_unsafe_webhook_registration_url_allows_unresolvable_public_hostname() -> None:
    """Registration gate must not require DNS (BDD fixture hosts)."""
    reject_unsafe_webhook_registration_url(
        "https://nonexistent-buyer-ssrf-fixture.invalid/hook",
        field="reporting_webhook.url",
    )


# DELETED WITH THE BEHAVIOR IT GRADED (Epic D lane C2, salesagent-fo99.2):
# test_push_notification_config_repo_upsert_rejects_ssrf_url called
# repo.upsert(url=..., authentication_type=..., authentication_token=...) and asserted the
# repository's own "defense-in-depth" ValueError. Both the signature and that second gate
# CEASE TO EXIST in this lane: upsert now takes a ValidatedWebhookRegistration, which IS
# the receipt that the registration gate ran, so there is nothing left for the repository
# to re-check. The test's SUBJECT was deleted -- this is not a failing test rationalized
# away.
#
# The obligation it stood for (an SSRF URL is refused before a push config is persisted)
# remains graded, on this same file, at the two surfaces where the refusal happens:
#   * test_create_media_buy_rejects_push_config_before_workflow
#   * test_sync_creatives_rejects_unsafe_push_config_url


@pytest.mark.asyncio
async def test_create_media_buy_rejects_reporting_webhook_anyurl() -> None:
    """Registration gate must run for real ReportingWebhook.url (AnyUrl, not str)."""
    req = _minimal_create_request(reporting_webhook=_reporting_webhook(_METADATA_URL))
    with pytest.raises(AdCPUrlNotAllowedError) as exc_info:
        await _create_media_buy_impl(req, identity=_identity())
    assert exc_info.value.field == "reporting_webhook.url"


@pytest.mark.asyncio
async def test_create_media_buy_rejects_push_config_before_workflow() -> None:
    """PNC SSRF must run before workflow metadata write (wiring + ordering)."""
    req = _minimal_create_request()
    mock_ctx = MagicMock()
    with (
        patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx),
        pytest.raises(AdCPUrlNotAllowedError) as exc_info,
    ):
        # ON THE REQUEST: push_notification_config is a request field, so the SSRF check
        # reads it off req rather than from a parameter beside it. The ordering this test
        # pins -- refuse the URL BEFORE any workflow metadata is written -- is unchanged.
        req.push_notification_config = PushNotificationConfigRequestFactory.payload(url=_METADATA_URL)
        await _create_media_buy_impl(
            req,
            identity=_identity(),
        )
    assert exc_info.value.field == "push_notification_config.url"
    mock_ctx.create_workflow_step.assert_not_called()
    mock_ctx.create_context.assert_not_called()


def test_sync_creatives_rejects_unsafe_push_config_url() -> None:
    """sync_creatives must reject metadata URL at registration before DB work."""
    with pytest.raises(AdCPUrlNotAllowedError) as exc_info:
        _sync_creatives_impl(
            # creatives defaults to one valid item: this test is about the webhook URL gate, and
            # sync-creatives-request.json declares creatives minItems 1, so an empty array is a
            # request refused before the gate under test is ever reached.
            req=sync_creatives_request(push_notification_config={"url": _METADATA_URL}),
            identity=_identity(),
        )
    assert exc_info.value.field == "push_notification_config.url"


@pytest.mark.asyncio
async def test_the_url_the_gate_judged_is_the_url_that_is_dialled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The destination must not be rewritten into something else before the dial.

    ``send_notification`` used to gate ``push_notification_config.url`` and then
    rewrite ``localhost`` to ``host.docker.internal`` before dialling — a host that
    is itself blocked, so the URL the gate approved and the URL that reached the
    socket were different, and the second was one the gate exists to refuse. A gate
    whose verdict does not describe the dialled destination is advisory. The
    rewrite is gone (``send_notification``'s "no rewrite hop" note); this pins that
    it stays gone. ``localhost`` specifically, because that is the one hostname the
    deleted rewrite triggered on.

    RETARGETED AT THE SEAM. It used to read the dialled URL off
    ``capture_outbound_webhooks`` (a stub under the service's own client) and then
    re-run ``WebhookURLValidator.validate_outbound_webhook_url`` on it. Both
    subjects are gone: the service holds no client to capture, and the outbound
    validator was deleted when address policy moved into
    ``EgressPolicy.resolve_for_dial``. The seam makes the second leg STRUCTURAL
    rather than assertable — the URL that is validated is by construction the URL
    the pinned transport is built for — so what is left to grade, and what the
    defect was actually about, is that the hop put on the wire names the
    REGISTERED destination verbatim.

    Graded on the dispatch log, not on the return value: ``sent is True`` is
    equally true whether the destination was the registered one or a rewrite of it,
    so it cannot tell the two apart. The property still matters for signing — RFC
    9421's ``@target-uri`` and ``@authority`` cover whatever URL is finally dialled,
    so a rewrite would sign one authority and dial another.
    """
    service = ProtocolWebhookService()

    with _tls_origin(monkeypatch, listen_host="localhost") as origin:
        origin.respond_with(200)
        configured = f"{origin.base_url}/webhook"

        # Hatch open because loopback is the only place a unit test can stand a
        # real origin up; the hostname under test is the rewrite's trigger, and
        # the hatch decides the ADDRESS policy, not the rewrite.
        with _egress_hatches(private=True), _dispatched_hops() as hops:
            sent = await service.send_notification(_config(configured), payload=_PAYLOAD, task=_TASK)

        assert sent is True
        assert hops == [configured], (
            f"the gate judged {configured!r} but the process dialled {hops} — a destination the gate never saw"
        )
        assert origin.last_request.headers["Host"].startswith("localhost:"), (
            f"the registered authority was not the dialled one: {origin.last_request.headers['Host']!r}"
        )
