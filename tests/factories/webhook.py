"""Factories for the webhook models: the stored config, and a delivery's task identity."""

from __future__ import annotations

import factory
from adcp.types import NotificationConfig as LibraryNotificationConfig
from adcp.types import PushNotificationConfig as LibraryPushNotificationConfig
from adcp.types import ReportingWebhook as LibraryReportingWebhook
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import PushNotificationConfig
from src.core.webhooks.delivery import WebhookTaskContext
from tests.factories.core import TenantFactory
from tests.factories.principal import PrincipalFactory
from tests.factories.request import _RequestFactory


class PushNotificationConfigFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PushNotificationConfig
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    principal = SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))

    id = Sequence(lambda n: f"webhook_{n:04d}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    url = factory.LazyFunction(lambda: "https://example.com/webhook")
    is_active = True
    # A conformant buyer supplies one at registration, and it is what makes the seller's
    # envelope valid: `operation_id` is REQUIRED on core/mcp-webhook-payload.json, the
    # seller MUST echo the registered value verbatim, and the spec forbids recovering it
    # from the URL. Defaulted here so a fixture that omits it is not silently registering a
    # webhook no conformant body can be built for.
    operation_id = Sequence(lambda n: f"op_{n:04d}")


class WebhookTaskContextFactory(factory.Factory):
    """A delivery's task identity — the value the sender carries to the delivery row.

    Seven fields, all required on the frozen dataclass, so every test that drives
    a sender had to spell all seven. Three files had spelled the identical
    literal, which is the copy-paste-with-variable-substitution shape the
    duplication ratchet exists to refuse (it went 72 -> 73 and failed the Quality
    Gate).

    ``sequence_number`` and ``notification_type`` default to the values a first,
    unmarked delivery carries. Both are worth overriding explicitly in any test
    that grades what reaches ``webhook_delivery_log``: they were re-derived from
    the payload downstream until the typed context travelled whole, so a test
    that leaves them at their defaults is not grading them.
    """

    class Meta:
        model = WebhookTaskContext

    task_id = "task-1"
    task_type = "create_media_buy"
    tenant_id = None
    principal_id = None
    media_buy_id = None
    sequence_number = 1
    notification_type = None


# ── The buyer-sent side: THREE schemas, three factories ────────────────────────
#
# ``PushNotificationConfigFactory`` above owns the STORED (ORM) row and has 14
# users. The buyer-sent side had none: 24 dict literals across 14 files in seven
# shapes, which is how two misspellings survived (``frequency`` for
# ``reporting_frequency``, ``events`` for ``event_types``) -- an unknown key is
# accepted by the model and dropped on the way out, so a scenario that
# "configures daily reporting" configured nothing.
#
# Those seven shapes were never one concept. The pin declares THREE webhook
# configs, with different required sets and different jobs:
#
#   push-notification-config   url                                    async TASK notifications
#   notification-config        subscriber_id, url, event_types        account-level subscription
#   reporting-webhook          url, authentication, reporting_frequency   automated REPORTING
#
# Each factory binds the SDK class rather than a local copy, so the shape moves
# with the pin. All three are exported from ``adcp.types``; none needed generating.


# ── Sending a webhook config the seller must REJECT ───────────────────────────
#
# A conformant baseline is only half of what these schemas need graded. The other
# half is that a malformed one comes back as an error envelope FROM THE SELLER --
# and that half has a trap in it.
#
# ``payload(**overrides)`` applies its overrides AFTER ``model_dump``, so it can
# carry a value the model itself would refuse; ``OMIT`` deletes a key outright.
# Verified against the pinned schemas, every one of these produces a document the
# schema rejects::
#
#     R = ReportingWebhookRequestFactory
#     R.payload(reporting_frequency="fortnightly")  # not one of hourly/daily/monthly
#     R.payload(reporting_frequency=OMIT)           # required property missing
#     R.payload(authentication=OMIT)                # required property missing
#     R.payload(url=12345)                          # not of type string
#     NotificationConfigRequestFactory.payload(event_types=["not_a_real_event"])
#
# THE TRAP: dispatching that through the ordinary seam grades NOTHING. The typed
# request is built in the test process, so pydantic raises there, production is
# never reached, and the scenario proves something about the model instead of the
# server -- transport framing, boundary translation, and which code each transport
# actually emits all go ungraded. prkv.33 measured that blast radius: all 86 UC-005
# instances recorded ``dispatched=False``.
#
# So a negative-path scenario dispatches the LITERAL payload
# (``when_request._call_raw`` / ``dispatch_request``) and asserts on the wire::
#
#     assert_envelope_shape(result.wire_error_envelope, "INVALID_REQUEST")
#
# ``build()`` is the opposite seam and stays correct for the positive path: it
# routes overrides through the model, so the caller gets DTO validation.


def hmac_authentication(credentials: str = "s" * 40) -> dict[str, object]:
    """An ``authentication`` block for an HMAC-signed webhook.

    ONE scheme, because the pinned schema allows at most one -- a two-scheme
    document is a document the spec forbids, and a test that builds one is
    asserting a refusal rather than configuring a webhook.
    """
    return {"schemes": ["HMAC-SHA256"], "credentials": credentials}


class PushNotificationConfigRequestFactory(_RequestFactory):
    """``core/push-notification-config.json`` -- async task notifications.

    Only ``url`` is required, so the baseline is the unsigned single-field
    config. Sign it with ``payload(authentication=hmac_authentication())``.
    """

    class Meta:
        model = LibraryPushNotificationConfig

    url = "https://buyer.example.com/webhook"


class NotificationConfigRequestFactory(_RequestFactory):
    """``core/notification-config.json`` -- an account-level subscription.

    Requires ``subscriber_id`` and ``event_types`` as well as ``url``: this
    subscription outlives any single media buy, so it must say who is subscribing
    and to what. Two sites built it without ``event_types`` and were sending a
    document the spec rejects.

    ``active`` is not set here, but it appears in the payload as ``True``: that
    default comes from the SDK class, not from this factory. A scenario grading
    the paused arm overrides it explicitly with ``payload(active=False)``, which
    is what the two ``paused`` steps in ``uc011_accounts.py`` already do.
    """

    class Meta:
        model = LibraryNotificationConfig

    url = "https://buyer.example.com/webhook"
    subscriber_id = "sub-baseline"
    #: A real member of the pinned enum. The SDK class rejects anything else --
    #: which a dict literal would have accepted silently, and is the reason these
    #: factories bind the library type rather than shaping a dict.
    event_types = ["scheduled"]


class ReportingWebhookRequestFactory(_RequestFactory):
    """``core/reporting-webhook.json`` -- automated reporting delivery.

    THE STRICTEST OF THE THREE: ``authentication`` and ``reporting_frequency``
    are both required alongside ``url``. Seeds written as
    ``{"url": ..., "frequency": "daily"}`` were missing both -- the cadence
    because ``frequency`` is not the field's name, and the auth block outright.

    ``reporting_frequency`` is an enum: hourly, daily, monthly.
    """

    class Meta:
        model = LibraryReportingWebhook

    url = "https://buyer.example.com/reporting"
    reporting_frequency = "daily"
    authentication = factory.LazyFunction(hmac_authentication)
