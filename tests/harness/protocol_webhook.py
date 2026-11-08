"""ProtocolWebhookEnv — integration test environment for ProtocolWebhookService.

Real: a local HTTP origin that actually serves the POST, the real database for
      the ``webhook_delivery_log`` rows the service writes, and a real
      ``ProtocolWebhookService`` instance.
Mocked: nothing. There is no ``EXTERNAL_PATCHES`` entry on purpose — the whole
      point of this env is that the transport is not substituted: the assertions
      grade ``src.core.security.outbound_http.asend`` putting real bytes on a
      real socket, indifferent to how the seam is implemented.

Why an env rather than the bare ``local_origin`` fixture plus direct calls:
``ProtocolWebhookService`` writes its delivery log through
``DeliveryRepository``, whose rows carry a foreign key to ``media_buys``, and
the ``PushNotificationConfig`` it is handed is an ORM instance.
``IntegrationEnv.__enter__`` is what binds the factory session, so outside an
env neither row can be created at all.

``LocalOriginMixin`` already means "start the origin, publish ``webhook_url``,
open both outbound escape hatches" — the origin necessarily listens on loopback
over plain HTTP, which the egress seam refuses by default. Composing it is how
that decision stays spelled once.

The ONE service instance per env (:attr:`ProtocolWebhookEnv.service`) is
load-bearing, not a convenience: the property this migration exists to create is
that a single service instance can deliver to two different hostnames, which is
only expressible if the tests share one instance across deliveries.

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    async def test_something(self, integration_db):
        with ProtocolWebhookEnv() as env:
            env.setup_default_data()
            env.set_http_status(200)

            delivered = await env.send()

            assert delivered is True
            assert env.delivery_attempts == 1
"""

from __future__ import annotations

from typing import Any

from adcp import create_mcp_webhook_payload
from adcp.types import McpWebhookPayload

from src.core.database.models import PushNotificationConfig, WebhookDeliveryLog
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.factories import PushNotificationConfigFactory, WebhookTaskContextFactory
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import LocalOriginMixin, WebhookOutcomeRowsMixin
from tests.harness.egress import FastOutboundBackoffMixin

# ``metadata["task_type"]`` is the INTERNAL label the service gates its
# delivery-log writes on; the payload's own ``task_type`` is the spec TaskType
# enum. They are deliberately different — see the comment at
# ``src/services/delivery_webhook_scheduler.py`` where production sets both.
DELIVERY_METADATA_TASK_TYPE = "media_buy_delivery"
DELIVERY_PAYLOAD_TASK_TYPE = "update_media_buy"

# The logger production's audit entries go to (``src/core/audit_logger.py``).
AUDIT_LOGGER_NAME = "adcp.audit"


# The task id the payload and the task context share by default. They used to be
# able to disagree: the sender scraped the id off the PAYLOAD, so whatever the
# context said was ignored. It is the context's value that reaches the audit
# trail and the delivery row now, so one default serves both.
_DEFAULT_TASK_ID = "task_001"
#: The correlation id the harness stands in for. A real one is BUYER-generated and
#: supplied at registration via ``push_notification_config.operation_id``, which the
#: seller must echo verbatim into every payload; ``core/mcp-webhook-payload.json`` makes
#: it required and forbids recovering it from the receiver URL. Named here rather than
#: inlined so a test grading the echo can see what it has to override.
_DEFAULT_OPERATION_ID = "op_harness_delivery"


class ProtocolWebhookEnv(FastOutboundBackoffMixin, WebhookOutcomeRowsMixin, LocalOriginMixin, IntegrationEnv):
    """Integration test environment for ``ProtocolWebhookService.send_notification``.

    The notification POST goes over real HTTP to a real local origin; the
    delivery-log rows go through the real database.

    Fluent API (from LocalOriginMixin):
        webhook_url                       -- the running origin's URL
        set_http_status(code, text)       -- answer every attempt with one status
        set_http_sequence(responses)      -- answer attempts in order, last repeats
        set_http_error()                  -- drop the connection without answering
        delivery_attempts / last_delivery -- what the endpoint actually received
        delivered_requests                -- every request, oldest first
    """

    MODULE = "src.services.protocol_webhook_service"

    _service: ProtocolWebhookService | None = None

    def _enter_pre(self) -> None:
        """Reset the memoized service, and guarantee the reset on the way out.

        Registered rather than done in a teardown override so a FAILED enter
        leaves no service from this env visible to the next one.
        """
        super()._enter_pre()
        self._service = None
        self._guard("protocol_service", lambda: setattr(self, "_service", None))

    # -- The subject under test ---------------------------------------------

    @property
    def service(self) -> ProtocolWebhookService:
        """The ONE ``ProtocolWebhookService`` this env drives every delivery through.

        Constructed directly rather than through
        ``get_protocol_webhook_service()`` so a test never mutates the module
        singleton (and, today, never appends to the process-wide shutdown
        registry). One instance per env is what makes "two destinations, one
        service" a statement a test can actually make.
        """
        if self._service is None:
            self._service = ProtocolWebhookService()
        return self._service

    # -- Programming the inputs ---------------------------------------------

    def make_config(
        self,
        *,
        url: str | None = None,
        authentication_type: str | None = None,
        authentication_token: str | None = None,
    ) -> PushNotificationConfig:
        """Store a ``PushNotificationConfig`` pointing at ``url``.

        ``url`` defaults to the origin that is really listening. Passing an
        explicit URL is how a test targets somewhere else — a second origin, or
        somewhere nothing is listening at all.
        """
        tenant, principal = self.setup_default_data()
        return PushNotificationConfigFactory(
            tenant=tenant,
            principal=principal,
            url=url if url is not None else self.webhook_url,
            authentication_type=authentication_type,
            authentication_token=authentication_token,
            is_active=True,
        )

    def make_payload(
        self,
        *,
        task_id: str = _DEFAULT_TASK_ID,
        result: dict[str, Any] | None = None,
        operation_id: str = _DEFAULT_OPERATION_ID,
    ) -> McpWebhookPayload:
        """Build the real SDK webhook payload production sends for a delivery report.

        Mirrors ``DeliveryWebhookScheduler._send_delivery_report``: the payload
        carries the spec ``TaskType`` (``update_media_buy``) while the metadata
        carries the internal ``media_buy_delivery`` label.

        ``operation_id`` is REQUIRED on ``core/mcp-webhook-payload.json`` and adcp 9
        types it ``str`` rather than ``str | None``, so the SDK now refuses to build a
        payload without one where 6.6.0 built a schema-invalid body. Production takes it
        off the REGISTRATION the delivery is going to and echoes it verbatim — the field
        is a BUYER-generated correlation id, and the spec forbids the seller recovering
        it by parsing the receiver URL.

        The default is the harness standing in for what a buyer registered, because these
        tests grade egress and signing rather than the echo itself. A test that wants to
        grade the ECHO should pass its own config's ``operation_id`` here, so the payload
        and the registration carry the same value the way production makes them.
        """
        return create_mcp_webhook_payload(
            task_id=task_id,
            task_type=DELIVERY_PAYLOAD_TASK_TYPE,
            status="completed",
            result=result if result is not None else {"media_buy_id": "mb_0001"},
            operation_id=operation_id,
        )

    # -- Driving production --------------------------------------------------

    async def send(
        self,
        *,
        config: PushNotificationConfig | None = None,
        payload: McpWebhookPayload | None = None,
        media_buy_id: str | None = None,
        task_type: str = DELIVERY_METADATA_TASK_TYPE,
        task_id: str = _DEFAULT_TASK_ID,
        sequence_number: int = 1,
        notification_type: str | None = None,
    ) -> bool:
        """Call the real ``send_notification`` and return what production returned.

        ``config`` defaults to an unauthenticated config for the running origin.
        ``media_buy_id`` is one of the four values the service requires before it
        writes a delivery-log row at all.

        ``sequence_number`` and ``notification_type`` are parameters because they
        are the two fields the delivery row carries that a caller can get WRONG.
        They used to be unreachable from here: ``send_notification`` took a loose
        dict, and the two were re-derived downstream from the PAYLOAD, so no
        caller could state them and no test could catch them being reset.
        """
        self._commit_factory_data()
        return await self.service.send_notification(
            push_notification_config=config if config is not None else self.make_config(),
            payload=payload if payload is not None else self.make_payload(),
            task=WebhookTaskContextFactory(
                task_id=task_id,
                task_type=task_type,
                tenant_id=self._tenant_id,
                principal_id=self._principal_id,
                media_buy_id=media_buy_id,
                sequence_number=sequence_number,
                notification_type=notification_type,
            ),
        )

    # -- Observing what production recorded ----------------------------------

    def delivery_logs(self, media_buy_id: str) -> list[WebhookDeliveryLog]:
        """Every ``webhook_delivery_log`` row for ``media_buy_id``, freshly read.

        ``record_outcome`` commits through its own ``get_db_session()``, so
        the env-bound session must drop anything it already has cached before
        the read or it would answer from its identity map.
        """
        self.get_session().expire_all()
        return self.query(WebhookDeliveryLog, tenant_id=self._tenant_id, media_buy_id=media_buy_id)
