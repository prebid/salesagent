"""The push-notification repository persists the VALUE, never loose strings.

Epic D lane C2 (GH #1802). ``ValidatedWebhookRegistration`` is the
receipt that BOTH ingest preconditions ran — the registration SSRF gate on the
URL half and the pinned ``Authentication`` model built inside ``_accept`` on the credential
half. Before this lane the
receipt evaporated at the persistence boundary: ``upsert`` took ``url`` /
``authentication_type`` / ``authentication_token`` as three unrelated strings,
so a caller that had never run either gate type-checked exactly like a caller
that had. The repository compensated by re-validating the URL itself
("defense-in-depth"), which is the shape this lane deletes: a value that exists
passed the gate, so the type is the receipt and there is nothing left to
re-check.

Why the signature case is an assertion and not a code-review note: the
compensating move under review pressure is to ADD a value-taking overload
beside the string-taking one, which leaves every unreceipted call site
type-checking exactly as it does today. ``_RAW_STRING_PARAMS`` is therefore
asserted ABSENT, not merely "a registration parameter is present" — an added
overload fails this case.

Integration rather than unit because the claim is about COLUMNS: that the
value's three fields are what a subsequent read of the row returns. A mocked
session would grade the call, not the write.
"""

from __future__ import annotations

import inspect

import pytest

from src.core.database.models import PushNotificationConfig
from src.core.database.repositories.push_notification_config import (
    PushNotificationConfigRepository,
)
from src.core.webhooks.registration import (
    ValidatedWebhookRegistration,
    accept_push_notification_config,
)
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "pncrepo_t1"
_PRINCIPAL_ID = "pncrepo_p1"

# A URL that clears the registration gate on its own merits (public host, https)
# — the case must fail because the SIGNATURE is wrong, never because the fixture
# URL needed a hatch the env did not open.
_WEBHOOK_URL = "https://buyer.example.com/adcp/webhook"

# The pinned AdCP 3.1.1 ``AuthenticationScheme`` spelling, which is what every
# writer in ``src/`` persists.
_HMAC_SCHEME = "HMAC-SHA256"
# >= 32 chars: the pinned schema (core/push-notification-config.json) sets
# authentication.credentials minLength 32, so a shorter fixture would be refused
# by the model before the case under test is reached.
_SECRET = "buyer-shared-secret-thirty-two-plus"

# The three parameters that must CEASE TO EXIST — the columns are written from
# the value's fields instead.
_RAW_STRING_PARAMS = ("url", "authentication_type", "authentication_token")


def _hmac_registration() -> ValidatedWebhookRegistration:
    """Build the value through the ONE public constructor buyers' configs go through."""
    return accept_push_notification_config(
        {
            "url": _WEBHOOK_URL,
            "authentication": {"schemes": [_HMAC_SCHEME], "credentials": _SECRET},
        }
    )


def _seeded_repo(env: BareIntegrationEnv) -> PushNotificationConfigRepository:
    """Create the tenant + principal the row's FKs require, return the repository."""
    tenant = TenantFactory(tenant_id=_TENANT_ID)
    PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)
    return PushNotificationConfigRepository(env.get_session(), _TENANT_ID)


class TestUpsertTakesTheValue:
    """``upsert(registration, ...)`` — the receipt, not three strings."""

    def test_signature_takes_the_value_and_exposes_no_raw_string_upsert(self):
        """The leading parameter is the value; the three string parameters are gone.

        ``eval_str=True`` resolves the module's ``from __future__ import
        annotations`` strings to the real class, so the case grades the TYPE the
        repository declares rather than the spelling of a name.
        """
        signature = inspect.signature(PushNotificationConfigRepository.upsert, eval_str=True)
        parameters = [param for name, param in signature.parameters.items() if name != "self"]

        assert parameters[0].annotation is ValidatedWebhookRegistration, (
            f"upsert's leading parameter is {parameters[0].name}: "
            f"{parameters[0].annotation!r} — persistence still accepts something "
            f"other than the gate's receipt"
        )
        assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, (
            f"the registration is {parameters[0].kind.description}; callers must be "
            f"able to hand the value over positionally, as the lane's call sites do"
        )

        leftover = [name for name in _RAW_STRING_PARAMS if name in signature.parameters]
        assert leftover == [], (
            f"upsert still accepts {leftover} as loose strings — a value-taking "
            f"overload ADDED BESIDE the raw-string signature leaves every "
            f"unreceipted call site type-checking exactly as it does today"
        )

    def test_upsert_writes_the_values_fields_into_the_columns(self, integration_db):
        """The persisted row's three columns equal the value's three fields."""
        with BareIntegrationEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env:
            repo = _seeded_repo(env)
            registration = _hmac_registration()

            config, created = repo.upsert(
                registration,
                config_id="pnc_value_1",
                principal_id=_PRINCIPAL_ID,
            )

            assert created is True
            stored = env.get_one(PushNotificationConfig, tenant_id=_TENANT_ID, id="pnc_value_1")
            assert stored is not None, "upsert reported a write that produced no row"
            assert stored.url == registration.url
            assert stored.authentication_type == registration.authentication_type
            assert stored.authentication_token == registration.authentication_token
            assert config.id == stored.id
