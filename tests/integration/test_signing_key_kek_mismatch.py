"""salesagent-dn4i -- _private_half_is_resolvable must ATTEMPT A DECRYPT.

Core Invariant under test: a tenant's KeyBacking.signs must be True only when
THIS deployment can actually open the key's private half -- never merely
because SOME passphrase is configured.

TDD RED. Production today (src/core/signing/posture.py, _private_half_is_resolvable)
returns True as soon as ANY passphrase is configured, regardless of whether it is
the RIGHT one for a given row -- it never attempts a decrypt. This test mints a
key under one KEK, then asks signing_key_backed() to resolve it under a
DIFFERENT one, and asserts the honest answer: cannot sign.

Covers: salesagent-dn4i.
"""

from __future__ import annotations

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import provision_key, signing_key_repo

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_KEK_ENV_NAME = "SALESAGENT_TEST_SIGNING_KEK"


def _set_kek(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Point the deployment KEK pointer at _KEK_ENV_NAME, holding *value*."""
    monkeypatch.setenv("ADCP_SIGNING_KEY_PASSPHRASE_ENV", _KEK_ENV_NAME)
    monkeypatch.setenv(_KEK_ENV_NAME, value)
    monkeypatch.setattr("src.core.config._config", None, raising=False)


class TestKeyBackingHonorsTheActualKek:
    """KeyBacking.signs reflects whether THIS process can decrypt the row, not
    merely whether a passphrase is configured at all."""

    def test_signs_is_false_when_the_configured_kek_cannot_open_the_row(self, integration_db, monkeypatch):
        """Mint a key under KEK-A. Reconfigure to KEK-B. signing_key_backed().signs
        must be False -- production today returns True (the bug)."""
        from datetime import UTC, datetime

        from src.core.signing.posture import signing_key_backed

        tenant_id = "dn4i_kek_mismatch"
        with BareIntegrationEnv(tenant_id=tenant_id) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=tenant_id, subdomain="dn4i-kek-mismatch")
            env.setup_default_data()
            repo = signing_key_repo(env, tenant_id)

            _set_kek(monkeypatch, "kek-a-correct-horse-battery-staple")
            provision_key(repo, tenant_id, "dn4i-kid-1")

            # Switch to a DIFFERENT KEK -- the row was encrypted under KEK-A, this
            # deployment now only holds KEK-B. It cannot decrypt.
            _set_kek(monkeypatch, "kek-b-a-totally-different-passphrase")

            backing = signing_key_backed(repo, now=datetime.now(UTC))

        assert backing.signs is False, (
            "signing_key_backed().signs must be False when the configured KEK cannot "
            "decrypt the row's private half -- production returned True, meaning the "
            "predicate never attempted a decrypt and only checked that SOME "
            "passphrase is configured (salesagent-dn4i)"
        )

    def test_signs_is_true_when_the_configured_kek_matches(self, integration_db, monkeypatch):
        """Control: the SAME KEK the key was minted under resolves signs=True."""
        from datetime import UTC, datetime

        from src.core.signing.posture import signing_key_backed

        tenant_id = "dn4i_kek_match"
        with BareIntegrationEnv(tenant_id=tenant_id) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=tenant_id, subdomain="dn4i-kek-match")
            env.setup_default_data()
            repo = signing_key_repo(env, tenant_id)

            _set_kek(monkeypatch, "the-one-true-kek-value")
            provision_key(repo, tenant_id, "dn4i-kid-2")

            backing = signing_key_backed(repo, now=datetime.now(UTC))

        assert backing.signs is True, "the correct KEK must resolve signs=True"

    def test_signs_is_false_when_no_kek_configured_at_all(self, integration_db, monkeypatch):
        """Control: no passphrase configured at all must also resolve signs=False
        (this branch already worked before the fix -- pins it stays working)."""
        from datetime import UTC, datetime

        from src.core.signing.posture import signing_key_backed

        tenant_id = "dn4i_no_kek"
        with BareIntegrationEnv(tenant_id=tenant_id) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=tenant_id, subdomain="dn4i-no-kek")
            env.setup_default_data()
            repo = signing_key_repo(env, tenant_id)

            _set_kek(monkeypatch, "a-kek-for-minting-only")
            provision_key(repo, tenant_id, "dn4i-kid-3")

            monkeypatch.delenv(_KEK_ENV_NAME, raising=False)
            monkeypatch.delenv("ADCP_SIGNING_KEY_PASSPHRASE_ENV", raising=False)
            monkeypatch.setattr("src.core.config._config", None, raising=False)

            backing = signing_key_backed(repo, now=datetime.now(UTC))

        assert backing.signs is False, "no KEK configured at all must resolve signs=False"
