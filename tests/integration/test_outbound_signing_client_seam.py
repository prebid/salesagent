"""TDD RED for the shared outbound-client seam's publishability gate.

#1291 C3 (``salesagent-z6nr.29``), design step 2 / architect review MEDIUM #1
(``salesagent-js3z.20``). The Core Invariant this pins:

    signing is STRICTLY ADDITIVE: a counterparty with no key, no publishable
    origin, or a broken get_adcp_capabilities never breaks a call that works
    unsigned today.

Spec grounding (v3.1.1:docs/building/by-layer/L1/security.mdx :1226): a
verifier MUST NOT accept a malformed/unresolvable signature as if it were
bearer-only auth. A tenant with an active signing key but NO publishable
origin (no ``https://`` host a receiver's ``identity.brand_json_url`` could
ever point at) would have every signature it emits be exactly that kind of
unresolvable signature — worse than sending nothing. C1 (webhook signing,
``src/core/signing/webhook_sender_factory.py``'s ``webhook_signing_posture``)
already solved this for the webhook direction by folding
``origin_is_publishable`` into the one posture object the sender reads. This
test pins that the SAME gate exists on the OUTBOUND-REQUEST seam this ticket
adds, ``build_adcp_multi_agent_client`` — so a tenant that holds a real,
active key but serves from an unpublishable origin (the 100% case before any
tenant configures a ``virtual_host``) gets ``signing=None``, never a signed
call nobody could ever verify.

Two cases, so the negative assertion is non-vacuous: the ONLY thing that
differs between them is origin publishability (both tenants hold an
identical, real, active signing key minted through production).

Nothing here passes until ``build_adcp_multi_agent_client`` exists in
``src/core/helpers/adapter_helpers.py`` (TDD red) -- the import happens
inside the test body, matching this codebase's established convention (see
``tests/integration/test_webhook_signing_boundary.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, NamedTuple

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import deployment_kek, provision_key, signing_key_repo

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_KID = "outbound-seam-key-1"

#: DOTTED, so `canonical_agent_url` derives `https://` for it (mirrors
#: `tests/integration/test_webhook_signing_boundary.py`'s `_AGENT_HOST`).
_PUBLISHABLE_HOST = "seller-outbound-signing.example.com"


class _Seeded(NamedTuple):
    repo: Any
    tenant: Any


@pytest.fixture
def signing_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[BareIntegrationEnv]:
    with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="tenant_outbound_seam") as env:
        yield env


def _seed_tenant_with_key(env: BareIntegrationEnv, *, virtual_host: str | None) -> _Seeded:
    """A tenant holding a real, active request-signing key, minted through production."""
    from tests.factories import TenantFactory

    kwargs: dict[str, Any] = {"tenant_id": env.tenant_id}
    if virtual_host is not None:
        kwargs["virtual_host"] = virtual_host
    tenant = TenantFactory(**kwargs)

    repo = signing_key_repo(env, env.tenant_id)
    provision_key(repo, env.tenant_id, _KID, alg="ed25519")
    # COMMITTED, not just flushed. ``provision_key`` add+flushes, which used to be enough
    # because these tests DONATED this very repository to the seam and so shared its
    # transaction. The seam now opens its own session, and an uncommitted key is invisible
    # to it: the publishable-origin test would go red and the non-publishable one would
    # keep passing for the wrong reason -- ``signing is None`` because no key resolved,
    # not because the origin was unpublishable. That is the vacuous pass this pair exists
    # to rule out, so the seed has to outlive the seeding transaction.
    env.get_session().commit()
    return _Seeded(repo=repo, tenant=tenant)


def _one_creative_agent() -> Any:
    from src.core.creative_agent_registry import CreativeAgent

    return CreativeAgent(agent_url="https://creative.example.com/mcp", name="test-creative-agent")


class TestBuildAdcpMultiAgentClientPublishabilityGate:
    """A tenant with an active key but no publishable origin gets ``signing=None``."""

    def test_unpublishable_origin_yields_signing_none_despite_an_active_key(self, integration_db, signing_env) -> None:
        """No ``virtual_host`` -> ``canonical_agent_url`` derives a non-https
        localhost/default origin -> ``origin_is_publishable`` is False -> the
        seam must not attempt a signed call nobody could verify."""
        from src.core.helpers.adapter_helpers import build_adcp_multi_agent_client

        seeded = _seed_tenant_with_key(signing_env, virtual_host=None)
        agent = _one_creative_agent()

        client = build_adcp_multi_agent_client(agents=[agent], tenant_id=seeded.tenant.tenant_id)

        sub_client = client.agents[agent.name]
        assert sub_client.signing is None, (
            "build_adcp_multi_agent_client attached a SigningConfig for a tenant with no "
            "publishable origin -- every signature it emits would be unresolvable by any "
            "conformant receiver (security.mdx @ v3.1.1 :1226), which is worse than sending "
            "the call unsigned"
        )

    def test_publishable_origin_with_the_same_key_yields_a_real_signing_config(
        self, integration_db, signing_env
    ) -> None:
        """Same key, same seam -- only the origin differs. Proves the None above
        is really the publishability gate and not e.g. a broken key resolution."""
        from adcp.signing.autosign import SigningConfig

        from src.core.helpers.adapter_helpers import build_adcp_multi_agent_client

        seeded = _seed_tenant_with_key(signing_env, virtual_host=_PUBLISHABLE_HOST)
        agent = _one_creative_agent()

        client = build_adcp_multi_agent_client(agents=[agent], tenant_id=seeded.tenant.tenant_id)

        sub_client = client.agents[agent.name]
        assert isinstance(sub_client.signing, SigningConfig), (
            "a tenant with an active key AND a publishable origin got no SigningConfig at all -- "
            f"got {sub_client.signing!r}"
        )
        assert sub_client.signing.key_id == _KID, (
            f"wired the wrong key: SigningConfig.key_id={sub_client.signing.key_id!r}, expected {_KID!r}"
        )
        assert sub_client.signing.alg == "ed25519"
