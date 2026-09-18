"""Integration tests for tenant signing-key PROVISIONING (#1291, salesagent-7x8t).

Written TDD RED and now green: the first six groups grade the provisioning
transports the ticket built -- the admin blueprint
(``src/admin/blueprints/signing_keys.py``, design step 5), the scripted path
(``scripts/ops/provision_signing_key.py``, step 6), the ``db:`` storage of
``provision_signing_key`` plus the ``signing_keys.private_key_pem_encrypted``
column (steps 1-3), and the KEK / ref-scheme gates (step 2). Each group names the
production module it fails for, so a regression reports which one went away.

Core Invariant under test: every ``signing_keys`` row is born through exactly ONE
function, which does not complete unless the private key behind the row resolves
and round-trips to the public JWK the row publishes -- so the agent can never
publish a key it cannot sign with, and never holds plaintext key material
anywhere.

What each group grades, and why it is written the way it is:

* **Acceptance 1 + 2 in ONE chain.** Provision through a real, non-test path and
  then read ``/.well-known/jwks.json`` over HTTP with the tenant's ``Host``. The
  ticket's two acceptance items are a single claim -- a key that is provisioned
  but not published is not usable, and a published key nobody can mint is not
  provisioned -- and no existing test connects them: both
  ``tests/integration/test_trust_root_documents.py`` and
  ``tests/e2e/test_trust_root_e2e.py`` seed the ROW with ``SigningKeyFactory``,
  never through production's mint. The fetch/kid helpers are IMPORTED from the A3
  suite rather than re-declared, because a second copy of ``_get_document`` is the
  duplication class the DRY invariant blocks PRs for.

* **The tenant is ROUTABLE, asserted before the act.** A tenant with no
  ``virtual_host``/``subdomain`` serves no JWKS at all, so the acceptance chain
  would fail (or, after a partial implementation, pass) for reasons that have
  nothing to do with provisioning. Every test that grades publication first reads
  the JWKS and asserts it answers 200 with an EMPTY key set -- which pins both
  that the host routes and that the tenant starts keyless, making the later
  appearance of the minted kid a real observed change.

* **Round trip through the full stack.** Provision (HTTP) -> read back (row) ->
  decrypt (production's resolver, using the deployment KEK) -> sign -> verify
  against the JWK the JWKS ENDPOINT served. A repository-level assertion that the
  row exists proves a sub-claim only; the acceptance's locus is what a
  counterparty can fetch and check.

* **No filesystem, asserted positively.** The design's storage decision is that
  the application writes key material to NO filesystem: the private PEM lives in
  the row, encrypted, as PKCS#8 ciphertext exactly as
  ``generate_signing_keypair(passphrase=...)`` returns it. "We stopped calling the
  writer" is not observable; a before/after snapshot of a sandboxed cwd + tempdir
  is.

* **Both gates refuse BEFORE key material exists.** Minting with no KEK
  configured would silently degrade "encrypted PEM in Postgres" into "private
  keys in the database"; minting a scheme this deployment forbids
  (``SigningSettings.allowed_key_ref_schemes``, enforced at READ time only today)
  persists and PUBLISHES a key the resolver will later refuse to load. Both must
  leave no row -- and, through the admin surface, must be a flash error and never
  a 5xx. A refusal carries no sentence of its own -- ``CODE_TABLE`` owns the
  buyer-facing text for ``CONFIGURATION_ERROR`` -- so the operator-actionable knob
  name travels in ``ConfigurationDetails.tracked_by`` and every assertion below
  reads the typed details (or the operator log) rather than ``str(exc)``.

* **The keyless wire, pinned but unchanged (acceptance 3).** ``TestKeylessWire``
  is EXPECTED TO PASS TODAY. It is not a red test: it guards the posture
  production already emits (``capabilities.py`` ``_build_signing_blocks``, whose
  values come from ``posture.py``'s ``agent_level_posture`` /
  ``webhook_signing_posture`` / ``emitted_identity``) so that D1
  (``salesagent-z6nr.20``) cannot switch a keyless tenant to silence without a
  test turning red. Silence is schema-legal and is exactly the dishonest
  declaration the epic's STRICT policy exists to prevent.

* **Revoke through the ROUTE, not through the repository.** ``revoke()`` and
  ``clear_signing_provider_cache()`` were both dark before this ticket, and the
  scan named the second one specifically: it is documented "for rotation tooling"
  and was called by nobody. Retirement is therefore graded end to end through the
  admin surface — the retired key stops signing the moment the route returns, and
  the published document carries its revocation marker for the grace window and
  then drops it. Selector-level grace arithmetic already lives in
  ``test_trust_root_documents.py`` and is not repeated here; what is graded here
  is the ROUTE causing it.

* **The dev KEK ``docker compose`` supplies is all provisioning needs.**
  ``db:`` minting refuses without a KEK, so a compose file that names no
  passphrase variable gives an operator a signing-keys page that can only fail.
  The two settings are read OUT of ``docker-compose.yml`` and then used as the
  only signing configuration in the process, so removing or renaming them there
  fails here rather than in someone's dev stack.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import tempfile
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml
from adcp.signing import alg_for_jwk, public_key_from_jwk, verify_signature

from src.core.database.models import SigningKey
from src.core.errors.details import ConfigurationDetails
from src.core.exceptions import AdCPConfigurationError
from src.core.signing.provider import _provider_cache
from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import (
    REQUEST_SIGNING,
    SIGNATURE_BASE,
    just_after_provisioning,
    resolve_provider,
    signing_key_repo,
)
from tests.helpers.signing import deployment_kek as _configure_deployment_kek

# Imported, never re-declared: one home for the Host-scoped document fetch and the
# kid projection. They live in tests/helpers/signing.py rather than in the A3 suite
# that first needed them, because a module whose job is to BE a test must not also
# be a helper library (tests/unit/test_architecture_no_cross_test_module_imports.py).
from tests.helpers.signing import get_trust_root_document as _get_document
from tests.helpers.signing import published_kids as _kids

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_JWKS_PATH = "/.well-known/jwks.json"
_CAPABILITIES_PATH = "/api/v1/capabilities"

#: The provisioning route. Shaped on the accounts blueprint -- the only UoW-only
#: admin blueprint and the pattern design step 5 names
#: (``url_prefix="/tenant/<tenant_id>/signing-keys"`` + a ``/create`` action).
_PROVISION_URL = "/tenant/{tenant_id}/signing-keys/create"

#: Retirement, on the same blueprint. Rotation is provision-new + revoke-old; a
#: window close cannot retire a key, because the current key is open-ended.
_REVOKE_URL = "/tenant/{tenant_id}/signing-keys/{kid}/revoke"

#: The compose service that runs the admin UI and the FastAPI app, and therefore
#: the one whose environment decides whether `docker compose up` can provision.
_COMPOSE_SERVICE = "adcp-server"

#: The SETTING that names the KEK variable. Pinned as a literal because it is
#: ``SigningSettings.key_passphrase_env``'s env name and so is not compose's to
#: rename; the variable it POINTS AT is read out of the file instead, so a
#: deliberate consistent rename stays green while a broken pointer goes red.
_KEK_POINTER_SETTING = "ADCP_SIGNING_KEY_PASSPHRASE_ENV"

#: The scheme gate. ``db:`` is in the default, but several tests below narrow or
#: widen it deliberately, so the ones that need the default spelled out say so.
_SCHEMES_SETTING = "ADCP_SIGNING_ALLOWED_KEY_REF_SCHEMES"

#: What ``generate_signing_keypair(passphrase=...)`` returns, verbatim. The
#: ciphertext to store IS the PEM -- no envelope format, no encryption code.
_ENCRYPTED_PEM_HEADER = b"-----BEGIN ENCRYPTED PRIVATE KEY-----"

#: The logger the signing resolver splits its two audiences across: the typed
#: error carries only wire-safe tokens, the OPERATOR diagnostic (which key, why)
#: goes here (``src/core/signing/provider.py`` ``_refuse``).
_RESOLVER_LOGGER = "src.core.signing.provider"


def _drop_cached_settings(monkeypatch) -> None:
    """Make the next ``get_settings()`` re-read the environment.

    ``Settings`` is composed once and cached in ``src.core.config._settings``, so a
    test that changes ``ADCP_SIGNING_*`` after the first read sees nothing until the
    cached object is dropped. One spelling of that, because the wrong one is silent:
    this suite arrived patching a ``_config`` global that #1721 does not have, which
    ``raising=False`` turned into a no-op rather than an error.
    """
    monkeypatch.setattr("src.core.config._settings", None)


class Seeded(NamedTuple):
    """A routable tenant plus the two clients its documents are read through."""

    env: BareIntegrationEnv
    tenant: Any
    client: Any  # starlette TestClient over the real FastAPI app


@pytest.fixture
def signing_tenant(integration_db, request) -> Any:
    """One tenant per test, REACHABLE AT ITS OWN VIRTUAL HOST, with no signing key.

    The routable host is load-bearing, not decoration: ``/.well-known/jwks.json``
    resolves its tenant from the ``Host`` header
    (``well_known.py`` -> ``route_landing_page``), so a tenant without a
    ``virtual_host``/``subdomain`` has no host serving its JWKS and every
    publication assertion below would grade nothing.

    A distinct slug per test because the integration database is not rolled back
    between tests in this suite: two live tenants sharing a ``virtual_host`` make
    host resolution pick an arbitrary one.
    """
    from tests.factories import TenantFactory

    slug = re.sub(r"[^a-z0-9]", "", request.node.name.lower())[-20:]
    tenant_id = f"skprov_{slug}"

    with BareIntegrationEnv(tenant_id=tenant_id) as env:
        tenant = TenantFactory(
            tenant_id=tenant_id,
            subdomain=f"seller-{slug}",
            virtual_host=f"seller-{slug}.example.com",
        )
        # A principal, so the REST identity used by the capabilities read resolves
        # to THIS tenant rather than to the no-tenant minimal response.
        env.setup_default_data()
        env.get_session()
        yield Seeded(env=env, tenant=tenant, client=env.get_rest_client())


@pytest.fixture
def deployment_kek(monkeypatch) -> Iterator[None]:
    """Configure the one deployment-wide KEK and allow the ``db:`` scheme.

    The KEK half is DELEGATED to ``tests.helpers.signing.deployment_kek`` rather
    than re-spelled: every suite that provisions through production needs the same
    pointer-plus-variable pair, and a second copy is how one of them ends up
    naming a passphrase variable nothing sets. Only the scheme gate is local,
    because tests below narrow it on purpose.

    ``key_passphrase`` is resolved from the environment on EVERY use (deliberately
    uncached in production), but the ``Settings`` object that carries
    ``key_passphrase_env`` is a process global, so the cache is dropped here.
    """
    with _configure_deployment_kek(monkeypatch):
        monkeypatch.setenv(_SCHEMES_SETTING, "db,env,file")
        _drop_cached_settings(monkeypatch)
        yield


# ---------------------------------------------------------------------------
# The two provisioning transports, and the read-backs
# ---------------------------------------------------------------------------


def _provision_via_admin_route(client, tenant_id: str, **form: str):
    """Provision through the ADMIN ROUTE with the Flask test client.

    Returns the (redirect-followed) response so refusal tests can read the flash.
    A refusal is a flash error on a 200 page -- ``AdCPConfigurationError`` must
    never surface as a 500.
    """
    url = _PROVISION_URL.format(tenant_id=tenant_id)
    response = client.post(url, data={"alg": "ed25519", **form}, follow_redirects=True)

    assert response.status_code == 200, (
        f"POST {url} must be a real admin provisioning route answering 200 after any redirect "
        f"-- on success AND on refusal (a flash error, never a 5xx); got {response.status_code}. "
        f"Body: {response.get_data(as_text=True)[:300]!r}"
    )
    return response


def _provision_via_ops_script(tenant_id: str, *argv: str) -> None:
    """Provision through ``scripts/ops/provision_signing_key.py``.

    A SECOND THIN TRANSPORT over the same provisioning function the route calls,
    never a second implementation. Existence is asserted rather than imported at
    module scope so a missing script reports itself as a missing production path
    instead of a collection error.
    """
    assert importlib.util.find_spec("scripts.ops.provision_signing_key") is not None, (
        "scripts/ops/provision_signing_key.py must exist: the scripted transport over the SAME "
        "provisioning function the admin route calls (design step 6). It is not importable."
    )
    from scripts.ops.provision_signing_key import main

    exit_code = main(["--tenant-id", tenant_id, *argv])

    assert exit_code == 0, f"the ops script must exit 0 after provisioning tenant {tenant_id!r}; got {exit_code!r}"


def _revoke_via_admin_route(client, tenant_id: str, kid: str):
    """Retire *kid* through the ADMIN ROUTE.

    The route, never ``repo.revoke(...)`` directly: the transport is what has to
    stamp ``revoked_at`` AND drop the cached provider, and a test that calls the
    repository grades neither.
    """
    url = _REVOKE_URL.format(tenant_id=tenant_id, kid=kid)
    response = client.post(url, follow_redirects=True)

    assert response.status_code == 200, (
        f"POST {url} must be a real admin revoke route answering 200 after any redirect; got "
        f"{response.status_code}. Body: {response.get_data(as_text=True)[:300]!r}"
    )
    return response


def _rows(env, tenant_id: str) -> list:
    """Every ``signing_keys`` row for *tenant_id*, read fresh from Postgres."""
    return env.query(SigningKey, tenant_id=tenant_id)


def _sole_kid(env, tenant_id: str) -> str:
    """The kid of the single row provisioning persisted for *tenant_id*."""
    rows = _rows(env, tenant_id)
    assert len(rows) == 1, (
        f"provisioning must persist exactly one signing_keys row for {tenant_id!r}; got {[row.kid for row in rows]}"
    )
    return rows[0].kid


def _served_jwk(client, tenant, kid: str) -> dict[str, Any]:
    """The JWK the JWKS ENDPOINT publishes for *kid* -- the counterparty's view."""
    document = _get_document(client, _JWKS_PATH, tenant)
    matches = [key for key in document["keys"] if key["kid"] == kid]
    assert len(matches) == 1, (
        f"the JWKS served at {tenant.virtual_host} must publish exactly one entry for the minted "
        f"kid {kid!r}; got {sorted(_kids(document['keys']))}"
    )
    return matches[0]


def _assert_starts_keyless(client, tenant) -> None:
    """Non-vacuity control: the host ROUTES, and the tenant has no key yet.

    Without this, "the kid is in keys[]" could be satisfied by a document that was
    already there, and a tenant whose host does not resolve would fail every
    assertion below for the wrong reason.
    """
    document = _get_document(client, _JWKS_PATH, tenant)
    assert _kids(document["keys"]) == set(), (
        f"this tenant must start with NO published key for the chain below to mean anything; "
        f"got {sorted(_kids(document['keys']))}"
    )


def _tree(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*")}


class TestProvisionedKeyIsPublishedByTheTrustRoot:
    """Acceptance 1 + 2, as one chain, once per production transport."""

    def test_admin_route_provisioning_publishes_the_minted_kid(
        self, signing_tenant, deployment_kek, authenticated_admin_client
    ):
        """Provision through the admin route; the minted kid appears in the JWKS.

        This is the link nothing in the repo makes today: ``provision_signing_key``
        has zero callers outside tests, so the publication path has only ever been
        graded against factory-seeded rows.
        """
        env, tenant, client = signing_tenant
        _assert_starts_keyless(client, tenant)

        _provision_via_admin_route(authenticated_admin_client, tenant.tenant_id)

        kid = _sole_kid(env, tenant.tenant_id)
        published = _get_document(client, _JWKS_PATH, tenant)

        assert kid in _kids(published["keys"]), (
            f"the key minted through the admin route (kid {kid!r}) must be served by the trust "
            f"root at {tenant.virtual_host} the moment it exists; got {sorted(_kids(published['keys']))}"
        )

    def test_ops_script_provisioning_publishes_the_minted_kid(self, signing_tenant, deployment_kek):
        """Same chain through the scripted path -- one function, two transports."""
        env, tenant, client = signing_tenant
        _assert_starts_keyless(client, tenant)

        _provision_via_ops_script(tenant.tenant_id)

        kid = _sole_kid(env, tenant.tenant_id)
        published = _get_document(client, _JWKS_PATH, tenant)

        assert kid in _kids(published["keys"]), (
            f"the key minted through scripts/ops/provision_signing_key.py (kid {kid!r}) must be "
            f"served by the trust root; got {sorted(_kids(published['keys']))}"
        )


class TestFullRoundTrip:
    """Provision -> read back -> decrypt -> sign -> verify against what is SERVED."""

    def test_provisioned_key_signs_and_verifies_against_the_published_jwk(
        self, signing_tenant, deployment_kek, authenticated_admin_client
    ):
        """The acceptance's real locus: a counterparty fetches the JWKS and checks
        a signature this agent produced.

        Every leg traverses the layer it claims to -- the write goes over HTTP
        through the admin route, the public half is read over HTTP from the JWKS
        endpoint, and the private half is loaded by PRODUCTION's resolver (row ->
        ``db:`` ref -> ciphertext -> KEK -> tripwire -> provider), never by the
        test. The stored ciphertext is asserted to be an ENCRYPTED PKCS#8 PEM, so
        "it decrypted" is a real step and not a plaintext read.
        """
        env, tenant, client = signing_tenant
        _assert_starts_keyless(client, tenant)

        _provision_via_admin_route(authenticated_admin_client, tenant.tenant_id)
        kid = _sole_kid(env, tenant.tenant_id)
        row = env.get_one(SigningKey, tenant_id=tenant.tenant_id, kid=kid)

        ciphertext = getattr(row, "private_key_pem_encrypted", None)
        assert ciphertext is not None, (
            "signing_keys must carry private_key_pem_encrypted -- the PKCS#8 ciphertext "
            "generate_signing_keypair(passphrase=...) returns. The application writes key "
            "material to no filesystem, so the row is the only place it can live."
        )
        assert bytes(ciphertext).startswith(_ENCRYPTED_PEM_HEADER), (
            "the stored bytes must be the ENCRYPTED PEM verbatim (no envelope format, no "
            f"hand-rolled encryption); got {bytes(ciphertext)[:40]!r}"
        )
        assert row.private_key_ref == f"db:{kid}", (
            "a db-scheme row is self-describing: the locator is the row's own kid, which is what "
            f"makes a ref copied between rows detectable; got {row.private_key_ref!r}"
        )

        served = _served_jwk(client, tenant, kid)

        provider = resolve_provider(
            signing_key_repo(env, tenant.tenant_id),
            tenant.tenant_id,
            now=just_after_provisioning(),
            kid=kid,
        )
        signature = asyncio.run(provider.sign(SIGNATURE_BASE))

        assert provider.key_id() == kid, (
            f"the resolved provider must sign under the published kid; got {provider.key_id()!r}"
        )
        assert verify_signature(
            alg=alg_for_jwk(served),
            public_key=public_key_from_jwk(served),
            signature_base=SIGNATURE_BASE,
            signature=signature,
        ), (
            "a signature made with the provisioned private key must verify against the JWK the "
            "JWKS ENDPOINT publishes -- otherwise we publish a key we cannot sign with"
        )


class TestNoKeyMaterialTouchesAFilesystem:
    """The storage decision, asserted positively rather than by omission."""

    def test_neither_provisioning_path_creates_a_file(
        self, signing_tenant, deployment_kek, authenticated_admin_client, monkeypatch, tmp_path
    ):
        """No file appears anywhere a provisioning path could plausibly write.

        The sandbox captures the three destinations available to code that has no
        configured key directory: a relative path (cwd), the process temp dir, and
        anything derived from either. A snapshot equality is the only assertion
        that survives an implementation that writes somewhere nobody thought of --
        "we deleted the writer" is not a behavior.

        THE ADMIN AUDIT SINK IS CONFIGURED OUT, AND THEN READ BACK. #1721 fixed
        ``log_admin_action`` to resolve ``tenant_id`` from ``request.view_args``, so
        this route audits where it previously (and silently) did not -- correctly,
        because it is the endpoint that mints a credential. Its secondary file sink
        resolves ``audit_logger.LOG_DIR`` at WRITE time and that default is
        ``Path("logs")``, RELATIVE, so under the chdir above it lands inside the
        sandbox and is indistinguishable from a key write.

        Giving it a configured home outside the sandbox is the only move that keeps
        the assertion at full strength: what the sandbox captures is still exactly
        the set of destinations reachable by code with NO configured directory,
        which is the class of write this test exists to forbid. The tempting
        alternative -- absolutising ``adcp_log_dir`` -- is rejected: it turns this
        green while hiding every audit write from the test forever. The relocated
        file is graded below instead, on both halves: the record must BE there, and
        it must not be where the key leaks.
        """
        env, tenant, client = signing_tenant
        sandbox = tmp_path / "fs"
        sandbox.mkdir()

        audit_dir = tmp_path / "audit"
        monkeypatch.setattr("src.core.audit_logger.LOG_DIR", audit_dir)

        monkeypatch.chdir(sandbox)
        monkeypatch.setattr(tempfile, "tempdir", str(sandbox))

        before = _tree(sandbox)

        _provision_via_admin_route(authenticated_admin_client, tenant.tenant_id)
        _provision_via_ops_script(tenant.tenant_id)

        assert _tree(sandbox) == before, (
            "provisioning must create NO file: the private PEM lives encrypted in the "
            f"signing_keys row. New paths: {sorted(str(p) for p in _tree(sandbox) - before)}"
        )

        rows = _rows(env, tenant.tenant_id)
        assert len(rows) == 2, f"both transports must have persisted a row; got {[row.kid for row in rows]}"
        assert all(row.private_key_ref == f"db:{row.kid}" for row in rows), (
            "every minted row references its own encrypted material in the database; got "
            f"{[row.private_key_ref for row in rows]}"
        )

        self._assert_the_route_audited_without_leaking_the_key(audit_dir, tenant.tenant_id, rows)

    @staticmethod
    def _assert_the_route_audited_without_leaking_the_key(audit_dir: Path, tenant_id: str, rows: list) -> None:
        """The relocated audit sink is graded, not merely moved out of the way.

        Two obligations, and the second is why this belongs in THIS class rather
        than in an auditing test: a route that mints a credential must leave an
        audit record, and that record must not become the filesystem the key
        material reaches. Moving the sink without reading it would spend the
        obligation the snapshot assertion used to carry.
        """
        structured = audit_dir / "structured.jsonl"
        assert structured.exists(), (
            "provisioning through the admin route must leave an audit record: this is the "
            "endpoint that mints a signing credential, and log_admin_action resolves its "
            f"tenant from request.view_args so the write happens whatever the decorator order. "
            f"Nothing was written to {structured}"
        )

        entries = [json.loads(line) for line in structured.read_text().splitlines() if line.strip()]
        provisioning = [
            entry for entry in entries if entry.get("operation") == "provision_signing_key" and entry.get("success")
        ]
        assert len(provisioning) == 1, (
            "exactly one successful provision_signing_key record, for the one admin-route mint "
            f"(the ops script is not an admin route and audits nothing); got "
            f"{[(entry.get('operation'), entry.get('success')) for entry in entries]}"
        )
        assert provisioning[0].get("tenant_id") == tenant_id, (
            "the record must name the tenant whose credential was minted -- the field #1721 "
            f"repaired; got {provisioning[0].get('tenant_id')!r}"
        )

        # The sink this test just sanctioned must not be the leak. Graded against the
        # material that actually exists rather than against a guessed marker: the stored
        # ciphertext and the PEM header are both disqualifying, and so is the plaintext
        # PKCS#8 banner an unencrypted regression would write.
        audited_bytes = structured.read_bytes()
        forbidden = [_ENCRYPTED_PEM_HEADER, b"-----BEGIN PRIVATE KEY-----"] + [
            bytes(row.private_key_pem_encrypted) for row in rows
        ]
        leaked = [needle for needle in forbidden if needle in audited_bytes]
        assert not leaked, (
            "the audit sink must never carry key material -- it is a FILE, and this class's "
            "whole subject is that no key material reaches one. The audit record names the "
            f"operation and the tenant, never the secret; leaked {[needle[:40] for needle in leaked]}"
        )


class TestMintingRefusesWithoutAKek:
    """No KEK, no key. There is no plaintext fallback."""

    def test_no_row_and_no_ciphertext_when_no_passphrase_env_is_configured(
        self, signing_tenant, authenticated_admin_client, monkeypatch
    ):
        """A deployment that configured no KEK must not mint at all.

        The fallback this forbids -- minting an UNencrypted PEM into the row --
        would turn "encrypted PEM in Postgres" into "private keys in the database"
        with no signal. The page must name ``key_passphrase_env``, because that is
        the exact knob the operator has to set -- and naming it is now a two-file
        obligation rather than a formatted sentence: ``CODE_TABLE`` owns
        ``str(exc)`` for ``CONFIGURATION_ERROR`` and names no knob, so
        ``provision_signing_key`` puts the knob in
        ``ConfigurationDetails.tracked_by`` and the blueprint flashes that field.
        Asserting on the RENDERED PAGE is what keeps both halves honest: dropping
        either one turns this red.
        """
        env, tenant, client = signing_tenant
        monkeypatch.delenv(_KEK_POINTER_SETTING, raising=False)
        monkeypatch.setenv(_SCHEMES_SETTING, "db,env,file")
        _drop_cached_settings(monkeypatch)

        response = _provision_via_admin_route(authenticated_admin_client, tenant.tenant_id)

        body = response.get_data(as_text=True)
        assert "key_passphrase_env" in body, (
            "the refusal page must name key_passphrase_env -- the setting the operator must "
            "configure. CODE_TABLE's generic sentence names no knob, so this only holds if the "
            "refusal carries it in ConfigurationDetails.tracked_by AND the blueprint renders that "
            f"field; the page said: {body[:400]!r}"
        )
        assert _rows(env, tenant.tenant_id) == [], (
            "a refused mint must leave NO row (and therefore no ciphertext); got "
            f"{[row.kid for row in _rows(env, tenant.tenant_id)]}"
        )
        assert _kids(_get_document(client, _JWKS_PATH, tenant)["keys"]) == set(), (
            "and nothing may be published for a mint that never happened"
        )


class TestForbiddenRefSchemeRefusesBeforeAnyKeyMaterialExists:
    """The deployment's allowed-scheme gate must run at MINT time, not only at read."""

    def test_no_row_when_the_deployment_forbids_the_requested_scheme(
        self, signing_tenant, deployment_kek, authenticated_admin_client, monkeypatch
    ):
        """A scheme this deployment will not resolve must not reach the database.

        ``_resolve_key_ref`` enforces ``allowed_key_ref_schemes`` at READ time
        only, so today a row can be persisted AND PUBLISHED whose private half the
        resolver will refuse to load -- a published key with no signable private
        half, detected only when signatures start being rejected. The gate belongs
        before ``generate_signing_keypair``, so the failure leaves no row and
        nothing published.
        """
        env, tenant, client = signing_tenant
        monkeypatch.setenv(_SCHEMES_SETTING, "env")
        _drop_cached_settings(monkeypatch)

        _provision_via_admin_route(authenticated_admin_client, tenant.tenant_id, ref_scheme="db")

        assert _rows(env, tenant.tenant_id) == [], (
            "minting a ref scheme the deployment forbids must refuse BEFORE any key material "
            f"exists -- no row; got {[row.private_key_ref for row in _rows(env, tenant.tenant_id)]}"
        )
        assert _kids(_get_document(client, _JWKS_PATH, tenant)["keys"]) == set(), (
            "and nothing may be published: publication must never outrun resolvability"
        )


class TestKeylessWire:
    """Acceptance 3 -- a pin on what a KEYLESS tenant's capabilities wire says.

    The v3.1.1 schema permits omitting the signing blocks entirely, so what this pins is
    that neither is ever omitted: an explicit ``false`` may become a different VALUE, but
    it may never become SILENCE. A receiver cannot tell "this seller does not sign" from
    "this seller did not say", which is the dishonest declaration the epic's STRICT policy
    exists to prevent.

    #1291 D1 changed one of the three values, deliberately and in the direction the pin
    allows: ``request_signing.supported`` follows ``SigningSettings.verifier_enabled``
    because the pin defines the field as whether this agent VERIFIES signatures on
    INCOMING requests -- using the COUNTERPARTY's keys, so it was never key-backed. The
    other two stand: no key means we do not SIGN, and nothing obliges a trust root.

    The ``identity`` assertion is graded rather than vacuous only now. While ``identity``
    sat in ``capability_declarations._UNBACKED_BLOCKS`` the block was SUPPRESSED for every
    tenant, so its absence here said nothing about a keyless one. This stage publishes the
    trust root and takes ``identity`` out of that table, so the ``None`` below is
    ``emitted_identity`` reporting that ``requires_trust_root`` does not fire -- an
    observed derivation, not a switch held off.
    """

    def test_keyless_tenant_verifies_but_neither_signs_nor_publishes(self, signing_tenant):
        from src.core.config import get_settings

        env, tenant, client = signing_tenant
        assert _rows(env, tenant.tenant_id) == [], "this tenant must have no signing key"

        # POST, and with the tenant's own Host. ``get_adcp_capabilities`` has exactly one
        # REST shape now -- ``RestBinding("POST", "/capabilities")`` in
        # ``src/core/tools/registry.py``; the parameterless GET was a second shape for the
        # same tool, took no body, and was deleted with the registry-derived routes. The
        # Host header is the SAME tenant-resolution path every other fetch in this module
        # uses (``_get_document``): ``_detect_tenant`` reads ``virtual_host`` first, and
        # without it the TestClient's default ``testserver`` names no tenant and the answer
        # is the no-tenant minimal document the assertion below rules out.
        response = client.post(_CAPABILITIES_PATH, json={}, headers={"Host": tenant.virtual_host})

        assert response.status_code == 200, (
            f"POST {_CAPABILITIES_PATH} with Host {tenant.virtual_host!r} must answer 200; "
            f"got {response.status_code} {response.text[:200]!r}"
        )
        data = response.json()

        # Non-vacuity: the no-tenant minimal response declares the same two
        # postures, so without this the assertions below would hold for a response
        # that never resolved a tenant at all. media_buy is built only on the
        # tenant-resolved path.
        assert data.get("media_buy") is not None, (
            "this must be the TENANT-RESOLVED response, not the no-tenant minimal one"
        )

        verifier_enabled = get_settings().signing.verifier_enabled
        assert data["request_signing"]["supported"] is verifier_enabled, (
            "request_signing is NOT key-backed -- it declares that this agent verifies signatures "
            f"on incoming requests with the counterparty's keys -- so a keyless tenant still "
            f"declares SigningSettings.verifier_enabled ({verifier_enabled}), explicitly and never "
            f"as silence; got {data.get('request_signing')!r}"
        )
        assert data["webhook_signing"]["supported"] is False, (
            "...and webhook_signing.supported false explicitly, because it IS key-backed and this "
            "tenant holds no key; got {}".format(data.get("webhook_signing"))
        )
        assert data.get("identity") is None, (
            "no identity block for a tenant with no keys: identity.key_origins has nothing to "
            f"anchor the verifier's consistency check against, and with every request_signing "
            f"bucket empty no required_when trigger fires; got {data.get('identity')!r}"
        )


class TestRevokeThroughTheRoute:
    """Retirement, end to end through the admin surface.

    Before this ticket ``revoke()`` and ``clear_signing_provider_cache()`` were
    both reachable only from tests -- and the second is the one the scan singled
    out, because the resolved provider is cached for
    ``provider._CACHE_TTL_SECONDS`` under ``(tenant_id, kid)`` and a revoke
    transport that forgets to drop it leaves a live signer for a retired key in
    process memory.
    """

    def test_a_revoked_key_stops_signing_the_moment_the_route_returns(
        self, signing_tenant, deployment_kek, authenticated_admin_client, caplog
    ):
        """No sleep, no cache priming, no repository call: just the route.

        The provider is resolved and USED before the revoke, which does two
        things at once -- it proves the key really was signing (so the refusal
        afterwards is an observed change and not the state it started in) and it
        populates the provider cache, which is the thing the revoke transport has
        to invalidate.

        WHY THE REFUSALS ARE GRADED ON TWO SURFACES. ``AdCPConfigurationError``
        carries no message of its own: ``str(exc)`` is ``CODE_TABLE``'s single
        ``CONFIGURATION_ERROR`` sentence, identical for every refusal in
        ``provider.py``, so the substring checks this test arrived with ("revoked",
        "no active") could no longer distinguish anything. ``provider._refuse``
        splits that deliberately -- wire-safe tokens into typed ``details``, the
        operator diagnostic into the log -- and this asserts BOTH halves, which is
        what still separates "refused as revoked" from "no such kid" (their typed
        details are identical by design) and from a resolver that refused for a KEK
        or round-trip reason.
        """
        env, tenant, client = signing_tenant
        _assert_starts_keyless(client, tenant)

        _provision_via_admin_route(authenticated_admin_client, tenant.tenant_id)
        kid = _sole_kid(env, tenant.tenant_id)
        repo = signing_key_repo(env, tenant.tenant_id)

        provider = resolve_provider(repo, tenant.tenant_id, now=just_after_provisioning(), kid=kid)
        served = _served_jwk(client, tenant, kid)
        assert verify_signature(
            alg=alg_for_jwk(served),
            public_key=public_key_from_jwk(served),
            signature_base=SIGNATURE_BASE,
            signature=asyncio.run(provider.sign(SIGNATURE_BASE)),
        ), "the key must be signing BEFORE the revoke, or the refusal below grades nothing"

        # Non-vacuity for the cache assertion: the entry has to be there to be gone.
        assert (tenant.tenant_id, kid) in _provider_cache, (
            "resolving a provider must cache it -- otherwise the post-revoke cache "
            "assertion below is true no matter what the revoke transport does"
        )

        _revoke_via_admin_route(authenticated_admin_client, tenant.tenant_id, kid)

        # Immediately -- the assertion is that no time has to pass.
        caplog.clear()
        with (
            caplog.at_level(logging.ERROR, logger=_RESOLVER_LOGGER),
            pytest.raises(AdCPConfigurationError) as designated,
        ):
            resolve_provider(repo, tenant.tenant_id, now=just_after_provisioning(), kid=kid)

        assert designated.value.details == ConfigurationDetails(
            tenant_id=tenant.tenant_id, capability="signing_key", rejected_value=kid
        ), (
            "the refusal a buyer can see names the tenant and the kid it would not sign with, and "
            f"nothing else -- no revoked_at, no locator, no OpenSSL text; got {designated.value.details!r}"
        )
        assert "revoked" in caplog.text, (
            "a key retired through the route must be refused AS REVOKED, not for some other reason. "
            "That fact is operator-facing, so provider._refuse logs it rather than putting it on the "
            f"wire; the resolver logged: {caplog.text!r}"
        )

        caplog.clear()
        with caplog.at_level(logging.ERROR, logger=_RESOLVER_LOGGER), pytest.raises(AdCPConfigurationError) as active:
            resolve_provider(repo, tenant.tenant_id, now=just_after_provisioning())

        assert active.value.details == ConfigurationDetails(tenant_id=tenant.tenant_id, capability=REQUEST_SIGNING), (
            "and the tenant must have no active signing key left at all -- a refusal carrying the "
            "PURPOSE rather than a kid, which is the one fact a reader of the envelope can act on; "
            f"got {active.value.details!r}"
        )
        assert "no active" in caplog.text, (
            f"and the operator must be told which surface went dark; the resolver logged: {caplog.text!r}"
        )

        # White-box on PROCESS STATE, deliberately, and stated as such: the
        # resolver refuses a revoked row before it ever consults the cache, so
        # there is no black-box symptom that distinguishes a revoke which drops
        # the cached provider from one that does not. The cache bust is still
        # required -- it is what stops a live signer for a retired key surviving
        # in memory for the TTL, which is exactly what C1's outbound sender will
        # hold. This asserts the EFFECT (no cached provider), not the call, so an
        # equivalent implementation keeps it green.
        assert (tenant.tenant_id, kid) not in _provider_cache, (
            "the revoke transport must drop the cached provider for the retired key "
            "(clear_signing_provider_cache); a live signer for a revoked kid must not "
            "outlive the request that retired it"
        )

    def test_the_revoked_key_carries_its_marker_then_leaves_the_published_document(
        self, signing_tenant, deployment_kek, authenticated_admin_client
    ):
        """Publication after a ROUTE revoke: marker inside the window, gone after it.

        The window is elapsed by moving the CLOCK, not by shrinking the window:
        ``ADCP_SIGNING_GRACE_SECONDS`` is validated to exceed the published
        ``Cache-Control`` max-age, so a zero grace is a configuration production
        refuses at startup -- correctly, since it would leave no margin for an
        intermediary. Sleeping the real window would sleep ten minutes. Freezing
        the clock past ``revoked_at + grace`` runs the real selector against the
        real configured window, which is the thing worth grading.

        The selector arithmetic itself is already graded in
        ``test_trust_root_documents.py``; what is new here is that the ADMIN ROUTE
        is what puts the key into that state.
        """
        from freezegun import freeze_time

        from src.core.config import get_settings

        env, tenant, client = signing_tenant
        _assert_starts_keyless(client, tenant)

        _provision_via_admin_route(authenticated_admin_client, tenant.tenant_id)
        kid = _sole_kid(env, tenant.tenant_id)

        assert "revoked_at" not in _served_jwk(client, tenant, kid), (
            "a live key must not be published with a revocation marker"
        )

        _revoke_via_admin_route(authenticated_admin_client, tenant.tenant_id, kid)

        # commit() expires the identity map, so the row is re-read and the write
        # the route made in ITS session is what the assertion sees.
        env.get_session()
        row = env.get_one(SigningKey, tenant_id=tenant.tenant_id, kid=kid)
        assert row.revoked_at is not None, "the route must stamp revoked_at through repo.revoke()"

        published = _served_jwk(client, tenant, kid)
        assert published.get("revoked_at") == row.revoked_at.isoformat(), (
            "inside the grace window the key stays published CARRYING its marker -- a cache that "
            "has not refreshed has to be able to evaluate the revocation, and an entry without "
            f"the marker is indistinguishable from a live key; got {published.get('revoked_at')!r}"
        )

        grace = timedelta(seconds=get_settings().signing.grace_seconds)
        with freeze_time(row.revoked_at + grace + timedelta(seconds=1)):
            after_grace = _get_document(client, _JWKS_PATH, tenant)

        assert _kids(after_grace["keys"]) == set(), (
            "once the grace window has elapsed the retired key leaves the published document "
            f"entirely -- the marker is a transition, not a permanent tombstone; got "
            f"{sorted(_kids(after_grace['keys']))}"
        )


class TestComposeProvisionsWithNoOperatorAction:
    """``docker compose up`` gives an operator a signing-keys page that works.

    ``db:`` minting refuses without a KEK and there is no plaintext fallback, so
    the dev stack needs a passphrase variable or its provisioning surface can only
    ever fail. That is a silent failure mode: nothing is wrong until someone tries
    to provision.
    """

    def test_the_dev_kek_docker_compose_supplies_is_all_provisioning_needs(self, signing_tenant, monkeypatch):
        """Read the compose settings, then use ONLY them, through a real transport.

        Deliberately not built on ``deployment_kek``: this test's whole subject is
        whether what the COMPOSE FILE sets is sufficient, so its configuration has
        to come out of that file and nothing else may be left set alongside it.
        """
        env, tenant, client = signing_tenant
        _assert_starts_keyless(client, tenant)

        compose = yaml.safe_load((Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text())
        service_env = compose["services"][_COMPOSE_SERVICE]["environment"]

        assert _KEK_POINTER_SETTING in service_env, (
            f"docker-compose.yml must set {_KEK_POINTER_SETTING} on the {_COMPOSE_SERVICE} service: "
            "without it `docker compose up` yields a signing-keys page whose every provision "
            "refuses for want of a key encryption key"
        )
        kek_variable = service_env[_KEK_POINTER_SETTING]
        assert service_env.get(kek_variable), (
            f"{_KEK_POINTER_SETTING} names {kek_variable!r}, but the compose file gives that variable "
            f"no value -- key_passphrase resolves to None and db: minting refuses. Renaming the KEK "
            f"variable means renaming it in both places; got {sorted(service_env)}"
        )
        assert _SCHEMES_SETTING not in service_env, (
            "compose sets no scheme override, so the dev stack depends on db: being in "
            "SigningSettings.allowed_key_ref_schemes BY DEFAULT. If compose starts overriding it, "
            "this test stops grading the default and must be rewritten rather than relaxed"
        )

        # Exactly the compose pair, and nothing this suite would otherwise leave behind.
        monkeypatch.delenv(_SCHEMES_SETTING, raising=False)
        monkeypatch.setenv(_KEK_POINTER_SETTING, kek_variable)
        monkeypatch.setenv(kek_variable, service_env[kek_variable])
        _drop_cached_settings(monkeypatch)

        _provision_via_ops_script(tenant.tenant_id)

        kid = _sole_kid(env, tenant.tenant_id)
        row = env.get_one(SigningKey, tenant_id=tenant.tenant_id, kid=kid)

        assert bytes(row.private_key_pem_encrypted).startswith(_ENCRYPTED_PEM_HEADER), (
            "the dev KEK must actually ENCRYPT the stored PEM -- a compose value that resolved to "
            "nothing would either refuse or (if the gate regressed) store plaintext"
        )
        assert kid in _kids(_get_document(client, _JWKS_PATH, tenant)["keys"]), (
            f"and the key provisioned with nothing but the compose settings must be published; got "
            f"{sorted(_kids(_get_document(client, _JWKS_PATH, tenant)['keys']))}"
        )
