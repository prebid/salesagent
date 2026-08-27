"""Integration tests for the trust root this agent publishes (#1291 A3, salesagent-z6nr.9).

TDD RED. Nothing here passes until ``src/core/agent_identity.py``,
``src/core/signing/trust_root.py``, ``src/routes/well_known.py`` and
``SigningKeyRepository.publishable_at`` exist.

Core Invariant under test: ONE canonical agent URL string per tenant, computed by
exactly one function, is the sole identity from which every trust-root document
and every published key origin is derived — so the byte-equal ``agents[].url``
match a verifier performs, the eTLD+1 origin binding, and the ``key_origins``
consistency check can never disagree with each other or with where our keys
actually resolve.

What each group grades, and why it is written the way it is:

* **Per-endpoint entries (R-H1).** security.mdx:1104 step 5 byte-equals the URL
  whose ``get_adcp_capabilities`` the counterparty invoked — and that is an
  ENDPOINT (``/mcp``, ``/a2a``), never a bare origin. The brand.json schema's own
  ``#/definitions/agents`` description blesses several same-type entries with
  distinct urls. The expected literals are therefore discovered by ASKING THE
  RUNNING APP which path it actually serves, never by rebuilding the string with
  the same helper production uses: a trailing-slash mismatch is the spec's own
  worked failure example (``https://x.com/mcp`` vs ``https://x.com/mcp/``), and
  Starlette's ``/mcp`` mount redirects while ``/a2a`` does not. A test that
  constructs its expectation the way the code does proves nothing.

* **``revoked_at`` on the JWKS, not only on adagents (R-H3).** Per F1 the JWKS is
  authoritative for REQUEST signing; the adagents pin governs sell-side webhooks
  only. The schema's justification for the grace window is that caches which have
  not refreshed "still find the key and can evaluate the revocation marker" — a
  JWKS entry without the marker is indistinguishable from a live key, which
  inverts the property the window exists for.

* **``publishable_at`` filters ``purpose``, and ``revoked_at`` is the only exit
  (R-M4).** ``not_after`` un-publishes a key whose signatures are still inside
  their verification window; ``not_before`` un-publishes a key that rotation must
  publish BEFORE it starts signing. Purpose is filtered because security.mdx:1079
  forbids co-tenanting governance keys with request-signing keys.

* **Grace EXCEEDS the published cache TTL (R-M2/R-L).** Equal values leave zero
  margin for an intermediary adding delay, so the relationship is asserted (2x,
  from one constant) rather than two magic numbers.

Schema authority: the vendored v3.1.1 fixtures under
``tests/fixtures/adcp_schemas_pinned/3.1.1/`` (``adagents.json`` and
``brand.json`` DIFFER at the older ``PINNED_SHA``, and
``core/authorized-agent-base.json`` — where ``signing_keys[]`` lives — does not
exist there at all). The adagents document additionally goes through
``adcp.adagents.validate_adagents_structure``: that is the exact code path our
OWN consumers run (``src/admin/blueprints/publisher_partners.py``), so it grades
the producer against the consumer rather than against a second opinion.

No BDD feature accompanies this work. Not because the harness cannot model these
documents — it models them as COUNTERPARTY state today (``BR-UC-020:721`` serves
a ``/.well-known/brand.json``; ``BR-UC-025:135`` a publisher's adagents.json) —
but because A3 emits no TOOL RESPONSE for the four-transport dispatch to grade.
The Core Invariant becomes tool-gradable at D1 (salesagent-z6nr.20), where
``get_adcp_capabilities`` starts emitting ``identity.brand_json_url``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import pytest
from adcp import get_adcp_spec_version

from tests.harness._base import BareIntegrationEnv
from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION
from tests.helpers.pinned_schema import validate_against_pinned_schema
from tests.helpers.signing import REQUEST_SIGNING
from tests.helpers.signing import get_trust_root_document as _get_document
from tests.helpers.signing import published_kids as _kids

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# A fixed instant. Publication arithmetic must be evaluated against the caller's
# ``now``, never against wall-clock time.
_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

_BRAND_SCHEMA = f"{EXPECTED_SPEC_VERSION}/brand.json"
_ADAGENTS_SCHEMA = f"{EXPECTED_SPEC_VERSION}/adagents.json"

_BRAND_PATH = "/.well-known/brand.json"
_ADAGENTS_PATH = "/.well-known/adagents.json"
_JWKS_PATH = "/.well-known/jwks.json"

# The key set of brand.json's "Brand Agent" oneOf variant (additionalProperties:
# false). Pinned so a document that silently drifts into another variant is
# caught by name rather than by a downstream validation failure.
_BRAND_AGENT_KEYS = {
    "$schema",
    "version",
    "agents",
    "brand_agent",
    "contact",
    "data_subject_contestation",
    "last_updated",
}


def _seed(env, slug: str, **key_kwargs):
    """Seed one tenant reachable at its own virtual host, plus one signing key.

    Every test gets its OWN slug: the integration database is not rolled back
    between tests in this suite, and two live tenants sharing a ``virtual_host``
    would make ``get_tenant_by_virtual_host`` resolve an arbitrary one.

    The subdomain is deliberately hyphenated — hyphens are legal there and
    ILLEGAL in brand.json's ``brand_agent_entry.id`` (``^[a-z0-9_]+$``).
    """
    from tests.factories import SigningKeyFactory, TenantFactory

    tenant = TenantFactory(
        tenant_id=f"tr_{slug}",
        subdomain=f"seller-{slug}",
        virtual_host=f"seller-{slug}.example.com",
    )
    key_kwargs.setdefault("not_before", _NOW - timedelta(days=1))
    key = SigningKeyFactory(tenant=tenant, **key_kwargs)
    env.get_session()  # commits pending factory data
    return tenant, key


def _served_endpoint_paths(client) -> dict[str, str]:
    """Ask the RUNNING APP which path a counterparty actually ends up calling.

    Returns ``{"mcp": ..., "a2a": ...}`` — the path AFTER any redirect the app
    itself issues. ``/mcp`` is a Starlette ``Mount`` and answers ``GET /mcp`` with
    a 307 to ``/mcp/``; ``/a2a`` is a JSON-RPC POST route and answers ``GET /a2a``
    with 405, i.e. it is served at exactly that path. That asymmetry is the whole
    point: the byte-equal match in security.mdx step 5 is against the URL the
    counterparty invoked, and the redirect decides what that string is.

    The ``404`` guard is what makes the fallback branch legitimate. "Not a redirect"
    only means "served at *candidate*" while SOMETHING answers *candidate*; a ``404``
    means nothing does, and recording it as the served path would publish a URL that
    resolves to nothing. It has a concrete trigger: a sibling test file that starts the
    app lifespan and does not restore leaves the Flask catch-all ``Mount("")`` on the
    singleton's route table, which swallows both bare paths and turns the ``307``/``405``
    above into ``404`` (``salesagent-66a1``, :mod:`tests.helpers.app_state`). Failing
    here names that cause; falling through produced a byte-equality mismatch that did
    not.
    """
    paths: dict[str, str] = {}
    for name, candidate in (("mcp", "/mcp"), ("a2a", "/a2a")):
        response = client.get(candidate, follow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            paths[name] = urlsplit(response.headers["location"]).path
            continue
        assert response.status_code != 404, (
            f"discovery is meaningless: nothing on this app answers {candidate!r} and it "
            "issues no redirect either, so no served path can be read off it. Most likely "
            "another test file started the app lifespan without restoring "
            "src.app.app.router.routes (see tests.helpers.app_state)"
        )
        paths[name] = candidate
    return paths


def _pinned_kids(adagents: dict[str, Any]) -> set[str]:
    return {key["kid"] for entry in adagents["authorized_agents"] for key in entry.get("signing_keys", [])}


def _entry_for(document: dict[str, Any], url: str) -> dict[str, Any]:
    """The brand.json ``agents[]`` entry whose url BYTE-EQUALS *url*.

    This is literally step 5 of the discovery algorithm, so the lookup the test
    performs is the lookup a counterparty performs.
    """
    matches = [entry for entry in document["agents"] if entry["url"] == url]
    assert len(matches) == 1, (
        f"brand.json must contain exactly one agents[] entry whose url byte-equals {url!r}; "
        f"got {[entry['url'] for entry in document['agents']]}"
    )
    return matches[0]


def _grace_seconds() -> int:
    from src.core.config import SigningConfig

    return int(SigningConfig().grace_seconds)


def _publishable(env, tenant_id: str, *, now: datetime = _NOW, purpose: str = REQUEST_SIGNING) -> list:
    from src.core.database.repositories.signing_key import SigningKeyRepository

    repo = SigningKeyRepository(env.get_session(), tenant_id)
    return repo.publishable_at(now=now, grace_seconds=_grace_seconds(), purpose=purpose)


class TestCanonicalAgentUrlIsTheOneIdentity:
    """One derivation feeds brand.json, the agent card and the admin URL builder."""

    def test_brand_json_publishes_one_entry_per_served_endpoint(self, integration_db):
        """R-H1: an entry per endpoint we actually serve, each byte-equal to the URL
        a counterparty calls, each with a distinct ``id`` and an explicit
        ``jwks_uri``.

        A bare-origin ``url`` byte-equals nothing anybody ever invoked and yields
        ``request_signature_agent_not_in_brand_json`` on every signed request —
        precisely the failure the Core Invariant exists to make impossible.
        """
        from src.core.agent_identity import canonical_agent_url, jwks_uri

        with BareIntegrationEnv(tenant_id="trust_root_env_a") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "a")
            client = env.get_rest_client()

            served = _served_endpoint_paths(client)
            origin = canonical_agent_url(tenant)
            expected_urls = {origin + served["mcp"], origin + served["a2a"]}

            document = _get_document(client, _BRAND_PATH, tenant)

            published_urls = [entry["url"] for entry in document["agents"]]
            assert set(published_urls) == expected_urls, (
                "brand.json agents[].url must byte-equal the endpoint URLs this app serves "
                f"(discovered from the running app: {sorted(expected_urls)}); got {sorted(published_urls)}"
            )
            assert len(published_urls) == len(set(published_urls)), (
                f"agents[].url values MUST be unique within the array; got {published_urls}"
            )

            ids = [entry["id"] for entry in document["agents"]]
            assert len(ids) == len(set(ids)), (
                "each per-endpoint entry needs a distinct id — the SDK's _pick_agent "
                f"disambiguates same-type entries by id alone; got {ids}"
            )
            for entry in document["agents"]:
                assert entry["type"] == "sales", f"entry {entry['id']!r} must declare type 'sales'"
                assert entry["jwks_uri"] == jwks_uri(tenant), (
                    "every entry carries an EXPLICIT jwks_uri (never relying on the verifier's "
                    f"default) and they all point at the one JWKS; got {entry.get('jwks_uri')!r}"
                )

    def test_agent_card_interface_url_equals_brand_json_a2a_entry_url(self, integration_db):
        """R-M2: the A2A interface URL on the agent card and brand.json's A2A
        ``agents[].url`` are the SAME string.

        The card is what a counterparty reads to learn where to call; brand.json is
        what it byte-matches against. Two derivations means two chances to disagree
        on a scheme, a port or a trailing slash — with no diagnostic.
        """
        with BareIntegrationEnv(tenant_id="trust_root_env_b") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "b")
            client = env.get_rest_client()

            served = _served_endpoint_paths(client)
            document = _get_document(client, _BRAND_PATH, tenant)
            card = _get_document(client, "/.well-known/agent-card.json", tenant)

            card_url = card["supportedInterfaces"][0]["url"]
            brand_a2a_url = _entry_for(document, card_url)["url"]

            assert card_url == brand_a2a_url, (
                f"agent card interface URL {card_url!r} must be the same string as brand.json's "
                f"A2A agents[].url {brand_a2a_url!r}"
            )
            assert card_url.endswith(served["a2a"]), (
                f"the card must advertise the A2A path this app serves ({served['a2a']!r}); got {card_url!r}"
            )

    def test_construct_agent_url_derives_from_the_canonical_helper(self, integration_db, monkeypatch):
        """R-M2: the admin blueprint's URL builder is the second migrated call site,
        not a third independent derivation.

        Its PRODUCTION/localhost ladder is what makes the adagents authorization
        checks compare against a different string from the one we publish.
        """
        from src.admin.blueprints.authorized_properties import _construct_agent_url
        from src.core.agent_identity import canonical_agent_url

        monkeypatch.delenv("ADCP_AGENT_URL", raising=False)
        monkeypatch.delenv("PRODUCTION", raising=False)

        with BareIntegrationEnv(tenant_id="trust_root_env_c") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "c")

            assert _construct_agent_url(tenant.tenant_id, None) == canonical_agent_url(tenant), (
                "_construct_agent_url must return the canonical agent URL, not a PRODUCTION-flag / "
                "localhost-port derivation of its own"
            )

    def test_agent_entry_ids_satisfy_the_schema_charset_for_a_hyphenated_subdomain(self, integration_db):
        """R1: ``brand_agent_entry.id`` is ``^[a-z0-9_]+$`` while tenant subdomains
        routinely carry hyphens — an unslugged id makes the whole document
        schema-invalid, and the id is a selector counterparties may pin.
        """
        from src.core.signing.trust_root import build_brand_json

        with BareIntegrationEnv(tenant_id="trust_root_env_d") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "d-news")

            assert "-" in tenant.subdomain, "this test is only meaningful for a hyphenated subdomain"
            document = build_brand_json(tenant, _publishable(env, tenant.tenant_id))

            for entry in document["agents"]:
                assert re.fullmatch(r"[a-z0-9_]+", entry["id"]), (
                    f"agents[].id must match ^[a-z0-9_]+$ (subdomain {tenant.subdomain!r} is "
                    f"hyphenated); got {entry['id']!r}"
                )
                assert len(entry["id"]) <= 100, f"agents[].id maxLength is 100; got {len(entry['id'])}"


class TestBrandJsonSchemaConformance:
    """The built document validates against the vendored v3.1.1 schema."""

    def test_brand_json_is_valid_and_is_the_brand_agent_variant(self, integration_db):
        """``oneOf`` makes "matches exactly one variant" a validation outcome: a
        document matching two variants fails just as hard as one matching none.

        The variant is then named explicitly, because choosing "Brand Agent" costs
        us ``authorized_operators[]`` (only on House Portfolio) — the eTLD+1
        delegation escape hatch of security.mdx step 3 — so brand.json MUST stay on
        the tenant's own host. A silent drift to another variant would change that
        constraint without changing any assertion here otherwise.
        """
        from src.core.signing.trust_root import build_brand_json

        with BareIntegrationEnv(tenant_id="trust_root_env_e") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "e")

            document = build_brand_json(tenant, _publishable(env, tenant.tenant_id))

            validate_against_pinned_schema(_BRAND_SCHEMA, document)

            assert set(document) <= _BRAND_AGENT_KEYS, (
                "the Brand Agent variant is additionalProperties: false; unexpected keys "
                f"{sorted(set(document) - _BRAND_AGENT_KEYS)}"
            )
            # THE VALUE, not its presence. "$schema" is a MEMBER of _BRAND_AGENT_KEYS
            # above, and a key-set check grades that the key EXISTS — it is structurally
            # blind to a document pointing at the wrong spec version, which is exactly the
            # drift a $schema exists to prevent. Derived from the pinned SDK here for the
            # same reason production derives it (#1757): a literal on both sides agrees
            # with itself while both are stale.
            assert (
                document["$schema"] == f"https://adcontextprotocol.org/schemas/{get_adcp_spec_version()}/brand.json"
            ), (
                "brand.json must point $schema at the PINNED spec version; got "
                f"{document['$schema']!r} against pin {get_adcp_spec_version()!r}"
            )
            assert "agents" in document, "the Brand Agent variant requires agents (or brand_agent)"
            assert "house" not in document, (
                "emitting `house` selects the House Portfolio variant — a different document with a "
                "different trust model"
            )


class TestPublicationSelector:
    """``publishable_at`` — revocation is the only exit, and purpose is scoped."""

    def test_publishable_at_is_scoped_to_the_requested_purpose(self, integration_db):
        """R-M4: a selector blind to ``purpose`` would land a governance key in the
        request-signing JWKS — the co-tenancy security.mdx:1079 forbids
        ("governance signing keys MUST be served from a separate origin").

        Latent today only because ``MINTABLE_PURPOSES`` holds a single value, and a
        latent violation of a MUST is still one. What makes the ROW side unreachable
        is the DB CHECK, not the selector — so the selector is graded from the query
        side: asking for another purpose must come back empty.
        """
        with BareIntegrationEnv(tenant_id="trust_root_env_f") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "f")

            assert _publishable(env, tenant.tenant_id, purpose=REQUEST_SIGNING), (
                "the request-signing key must be publishable for its own purpose"
            )
            assert _publishable(env, tenant.tenant_id, purpose="webhook-signing") == [], (
                "publishable_at must filter on purpose — asking for another purpose must not return "
                "this tenant's request-signing key material"
            )

    def test_key_past_not_after_is_still_published(self, integration_db):
        """Publication is window-blind by design: signatures made under a key whose
        window has closed are still inside their verification window, so
        un-publishing it opens exactly the gap rotation overlap exists to prevent.
        Retirement MUST go through ``revoked_at``.
        """
        with BareIntegrationEnv(tenant_id="trust_root_env_g") as env:
            env.setup_default_data()
            tenant, key = _seed(env, "g", not_after=_NOW - timedelta(hours=1))

            published = _publishable(env, tenant.tenant_id)

            assert [row.kid for row in published] == [key.kid], (
                "a key whose not_after has passed MUST still be published — its signatures are still "
                f"verifiable; got {[row.kid for row in published]}"
            )

    def test_key_before_not_before_is_already_published(self, integration_db):
        """Rotation publishes AHEAD of signing: the incoming key must be resolvable
        by every verifier before it produces its first signature.
        """
        with BareIntegrationEnv(tenant_id="trust_root_env_h") as env:
            env.setup_default_data()
            tenant, key = _seed(env, "h", not_before=_NOW + timedelta(days=7))

            published = _publishable(env, tenant.tenant_id)

            assert [row.kid for row in published] == [key.kid], (
                "a key whose not_before is in the future MUST already be published — rotation "
                f"publishes before it signs; got {[row.kid for row in published]}"
            )

    def test_two_overlapping_keys_are_both_published(self, integration_db):
        """The rotation overlap A2 provisions is what the JWKS exists to expose."""
        from src.core.signing.trust_root import build_jwks
        from tests.factories import SigningKeyFactory

        with BareIntegrationEnv(tenant_id="trust_root_env_i") as env:
            env.setup_default_data()
            tenant, outgoing = _seed(env, "i")
            incoming = SigningKeyFactory(tenant=tenant, not_before=_NOW - timedelta(minutes=5))
            env.get_session()

            jwks = build_jwks(_publishable(env, tenant.tenant_id))

            assert _kids(jwks["keys"]) == {outgoing.kid, incoming.kid}, (
                f"both keys of a rotation overlap must appear in the JWKS; got {sorted(_kids(jwks['keys']))}"
            )


class TestRevocationGraceWindow:
    """The marker travels with the key, and the window outlives the cache."""

    def test_revoked_key_in_grace_carries_revoked_at_in_both_documents(self, integration_db):
        """R-H3: the JWKS is authoritative for REQUEST signing, so a revoked key
        inside its grace window must appear there CARRYING its marker — a cache that
        has not refreshed must be able to tell it apart from a live key. An entry
        without the marker inverts the property the grace window exists for.
        """
        from src.core.signing.trust_root import build_adagents_json, build_jwks
        from tests.factories import AuthorizedPropertyFactory, SigningKeyFactory

        with BareIntegrationEnv(tenant_id="trust_root_env_j") as env:
            env.setup_default_data()
            tenant, live = _seed(env, "j")
            revoked_at = _NOW - timedelta(seconds=_grace_seconds() // 2)
            revoked = SigningKeyFactory(tenant=tenant, not_before=_NOW - timedelta(days=2), revoked_at=revoked_at)
            # An authorization record is a PRECONDITION for this test, not incidental setup:
            # every authorized_agents[*] variant requires its selector array (minItems: 1), so a
            # tenant with no backing record correctly publishes authorized_agents == [] (asserted
            # by test_adagents_claims_no_authorization_without_a_backing_record). Without a record
            # there is no pin to inspect, and the marker assertions below would pass VACUOUSLY —
            # `all(...)` over an empty list is true. Same shape as the two sibling tests.
            authorization = AuthorizedPropertyFactory(
                tenant=tenant, publisher_domain=tenant.virtual_host, tags=["premium_news"]
            )
            env.get_session()

            keys = _publishable(env, tenant.tenant_id)
            jwks = build_jwks(keys)
            adagents = build_adagents_json(tenant, keys, [authorization])

            assert _kids(jwks["keys"]) == {live.kid, revoked.kid}, (
                f"a key inside its grace window stays in the JWKS; got {sorted(_kids(jwks['keys']))}"
            )

            by_kid = {entry["kid"]: entry for entry in jwks["keys"]}
            assert "revoked_at" in by_kid[revoked.kid], (
                "the revoked JWKS entry MUST carry revoked_at — without it a stale cache cannot "
                "distinguish it from a live key, and the JWKS is what governs request signing"
            )
            assert "revoked_at" not in by_kid[live.kid], "the live key must NOT carry a revocation marker"

            stamped = datetime.fromisoformat(by_kid[revoked.kid]["revoked_at"])
            assert stamped.tzinfo is not None, (
                "revoked_at is format: date-time — emit RFC 3339 with an explicit offset, not naive "
                f"UTC; got {by_kid[revoked.kid]['revoked_at']!r}"
            )
            assert stamped == revoked_at, f"revoked_at must be the stored instant; got {stamped}"

            assert revoked.kid in _pinned_kids(adagents), (
                "the adagents pin is built from the SAME publishable_at result, so it carries the "
                f"revoked key too; got {sorted(_pinned_kids(adagents))}"
            )
            marked = [
                key
                for entry in adagents["authorized_agents"]
                for key in entry.get("signing_keys", [])
                if key["kid"] == revoked.kid
            ]
            assert all("revoked_at" in key for key in marked), (
                "and it carries the same marker — one query, two projections, so the two documents "
                "cannot disagree about which keys are revoked"
            )

    def test_revoked_key_past_grace_disappears_from_both_documents(self, integration_db):
        """Once the cache TTL has elapsed across all verifiers the key is removed."""
        from src.core.signing.trust_root import build_adagents_json, build_jwks
        from tests.factories import SigningKeyFactory

        with BareIntegrationEnv(tenant_id="trust_root_env_k") as env:
            env.setup_default_data()
            tenant, live = _seed(env, "k")
            expired = SigningKeyFactory(
                tenant=tenant,
                not_before=_NOW - timedelta(days=3),
                revoked_at=_NOW - timedelta(seconds=_grace_seconds() * 2),
            )
            env.get_session()

            keys = _publishable(env, tenant.tenant_id)
            jwks = build_jwks(keys)
            adagents = build_adagents_json(tenant, keys, [])

            assert _kids(jwks["keys"]) == {live.kid}, (
                f"a key revoked beyond the grace window must be gone from the JWKS; got {sorted(_kids(jwks['keys']))}"
            )
            assert expired.kid not in _pinned_kids(adagents), (
                f"...and gone from the adagents pin too; got {sorted(_pinned_kids(adagents))}"
            )

    def test_grace_is_twice_the_published_cache_max_age(self, integration_db):
        """R-L: the schema's removal rule is "once the cache TTL has elapsed ACROSS
        ALL verifiers". Equal values leave zero margin for an intermediary adding
        delay, so grace must EXCEED the TTL — 2x, derived from ONE constant so the
        two cannot drift apart.
        """
        # From the FACADE, not from trust_root: the constant moved to the layer's
        # dependency-free leaf (#1757) because src.core.config derives grace_seconds from
        # it and three modules inside the signing package import config back — a cycle
        # while it sat behind the package's __init__. The facade still exports it, which
        # is the sanctioned caller shape and is stable across that move.
        from src.core.signing import CACHE_MAX_AGE_SECONDS

        with BareIntegrationEnv(tenant_id="trust_root_env_l") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "l")
            client = env.get_rest_client()

            assert _grace_seconds() == 2 * CACHE_MAX_AGE_SECONDS, (
                "the revocation grace window must be derived from the published cache max-age (2x), "
                f"not configured independently; grace={_grace_seconds()} max_age={CACHE_MAX_AGE_SECONDS}"
            )

            for path in (_BRAND_PATH, _ADAGENTS_PATH, _JWKS_PATH):
                response = client.get(path, headers={"Host": tenant.virtual_host})
                cache_control = response.headers.get("cache-control", "")
                assert f"max-age={CACHE_MAX_AGE_SECONDS}" in cache_control, (
                    f"{path} must publish an explicit Cache-Control max-age={CACHE_MAX_AGE_SECONDS} — "
                    f"a proxy inventing its own TTL can mask a rotation; got {cache_control!r}"
                )


class TestWellKnownEndpoints:
    """Three plain documents, unauthenticated, resolved per Host."""

    def test_all_three_documents_are_served_without_authentication(self, integration_db):
        """These documents bootstrap the trust chain: requiring the signature they
        exist to let a verifier check would deadlock every counterparty.
        """
        with BareIntegrationEnv(tenant_id="trust_root_env_m") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "m")
            client = env.get_rest_client()

            for path in (_BRAND_PATH, _ADAGENTS_PATH, _JWKS_PATH):
                anonymous = client.get(path, headers={"Host": tenant.virtual_host})
                assert anonymous.status_code == 200, (
                    f"{path} must be served with no auth header; got {anonymous.status_code}"
                )
                garbage = client.get(path, headers={"Host": tenant.virtual_host, "x-adcp-auth": "not-a-real-token"})
                assert garbage.status_code == 200, (
                    f"{path} must sit OUTSIDE the protected surface — an unusable token must not "
                    f"change the answer; got {garbage.status_code}"
                )

    def test_documents_are_scoped_to_the_host_tenant(self, integration_db):
        """Each tenant is a distinct seller identity. Serving tenant B's key material
        under tenant A's host would let a verifier accept B's signature for A.
        """
        from src.core.agent_identity import canonical_agent_url

        with BareIntegrationEnv(tenant_id="trust_root_env_n") as env:
            env.setup_default_data()
            tenant_a, key_a = _seed(env, "n1")
            tenant_b, key_b = _seed(env, "n2")
            client = env.get_rest_client()

            assert canonical_agent_url(tenant_a) != canonical_agent_url(tenant_b), (
                "the two seeded tenants must have distinct canonical URLs for this test to mean anything"
            )

            jwks_a = _get_document(client, _JWKS_PATH, tenant_a)
            jwks_b = _get_document(client, _JWKS_PATH, tenant_b)

            assert _kids(jwks_a["keys"]) == {key_a.kid}, (
                f"tenant A's JWKS must carry only tenant A's key; got {sorted(_kids(jwks_a['keys']))}"
            )
            assert _kids(jwks_b["keys"]) == {key_b.kid}, (
                f"tenant B's JWKS must carry only tenant B's key; got {sorted(_kids(jwks_b['keys']))}"
            )

            brand_a = _get_document(client, _BRAND_PATH, tenant_a)
            assert all(entry["url"].startswith(canonical_agent_url(tenant_a) + "/") for entry in brand_a["agents"]), (
                "every entry under tenant A's host must be anchored on tenant A's own origin — the "
                "Brand Agent variant has no authorized_operators[] escape hatch, so brand.json and "
                f"the agent MUST share an origin; got {[e['url'] for e in brand_a['agents']]}"
            )


class TestAdagentsDocument:
    """The publisher-pin document — same key set, and never a fabricated claim."""

    def test_adagents_is_schema_valid_and_passes_the_consumer_validator(self, integration_db):
        """Graded twice on purpose: jsonschema against the vendored v3.1.1 file, and
        ``validate_adagents_structure`` — the exact function our own admin code runs
        when it consumes a PUBLISHER's adagents.json.
        """
        from adcp.adagents import validate_adagents_structure

        from tests.factories import AuthorizedPropertyFactory

        with BareIntegrationEnv(tenant_id="trust_root_env_o") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "o")
            AuthorizedPropertyFactory(tenant=tenant, publisher_domain=tenant.virtual_host, tags=["premium_news"])
            env.get_session()
            client = env.get_rest_client()

            document = _get_document(client, _ADAGENTS_PATH, tenant)

            validate_against_pinned_schema(_ADAGENTS_SCHEMA, document)

            report = validate_adagents_structure(document)
            assert report.schema_valid, f"our own consumer path rejects the document we publish: {report.errors}"
            assert report.authorized_agents_count >= 1, (
                "the document must carry the authorization backed by the seeded authorized-property "
                f"record; got {report.authorized_agents_count} entries"
            )

    def test_adagents_signing_keys_are_the_jwks_key_set(self, integration_db):
        """R7: both documents are produced from the SAME ``publishable_at`` result.
        If the pin drifts from the JWKS, our own sell-side webhooks are rejected
        against our own published document.
        """
        from tests.factories import AuthorizedPropertyFactory

        with BareIntegrationEnv(tenant_id="trust_root_env_p") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "p")
            AuthorizedPropertyFactory(tenant=tenant, publisher_domain=tenant.virtual_host, tags=["premium_news"])
            env.get_session()
            client = env.get_rest_client()

            jwks = _get_document(client, _JWKS_PATH, tenant)
            adagents = _get_document(client, _ADAGENTS_PATH, tenant)

            assert _pinned_kids(adagents) == _kids(jwks["keys"]), (
                "the adagents signing_keys[] pin and the JWKS must be the same key set; "
                f"pin={sorted(_pinned_kids(adagents))} jwks={sorted(_kids(jwks['keys']))}"
            )

    def test_adagents_claims_no_authorization_without_a_backing_record(self, integration_db):
        """R-M1: ``authorizations`` come from the existing authorized-properties
        records and are NEVER fabricated — fabricating them means self-attesting an
        authorization no publisher granted.
        """
        with BareIntegrationEnv(tenant_id="trust_root_env_q") as env:
            env.setup_default_data()
            tenant, _ = _seed(env, "q")
            client = env.get_rest_client()

            response = client.get(_ADAGENTS_PATH, headers={"Host": tenant.virtual_host})

            assert response.status_code == 200, (
                f"the endpoint answers for a tenant with no properties; got {response.status_code}"
            )
            assert response.json()["authorized_agents"] == [], (
                "with no authorized-property record on file the document must claim NO authorization; "
                f"got {response.json()['authorized_agents']}"
            )
