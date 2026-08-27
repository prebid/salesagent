"""A notification subscriber's credentials are WRITE-ONLY — never echoed back.

Core obligation: ``core/notification-config.json`` @ v3.1.1, top level — *"Credentials
and shared secrets in ``authentication.credentials`` are write-only — sellers MUST NOT
echo them back"*, restated on ``sync-accounts-response`` as *"``authentication
.credentials`` is omitted on every entry (write-only)"*. Echoing one turns the response
into a credential-disclosure surface: anything that can read a buyer's account echo
learns the secret the buyer registered.

## Why this obligation moved here, and why this level is the only legal one

It used to be graded by the UC-011 BDD scenario "Register a paused account-level
notification subscriber and read it back", whose Given registered a Bearer credential
precisely so the omitted-credentials Then had teeth. #1291 D1 made that scenario
impossible: security.mdx @ v3.1.1's downgrade-and-injection-resistance rule makes a
seller that SUPPORTS request signing reject an UNSIGNED request carrying
``accounts[].notification_configs[].authentication`` with ``request_signature_required``,
and D1 made this deployment such a seller. A BDD scenario dispatches unsigned on every
transport, so registering a credential there now asserts an outcome the pin forbids.

Signing the BDD scenario was considered and rejected: the verifier is ASGI middleware, so
only the REST transport traverses it (A2A dispatches through ``on_message_send()``, MCP
through FastMCP's in-memory transport), and the scenario would have read as signed on
every transport while being graded on one.

This module drives ``_sync_accounts_impl`` directly, BELOW the transport boundary. That is
not a workaround — it is the correct level for this obligation, which is a property of the
RESPONSE BUILDER (what the seller puts in the echo) and not of the transport. No inbound
signature question arises here because no inbound request exists.

The complementary obligation — that an unsigned request carrying a credential is REJECTED
— is graded on the wire by
``tests/integration/test_request_signature_operations.py::TestWebhookAuthenticationForcesASignature``,
which covers both spec triggers plus the declared-nothing posture D1 made reachable. The
two halves together are what the single BDD scenario used to carry.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.schemas.account import SyncAccountsRequest
from tests.harness.account_sync import AccountSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: The buyer's secret. Long enough to be a real credential rather than a placeholder the
#: response builder might strip for being malformed, and distinctive enough that a leak is
#: unambiguous wherever it surfaces.
_CREDENTIAL = "buyer-shared-secret-" + "z" * 20

_SUBSCRIBER_ID = "buyer-primary"
_SUBSCRIBER_URL = "https://buyer.example/webhooks/adcp/creative"

#: ACCOUNT-surface notification types. Media-buy-anchored types are refused on this
#: surface with INVALID_REQUEST before anything is persisted, which would make every
#: assertion below hold for the wrong reason.
_EVENT_TYPES = ["creative.status_changed", "creative.purged"]


def _request(*, active: bool) -> SyncAccountsRequest:
    """Provision one account whose single subscriber carries a real credential."""
    return SyncAccountsRequest(
        accounts=[
            {
                "brand": {"domain": "acme-corp.com"},
                "operator": "acme-corp.com",
                "billing": "operator",
                "notification_configs": [
                    {
                        "subscriber_id": _SUBSCRIBER_ID,
                        "url": _SUBSCRIBER_URL,
                        "event_types": list(_EVENT_TYPES),
                        "active": active,
                        "authentication": {"schemes": ["Bearer"], "credentials": _CREDENTIAL},
                    }
                ],
            }
        ]
    )


def _echoed_subscribers(response: Any) -> list[Any]:
    return list(response.accounts[0].notification_configs or [])


class TestCredentialsAreNeverEchoed:
    """The registered credential must not appear anywhere in the response."""

    async def test_sync_accounts_echoes_the_subscriber_without_its_credential(self, integration_db):
        """The subscriber comes back — with its url, types and paused state — minus the secret.

        Asserted in both directions on purpose. That the subscriber IS echoed is what makes
        the omission meaningful: a builder that dropped the whole ``notification_configs``
        array would satisfy "no credential echoed" while failing the surface entirely, and
        that is the vacuous pass this test exists to avoid.

        ``active: false`` — a PAUSED subscriber, the same shape the retired BDD scenario
        used. Paused matters: it is the one case that skips the outbound proof challenge,
        so the echo is reached without a network round trip, and it keeps this test about
        the response builder rather than about proof-of-control.
        """
        with AccountSyncEnv() as env:
            env.setup_default_data()

            response = await env.call_impl_async(req=_request(active=False))

            subscribers = _echoed_subscribers(response)
            assert len(subscribers) == 1, (
                f"the seller must echo the applied notification_configs; got {subscribers!r}. "
                "Without the echo there is nothing to check the credential's absence against"
            )
            echoed = subscribers[0]
            assert str(getattr(echoed, "subscriber_id", "")) == _SUBSCRIBER_ID
            assert str(getattr(echoed, "url", "")) == _SUBSCRIBER_URL

            authentication = getattr(echoed, "authentication", None)
            if authentication is not None:
                as_dict = (
                    authentication if isinstance(authentication, dict) else authentication.model_dump(exclude_none=True)
                )
                assert "credentials" not in as_dict, (
                    "authentication.credentials is WRITE-ONLY (core/notification-config.json @ "
                    f"v3.1.1) and was echoed back: {as_dict!r}. Anything that can read this "
                    "account echo now knows the buyer's shared secret"
                )

    async def test_the_credential_appears_nowhere_in_the_serialized_response(self, integration_db):
        """The whole serialized document is searched, not just the field we expect.

        The field-level check above only looks where a credential is SUPPOSED to live. This
        one serializes the entire response and greps for the secret, which is what catches a
        leak into a field nobody thought to check — an echoed request blob, a debug ``ext``,
        an error message quoting the input. "MUST NOT echo them back" is a statement about
        the whole document, so the assertion is too.
        """
        with AccountSyncEnv() as env:
            env.setup_default_data()

            response = await env.call_impl_async(req=_request(active=False))

            serialized = response.model_dump_json()
            assert _CREDENTIAL not in serialized, (
                f"the registered credential leaked into the sync_accounts response body. It must "
                f"appear nowhere in the document (core/notification-config.json @ v3.1.1): "
                f"{serialized[:400]!r}"
            )
