"""salesagent-dn4i — the LIVE SERVER cannot open a key minted under a different KEK.

**PROVES.** A key encrypted under one KEK, minted directly against the shared
database by THIS TEST PROCESS (simulating an out-of-band/misconfigured
minting path), is correctly declared unusable by the LIVE SERVER container,
which holds a DIFFERENT KEK (docker-compose.e2e.yml's ``ADCP_SIGNING_DEV_KEK``)
and was never told about the runner's. Fetched over real HTTPS from the live
server, ``webhook_signing.supported`` must be honestly ``false`` — never
raising at delivery time, never silently ``true``.

**DOES NOT PROVE.** That a signature exists or verifies — no signing happens
anywhere in this test. The precondition is only that a JWKS is published and
fetchable (salesagent-mp53.7), not that the server can sign.

Why this MUST be a two-process test, not an in-process one (this ticket's own
acceptance text): an in-process test simulates the second process, which is
exactly how commit 93954ea59 went green while its stated mechanism was false.
This module's whole point is to be the honest version: the runner and the
live server are genuinely different OS processes holding genuinely different
KEK values in genuinely different environments.

tests/integration/test_signing_key_kek_mismatch.py already pins the LOGIC
in-process (the predicate itself) — this module pins the two-process
PROPERTY that logic exists to serve.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.e2e._signing_e2e import ca_verified_ssl_context, drop_tenant, netloc, tls_base_url
from tests.e2e.utils import live_db_env

_SLUG = "dn4ie2e"
_TENANT_ID = "dn4i_e2e"
_CAPABILITIES_PATH = "/api/v1/capabilities"
_ALG = "ed25519"

#: The RUNNER's own KEK -- deliberately NOT docker-compose.e2e.yml's
#: ADCP_SIGNING_DEV_KEK ("dev-only-signing-kek-not-a-secret"). Holding a
#: DIFFERENT value is the entire point: the live server container was never
#: told about this one.
_RUNNER_KEK_ENV_NAME = "DN4I_RUNNER_ONLY_KEK"
_RUNNER_KEK_VALUE = "runner-side-mismatched-passphrase-the-container-never-sees"


def _build_tenant_and_mint_under_runner_kek(
    live_server: dict, monkeypatch: pytest.MonkeyPatch, *, tenant_id: str, host: str
) -> str:
    """Create the routable tenant AND mint its db: signing key in ONE
    live_db_env session (tests.e2e._signing_e2e's tenant-seam helpers each open
    their own live_db_env, and nesting two is rejected -- see
    tests.e2e.utils.live_db_env's own guard). The key is encrypted under the
    RUNNER's own (mismatched) KEK -- never through the admin route, which
    would mint it under the CONTAINER's KEK instead (the matched, unrelated
    case salesagent-mp53.7 already covers).

    Returns the minted kid.
    """
    from src.core.database.repositories.signing_key import SigningKeyRepository
    from src.core.signing.keys import provision_signing_key
    from tests.factories import TenantFactory

    monkeypatch.setenv("ADCP_SIGNING_KEY_PASSPHRASE_ENV", _RUNNER_KEK_ENV_NAME)
    monkeypatch.setenv(_RUNNER_KEK_ENV_NAME, _RUNNER_KEK_VALUE)
    monkeypatch.setattr("src.core.config._config", None, raising=False)

    with live_db_env(live_server) as env:
        TenantFactory(tenant_id=tenant_id, subdomain=f"seller-{_SLUG}", virtual_host=host)
        env._commit_factory_data()

        repo = SigningKeyRepository(env.get_session(), tenant_id)
        provisioned = provision_signing_key(repo, tenant_id=tenant_id, alg=_ALG, kid=f"{_SLUG}-runner-kek-key")
        env.get_session().commit()
        return provisioned.row.kid


async def _fetch_capabilities(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get(_CAPABILITIES_PATH)
    assert response.status_code == 200, (
        f"the capabilities document must be served anonymously over TLS at {_CAPABILITIES_PATH!r}; "
        f"got HTTP {response.status_code}. Body: {response.text[:300]!r}"
    )
    return response.json()


@pytest.mark.asyncio
async def test_live_server_declares_no_signing_for_a_key_it_cannot_decrypt(
    docker_services_e2e, live_server, monkeypatch
):
    """A key minted under the RUNNER's KEK, resolved by the LIVE SERVER holding a
    DIFFERENT KEK: webhook_signing.supported must be honestly false.
    """
    base_url = tls_base_url(live_server)
    verify = ca_verified_ssl_context()
    host = netloc(base_url)

    drop_tenant(live_server, _TENANT_ID)
    try:
        kid = _build_tenant_and_mint_under_runner_kek(live_server, monkeypatch, tenant_id=_TENANT_ID, host=host)

        async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=15.0) as client:
            served = await _fetch_capabilities(client)
    finally:
        drop_tenant(live_server, _TENANT_ID)

    webhook_signing = served.get("webhook_signing") or {}
    assert webhook_signing.get("supported") is False, (
        f"the live server holds a DIFFERENT KEK than the one key {kid!r} was minted under (runner-only, "
        f"never told to the container) -- it must declare webhook_signing.supported=false, not silently "
        f"advertise a signing capability it cannot perform. Served webhook_signing: {webhook_signing!r}"
    )
