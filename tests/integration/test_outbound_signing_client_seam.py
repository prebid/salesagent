"""The outbound request-signing seam: who signs, and that the wire carries it.

#1291 C3. The Core Invariant this pins:

    signing is STRICTLY ADDITIVE: a counterparty with no key, a tenant with no
    publishable origin, or a caller with no tenant in scope never breaks a call
    that works unsigned today -- but a tenant that DOES sign has its signature on
    the actual bytes that leave.

Spec grounding (pinned AdCP 3.1.1, ``docs/building/by-layer/L1/security.mdx``
:1226): a verifier MUST NOT accept a malformed/unresolvable signature as if it
were bearer-only auth. A tenant with an active signing key but NO publishable
origin (no ``https://`` host a receiver's ``identity.brand_json_url`` could ever
point at) would have every signature it emits be exactly that kind of
unresolvable signature -- worse than sending nothing. C1 (webhook signing)
already solved this for the webhook direction by folding ``origin_is_publishable``
into one posture object; the same gate exists on the outbound-REQUEST direction.

RETARGETED by #1802, which moved the seam without moving the obligation. This
file used to grade the deleted multi-agent-client factory in
``adapter_helpers``, which attached a ``SigningConfig`` to an
``ADCPMultiAgentClient`` -- an un-pinned dialer beside
the guarded egress seam, now banned outright by ``ruff-egress.toml``. Its two
halves survive as:

* :func:`src.core.helpers.adapter_helpers.request_signer_for_tenant` -- the
  posture gate, returning the signing CALLBACK or ``None``; and
* :func:`src.core.utils.mcp_client.call_mcp_tool`'s ``sign=`` parameter, which
  installs that callback as an httpx request hook so the signature is computed
  inside the guarded transport, per HTTP message and per retry, over the exact
  bytes on the wire.

Every obligation the predecessor graded is kept, and each is re-aimed at the
surviving subject:

* predecessor obligation: an active key on an unpublishable origin does not
  sign. Graded here as ``request_signer_for_tenant`` returning ``None``.
* predecessor obligation: the same key on a publishable origin yields a real
  ``SigningConfig`` carrying ``kid``/``alg``. Graded here as the returned
  callback signing with that ``kid``/``alg`` under the request-signing tag.

and three the predecessor could not grade are added, because the new seam makes
them expressible: the two remaining DECIDED-``None`` postures, the no-silent-
downgrade rule, and -- the reason the seam was moved at all -- that a real dial
through ``call_mcp_tool`` actually puts the signature on the wire.

``TestTheSeamPutsTheSignatureOnTheWire`` dials a REAL MCP counterparty (a
uvicorn/fastmcp origin over TLS on loopback, the same ``mcp_origin_tls`` fixture
``tests/integration/test_mcp_client_util.py`` uses) and reads the headers the
counterparty's socket actually saw. Nothing about the transport is faked, so a
signature that is minted but never attached -- the exact failure mode the SDK's
``ContextVar`` approach had (adcontextprotocol/adcp-client-python#1017) -- cannot
pass. The signed and unsigned legs differ in ONE thing: the tenant's origin
publishability, which is what decides whether ``request_signer_for_tenant``
hands ``call_mcp_tool`` a signer at all.

Production imports happen inside the test bodies, matching this codebase's
established convention (see ``tests/integration/test_webhook_signing_boundary.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, NamedTuple

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.egress_backoff import set_flags
from tests.helpers.signing import deployment_kek, provision_key, signing_key_repo

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_KID = "outbound-seam-key-1"

#: DOTTED, so `canonical_agent_url` derives `https://` for it (mirrors
#: `tests/integration/test_webhook_signing_boundary.py`'s `_AGENT_HOST`).
_PUBLISHABLE_HOST = "seller-outbound-signing.example.com"

#: The profile a conformant receiver checks a protocol request against. A
#: webhook-tagged signature here is one it rejects, so the tag is part of the
#: obligation, not decoration.
_REQUEST_SIGNING_TAG = "adcp/request-signing/v1"

#: A stand-in for one framed MCP message. The seam signs the transport's real
#: bytes; these are only what the posture tests hand the callback directly.
_A_REQUEST: dict[str, Any] = {
    "method": "POST",
    "url": "https://counterparty.example.com/mcp",
    "headers": {"content-type": "application/json"},
    "body": b'{"jsonrpc":"2.0","method":"tools/call"}',
}


class _Seeded(NamedTuple):
    repo: Any
    tenant: Any


@pytest.fixture
def signing_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[BareIntegrationEnv]:
    from src.core.signing.provider import clear_signing_provider_cache

    # The provider cache is keyed on (tenant_id, kid) and outlives a per-test
    # database, so a neighbouring test's material for this very tenant/kid would
    # otherwise satisfy a resolution this test expects to fail (and vice versa).
    clear_signing_provider_cache()
    with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="tenant_outbound_seam") as env:
        yield env
    clear_signing_provider_cache()


def _seed_tenant(env: BareIntegrationEnv, *, virtual_host: str | None, with_key: bool = True) -> _Seeded:
    """A tenant, optionally holding a real, active request-signing key minted through production."""
    from tests.factories import TenantFactory

    kwargs: dict[str, Any] = {"tenant_id": env.tenant_id}
    if virtual_host is not None:
        kwargs["virtual_host"] = virtual_host
    tenant = TenantFactory(**kwargs)

    repo = signing_key_repo(env, env.tenant_id)
    if with_key:
        provision_key(repo, env.tenant_id, _KID, alg="ed25519")
    # COMMITTED, not just flushed. ``provision_key`` add+flushes, which used to be enough
    # because these tests DONATED this very repository to the seam and so shared its
    # transaction. The seam now opens its own session, and an uncommitted key is invisible
    # to it: the publishable-origin test would go red and the non-publishable one would
    # keep passing for the wrong reason -- no signer because no key resolved, not because
    # the origin was unpublishable. That is the vacuous pass this file exists to rule out,
    # so the seed has to outlive the seeding transaction.
    env.get_session().commit()
    return _Seeded(repo=repo, tenant=tenant)


def _signature_input(headers: dict[str, str]) -> str:
    """The ``Signature-Input`` header, insisting the signature headers are all present.

    ``Signature-Input`` alone is not a signed request: ``Signature`` carries the
    bytes and ``Content-Digest`` is what binds them to the body
    (``cover_content_digest=True``). Asserting the trio in one place keeps every
    caller from grading a third of the obligation.
    """
    lowered = {name.lower(): value for name, value in headers.items()}
    for required in ("signature-input", "signature", "content-digest"):
        assert required in lowered, (
            f"the request carries no {required!r} header, so it is not an RFC 9421 signed "
            f"request; got {sorted(lowered)}"
        )
    return lowered["signature-input"]


def _assert_signed_by_the_seeded_key(headers: dict[str, str]) -> None:
    """The signature is THIS tenant's key, under the request-signing profile."""
    signature_input = _signature_input(headers)
    assert f'keyid="{_KID}"' in signature_input, (
        f"signed with the wrong key: expected keyid={_KID!r} in {signature_input!r}"
    )
    assert 'alg="ed25519"' in signature_input, f"signed under the wrong algorithm: {signature_input!r}"
    assert f'tag="{_REQUEST_SIGNING_TAG}"' in signature_input, (
        "signed under the wrong profile tag -- a webhook-tagged signature on a protocol "
        f"request is one a conformant verifier rejects: {signature_input!r}"
    )


class TestRequestSignerForTenantPostureGate:
    """WHICH tenants sign. ``None`` only ever for a DECIDED posture."""

    def test_no_tenant_in_scope_yields_no_signer(self, integration_db, signing_env) -> None:
        """A caller with no tenant yet (e.g. a connectivity smoke-check against an
        as-yet-unsaved agent config) dials unsigned, exactly as before the seam existed."""
        from src.core.helpers.adapter_helpers import request_signer_for_tenant

        assert request_signer_for_tenant(tenant_id=None) is None

    def test_no_active_request_signing_key_yields_no_signer(self, integration_db, signing_env) -> None:
        """A publishable origin is not enough: with no key there is nothing to sign with."""
        from src.core.helpers.adapter_helpers import request_signer_for_tenant

        seeded = _seed_tenant(signing_env, virtual_host=_PUBLISHABLE_HOST, with_key=False)

        assert request_signer_for_tenant(tenant_id=seeded.tenant.tenant_id) is None

    def test_unpublishable_origin_yields_no_signer_despite_an_active_key(self, integration_db, signing_env) -> None:
        """No ``virtual_host`` -> ``canonical_agent_url`` derives a non-https
        localhost/default origin -> ``origin_is_publishable`` is False -> the seam
        must not attempt a signed call nobody could verify."""
        from src.core.helpers.adapter_helpers import request_signer_for_tenant

        seeded = _seed_tenant(signing_env, virtual_host=None)

        assert request_signer_for_tenant(tenant_id=seeded.tenant.tenant_id) is None, (
            "request_signer_for_tenant handed back a signer for a tenant with no publishable "
            "origin -- every signature it emits would be unresolvable by any conformant "
            "receiver (security.mdx @ v3.1.1 :1226), which is worse than sending the call "
            "unsigned"
        )

    def test_publishable_origin_with_the_same_key_yields_a_signer_bound_to_that_key(
        self, integration_db, signing_env
    ) -> None:
        """Same key, same seam -- only the origin differs. Proves the ``None`` above is
        really the publishability gate and not e.g. a broken key resolution."""
        from src.core.helpers.adapter_helpers import request_signer_for_tenant

        seeded = _seed_tenant(signing_env, virtual_host=_PUBLISHABLE_HOST)

        signer = request_signer_for_tenant(tenant_id=seeded.tenant.tenant_id)

        assert signer is not None, "a tenant with an active key AND a publishable origin got no signer at all"
        _assert_signed_by_the_seeded_key(signer(**_A_REQUEST))

    def test_every_call_gets_a_fresh_nonce(self, integration_db, signing_env) -> None:
        """The callback is invoked per HTTP message and per retry, so a reused nonce
        would make a replay indistinguishable from a retry -- RFC 9421 requires a
        conformant verifier to reject a replayed nonce, which it can only do if ours
        differ."""
        from src.core.helpers.adapter_helpers import request_signer_for_tenant

        seeded = _seed_tenant(signing_env, virtual_host=_PUBLISHABLE_HOST)
        signer = request_signer_for_tenant(tenant_id=seeded.tenant.tenant_id)
        assert signer is not None

        first = _signature_input(signer(**_A_REQUEST))
        second = _signature_input(signer(**_A_REQUEST))

        assert first != second, (
            f"two signatures of the same request are byte-identical ({first!r}), so the nonce is not fresh per attempt"
        )

    def test_unbuildable_key_material_raises_instead_of_dialling_unsigned(
        self, integration_db, signing_env, monkeypatch
    ) -> None:
        """NO SILENT DOWNGRADE. Once the gate says this tenant signs, material that
        cannot be built must raise -- the predecessor swallowed this and dialled
        unsigned, so a broken KEK looked identical to a tenant that had simply never
        provisioned a key."""
        from src.core.exceptions import AdCPConfigurationError
        from src.core.helpers.adapter_helpers import request_signer_for_tenant
        from src.core.signing.provider import clear_signing_provider_cache

        seeded = _seed_tenant(signing_env, virtual_host=_PUBLISHABLE_HOST)

        # The deployment KEK the private half was wrapped under is now the wrong
        # one: the row, the posture and the origin are all still perfectly good, so
        # the gate still says this tenant signs.
        monkeypatch.setenv("SALESAGENT_TEST_SIGNING_KEK", "not-the-key-this-was-wrapped-under")
        # Drop the cached settings so nothing built before this point can keep
        # serving the old ``SigningSettings``. ``key_passphrase`` itself re-reads the
        # environment per use, so this is belt-and-braces rather than the mechanism --
        # but it is spelled WITHOUT ``raising=False``: ``_settings`` is the real module
        # global on this boundary (the predecessor named ``_config``, which no longer
        # exists), and a silently-created attribute would make this test pass for
        # having asserted nothing about the KEK at all.
        monkeypatch.setattr("src.core.config._settings", None)
        clear_signing_provider_cache()

        with pytest.raises(AdCPConfigurationError):
            request_signer_for_tenant(tenant_id=seeded.tenant.tenant_id)


@pytest.mark.asyncio
class TestTheSeamPutsTheSignatureOnTheWire:
    """WHAT the counterparty's socket receives, over a real MCP dial.

    The gate's answer is only worth anything if ``call_mcp_tool`` consumes it. The
    two legs run the identical dial and differ in exactly one input -- the tenant's
    origin publishability -- so the unsigned leg is this file's own control against
    an assertion that would pass on any request at all.
    """

    async def _dial(self, origin: Any, tenant_id: str) -> None:
        from src.core.helpers.adapter_helpers import request_signer_for_tenant
        from src.core.utils.mcp_client import call_mcp_tool

        await call_mcp_tool(
            agent_url=origin.base_url,
            tool="list_creative_formats",
            arguments={},
            timeout=10,
            max_attempts=1,
            sign=request_signer_for_tenant(tenant_id=tenant_id),
        )

    @staticmethod
    def _header_capturing_tool(seen: list[dict[str, str]]) -> Any:
        """An MCP tool that records the headers of the request that invoked it."""

        def list_creative_formats() -> dict:
            from fastmcp.server.dependencies import get_http_request

            seen.append(dict(get_http_request().headers))
            return {"formats": []}

        return list_creative_formats

    @pytest.mark.timeout(60)
    async def test_a_signing_tenants_dial_arrives_signed(
        self, integration_db, signing_env, mcp_origin_tls, monkeypatch
    ) -> None:
        """The bytes the counterparty received carry this tenant's RFC 9421 signature."""
        # The MCP origin is a loopback address, which egress policy refuses by
        # default; https is required unconditionally regardless (salesagent-e6h0),
        # and the origin serves real TLS off the shared generated leaf.
        set_flags(monkeypatch, private=True)
        seeded = _seed_tenant(signing_env, virtual_host=_PUBLISHABLE_HOST)
        seen: list[dict[str, str]] = []

        await self._dial(
            mcp_origin_tls(list_creative_formats=self._header_capturing_tool(seen)), seeded.tenant.tenant_id
        )

        assert seen, "the counterparty never served the tool, so nothing about the wire was graded"
        _assert_signed_by_the_seeded_key(seen[-1])

    @pytest.mark.timeout(60)
    async def test_a_non_signing_tenants_dial_arrives_unsigned_and_succeeds(
        self, integration_db, signing_env, mcp_origin_tls, monkeypatch
    ) -> None:
        """Strictly additive: the same dial for a tenant the gate refuses to sign for
        still completes, carrying no signature headers at all."""
        set_flags(monkeypatch, private=True)
        seeded = _seed_tenant(signing_env, virtual_host=None)
        seen: list[dict[str, str]] = []

        await self._dial(
            mcp_origin_tls(list_creative_formats=self._header_capturing_tool(seen)), seeded.tenant.tenant_id
        )

        assert seen, "the counterparty never served the tool, so nothing about the wire was graded"
        received = {name.lower() for name in seen[-1]}
        assert not received & {"signature", "signature-input"}, (
            "a tenant the posture gate refuses to sign for still emitted signature headers: "
            f"{sorted(received & {'signature', 'signature-input'})}"
        )
