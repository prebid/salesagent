"""The admin webhook lifecycle, graded through the real Flask routes.

GH #1802. An operator who registers a push-notification webhook must
be able to DEACTIVATE it, DELETE it, and RE-REGISTER its URL. Today none of
those three exits works, and each one fails in a way the operator reads as a
generic error:

* ``register_webhook`` (``src/admin/blueprints/principals.py``) raw-selects the
  table one line after constructing ``PushNotificationConfigRepository``, and
  the hand-written filter is ``(tenant_id, principal_id, url)`` with no
  ``is_active`` predicate — so a soft-deleted row blocks re-registration of that
  URL for that principal permanently;
* ``delete_webhook`` and ``toggle_webhook`` both filter on ``config_id``, which
  is not a column (the primary key is ``id``, ``models.py:2074``). Both are
  reached from ``templates/webhook_management.html`` (:129 and :447) with
  ``webhook.id``, so ``filter_by(config_id=...)`` raises ``InvalidRequestError``
  for EVERY row and both broad ``except Exception`` arms (:729-732, :759-761)
  render it as an operator-facing failure. Neither route has ever worked.

Why the routes and not the repository: the defect IS the hand-written filter in
the route. A repository call from a test body cannot execute
``filter_by(config_id=...)`` and would stay green through the whole deadlock —
which is exactly how it survived. Nothing is mocked: real Flask handlers, real
``PushNotificationConfigRepository``, real Postgres.

What is asserted, and what is deliberately NOT: the OUTCOME (which rows exist,
which id, which ``is_active``, which stored secret), never the
``InvalidRequestError`` text. An assertion on the exception message would pin
the bug in place instead of grading the fix.

Reads go through ``PushNotificationConfigUoW`` rather than a raw select, so the
cases are graded against the same repository the fix routes the routes through,
on a fresh session that cannot serve a stale snapshot of the handler's write.

Spec grounding: none applies — the admin blueprint is an internal operator
surface, not an AdCP transport, so there is no wire contract or conformance
storyboard step in play. The obligations here come from the ticket and from the
solution-review gate (``bd show GH #1802``), not from the pin.

RED vs GUARD at HEAD (see the module's four cases below):

* the three lifecycle cases are RED — the behaviour does not exist yet;
* ``test_re_registering_an_active_url_does_not_rotate_its_stored_secret``
  PASSES at HEAD and is a REGRESSION GUARD against the fix's own new branch:
  the fix calls ``find_by_url(..., active_only=False)`` and reuses the found
  row's id, which silently overwrites a LIVE registration's HMAC secret unless
  the found-and-active case warns and leaves the row alone (gate correction 2).
"""

from __future__ import annotations

import pytest

from src.core.database.repositories.uow import PushNotificationConfigUoW
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness._base import IntegrationEnv

# The registration form, the flash reader and the admitted URL are already
# spelled once, by the module that owns this route's ingest cases. Imported
# rather than re-typed: two copies of "what a browser posts to the webhook
# registration form" drift the moment the form changes, and the non-canonical
# ``hmac_sha256`` spelling the gate refuses is exactly what crept in last time.
from tests.integration.test_admin_ingest_url_policy import (
    ADMITTED_URL,
    flashes,
    post_register_hmac_webhook,
)
from tests.integration.test_outbound_http import set_flags

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "admin_webhook_lifecycle"
PRINCIPAL_ID = "admin_webhook_lifecycle_principal"

# The operator-facing outcomes, as (category, message) pairs. Constants rather
# than inline literals so a case cannot be quietly re-typed into whatever the
# route happens to emit.
REGISTERED = ("success", "Webhook registered successfully")
ALREADY_REGISTERED = ("warning", "Webhook URL already registered for this principal")
DELETED = ("success", "Webhook deleted successfully")

# Two distinct secrets, both over the pinned ``credentials`` minLength of 32,
# so "the stored secret is still the first one" is a real comparison rather than
# a comparison of two equal strings.
ORIGINAL_SECRET = "original-shared-secret-32-chars-or-more"
ROTATED_SECRET = "rotated-shared-secret-32-chars-or-more"


@pytest.fixture
def seeded_principal(integration_db):
    """A committed tenant + principal for the webhook routes to write against.

    ``IntegrationEnv`` is what binds the factories to a session; the factory
    commits, so each Flask handler's own session sees the rows.
    """
    with IntegrationEnv() as env:
        tenant = TenantFactory(
            tenant_id=TENANT_ID,
            name="Admin Webhook Lifecycle",
            subdomain="adminwebhooklifecycle",
        )
        PrincipalFactory(tenant=tenant, principal_id=PRINCIPAL_ID)
        yield env


def post_register(client, *, secret: str = ORIGINAL_SECRET):
    """Register ``ADMITTED_URL`` for this module's principal, as the form posts it."""
    return post_register_hmac_webhook(
        client,
        ADMITTED_URL,
        secret,
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
    )


def post_delete_webhook(client, config_id: str):
    """POST the delete form the listing renders, with the id the listing passes.

    ``webhook_management.html:129`` builds this URL with ``config_id=webhook.id``,
    so the id spelled here is the id the operator's browser actually sends.
    """
    return client.post(
        f"/tenant/{TENANT_ID}/principals/{PRINCIPAL_ID}/webhooks/{config_id}/delete",
        follow_redirects=False,
    )


def post_toggle_webhook(client, config_id: str):
    """POST the toggle endpoint the listing's ``fetch()`` calls (``:447``), same id."""
    return client.post(
        f"/tenant/{TENANT_ID}/principals/{PRINCIPAL_ID}/webhooks/{config_id}/toggle",
        follow_redirects=False,
    )


def active_config_ids() -> list[str]:
    """The ids of every ACTIVE config for this principal, read on a fresh session.

    ``list_active_by_principal`` is the method the senders read through, so this
    is the same question delivery asks: an id missing from this list is a
    webhook that receives nothing.
    """
    with PushNotificationConfigUoW(TENANT_ID) as uow:
        assert uow.push_notification_configs is not None
        return [config.id for config in uow.push_notification_configs.list_active_by_principal(PRINCIPAL_ID)]


def stored_row(config_id: str) -> dict[str, object] | None:
    """The stored state of one row regardless of ``is_active``, or None if gone."""
    with PushNotificationConfigUoW(TENANT_ID) as uow:
        assert uow.push_notification_configs is not None
        config = uow.push_notification_configs.get_by_id(config_id, PRINCIPAL_ID, active_only=False)
        if config is None:
            return None
        return {"is_active": config.is_active, "authentication_token": config.authentication_token}


def deactivate(config_id: str) -> None:
    """Deactivate a row the way production's only producer of ``is_active=False`` does.

    ``soft_delete`` is what the A2A ``deleteTaskPushNotificationConfig`` handler
    calls (``src/a2a_server/adcp_a2a_server.py:1366``). Using it rather than the
    admin toggle keeps this setup independent of ``toggle_webhook``, which is
    itself under test two cases below and does not work.
    """
    with PushNotificationConfigUoW(TENANT_ID) as uow:
        assert uow.push_notification_configs is not None
        assert uow.push_notification_configs.soft_delete(config_id, PRINCIPAL_ID) is True


def test_re_registering_a_deactivated_url_reactivates_the_original_row(
    authenticated_admin_client, seeded_principal, monkeypatch
):
    """RED. A deactivated URL can be registered again, and leaves ONE row behind.

    "Succeeds" alone does not grade the decision this fix has to make. It passes
    under both alternatives — a fresh ``uuid4()`` that INSERTS a second row and
    leaves the deactivated one as debris, and reuse of the found row's id that
    takes ``upsert``'s reactivation branch. The consequence is what separates
    them, so the consequence is what is asserted: exactly one active row, and
    its id is the ORIGINAL row's id (gate correction 6).

    Two active rows for one (principal, url) would be delivered to twice and
    would have to be cleared one at a time through a delete route that does not
    work.
    """
    set_flags(monkeypatch, private=True)
    client = authenticated_admin_client

    assert post_register(client).status_code == 302
    registered = active_config_ids()
    assert len(registered) == 1, f"the registration must persist exactly one active config, found {registered}"
    original_id = registered[0]

    deactivate(original_id)
    assert active_config_ids() == [], "the soft delete left the row active, so the case would grade nothing"

    response = post_register(client)

    assert response.status_code == 302
    assert active_config_ids() == [original_id], (
        f"after re-registration the principal must have exactly one active config and it must be "
        f"the original row reactivated, not a second row inserted beside the deactivated one; "
        f"found {active_config_ids()} against original id {original_id!r}"
    )
    assert flashes(client) == [REGISTERED, REGISTERED], (
        "re-registering a deactivated URL must succeed — the duplicate check reads "
        "(tenant_id, principal_id, url) with no is_active predicate, so a soft-deleted "
        "row blocks its own URL forever"
    )


def test_delete_webhook_removes_the_row_the_listing_links_to(authenticated_admin_client, seeded_principal, monkeypatch):
    """RED. The delete button on the listing actually deletes the row.

    Asserted as the OUTCOME — the row is gone — never as the
    ``InvalidRequestError`` the route raises today. Asserting the error text
    would pin the defect instead of grading its removal.
    """
    set_flags(monkeypatch, private=True)
    client = authenticated_admin_client

    assert post_register(client).status_code == 302
    (row_id,) = active_config_ids()

    response = post_delete_webhook(client, row_id)

    assert response.status_code == 302
    assert stored_row(row_id) is None, (
        f"the deleted row is still stored: {stored_row(row_id)} — the operator cannot clear "
        f"the registration that blocks the URL"
    )
    assert active_config_ids() == []
    assert flashes(client) == [REGISTERED, DELETED], (
        "delete_webhook filters on config_id, which is not a column, and its broad "
        "except arm renders the resulting programming error as an operator failure"
    )


def test_toggle_webhook_flips_is_active_on_the_row_the_listing_links_to(
    authenticated_admin_client, seeded_principal, monkeypatch
):
    """RED. The Disable/Enable button actually flips ``is_active``, both ways.

    Both directions, because a route that only ever set ``is_active=False``
    would satisfy a single flip while leaving the operator unable to re-enable
    the row — which is half of the deadlock. The JSON body is asserted too: the
    listing's ``fetch()`` arm (``webhook_management.html:447``) reads
    ``data.success`` and ``data.is_active`` to repaint the row.
    """
    set_flags(monkeypatch, private=True)
    client = authenticated_admin_client

    assert post_register(client).status_code == 302
    (row_id,) = active_config_ids()

    disabled = post_toggle_webhook(client, row_id)

    assert disabled.status_code == 200, (
        "toggle_webhook filters on config_id, which is not a column, so it 500s for every row"
    )
    assert disabled.get_json() == {"success": True, "is_active": False}
    assert stored_row(row_id) == {"is_active": False, "authentication_token": ORIGINAL_SECRET}
    assert active_config_ids() == []

    re_enabled = post_toggle_webhook(client, row_id)

    assert re_enabled.status_code == 200
    assert re_enabled.get_json() == {"success": True, "is_active": True}
    assert stored_row(row_id) == {"is_active": True, "authentication_token": ORIGINAL_SECRET}
    assert active_config_ids() == [row_id]


def test_a_non_canonical_spelling_of_a_registered_url_is_still_a_duplicate(
    authenticated_admin_client, seeded_principal, monkeypatch
):
    """The duplicate check must key on what is STORED, not on what was POSTED.

    What gets persisted is ``str(config.url)`` off a pydantic ``AnyUrl``, which
    NORMALIZES. Measured: a path-less ``https://127.0.0.1:9999`` is stored as
    ``https://127.0.0.1:9999/`` -- the posted string and the stored string are
    not equal.

    Keyed on the raw form value, the lookup then misses the row it just wrote,
    BOTH branches of the duplicate check collapse into the "not found" arm, and
    a SECOND active row is inserted for the same destination. The operator is
    told "registered successfully" twice, the sender delivers twice, and one
    copy is signed with a secret the receiver cannot verify.

    This case is what makes the branch keyed CORRECTLY rather than keyed
    plausibly: every other test in this module posts a URL that is already
    canonical, so all of them pass against the wrong key.
    """
    set_flags(monkeypatch, private=True)
    client = authenticated_admin_client

    # Path-less on purpose: this is the spelling AnyUrl rewrites.
    path_less = "https://127.0.0.1:9999"

    first = post_register_hmac_webhook(
        client, path_less, ORIGINAL_SECRET, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID
    )
    assert first.status_code == 302
    (row_id,) = active_config_ids()

    second = post_register_hmac_webhook(
        client, path_less, ROTATED_SECRET, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID
    )

    assert second.status_code == 302
    assert active_config_ids() == [row_id], (
        f"re-posting a URL whose stored form is normalized inserted a second active row: "
        f"{active_config_ids()} — the sender will now deliver twice, one copy signed with a "
        f"secret the receiver cannot verify"
    )
    assert stored_row(row_id) == {"is_active": True, "authentication_token": ORIGINAL_SECRET}


def test_re_registering_an_active_url_does_not_rotate_its_stored_secret(
    authenticated_admin_client, seeded_principal, monkeypatch
):
    """REGRESSION GUARD — passes at HEAD, and the fix must not break it.

    This case grades the branch the fix INTRODUCES. Today the duplicate check
    refuses every match, active or not, so an active duplicate is warned about
    for the wrong reason and the secret survives by accident. After the fix,
    ``find_by_url(..., active_only=False)`` returns ACTIVE rows too, and reusing
    the found row's id would send an active registration into ``upsert``'s
    update branch — silently overwriting a LIVE webhook's HMAC secret with
    whatever the form posted, with no warning and no way for the sender to know
    its signatures stopped verifying.

    The required branch (gate correction 2): found + ACTIVE warns and changes
    nothing; found + INACTIVE reuses the id; not found gets a fresh uuid4.
    """
    set_flags(monkeypatch, private=True)
    client = authenticated_admin_client

    assert post_register(client, secret=ORIGINAL_SECRET).status_code == 302
    (row_id,) = active_config_ids()

    response = post_register(client, secret=ROTATED_SECRET)

    assert response.status_code == 302
    assert active_config_ids() == [row_id]
    assert stored_row(row_id) == {"is_active": True, "authentication_token": ORIGINAL_SECRET}, (
        f"the live registration's HMAC secret was rotated by a second registration of the same "
        f"URL: {stored_row(row_id)} — every signature the sender produces from here on fails to "
        f"verify at a receiver that still holds the original secret"
    )
    assert flashes(client) == [REGISTERED, ALREADY_REGISTERED], (
        "re-registering a URL that is ALREADY ACTIVE must warn and change nothing"
    )
