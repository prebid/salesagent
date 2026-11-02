"""``PushNotificationConfigRepository`` persists the VALUE, and only what it owns.

Two claims share this module because they are two halves of one write.

**The receipt.** ``ValidatedWebhookRegistration`` is proof that BOTH ingest
preconditions ran — the registration SSRF gate on the URL half and the pinned
``Authentication`` model built inside ``_accept`` on the credential half. The
receipt used to evaporate at the persistence boundary: ``upsert`` took ``url`` /
``authentication_type`` / ``authentication_token`` as three unrelated strings, so
a caller that had never run either gate type-checked exactly like a caller that
had. The repository compensated by re-validating the URL itself
("defense-in-depth"), which is the shape that got deleted: a value that exists
passed the gate, so the type is the receipt and there is nothing left to
re-check.

Why the signature case is an assertion and not a code-review note: the
compensating move under review pressure is to ADD a value-taking overload beside
the string-taking one, which leaves every unreceipted call site type-checking
exactly as it does today. ``_RAW_STRING_PARAMS`` is therefore asserted ABSENT,
not merely "a registration parameter is present" — an added overload fails this
case.

**Preserve-if-not-passed.** The three kwargs that are NOT value fields
(``validation_token``, ``session_id``, ``webhook_secret``) are each owned by a
different registration surface, and those surfaces share config ids. Omitting a
field must keep the existing row's value — otherwise one path silently nulls a
``validation_token`` another set for the same config id — while an explicit
``None`` must still clear.

Integration rather than unit because both claims are about COLUMNS: what a
subsequent read of the row returns. A mocked session would grade the call, not
the write.

The migration that folds legacy ``authentication_type`` spellings onto the pinned
vocabulary is graded next door, in
``tests/integration/test_webhook_auth_scheme_normalization_migration.py``; what
this module grades is the other end of that fact — that a write through the
repository lands the canonical ``AuthenticationScheme`` member, so the migration
has no new non-canonical rows to fold.
"""

from __future__ import annotations

import inspect

import pytest
from adcp.types import AuthenticationScheme
from sqlalchemy import select

from src.core.database.models import PushNotificationConfig
from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
from src.core.webhooks.registration import ValidatedWebhookRegistration, accept_push_notification_config
from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# A URL that clears the registration gate on its own merits (public host, https)
# — a case here must fail because the WRITE is wrong, never because the fixture
# URL needed a hatch the gate did not open.
_WEBHOOK_URL = "https://buyer.example.com/adcp/webhook"
_UPDATED_URL = "https://buyer.example.com/adcp/webhook-v2"

# The SDK's own spellings, never literals: the pinned enum is the single
# definition of how a supported scheme is written, and hand-typing it here is how
# "hmac_sha256" — which nothing in src/ compares against — became a persisted
# value in the first place (see a1f4c7d92b30).
_HMAC_SCHEME = AuthenticationScheme.HMAC_SHA256.value
_BEARER_SCHEME = AuthenticationScheme.Bearer.value

# >= 32 chars: the pinned schema (core/push-notification-config.json) sets
# authentication.credentials minLength 32, so a shorter fixture would be refused
# by the model before the case under test is reached.
_CREDENTIAL = "buyer-shared-secret-thirty-two-plus"
_ROTATED_CREDENTIAL = "rotated-buyer-secret-thirty-two-plus"

# The admin-registered HMAC secret. A separate column from the buyer's
# credential, and a separate constant, so a case that confuses the two fails.
_WEBHOOK_SECRET = "s" * 32

# The three parameters that must CEASE TO EXIST — the columns are written from
# the value's fields instead.
_RAW_STRING_PARAMS = ("url", "authentication_type", "authentication_token")


def _registration(
    url: str = _WEBHOOK_URL,
    scheme: str = _HMAC_SCHEME,
    credentials: str = _CREDENTIAL,
) -> ValidatedWebhookRegistration:
    """Build the value through the ONE public constructor buyers' configs go through."""
    return accept_push_notification_config(
        {"url": url, "authentication": {"schemes": [scheme], "credentials": credentials}}
    )


def _refreshed(session, config_id: str) -> PushNotificationConfig:
    """Re-read the row from the database rather than trusting the identity map."""
    session.expire_all()
    return session.scalars(select(PushNotificationConfig).filter_by(id=config_id)).one()


@pytest.fixture
def identity(integration_db, factory_session):
    """(repo, tenant, principal): the FK rows every config row here requires."""
    tenant = TenantFactory()
    principal = PrincipalFactory(tenant=tenant)
    return PushNotificationConfigRepository(factory_session, tenant.tenant_id), tenant, principal


@pytest.fixture
def seeded(identity):
    """(repo, principal, cfg): an existing row with every non-value field populated."""
    repo, tenant, principal = identity
    cfg = PushNotificationConfigFactory(
        tenant=tenant,
        principal=principal,
        url=_WEBHOOK_URL,
        authentication_type=_HMAC_SCHEME,
        authentication_token=_CREDENTIAL,
        validation_token="vtok",
        session_id="sess-1",
        webhook_secret=_WEBHOOK_SECRET,
    )
    return repo, principal, cfg


def test_signature_takes_the_value_and_exposes_no_raw_string_upsert():
    """The leading parameter is the value; the three string parameters are gone.

    ``eval_str=True`` resolves the module's ``from __future__ import annotations``
    strings to the real class, so the case grades the TYPE the repository
    declares rather than the spelling of a name.
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


def test_upsert_writes_the_values_fields_into_the_columns(identity, factory_session):
    """The inserted row's three value columns equal the value's three fields."""
    repo, tenant, principal = identity
    registration = _registration()

    config, created = repo.upsert(
        registration,
        config_id="pnc_value_1",
        principal_id=principal.principal_id,
    )

    assert created is True
    stored = _refreshed(factory_session, "pnc_value_1")
    assert stored.tenant_id == tenant.tenant_id
    assert stored.principal_id == principal.principal_id
    assert stored.url == registration.url
    assert stored.authentication_type == registration.authentication_type
    assert stored.authentication_token == registration.authentication_token
    # The persisted spelling is the pinned enum member, so the normalization
    # migration next door has no new non-canonical rows to fold.
    assert stored.authentication_type == _HMAC_SCHEME
    assert config.id == stored.id


def test_upsert_insert_defaults_unpassed_fields_to_none(identity, factory_session):
    """On INSERT, an omitted non-value field is NULL — there is nothing to preserve."""
    repo, _tenant, principal = identity

    config, created = repo.upsert(
        _registration(),
        config_id="pnc_fresh",
        principal_id=principal.principal_id,
    )

    assert created is True
    row = _refreshed(factory_session, config.id)
    assert (row.validation_token, row.session_id, row.webhook_secret) == (None, None, None)
    assert row.is_active is True


def test_upsert_preserves_unpassed_token_fields(seeded, factory_session):
    """An UPDATE replaces the value columns and touches no field the caller omitted."""
    repo, principal, cfg = seeded
    registration = _registration(url=_UPDATED_URL, scheme=_BEARER_SCHEME, credentials=_ROTATED_CREDENTIAL)

    updated, created = repo.upsert(
        registration,
        config_id=cfg.id,
        principal_id=principal.principal_id,
    )

    assert created is False
    row = _refreshed(factory_session, cfg.id)
    assert (row.url, row.authentication_type, row.authentication_token) == (
        registration.url,
        _BEARER_SCHEME,
        _ROTATED_CREDENTIAL,
    )
    assert (row.validation_token, row.session_id, row.webhook_secret) == (
        "vtok",
        "sess-1",
        _WEBHOOK_SECRET,
    )
    assert updated.id == row.id


def test_upsert_explicit_none_still_clears(seeded, factory_session):
    """An explicit ``None`` is a caller decision to clear, not an omission."""
    repo, principal, cfg = seeded

    repo.upsert(
        _registration(url=_UPDATED_URL),
        config_id=cfg.id,
        principal_id=principal.principal_id,
        validation_token=None,
        session_id=None,
        webhook_secret=None,
    )

    row = _refreshed(factory_session, cfg.id)
    assert (row.validation_token, row.session_id, row.webhook_secret) == (None, None, None)
