"""E2E-6: the key lifecycle observed through the served trust-root documents.

salesagent-mp53.6 (#1291) — the LIFECYCLE half of the publication instrument.
``test_jwks_publication_e2e`` (mp53.7) proved that ONE key minted through a
production path reaches the document a counterparty resolves to. This module
proves that PROVISION, ROTATION, REVOCATION and RETIREMENT each move the served
documents the way ``docs/operations/signing-key-runbook.md`` says they do.

**PROVES.** Every transition is DRIVEN through a production transport over real
HTTP — the admin ``signing-keys/create`` and ``signing-keys/<kid>/revoke``
routes, running inside the server container — and every one of them is OBSERVED
only in documents an unauthenticated counterparty can fetch over TLS:

* **publishable** — the ``kid`` set of ``/.well-known/jwks.json`` and of the
  ``adagents.json`` ``signing_keys[]`` pin (``publishable_at``);
* **active — the key this agent SIGNS with** — the ``kid`` in the protected
  header of the ``/.well-known/governance-revocations.json`` JWS. That header is
  the ONLY counterparty-fetchable signal of ``active_at``, which is what makes
  "publishable but not active" gradeable from outside at all;
* **revoked** — the ``revoked_at`` marker carried on the published JWK, plus the
  PERMANENT ``revoked_kids`` record in that same JWS payload (``all_revoked``).

The distinction the repository calls load-bearing — ``active_at`` (this agent
SIGNS) versus ``publishable_at`` (this key is PUBLISHED) — is therefore read out
of TWO DIFFERENT SERVED DOCUMENTS, never out of a selector this test called or a
row this test wrote. A key inside its revocation grace window is publishable but
not active; retirement MUST set ``revoked_at``, because closing the active window
retires a SIGNER and never a PUBLICATION.

**DOES NOT PROVE.** That any signature this agent makes verifies (E2E-3 /
salesagent-mp53.3), or that a counterparty can DISCOVER these documents from the
served capabilities block (mp53.7). Both are graded elsewhere on purpose: a
failure here must be attributable to the lifecycle, not to discovery or to the
signer.

**``not_after`` is deliberately NOT driven, and that is a fact rather than a
scope call.** No production writer ever sets ``not_after`` non-NULL:
``src/core/signing/keys.py:171`` is its only call site and it always passes
``None`` (grep across ``src/core/signing``, ``src/admin``, ``src/routes``). So
``active_at``'s ``not_after`` branch and ``publishable_at``'s ``not_after``
blindness are unreachable in this deployment, there is no transport that could
close a window, and the absence of one is itself the strongest form of the
runbook's "retirement goes through revoke" imperative.

**Phase D moves the CLOCK, not a transport — the one exception in this module.**
``SigningConfig.grace_seconds`` has a 301s floor (``src/core/config.py`` refuses
any value at or below the published ``CACHE_MAX_AGE_SECONDS``), so an e2e test
cannot wait a grace window out. It backdates ``revoked_at`` on the live database
instead — the precedent is
``tests/integration/test_trust_root_documents.py::test_revoked_key_past_grace_disappears_from_both_documents``
— and touches no production selector. Everything else in phases A-E is a real
POST to a real route.

**One test function, five phases, one tenant.** The e2e tox env does not pass
``-p no:randomly``, so five order-dependent phase functions sharing one tenant
would be a flake bomb under pytest-randomly's shuffling; and the stack aliases
exactly one dotted TLS name, so a second tenant has nowhere to live. An
early-phase failure masking the later phases is accepted, matching the exemplar.

The four mutations this module is built to fail under, each independent, each
reverted and re-confirmed green afterwards:

1. add ``.limit(1)`` to ``SigningKeyRepository.publishable_at`` — phase B's
   both-kids assertion dies while the JWS ``kid`` still flips to kid2;
2. change ``active_at``'s ``order_by`` to ``created_at.asc()`` — the JWS ``kid``
   never flips to kid2 in phase B while the JWKS still carries both;
3. drop the ``revoked_at`` marker from ``src/core/signing/trust_root.py``'s
   ``_published_jwk`` — phase C dies;
4. make ``all_revoked`` apply the grace cutoff — phase D dies.

(1) and (2) together are what prove the JWKS and the revocation-list JWS are read
from two DIFFERENT selectors, which is the whole claim phase B makes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from src.core.agent_identity import ADAGENTS_JSON_PATH, GOVERNANCE_REVOCATIONS_PATH, JWKS_PATH
from tests.e2e._signing_e2e import (
    ca_verified_ssl_context,
    keyless_declaring_tenant_fixture,
    netloc,
    provision_signing_key_via_admin,
    revoke_signing_key_via_admin,
    tls_base_url,
)
from tests.e2e.utils import live_repo_session
from tests.helpers.signing import b64url_json, jws_parts, published_kids, signing_key_repo

#: Distinct from every other signing e2e module's tenant/slug — every one of them
#: provisions at the SAME TLS netloc and ``ix_tenants_virtual_host`` is UNIQUE, so
#: a distinct id is what makes a leaked tenant blame the right module.
_SLUG = "keylife_e2e"
_TENANT_ID = "keylife_e2e"

#: The operation this tenant declares a ``request_signing`` posture for.
#: ``supported_for``, never ``required_for``: every document below is fetched
#: anonymously, and a required bucket would have the verifier refuse the fetch.
_DECLARED_OPERATION = "get_products"


def _pinned_signing_keys(adagents: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``signing_keys[]`` entries of the adagents pin, across every agent entry.

    Asserts the pin is NON-EMPTY rather than returning an empty list quietly: the
    seeded ``AuthorizedProperty`` record is a PRECONDITION for the document
    carrying any ``authorized_agents`` entry at all (every variant requires its
    selector array), and a marker assertion over an empty list passes vacuously.
    """
    entries = [key for agent in adagents["authorized_agents"] for key in agent.get("signing_keys", [])]
    assert entries, (
        "the adagents pin must carry signing_keys[] — the tenant seam seeds an AuthorizedProperty "
        "record precisely so authorized_agents is non-empty, and an empty pin would make every "
        f"marker assertion below vacuous. Document: {adagents!r}"
    )
    return entries


def _revocation_signals(document: dict[str, Any]) -> tuple[str, list[str]]:
    """The two lifecycle signals the served revocation list carries.

    Returns ``(signing_kid, revoked_kids)`` — the protected header's ``kid``,
    which is the wire-observable identity of ``active_at``, and the payload's
    permanent revocation record, which is ``all_revoked``. Both read through the
    promoted JWS helpers, so this module cannot drift into a second envelope
    check or a second base64url decoder.
    """
    b64_protected, b64_payload, _signature = jws_parts(document)
    header = b64url_json(b64_protected)
    payload = b64url_json(b64_payload)
    assert "kid" in header, f"the revocation list's protected header must name the key that signed it; got {header!r}"
    return header["kid"], payload["revoked_kids"]


async def _get_json(client: httpx.AsyncClient, path: str, *, expect_status: int = 200) -> dict[str, Any]:
    """GET a served document over the CA-verified client, failing loudly on the wrong status.

    Re-fetching the SAME path after each transition is what this module is built
    on, and nothing between the client and the app caches: ``httpx`` has no cache,
    and the e2e TLS sidecar (``config/nginx/nginx-tls-test.conf.template``)
    declares no ``proxy_cache`` zone, so the ``Cache-Control: max-age=300`` the
    app publishes is advice to a counterparty and never a stale read here.
    """
    response = await client.get(path)
    assert response.status_code == expect_status, (
        f"GET {path} must return HTTP {expect_status}; got {response.status_code}. Body: {response.text[:300]!r}"
    )
    return response.json()


def _backdate_revocation(live_server: dict, *, kid: str, seconds: float) -> None:
    """Move *kid*'s ``revoked_at`` *seconds* further into the past, on the live DB.

    The one transition in this module that is a CLOCK MOVE rather than a
    production transport — see the module docstring. It reaches the row through
    the production repository (``SigningKeyRepository.get_by_kid``) rather than a
    raw query, so the tenant scope is the same one every server-side read applies.

    Uses :func:`live_repo_session`, not :func:`live_db_env`: this test's fixture
    (``keyless_declaring_tenant`` -> ``provisioned_trust_root_tenant``) already
    holds a ``live_db_env`` bound for the whole test body, and a second one
    cannot nest inside it (global factory-binding state) — this helper mutates
    an EXISTING row through the repository only, never a factory, so it needs no
    binding at all.
    """
    with live_repo_session(live_server) as env:
        repo = signing_key_repo(env, _TENANT_ID)
        row = repo.get_by_kid(kid)
        assert row is not None, f"the live database must still hold {kid!r} for tenant {_TENANT_ID!r}"
        assert row.revoked_at is not None, (
            f"{kid!r} must already be revoked through the admin route before its revocation can be "
            "backdated — this helper ages an existing revocation, it never creates one"
        )
        row.revoked_at = row.revoked_at - timedelta(seconds=seconds)
        env._commit_factory_data()


def _grace_seconds() -> int:
    """The grace window the SERVER applies, read from the same defaults it reads.

    ``docker-compose.e2e.yml`` sets no ``ADCP_SIGNING_*`` grace override, so the
    container and this runner resolve the identical ``SigningConfig`` default.
    """
    from src.core.config import SigningConfig

    return int(SigningConfig().grace_seconds)


#: A tenant that DECLARES a signing posture and owns NO key, at a caller-supplied
#: netloc: every key this module observes must arrive through the admin route,
#: minted inside the server container under the CONTAINER's KEK, or the module
#: grades its own fixture. See :func:`keyless_declaring_tenant_fixture`.
keyless_declaring_tenant = keyless_declaring_tenant_fixture(
    tenant_id=_TENANT_ID, slug=_SLUG, operation=_DECLARED_OPERATION
)


@pytest.mark.asyncio
async def test_key_lifecycle_is_visible_in_the_documents_a_counterparty_fetches(
    docker_services_e2e, live_server, keyless_declaring_tenant
):
    """Provision, rotate, revoke, retire — each observed only on the wire."""
    base_url = tls_base_url(live_server)
    verify = ca_verified_ssl_context()

    # The netloc INCLUDES the port: get_tenant_by_virtual_host matches the Host
    # header as an exact string, and httpx sends the port unless it is the
    # scheme default.
    _tenant, key = keyless_declaring_tenant(netloc(base_url))
    assert key is None, "every key this module observes must arrive through the admin route, not the fixture"

    async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=15.0) as client:
        # ── Phase A — PROVISION + PUBLISH ─────────────────────────────────────
        kid1 = provision_signing_key_via_admin(base_url, tenant_id=_TENANT_ID)

        jwks = await _get_json(client, JWKS_PATH)
        assert published_kids(jwks["keys"]) == {kid1}, (
            f"the served JWKS must carry exactly the one key the admin route reported provisioning "
            f"({kid1!r}); got {sorted(published_kids(jwks['keys']))}"
        )

        signing_kid, revoked_kids = _revocation_signals(await _get_json(client, GOVERNANCE_REVOCATIONS_PATH))
        assert signing_kid == kid1, (
            f"the tenant's only key must also be the one that SIGNS the published revocation list — that "
            f"protected-header kid is the only wire signal of active_at; got {signing_kid!r}, expected {kid1!r}"
        )
        assert revoked_kids == [], (
            f"nothing has been revoked yet, so the permanent revocation record must be empty; got {revoked_kids!r}"
        )

        # ── Phase B — ROTATE (publish before sign) ────────────────────────────
        kid2 = provision_signing_key_via_admin(base_url, tenant_id=_TENANT_ID)
        # The flip below is ordered by created_at (server_default=func.now(), so
        # Postgres transaction-start time, distinct across two HTTP requests),
        # NOT by kid: a kid's trailing random hex is not the discriminator, and a
        # future change to that default would otherwise turn a correctness
        # failure into an unexplained 50% flake.
        assert kid2 != kid1, f"the second provisioning must mint a distinct key; both came back as {kid1!r}"

        jwks = await _get_json(client, JWKS_PATH)
        assert published_kids(jwks["keys"]) == {kid1, kid2}, (
            "both keys of a rotation overlap must be PUBLISHED — the incoming key has to be in every "
            "verifier's cache before it signs anything, and the outgoing key has to stay until the "
            f"signatures it made age out; got {sorted(published_kids(jwks['keys']))}"
        )

        signing_kid, revoked_kids = _revocation_signals(await _get_json(client, GOVERNANCE_REVOCATIONS_PATH))
        assert signing_kid == kid2, (
            f"the newest key SIGNS the moment it exists, while both stay published — that asymmetry between "
            f"active_at and publishable_at is what this phase grades; the list is still signed by {signing_kid!r}, "
            f"expected {kid2!r}"
        )
        assert revoked_kids == [], f"rotation revokes nothing; got {revoked_kids!r}"

        # ── Phase C — REVOKE (retires a signer, never a publication) ──────────
        assert revoke_signing_key_via_admin(base_url, tenant_id=_TENANT_ID, kid=kid1) == kid1, (
            "the admin revoke route must report revoking the kid it was asked to revoke"
        )

        jwks = await _get_json(client, JWKS_PATH)
        assert published_kids(jwks["keys"]) == {kid1, kid2}, (
            "a revoked key stays PUBLISHED for its grace period — a cache that has not refreshed must "
            "still find the key so it can evaluate the marker; got "
            f"{sorted(published_kids(jwks['keys']))}"
        )
        published_by_kid = {entry["kid"]: entry for entry in jwks["keys"]}
        assert "revoked_at" in published_by_kid[kid1], (
            "the published JWK of a revoked key MUST carry revoked_at — without the marker it is "
            f"indistinguishable from a live key, and this document governs request signing; got "
            f"{published_by_kid[kid1]!r}"
        )
        assert "revoked_at" not in published_by_kid[kid2], (
            f"the surviving key must NOT carry a revocation marker; got {published_by_kid[kid2]!r}"
        )

        pinned_by_kid = {
            entry["kid"]: entry for entry in _pinned_signing_keys(await _get_json(client, ADAGENTS_JSON_PATH))
        }
        assert set(pinned_by_kid) == {kid1, kid2}, (
            "the adagents signing_keys[] pin is built from the SAME publishable_at result as the JWKS, so "
            f"the two cannot disagree about which keys exist; pin has {sorted(pinned_by_kid)}"
        )
        assert "revoked_at" in pinned_by_kid[kid1], (
            f"and the pin carries the same marker as the JWKS entry; got {pinned_by_kid[kid1]!r}"
        )

        signing_kid, revoked_kids = _revocation_signals(await _get_json(client, GOVERNANCE_REVOCATIONS_PATH))
        assert signing_kid == kid2, (
            f"revoking the OUTGOING key must not disturb the signer; the list is signed by {signing_kid!r}, "
            f"expected {kid2!r}"
        )
        assert revoked_kids == [kid1], (
            f"the revoked key must appear in the permanent revocation record; got {revoked_kids!r}"
        )

        # ── Phase D — GRACE ELAPSES (a clock move, not a transport) ───────────
        _backdate_revocation(live_server, kid=kid1, seconds=2 * _grace_seconds())

        jwks = await _get_json(client, JWKS_PATH)
        assert published_kids(jwks["keys"]) == {kid2}, (
            "past its grace window the revoked key is REMOVED from the published key set — every verifier's "
            f"cache has had time to see the marker; got {sorted(published_kids(jwks['keys']))}"
        )
        assert published_kids(_pinned_signing_keys(await _get_json(client, ADAGENTS_JSON_PATH))) == {kid2}, (
            "...and removed from the adagents pin in the same breath, since both project one query"
        )

        signing_kid, revoked_kids = _revocation_signals(await _get_json(client, GOVERNANCE_REVOCATIONS_PATH))
        assert revoked_kids == [kid1], (
            "the revocation record is PERMANENT where the publication window is not — a kid dropped from "
            f"revoked_kids is a kid UN-REVOKED; got {revoked_kids!r}"
        )
        assert signing_kid == kid2, f"the signer is unchanged by an elapsed grace window; got {signing_kid!r}"

        # ── Phase E — FAIL CLOSED (last: it withdraws the instrument) ─────────
        assert revoke_signing_key_via_admin(base_url, tenant_id=_TENANT_ID, kid=kid2) == kid2, (
            "the admin revoke route must report revoking the kid it was asked to revoke"
        )

        await _get_json(client, GOVERNANCE_REVOCATIONS_PATH, expect_status=404)

        # The disambiguating control, in the SAME phase and at the SAME host: the
        # trust-root seam returns an identical 404 for a declined handler and for a
        # routing miss, so a lone 404 above is equally satisfied by the tenant row
        # vanishing, the virtual_host breaking, or TLS resolving elsewhere. A 200
        # on one document beside a 404 on the other is the only pair that isolates
        # "no active key resolves, so the list is withdrawn rather than served
        # signed by a dead key" from "the tenant is unreachable".
        jwks = await _get_json(client, JWKS_PATH)
        assert published_kids(jwks["keys"]) == {kid2}, (
            "revoking the last active key withdraws the SIGNED document but not the published key set — "
            f"kid2 stays in the JWKS for its own grace period; got {sorted(published_kids(jwks['keys']))}"
        )
        assert "revoked_at" in {entry["kid"]: entry for entry in jwks["keys"]}[kid2], (
            "and it is published CARRYING its revocation marker, which is what tells a counterparty the "
            "404 beside it is a deliberate withdrawal rather than an outage"
        )
