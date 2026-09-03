"""Vendor and operator egress, driven at a REAL local origin — the retry-drift gate.

GH #1802 migrates the ten remaining operator-configured call sites onto
``src/core/security/outbound_http.py``. The seam decides address policy, TLS
policy, redirect refusal, the response-size cap, the attempt count and the
backoff schedule, and it grades all six exactly once, in
``tests/integration/test_outbound_http.py``. Re-asserting any of them here would
be the duplication the seam exists to delete.

What this file grades is the one property the seam CANNOT decide for a call
site: **how many times the site is willing to hit the origin**. The seam takes
``max_attempts`` as an argument, so a migration that passes the wrong number —
or that leaves a second attempt-count wrapped around the method, where neither
the ``ruff-egress.toml`` import bans nor ``test_architecture_no_call_site_backoff``
can see it — turns one failed campaign create into three. Campaign, flight and
creative creation are POSTs; they are not idempotent; a buyer pays for the
duplicates.

So every case here points a real production method at a real HTTP server that
really ran, programs it to fail, and asserts on ``local_origin.hits`` — the
count of requests the server actually received. A transport mock's
``call_count`` cannot make that claim: it counts calls the test itself
arranged, on a code path the client never walked.

The private-range egress escape hatch is opened explicitly (``allow_local_origin``)
because a local origin is loopback, which the seam refuses by default and
rightly so; it is a no-op before the migration (``requests`` has no address
policy) and load-bearing after it, so the cases read identically on both sides
of the change. The origin itself is served over real TLS (``local_origin_tls``,
GH #1802) since the seam requires https unconditionally now — there is
no scheme hatch left to open.

Two of the ten sites had no origin-driven case here, and that was a finding
rather than a gap: a host spelled as a literal at its call site leaves a test
nothing to point anywhere. Both have since been hoisted to a module-level
constant in the services layer, so the gates they blocked are writable:

* ``src/admin/blueprints/settings.py``'s four Approximated calls became one
  ``APPROXIMATED_BASE_URL`` in ``src/services/approximated_client.py``
  (GH #1802). Still no origin-driven case here — the affordance
  exists, the happy paths remain to be written.
* ``src/admin/blueprints/auth.py``'s token exchange became
  ``GOOGLE_TOKEN_URL`` in ``src/services/google_oauth_client.py``
  (GH #1802), which is what lets the Google cases below drive real
  production code — service and ``gam_callback`` alike — at a real origin.

The injectability cases in section 3 pin both constants in place. They were the
red that unblocked the gates, and they stay as the regression that would name
the cause if either host were ever inlined back into a call site.
"""

from __future__ import annotations

import gzip
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from src.core.exceptions import AdCPError
from src.core.schemas import Principal, ReportingPeriod
from src.core.security.egress.attempts import OutboundDeliveryFailed
from src.core.security.outbound_http import OutboundError
from tests.harness._base import IntegrationEnv
from tests.helpers.local_http_origin import LocalOrigin, OriginResponse

# Reused rather than restated: which escape hatches a case opens is one decision
# with one home, and the backoff knob that keeps a retry case fast is the seam
# suite's own helper.
from tests.integration.property_list_helpers import allow_local_origin
from tests.integration.test_outbound_http import fast_backoff

pytestmark = [pytest.mark.integration]

_TODAY = datetime(2026, 7, 29, tzinfo=UTC)

# The exception TYPE a vendor call raises is exactly what the migration changes:
# ``requests.exceptions.RequestException`` today, an ``OutboundError`` or a mapped
# ``AdCPError`` once the site routes through the seam. This file grades attempt
# counts, so it names all three and leaves the taxonomy to be graded where it
# belongs — ``tests/integration/test_outbound_http.py`` for the seam's own
# classes, the adapter's error tests for the mapping.
_VENDOR_FAILURE = (requests.exceptions.RequestException, OutboundError, AdCPError)


class _BareEnv(IntegrationEnv):
    """Binds the factory session to the real database; patches nothing.

    The only egress in this file goes to a server that really ran, so an env
    that patched anything would be patching the thing under test.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Adapter construction — real schema objects, no Mock principals.
# ---------------------------------------------------------------------------


def _principal(adapter: str, mappings: dict[str, Any] | None = None) -> Principal:
    """A real ``Principal`` carrying an advertiser id for *adapter*."""
    return Principal(
        principal_id="test_principal",
        name="Test Buyer",
        platform_mappings=mappings or {adapter: {"advertiser_id": "123"}},
    )


def _kevel(origin: LocalOrigin):
    """A live-mode Kevel adapter whose vendor client dials the local origin.

    ``Kevel.__init__`` hardcodes ``https://api.kevel.co/v1`` and ignores
    ``config["base_url"]``, and its ``_vendor`` is a frozen
    ``VendorHttpClient`` built inside ``__init__`` — a post-construction
    ``adapter.base_url = ...`` no longer reaches the dial. Swapping the whole
    frozen client (keeping its real headers) is the only way in; Kevel's own
    constructor logic stays untouched.
    """
    from src.adapters.kevel import Kevel
    from src.adapters.vendor_http import VendorHttpClient, require_vendor

    adapter = Kevel(
        config={"network_id": "456", "api_key": "test-key"},
        principal=_principal("kevel"),
        dry_run=False,
        tenant_id="test_tenant",
    )
    real_headers = require_vendor(adapter._vendor, vendor="Kevel").headers
    adapter._vendor = VendorHttpClient(base_url=origin.base_url, headers=real_headers)
    return adapter


def _dry_run_kevel():
    """A dry-run Kevel adapter — constructed with no credentials at all."""
    from src.adapters.kevel import Kevel

    return Kevel(config={}, principal=_principal("kevel"), dry_run=True, tenant_id="test_tenant")


def _dry_run_triton():
    """A dry-run Triton adapter — constructed with no credentials at all."""
    from src.adapters.triton_digital import TritonDigital

    return TritonDigital(config={}, principal=_principal("triton"), dry_run=True, tenant_id="test_tenant")


def _triton(origin: LocalOrigin):
    from src.adapters.triton_digital import TritonDigital

    return TritonDigital(
        config={"base_url": origin.base_url, "auth_token": "test-token"},
        principal=_principal("triton"),
        dry_run=False,
        tenant_id="test_tenant",
    )


def _xandr(origin: LocalOrigin):
    """A concrete Xandr adapter pointed at the local origin.

    ``XandrAdapter`` does not implement four of ``AdServerAdapter``'s abstract
    methods, so it cannot be instantiated as shipped. The subclass supplies the
    missing names and nothing else — none of them is on the path under test.
    """
    from src.adapters.xandr import XandrAdapter

    class _ConcreteXandr(XandrAdapter):
        def add_creative_assets(self, *args, **kwargs): ...

        def associate_creatives(self, *args, **kwargs): ...

        def check_media_buy_status(self, *args, **kwargs): ...

        def update_media_buy_performance_index(self, *args, **kwargs): ...

    return _ConcreteXandr(
        config={
            "api_endpoint": origin.base_url,
            "username": "user",
            "password": "pass",
            "member_id": "789",
        },
        principal=_principal("xandr", {"xandr": {"advertiser_id": "123"}}),
        tenant_id="test_tenant",
    )


def _xandr_authenticated(origin: LocalOrigin):
    """A Xandr adapter that has already authenticated — the auth dial short-circuits.

    Seeding ``token``/``token_expiry`` alone used to be the whole shortcut, but a
    token is no longer what ``_make_request`` dials with: the credentialed
    ``VendorHttpClient`` is, and ``_authenticate`` is the only thing that builds
    it. Skipping auth therefore has to hand over the post-auth state *whole* —
    token, expiry and the client that carries the same token in its frozen
    headers — or the adapter is left in a state no real authentication can
    produce (a live token with nothing to dial through).
    """
    from src.adapters.vendor_http import VendorHttpClient

    adapter = _xandr(origin)
    adapter.token = "seeded-token"
    adapter.token_expiry = datetime.now(UTC) + timedelta(hours=1)
    adapter._vendor = VendorHttpClient(
        base_url=origin.base_url,
        headers={"Authorization": "seeded-token", "Content-Type": "application/json"},
    )
    return adapter


def _mock_ad_server(origin: LocalOrigin):
    """A mock adapter configured to post its HITL completion webhook at *origin*."""
    from src.adapters.mock_ad_server import MockAdServer

    principal = Principal(
        principal_id="test_principal",
        name="Test Buyer",
        platform_mappings={
            "mock": {
                "advertiser_id": "123",
                "hitl_config": {
                    "enabled": True,
                    "mode": "async",
                    "async_settings": {"webhook_url": origin.base_url, "webhook_on_complete": True},
                },
            }
        },
    )
    return MockAdServer(config={}, principal=principal, dry_run=False, tenant_id="test_tenant")


def _broadstreet(origin: LocalOrigin):
    from src.adapters.broadstreet.client import BroadstreetClient

    return BroadstreetClient(access_token="test-token", network_id="456", base_url=origin.base_url)


# ---------------------------------------------------------------------------
# Google's OAuth token exchange — the credentials a case sends, in one place.
#
# Named rather than inlined because the happy-path case asserts the very values
# the exchange was handed: a second copy of the secret written into the
# assertion could drift from the one that was sent and still pass, which is the
# opposite of grading what crossed the socket.
# ---------------------------------------------------------------------------

_GOOGLE_CLIENT_ID = "gam-oauth-client.apps.googleusercontent.com"
_GOOGLE_CLIENT_SECRET = "GOCSPX-gam-oauth-client-secret"
_GOOGLE_AUTH_CODE = "4/single-use-authorization-code"
_GOOGLE_CALLBACK_URI = "https://sales-agent.example/admin/auth/gam/callback"


def _point_google_token_url_at(origin: LocalOrigin, monkeypatch) -> None:
    """Re-point the Google token endpoint at *origin*.

    Monkeypatching the module constant IS the injection point: the service
    holds Google's endpoint as a typed ``VendorConstant`` (GH #1802),
    so a test can stand a local origin in for Google without production
    growing a knob to be configured wrong in a deployment. It is also why the
    exchange had to leave the Flask view — a literal inside ``gam_callback``
    had no seam at all.
    """
    from src.core.security.egress.destination import VendorConstant
    from src.services import google_oauth_client

    monkeypatch.setattr(google_oauth_client, "GOOGLE_TOKEN_URL", VendorConstant(url=f"{origin.base_url}/token"))


def _exchange_at(origin: LocalOrigin, monkeypatch):
    """Run the real token exchange against *origin* with the constants above."""
    from src.services.google_oauth_client import exchange_authorization_code

    _point_google_token_url_at(origin, monkeypatch)
    return exchange_authorization_code(
        _GOOGLE_AUTH_CODE,
        client_id=_GOOGLE_CLIENT_ID,
        client_secret=_GOOGLE_CLIENT_SECRET,
        redirect_uri=_GOOGLE_CALLBACK_URI,
    )


# ---------------------------------------------------------------------------
# 1. Retry drift — one failed vendor call must cost the origin exactly ONE hit.
# ---------------------------------------------------------------------------


def test_xandr_authenticate_does_not_retry_a_failing_origin(local_origin_tls, monkeypatch):
    """``XandrAdapter._authenticate`` must hit a 500 origin once, not three times.

    This is the headline case. ``_authenticate`` is wrapped in ``@api_retry``
    (``src/core/retry_utils.py``: ``max_attempts=3``), so the site owns an
    attempt count of its own — one that survives the migration untouched and
    that no structural guard can see, because ``no_call_site_backoff`` only
    matches an ``ast.Pow`` and ``retry_utils`` compounds with ``*=``.
    """
    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b'{"error": "boom"}')

    adapter = _xandr(local_origin_tls)

    with pytest.raises(_VENDOR_FAILURE):
        adapter._authenticate()

    assert local_origin_tls.hits == 1


def test_xandr_make_request_does_not_retry_a_failing_origin(local_origin_tls, monkeypatch):
    """``XandrAdapter._make_request`` must hit a 500 origin once.

    The token is pre-seeded so ``_authenticate`` returns early and every hit
    counted here belongs to the request itself — otherwise the two decorated
    methods nest and the number says nothing about either one.
    """
    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b'{"error": "boom"}')

    adapter = _xandr_authenticated(local_origin_tls)

    with pytest.raises(_VENDOR_FAILURE):
        adapter._make_request("POST", "/campaign", {"name": "x"})

    assert local_origin_tls.hits == 1


def test_xandr_make_request_compounds_its_retries_with_authenticate(local_origin_tls, monkeypatch):
    """A Xandr call on an expired token must still cost the origin ONE hit.

    ``_make_request`` calls ``_authenticate`` and both carry ``@api_retry``, so
    the two attempt counts multiply rather than add. This is the worst case a
    buyer can hit — the token expires, the vendor is down — and it is the one
    the ``max_attempts=1`` the migration passes to the seam would describe
    least accurately.
    """
    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b'{"error": "boom"}')

    adapter = _xandr(local_origin_tls)

    with pytest.raises(_VENDOR_FAILURE):
        adapter._make_request("POST", "/campaign", {"name": "x"})

    assert local_origin_tls.hits == 1


def test_kevel_update_does_not_retry_a_failing_origin(local_origin_tls, monkeypatch):
    """A failed Kevel campaign pause costs one hit and surfaces as a transient service failure.

    Asserts the WIRE contract (SERVICE_UNAVAILABLE / transient), not the Python
    class. A 5xx now arrives as ``OutboundDeliveryFailed``, which already IS an
    ``AdCPServiceUnavailableError`` with exactly the code and recovery
    ``AdCPAdapterError`` carried — the mapper re-raises it unchanged rather than
    rewrapping, which is the duplication this epic removes. Pinning the class here
    would grade an implementation detail the migration deliberately changes at
    every site.
    """
    from src.core.exceptions import AdCPError

    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b'{"error": "boom"}')

    adapter = _kevel(local_origin_tls)

    with pytest.raises(AdCPError) as exc_info:
        adapter.update_media_buy(
            media_buy_id="kevel_999",
            action="pause_media_buy",
            package_id=None,
            budget=None,
            today=_TODAY,
        )

    assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
    assert exc_info.value.recovery == "transient"
    assert local_origin_tls.hits == 1


def test_triton_status_check_does_not_retry_a_failing_origin(local_origin_tls, monkeypatch):
    """A failed Triton status check costs one hit and degrades to ``unknown``.

    The degradation is asserted alongside the count because it is the arm the
    migration has to preserve: ``except requests.exceptions.RequestException``
    narrows to ``except OutboundError``, and a miss there turns a soft
    "unknown" into a raised error on a read path.
    """
    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b'{"error": "boom"}')

    adapter = _triton(local_origin_tls)
    response = adapter.check_media_buy_status(media_buy_id="triton_999", today=_TODAY)

    assert response.status == "unknown"
    assert local_origin_tls.hits == 1


def test_broadstreet_request_does_not_retry_a_failing_origin(local_origin_tls, monkeypatch):
    """A 500 from Broadstreet costs one hit and raises ``BroadstreetAPIError``."""
    from src.adapters.broadstreet.client import BroadstreetAPIError

    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b'{"error": "boom"}')

    client = _broadstreet(local_origin_tls)

    with pytest.raises(BroadstreetAPIError) as exc_info:
        client.get("/networks")

    assert exc_info.value.status_code == 500
    assert local_origin_tls.hits == 1


def test_mock_ad_server_webhook_does_not_retry_a_failing_origin(local_origin_tls, monkeypatch):
    """The HITL completion webhook costs one hit and never raises."""
    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b'{"error": "boom"}')

    adapter = _mock_ad_server(local_origin_tls)
    adapter._send_completion_webhook("step_1", approved=True)

    assert local_origin_tls.hits == 1


def test_gam_report_download_does_not_retry_a_failing_origin(local_origin_tls, monkeypatch):
    """A failed GAM report download costs one hit.

    The GAM SOAP client is a plain fake, not a transport mock: it stands in for
    the external ad server that hands back a download URL, which is the only
    way this call site can be reached at all. The HTTP that follows is real.

    ``ReportingConfig.ALLOWED_DOMAINS`` is widened for the same reason
    ``allow_local_origin`` opens the seam's hatches — the check is a
    Google-provenance guard on the URL, and a loopback origin is not Google.
    """
    from src.adapters import gam_reporting_service as grs

    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    monkeypatch.setattr(grs.ReportingConfig, "ALLOWED_DOMAINS", ["127.0.0.1"])
    local_origin_tls.respond_with(500, body=b"boom")

    service = _gam_reporting_service(local_origin_tls)

    # The TYPED error reaches the caller. `_run_report` used to catch it and
    # rewrap it as `Exception("Error running GAM report: ...")`, so the only thing
    # to assert on was that relabelled string -- and the download branch's
    # migration onto `raise_mapped_outbound_error` bought nothing observable,
    # because this outer handler swallowed the classification on the way out.
    # An `except AdCPError: raise` arm ahead of the catch-all is what changed, and
    # this is where it shows: the seam's own class, its attempt count, and its
    # fixed message survive to the caller.
    with pytest.raises(OutboundDeliveryFailed) as raised:
        service._run_report({"reportQuery": {}})

    assert raised.value.attempts == 1, (
        f"a report download must not retry a failing origin; the error records {raised.value.attempts} attempts"
    )
    assert raised.value.http_status == 500, (
        f"the last observed status is part of what the typed error carries and the "
        f"bare rewrap lost; got {raised.value.http_status!r}"
    )
    assert local_origin_tls.hits == 1


def test_base_workflow_slack_notification_reaches_the_origin_once(local_origin_tls, monkeypatch, integration_db):
    """The workflow Slack notification must reach its webhook exactly once.

    Exactly once, not zero: the count grades both halves of the obligation, and
    a site that never fires cannot have its attempt count migrated correctly
    either.
    """
    from src.adapters.base_workflow import BaseWorkflowManager
    from src.core.config_loader import current_tenant

    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(200, body=b"ok")

    # The tenant context is a ContextVar, so it is reset explicitly rather than
    # left behind for whichever test this worker runs next.
    token = current_tenant.set(
        {
            "tenant_id": "test_tenant",
            "name": "Test Tenant",
            "slack_webhook_url": local_origin_tls.base_url,
            "slack": {"webhook_url": local_origin_tls.base_url},
        }
    )
    try:
        manager = BaseWorkflowManager(tenant_id="test_tenant")
        manager._send_workflow_notification("step_1", {"platform": "mock", "automation_mode": "manual"})
    finally:
        current_tenant.reset(token)

    assert local_origin_tls.hits == 1


def test_admin_slack_test_message_does_not_retry_a_failing_origin(
    local_origin_tls, monkeypatch, authenticated_admin_session, integration_db
):
    """``POST /tenant/<id>/test_slack`` costs the webhook one hit on a 500."""
    from tests.factories import TenantFactory

    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(500, body=b"boom")

    with _BareEnv():
        TenantFactory(tenant_id="slack_test_tenant", slack_webhook_url=local_origin_tls.base_url)

    response = authenticated_admin_session.post("/tenant/slack_test_tenant/test_slack")

    assert response.get_json()["success"] is False
    assert local_origin_tls.hits == 1


def test_google_token_exchange_does_not_retry_a_rejecting_origin(local_origin_tls, monkeypatch):
    """A rejected authorization code costs the token endpoint exactly ONE hit.

    An authorization code is single-use, so a retried exchange cannot succeed —
    it only burns the code and turns one operator-visible failure into three.

    The failure is asserted to PROPAGATE out of the service uncaught, carrying
    the status. Interpreting a vendor status is the caller's job — the
    blueprint's flash wording keys off ``exc.http_status`` — and it is only
    reachable if the service refuses to swallow or re-shape the error, the same
    split ``approximated_client.get_dns_token`` documents.

    NOTE: a 400 is terminal at the seam regardless of ``max_attempts`` (it is
    not in ``_RETRYABLE_STATUSES``), so ``hits == 1`` here grades the
    propagation split, not the attempt COUNT — a ``max_attempts`` regression
    would not redden this case. See
    ``test_google_token_exchange_does_not_retry_a_retryable_failure`` (503,
    matching every other retry-drift case in this file) for that grader.
    """
    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(400, body=b'{"error": "invalid_grant"}')

    with pytest.raises(OutboundError) as exc_info:
        _exchange_at(local_origin_tls, monkeypatch)

    assert exc_info.value.http_status == 400
    assert local_origin_tls.hits == 1


def test_google_token_exchange_does_not_retry_a_retryable_failure(local_origin_tls, monkeypatch):
    """A retryable (503) failure still costs the token endpoint exactly ONE hit.

    The sibling 400 case above cannot see a ``max_attempts`` regression — 400
    is terminal at the seam no matter what the call site asks for. 503 IS in
    ``_RETRYABLE_STATUSES``, so this is the case (matching every other
    retry-drift case in this file, e.g. ``test_kevel_update_does_not_retry_a_failing_origin``)
    that would actually redden if the service's ``max_attempts=1`` regressed:
    a retried exchange burns a single-use authorization code, turning one
    operator-visible failure into three.
    """
    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')

    with pytest.raises(OutboundError) as exc_info:
        _exchange_at(local_origin_tls, monkeypatch)

    assert exc_info.value.http_status == 503
    assert local_origin_tls.hits == 1


def test_gam_callback_flashes_googles_rejection_on_a_400(local_origin_tls, monkeypatch, admin_client):
    """``GET /auth/gam/callback`` turns Google's 400 into the operator's message.

    This is the arm of the extraction that must NOT move: the service raises,
    and the status-keyed wording stays in the blueprint because it is UI copy,
    not vendor logic. The message is asserted whole rather than by substring —
    it names the three causes a 400 collapses (expired code, redirect-URI
    mismatch, invalid credentials) precisely because Google's own body is gone
    by the time the seam raises, so an operator who loses a word of it loses
    the only diagnosis they get.

    Driven at a real origin rather than by patching the exchange out: a mocked
    exception proves the ``except`` arm can be entered, not that a real 400
    from a real socket arrives there as an ``OutboundError`` whose
    ``http_status`` is 400. Only the second claim survives the extraction.
    """
    from src.core import config
    from src.core.config import GAMOAuthConfig

    allow_local_origin(monkeypatch)
    fast_backoff(monkeypatch)
    _point_google_token_url_at(local_origin_tls, monkeypatch)
    local_origin_tls.respond_with(400, body=b'{"error": "invalid_grant"}')
    # Real credentials object, not a mock: the view reads ``client_id`` and
    # ``client_secret`` off it and puts both on the wire.
    monkeypatch.setattr(
        config,
        "get_gam_oauth_config",
        lambda: GAMOAuthConfig(client_id=_GOOGLE_CLIENT_ID, client_secret=_GOOGLE_CLIENT_SECRET),
    )

    response = admin_client.get(f"/auth/gam/callback?code={_GOOGLE_AUTH_CODE}&state=gam_oauth_tenant")

    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/tenant/gam_oauth_tenant/settings"
    with admin_client.session_transaction() as session:
        assert session["_flashes"] == [
            (
                "error",
                "Google rejected the authorization code. This is usually an expired code, a "
                "redirect-URI mismatch, or invalid OAuth credentials — try again, and contact "
                "your administrator if it persists.",
            )
        ]
    assert local_origin_tls.hits == 1


# ---------------------------------------------------------------------------
# 2. Happy paths — the vendor shapes the adapters actually parse.
# ---------------------------------------------------------------------------


def test_broadstreet_get_returns_the_parsed_body(local_origin_tls, monkeypatch):
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(200, body=b'{"networks": [{"id": 456}]}')

    client = _broadstreet(local_origin_tls)

    assert client.get("/networks") == {"networks": [{"id": 456}]}
    assert local_origin_tls.hits == 1


def test_broadstreet_sends_its_credential_in_the_query_string(local_origin_tls, monkeypatch):
    """Broadstreet authenticates by QUERY PARAMETER, so the credential is graded on the socket.

    The client's only other credential coverage asserted on a URL-builder's return
    string. A builder can be correct and the request still ship without the token —
    nothing downstream would notice, because this origin does not check auth, the hit
    count is 1 either way, and the body still parses. This reads the path the origin
    actually received.
    """
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(200, body=b'{"networks": []}')

    client = _broadstreet(local_origin_tls)
    client.get("/networks")

    assert "access_token=test-token" in local_origin_tls.requests[0].path


def test_broadstreet_report_sends_credential_and_caller_params_in_one_path(local_origin_tls, monkeypatch):
    """The per-call query params and the client's credential arrive TOGETHER.

    Two ways to pass this test wrongly: send the credential and drop the caller's
    dates, or send the dates and drop the credential. Both are single-key checks
    passing while the request is unusable, so this asserts all three keys on the one
    path the origin saw.
    """
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(200, body=b'{"records": []}')

    client = _broadstreet(local_origin_tls)
    client.get_advertisement_report(
        advertiser_id="adv-1",
        advertisement_id="ad-1",
        start_date="2026-01-01",
        end_date="2026-01-31",
    )

    path = local_origin_tls.requests[0].path
    assert "access_token=test-token" in path
    assert "start_date=2026-01-01" in path
    assert "end_date=2026-01-31" in path


def test_triton_status_check_reads_an_active_campaign(local_origin_tls, monkeypatch):
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(200, body=b'{"active": true, "endDate": "2030-01-01T00:00:00+00:00"}')

    adapter = _triton(local_origin_tls)
    response = adapter.check_media_buy_status(media_buy_id="triton_999", today=_TODAY)

    assert response.status == "active"
    assert local_origin_tls.hits == 1


def test_triton_delivery_report_csv_decodes_the_vendors_charset(local_origin_tls, monkeypatch):
    """Triton's delivery-report CSV decodes using the origin's OWN Content-Type charset, not a silent guess.

    ``OutboundResult.text`` (GH #1802) replicates
    ``httpx.Response.text``'s charset-detection stdlib-only: the Content-Type
    header's charset parameter when Python knows the codec, UTF-8 otherwise.
    This is the one migrated ``.text`` read (``triton_digital.py:488``,
    the delivery-report CSV parse) that mutation testing
    (GH #1802) found had NO behavioral grader at all — the two
    pre-existing Triton tests in this file exercise ``check_media_buy_status``,
    which reads ``.json()``, not this CSV-report branch.

    The report body is genuinely ``iso-8859-1`` (a single ``0xE9`` byte for
    'é'), which is NOT valid UTF-8 — decoding it against the wrong charset
    would corrupt the package name into the U+FFFD replacement character, not
    merely look slightly off. Asserting the exact ``package_id`` string is
    what makes this a real test of the charset-DETECTION path rather than of
    ASCII-only bytes any decode would happen to get right.
    """
    allow_local_origin(monkeypatch)
    report_url = f"{local_origin_tls.base_url}/reports/report-1.csv"
    csv_body = "flightName,impressions,totalRevenue\r\nCafé Morning Drive,50000,1250.50\r\n".encode("iso-8859-1")

    local_origin_tls.respond_in_sequence(
        [
            (200, b'{"id": "job-1"}'),
            (200, f'{{"status": "COMPLETED", "url": "{report_url}"}}'.encode()),
            OriginResponse(status=200, body=csv_body, content_type="text/csv; charset=iso-8859-1"),
        ]
    )

    adapter = _triton(local_origin_tls)
    date_range = ReportingPeriod(start=_TODAY - timedelta(days=7), end=_TODAY)

    result = adapter.get_media_buy_delivery(media_buy_id="triton_999", date_range=date_range, today=_TODAY)

    assert local_origin_tls.hits == 3, f"expected 3 dials (submit, poll, download); origin saw {local_origin_tls.hits}"
    assert result.totals.impressions == 50000
    assert result.totals.spend == 1250.50
    assert len(result.by_package) == 1
    assert result.by_package[0].package_id == "Café Morning Drive", (
        f"package_id decoded as {result.by_package[0].package_id!r} — a UTF-8-only decode of this "
        "iso-8859-1 byte sequence would corrupt 'é' into the U+FFFD replacement character"
    )
    assert result.by_package[0].impressions == 50000
    assert result.by_package[0].spend == 1250.50


def test_kevel_pause_succeeds_against_a_local_origin(local_origin_tls, monkeypatch):
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(200, body=b'{"Id": 999, "IsActive": false}')

    adapter = _kevel(local_origin_tls)
    response = adapter.update_media_buy(
        media_buy_id="kevel_999",
        action="pause_media_buy",
        package_id=None,
        budget=None,
        today=_TODAY,
    )

    assert response.media_buy_id == "kevel_999"
    assert local_origin_tls.hits == 1


def test_xandr_make_request_returns_the_parsed_body(local_origin_tls, monkeypatch):
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(200, body=b'{"response": {"status": "OK", "id": 42}}')

    adapter = _xandr_authenticated(local_origin_tls)

    assert adapter._make_request("GET", "/campaign") == {"response": {"status": "OK", "id": 42}}
    assert local_origin_tls.hits == 1


def test_xandr_rebuilds_its_vendor_client_when_the_token_rotates(local_origin_tls, monkeypatch):
    """A rotated Xandr token reaches the wire because the whole client was REBUILT.

    Two calls, an expiry forced into the past between them, so both really
    authenticate: the origin sees auth, request, auth, request and answers the
    two auth dials with *different* tokens. What is graded is the header on the
    two request dials — ``tok-1`` then ``tok-2`` — read off the bytes the server
    received, plus the object identity of ``adapter._vendor`` across the
    rotation. Identity is the load-bearing half: an equal-but-mutated client
    would satisfy the header assertion today and still leave a live object whose
    ``Authorization`` and ``base_url`` could drift apart under a partial update.
    Replacing the frozen client whole is what makes that state unrepresentable.

    The auth dials themselves must carry no ``Authorization`` at all — the
    bootstrap client is uncredentialed by construction, which is precisely why
    it can exist before a token does.
    """
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_in_sequence(
        [
            (200, b'{"response": {"status": "OK", "token": "tok-1"}}'),
            (200, b'{"response": {"status": "OK", "id": 1}}'),
            (200, b'{"response": {"status": "OK", "token": "tok-2"}}'),
            (200, b'{"response": {"status": "OK", "id": 2}}'),
        ]
    )

    adapter = _xandr(local_origin_tls)

    assert adapter._make_request("POST", "/campaign", {"name": "first"}) == {"response": {"status": "OK", "id": 1}}
    client_before_rotation = adapter._vendor

    adapter.token_expiry = datetime.now(UTC) - timedelta(seconds=1)

    assert adapter._make_request("POST", "/campaign", {"name": "second"}) == {"response": {"status": "OK", "id": 2}}
    client_after_rotation = adapter._vendor

    # auth #1, request #1, auth #2, request #2 — one attempt per dial, four dials.
    assert local_origin_tls.hits == 4
    assert [req.path for req in local_origin_tls.requests] == ["/auth", "/campaign", "/auth", "/campaign"]
    # The auth dial's verb went from IMPLICIT (outbound_http.send's default
    # POST) to EXPLICIT (_bootstrap.call("POST", ...)) in this migration —
    # exactly where a typo hides, so it is graded on the wire like the rest.
    assert [req.method for req in local_origin_tls.requests] == ["POST", "POST", "POST", "POST"]
    assert local_origin_tls.requests[0].headers.get("Authorization") is None
    assert local_origin_tls.requests[1].headers["Authorization"] == "tok-1"
    assert local_origin_tls.requests[2].headers.get("Authorization") is None
    assert local_origin_tls.requests[3].headers["Authorization"] == "tok-2"
    assert client_before_rotation is not client_after_rotation


def test_mock_ad_server_webhook_posts_the_completion_payload(local_origin_tls, monkeypatch):
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(200, body=b"ok")

    adapter = _mock_ad_server(local_origin_tls)
    adapter._send_completion_webhook("step_1", approved=True)

    payload = local_origin_tls.last_request.json()
    assert payload["event"] == "task_completed"
    assert payload["step_id"] == "step_1"
    assert payload["status"] == "completed"
    assert local_origin_tls.hits == 1


def test_gam_report_download_parses_the_gzipped_csv(local_origin_tls, monkeypatch):
    from src.adapters import gam_reporting_service as grs

    allow_local_origin(monkeypatch)
    monkeypatch.setattr(grs.ReportingConfig, "ALLOWED_DOMAINS", ["127.0.0.1"])

    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
        gz.write(b"Dimension.DATE,Column.AD_SERVER_IMPRESSIONS\n2026-07-29,100\n")
    local_origin_tls.respond_with(200, body=buffer.getvalue(), content_type="application/octet-stream")

    service = _gam_reporting_service(local_origin_tls)
    rows = service._run_report({"reportQuery": {}})

    assert rows == [{"Dimension.DATE": "2026-07-29", "Column.AD_SERVER_IMPRESSIONS": "100"}]
    assert local_origin_tls.hits == 1


def test_google_token_exchange_posts_the_form_encoded_body(local_origin_tls, monkeypatch):
    """The exchange puts the five OAuth fields on the wire, form-encoded, once.

    Every assertion here reads bytes the server received. That matters more
    than usual for this call: the seam offers ``json=`` and ``content=``, and a
    ``json=`` body type-checks perfectly while sending ``application/json`` to
    an endpoint that only accepts ``application/x-www-form-urlencoded`` — a
    swap no unit test of the caller could see, and one Google would answer with
    the same opaque 400 as a genuinely bad code.

    The parsed return is asserted as a whole ``GoogleTokenResponse`` rather
    than just ``.refresh_token``: constructing it is the parse boundary this
    lane exists to create, so every field it claims to model is graded against
    what the origin actually sent.
    """
    from src.services.google_oauth_client import GoogleTokenResponse

    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(
        200,
        body=(
            b'{"refresh_token": "1//rt-graded", "access_token": "ya29.at-graded", '
            b'"expires_in": 3599, "token_type": "Bearer", '
            b'"scope": "https://www.googleapis.com/auth/dfp"}'
        ),
    )

    result = _exchange_at(local_origin_tls, monkeypatch)

    assert result == GoogleTokenResponse(
        refresh_token="1//rt-graded",
        access_token="ya29.at-graded",
        expires_in=3599,
        token_type="Bearer",
        scope="https://www.googleapis.com/auth/dfp",
    )
    request = local_origin_tls.last_request
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.body.decode()) == {
        "client_id": [_GOOGLE_CLIENT_ID],
        "client_secret": [_GOOGLE_CLIENT_SECRET],
        "code": [_GOOGLE_AUTH_CODE],
        "grant_type": ["authorization_code"],
        "redirect_uri": [_GOOGLE_CALLBACK_URI],
    }
    assert local_origin_tls.hits == 1


def test_google_token_exchange_ignores_a_field_it_does_not_model(local_origin_tls, monkeypatch):
    """A key the dataclass does not model must not fail a SUCCESSFUL exchange.

    The dict this replaces read ``token_data.get("refresh_token")`` and ignored
    everything else, so a field Google adds — or returns only for some scope
    combination, ``id_token`` being the obvious one — was harmless. A
    ``GoogleTokenResponse(**data)`` splat would turn the same body into a
    ``TypeError`` raised inside ``gam_callback``'s try block, which the view's
    outer ``except Exception`` reports to the operator as "OAuth callback
    failed" on an exchange that in fact succeeded, with the refresh token
    thrown away. Typing the response must not cost that tolerance: the parse is
    a closed field set that DROPS what it does not model, not one that rejects
    it.
    """
    allow_local_origin(monkeypatch)
    local_origin_tls.respond_with(
        200,
        body=b'{"refresh_token": "1//rt-tolerant", "id_token": "eyJhbGciOi.stub", "a_field_google_adds_later": 1}',
    )

    assert _exchange_at(local_origin_tls, monkeypatch).refresh_token == "1//rt-tolerant"
    assert local_origin_tls.hits == 1


# ---------------------------------------------------------------------------
# 3. The two hoisted vendor hosts — the affordance their gates needed.
# ---------------------------------------------------------------------------


def test_approximated_base_url_is_injectable():
    """The Approximated service's four calls must not hardcode the host.

    Gate 2 of the ticket ("adapter happy paths against a local origin") cannot
    be written for these four while ``https://cloud.approximated.app`` is a
    string literal repeated at each call site: a test has no seam to point
    anywhere. Hoisting the base to one module-level constant is what makes the
    gate writable, and it deletes three copies of a URL at the same time.

    Lives in ``src/services/approximated_client.py`` since GH #1802
    moved the vendor client out of the admin blueprint into the services layer
    — every other operator-configured vendor already routed through
    ``src/adapters/`` or a service, not a Flask blueprint.
    """
    from src.services import approximated_client

    base = getattr(approximated_client, "APPROXIMATED_BASE_URL", None)
    assert base is not None, (
        "src/services/approximated_client.py hardcodes https://cloud.approximated.app at "
        "each of its four call sites, so none of them can be driven at a local "
        "origin. Hoist the host to a module-level APPROXIMATED_BASE_URL."
    )

    # GH #1802: an injectable constant is not enough on its own — the
    # ticket #1802/F11 shape was exactly this, an os.environ.get(...) default,
    # which is "injectable" (this assertion passed against it too) yet also an
    # import-time credential-redirection knob. The constant must be the typed
    # VendorConstant (src/core/security/egress/destination.py), not a bare
    # string built from an env read, so the destination-rewrite guard's env-
    # sourced-destination detector has something to type-check against.
    from src.core.security.egress.destination import VendorConstant

    assert isinstance(base, VendorConstant), (
        "APPROXIMATED_BASE_URL must be a VendorConstant (src/core/security/egress/destination.py), "
        f"not a bare {type(base).__name__} built from os.environ.get(...) — the vendor-constant type "
        "is what makes an env-sourced redirect of this credentialed endpoint unconstructible."
    )
    assert base.url == "https://cloud.approximated.app"


def test_google_token_url_is_injectable():
    """The OAuth token exchange must not hardcode Google's host at its call site.

    Gate 3 asks for the token POST to succeed against a local origin standing
    in for Google. That is unreachable while the URL is a literal inside the
    view function.

    Lives in ``src/services/google_oauth_client.py`` since GH #1802
    moved the exchange out of the admin blueprint into the services layer —
    the same move ``approximated_client`` made above, for the same reason: an
    operator-configured vendor call belongs in ``src/adapters/`` or a service,
    never in a Flask view.
    """
    from src.services import google_oauth_client

    token_url = getattr(google_oauth_client, "GOOGLE_TOKEN_URL", None)
    assert token_url is not None, (
        "The Google token exchange hardcodes https://oauth2.googleapis.com/token at its "
        "call site, so it cannot be driven at a local origin. Hoist it to a module-level "
        "GOOGLE_TOKEN_URL in src/services/google_oauth_client.py."
    )

    # GH #1802: same requirement as APPROXIMATED_BASE_URL above — a
    # non-None module attribute is not enough; it must be the typed
    # VendorConstant, never a bare string (env-sourced or otherwise), so the
    # env-sourced-destination guard has a type to check the site is built from.
    from src.core.security.egress.destination import VendorConstant

    assert isinstance(token_url, VendorConstant), (
        "GOOGLE_TOKEN_URL must be a VendorConstant (src/core/security/egress/destination.py), "
        f"not a bare {type(token_url).__name__} — see APPROXIMATED_BASE_URL's identical requirement above."
    )
    assert token_url.url == "https://oauth2.googleapis.com/token"


# ---------------------------------------------------------------------------
# 4. Construction is the proof — a dial exists only where credentials do.
#
# The cases above grade what a vendor call COSTS the origin. These grade
# whether the call is reachable at all. ``AdServerAdapter._api`` type-checks on
# every subclass — GAM, mock, Broadstreet — off two bare annotations no
# ``__init__`` outside Kevel and Triton ever assigns, and on Kevel and Triton
# it type-checks in the dry-run branch too, where the credentials were never
# supplied. A frozen ``VendorHttpClient`` built where the credentials are
# proven turns "can dial the vendor" into a value the type system can see, and
# the un-credentialed case into ``None`` that ``require_vendor`` refuses by
# name rather than an ``AttributeError`` mid-flight.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vendor", ["Kevel", "Triton Digital", "Xandr"])
def test_require_vendor_refuses_an_unconfigured_client(vendor):
    """``require_vendor(None, ...)`` raises a typed, vendor-named configuration error.

    The vendor is parametrized because the name has to be interpolated from the
    argument, not baked into one adapter's copy of the guard: exactly one
    ``require_vendor`` exists in ``src/`` (DRY tier-1), so it must be able to
    speak for every vendor that holds a ``VendorHttpClient``.
    """
    from src.adapters.vendor_http import require_vendor
    from src.core.exceptions import AdCPConfigurationError

    with pytest.raises(AdCPConfigurationError) as exc_info:
        require_vendor(None, vendor=vendor)

    assert str(exc_info.value) == f"{vendor} credentials are not configured; cannot dial the vendor API."


def test_vendor_client_refuses_post_construction_mutation():
    """``VendorHttpClient`` is frozen: the dial cannot be re-pointed after construction.

    The freeze is what makes the construction site the whole proof. If the base
    or the headers could be swapped afterwards, an adapter could be handed a
    client built from credentials it never had, which is the defect restated
    one attribute over.
    """
    from dataclasses import FrozenInstanceError

    from src.adapters.vendor_http import VendorHttpClient

    client = VendorHttpClient(base_url="https://vendor.example/v1", headers={"X-Key": "k"})

    with pytest.raises(FrozenInstanceError):
        client.base_url = "https://elsewhere.example/v1"


@pytest.mark.parametrize("build_dry_run_adapter", [_dry_run_kevel, _dry_run_triton], ids=["kevel", "triton"])
def test_a_dry_run_adapter_holds_no_vendor_client(build_dry_run_adapter):
    """A dry-run adapter constructs with ``_vendor is None`` — nothing to dial with.

    Construction-side proof, and the half that ``require_vendor`` depends on:
    dry-run supplies no ``api_key``/``auth_token``, so there is no credentialed
    client to build, and the attribute must say so rather than be absent (an
    absent attribute is the ``AttributeError`` the bare base-class annotations
    already produce today).
    """
    adapter = build_dry_run_adapter()

    assert adapter._vendor is None


def test_an_unauthenticated_xandr_adapter_holds_no_vendor_client(local_origin_tls):
    """A freshly-constructed Xandr adapter has ``_vendor is None`` until it authenticates.

    Xandr's ``None`` means something different from Kevel's and Triton's above:
    the adapter has no dry-run concept at all, and its credential is not config,
    it is a token the vendor issues. So the un-dialable state is not a mode, it
    is simply "before the first ``/auth``" — and it must be the same ``None``
    ``require_vendor`` refuses by name, not a missing attribute that surfaces as
    an ``AttributeError`` halfway through a campaign create.

    The origin fixture only supplies a base URL to construct against; this case
    opens no egress hatch and dials nothing, which is the point.
    """
    adapter = _xandr(local_origin_tls)

    assert adapter._vendor is None


# ---------------------------------------------------------------------------
# GAM SOAP stand-in — a fake external ad server, not a transport mock.
# ---------------------------------------------------------------------------


class _FakeReportService:
    """The four ``ReportService`` methods ``_run_report`` calls, and nothing else."""

    def __init__(self, download_url: str) -> None:
        self._download_url = download_url

    def runReportJob(self, report_job):  # noqa: N802 - GAM SOAP name
        return {"id": "report_job_1"}

    def getReportJobStatus(self, report_job_id):  # noqa: N802 - GAM SOAP name
        return "COMPLETED"

    def getReportDownloadURL(self, report_job_id, export_format):  # noqa: N802 - GAM SOAP name
        return self._download_url


class _FakeGAMClient:
    def __init__(self, download_url: str) -> None:
        self._report_service = _FakeReportService(download_url)

    def GetService(self, name):  # noqa: N802 - GAM SDK name
        return self._report_service


def _gam_reporting_service(origin: LocalOrigin):
    """A reporting service whose report downloads land on *origin*.

    ``network_timezone`` is passed so construction never reaches
    ``NetworkService`` — the timezone lookup is not on the path under test.
    """
    from src.adapters.gam_reporting_service import GAMReportingService

    return GAMReportingService(
        _FakeGAMClient(f"{origin.base_url}/report.csv.gz"),
        network_timezone="America/New_York",
    )
