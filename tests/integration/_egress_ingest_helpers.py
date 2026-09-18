"""Cross-module helpers for the integration egress/ingest suites.

Extracted at the GH #1802 merge. Each of these lived in a module that is ITSELF a test
suite, so a sibling importing one dragged that whole suite into its collection — the
shape ``test_architecture_no_cross_test_module_imports`` forbids. The symbols are
unchanged; only their home is. Imports needed by a moved symbol are re-declared here
rather than star-imported, so this module stands on its own.
"""

from __future__ import annotations

from adcp.types import AuthenticationScheme

from tests.factories.format import AGENT_URL, FormatFactory

TENANT_ID = "ingest_url_policy"
PRINCIPAL_ID = "ingest_url_policy_principal"


ADMITTED_URL = "https://127.0.0.1:9999/agent"


def flashes(client) -> list[tuple[str, str]]:
    """The (category, message) pairs queued for the next rendered page.

    Read from the session rather than from rendered HTML: the flash is the
    thing the handler produced, and the template is not under test here.
    """
    with client.session_transaction() as session:
        return list(session.get("_flashes", []))


def post_register_hmac_webhook(
    client, url: str, secret: str, *, tenant_id: str = TENANT_ID, principal_id: str = PRINCIPAL_ID
):
    """POST the principal-webhook registration form as an HMAC-SHA256 registration.

    ``auth_type`` is the enum member rather than a literal: the form's option
    values are rendered from ``AuthenticationScheme`` (webhook_management.html),
    so this posts what a browser posts, and the non-canonical ``"hmac_sha256"``
    spelling the gate refuses cannot creep back in through a test.

    ``tenant_id`` / ``principal_id`` default to this module's fixtures and are
    parameters only so the cross-surface equivalence pin in
    ``test_webhook_hmac_credentials_ingest_refusal.py`` can drive this same form
    against the tenant its own harness seeded, instead of spelling the route a
    second time.
    """
    return client.post(
        f"/tenant/{tenant_id}/principals/{principal_id}/webhooks/register",
        data={"url": url, "auth_type": AuthenticationScheme.HMAC_SHA256, "hmac_secret": secret},
        follow_redirects=False,
    )


_FORMAT_ID = "display_300x250_image"


def _registered_format() -> object:
    """A Format the tenant's registry advertises — the operator's own agent_url.

    ``format_id`` is built from a plain dict, not a pre-constructed
    ``src.core.schemas.FormatId`` instance, so the fixture matches what
    production holds: a dict goes through real field validation and lands on
    the same ``FormatReferenceStructuredObject`` variant the buyer's
    ``CreativeAsset.format_id`` resolves to on the wire — which is what
    ``registry.list_all_formats`` actually returns (its formats are built by
    validating agent JSON responses, not by embedding pre-built ``FormatId``
    objects). Passing an already-built ``FormatId`` keeps that subclass as-is
    and puts a shape in the registry that no wire response produces.

    This is fidelity, no longer a pass/fail condition. ``_create_new_creative``
    used to select with ``fmt.format_id == creative_format`` — Pydantic
    ``BaseModel.__eq__``, which demands an EXACT class match, so a subclass on
    either side matched nothing. That comparison is gone: the match now runs
    through ``src.core.format_resolver.find_format``, which compares
    ``format_identity`` — the ``(canonical agent_url, id)`` pair — and is
    class-insensitive by construction (#2093). Do not read this fixture as
    proof that the class-sensitive compare is still guarded; the guard is
    ``find_format`` being the one definition of the match.
    """
    return FormatFactory(format_id={"id": _FORMAT_ID, "agent_url": AGENT_URL})


def _assert_no_push_config_persisted(tenant_id: str, principal_id: str) -> None:
    """The refused URL left no push_notification_configs row.

    The repository upsert is the single write funnel for this table
    (GH #1697 disposition row 19: the repository is the verification
    point, deliberately not the fix site), so an empty active list for the
    principal IS "the refusal preceded the store".
    """
    from src.core.database.repositories.uow import PushNotificationConfigUoW

    with PushNotificationConfigUoW(tenant_id) as uow:
        assert uow.push_notification_configs is not None
        persisted = uow.push_notification_configs.list_active_by_principal(principal_id)
    assert persisted == [], (
        f"a refused push_notification_config.url must not be persisted, found {[(c.id, c.url) for c in persisted]}"
    )
