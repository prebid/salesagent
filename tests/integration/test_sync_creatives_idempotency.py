"""sync_creatives HONOURS its idempotency_key: replay on retry, refuse on conflict.

prkv.31. The field was required and shape-validated at the boundary, but _impl never
consulted the replay cache, so a buyer whose sync timed out and retried executed the sync
twice. Taking the key while ignoring it is worse than not taking it: the spec attaches the
at-most-once promise to the field's presence.

Graded against a real database, because the guarantee IS the cache row -- a mocked probe
would prove only that a function was called.
"""

import pytest
from adcp.types import AccountReference

from src.core.exceptions import AdCPIdempotencyConflictError
from tests.helpers.envelope_assertions import raises_adcp
from tests.integration.test_creative_v3 import (
    ACCOUNT_ID,
    _headers,
    _make_creative_dict,
    _seed_account_for,
    _sync_creatives,
)

TENANT_ID = "tenant_sync_idem"
PRINCIPAL_ID = "principal_sync_idem"


@pytest.fixture
def synced(integration_db, bound_factory_session):
    """Tenant + principal + account the sync can reach, through the factories.

    Factories rather than the hand-rolled session block the sibling module still uses:
    CLAUDE.md forbids get_db_session() in a test body, and pre-existing debt in a
    neighbouring file is not a licence to add more.

    Yields the HEADERS the calls present, not an identity: ``invoke_tool`` takes headers
    and the resolver is their one reader, so the principal and the tenant's
    ``approval_mode`` are read off the rows seeded here.
    """
    from tests.factories import PrincipalFactory, TenantFactory

    # The TENANT OBJECT is passed, not just its id: tenant_id on these factories is a
    # LazyAttribute off a SubFactory, so supplying the id alone still builds a second
    # tenant and collides on the (tenant_id, currency_code) key.
    tenant = TenantFactory(tenant_id=TENANT_ID, subdomain="sync-idem", ad_server="mock", approval_mode="auto-approve")
    # No CurrencyLimitFactory call: TenantFactory already creates the USD limit, and a
    # second one violates uq_currency_limit.
    PrincipalFactory(tenant=tenant, principal_id=PRINCIPAL_ID)
    _seed_account_for(TENANT_ID, (PRINCIPAL_ID,))
    return _headers(TENANT_ID, PRINCIPAL_ID)


def _sync(headers, *, key, creative_id="c_idem", name=None):
    payload = _make_creative_dict(creative_id=creative_id)
    if name is not None:
        payload["name"] = name
    return _sync_creatives(
        creatives=[payload],
        idempotency_key=key,
        account=AccountReference(root={"account_id": ACCOUNT_ID}),
        headers=headers,
    )


@pytest.mark.requires_db
def test_a_retry_with_the_same_key_replays_instead_of_re_executing(synced):
    """The at-most-once promise: the second call returns the first result, unchanged."""
    key = "sync-idem-replay-000001"

    first = _sync(synced, key=key)
    second = _sync(synced, key=key)

    assert [c.creative_id for c in second.creatives] == [c.creative_id for c in first.creatives], (
        "a retry carrying the same idempotency_key must return the ORIGINAL result"
    )
    # The replay is served from the cache rather than re-run: a second execution would
    # re-sync the creative, so the response bodies must be identical, not merely similar --
    # EXCEPT for `replayed`, which is the one field whose whole job is to differ. It is a
    # declared ProtocolEnvelope field set by the boundary on a cache hit, so asserting the
    # dumps are equal outright would demand the marker never work.
    first_body = first.model_dump(mode="json")
    second_body = second.model_dump(mode="json")
    assert second_body.pop("replayed", None) is True, "the retry must be marked as a replay"
    assert first_body.pop("replayed", None) is not True, "the first call is not a replay"
    assert second_body == first_body


@pytest.mark.requires_db
def test_the_same_key_with_a_different_payload_is_refused(synced):
    """A key is a promise about ONE request; reusing it for another is a conflict."""
    key = "sync-idem-conflict-00001"

    _sync(synced, key=key, name="Original Name")

    # The boundary answers every failure with a RESPONSE and raises AdcpFailure carrying
    # it, so what a caller receives is the buyer-facing code, not the typed exception the
    # raise site built (tests/CLAUDE.md § Error verification policy). raises_adcp grades
    # that code and still names the refusal by its class.
    with raises_adcp(AdCPIdempotencyConflictError):
        _sync(synced, key=key, name="A Different Name")


@pytest.mark.requires_db
def test_a_different_key_executes_normally(synced):
    """The refusal must be specific to key reuse, not merely strict."""
    first = _sync(synced, key="sync-idem-distinct-0001", creative_id="c_idem_a")
    second = _sync(synced, key="sync-idem-distinct-0002", creative_id="c_idem_b")

    assert [c.creative_id for c in first.creatives] == ["c_idem_a"]
    assert [c.creative_id for c in second.creatives] == ["c_idem_b"]


@pytest.mark.requires_db
def test_a_dry_run_and_a_real_sync_are_different_requests(synced):
    """Reusing a dry run's key for the real sync is a conflict, not a silent replay.

    ``dry_run`` is a request field the spec does not exclude from the payload digest
    (the exclusion list is closed: idempotency_key, context, governance_context), so a
    preview and the commit that follows it are two different requests. Sending both under
    one key is what ``creative/sync-creatives-request.json`` tells the client not to do --
    "MUST be unique per (seller, request) pair... use a fresh UUID v4 for each request" --
    and rule 5 answers it: same key, different payload, IDEMPOTENCY_CONFLICT.

    The danger this replaces was real and is now structurally impossible: the impl used to
    skip caching dry runs so that a dry run's response could not answer a later real sync.
    With the payload in the digest it cannot, because the two never match.
    """
    key = "sync-idem-dryrun-000001"

    dry = _sync_creatives(
        creatives=[_make_creative_dict(creative_id="c_idem_dry")],
        idempotency_key=key,
        dry_run=True,
        account=AccountReference(root={"account_id": ACCOUNT_ID}),
        headers=synced,
    )
    assert dry.dry_run is True

    with raises_adcp(AdCPIdempotencyConflictError):
        _sync_creatives(
            creatives=[_make_creative_dict(creative_id="c_idem_dry")],
            idempotency_key=key,
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=synced,
        )

    # A fresh key is what the schema asks the client for, and it executes.
    real = _sync_creatives(
        creatives=[_make_creative_dict(creative_id="c_idem_dry")],
        idempotency_key="sync-idem-dryrun-000002",
        account=AccountReference(root={"account_id": ACCOUNT_ID}),
        headers=synced,
    )
    assert real.dry_run is not True
