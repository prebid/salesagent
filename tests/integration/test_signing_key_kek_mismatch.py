"""salesagent-dn4i -- _private_half_unresolvable_because must ATTEMPT A DECRYPT.

Core Invariant under test: a tenant's KeyBacking.signs must be True only when
THIS deployment can actually open the key's private half -- never merely
because SOME passphrase is configured.

``_private_half_unresolvable_because`` (src/core/signing/posture.py; formerly
``_private_half_is_resolvable``) once returned True as
soon as ANY passphrase was configured, regardless of whether it was the RIGHT one
for a given row -- it never attempted a decrypt, so a rotated or per-environment
KEK made the wire say ``supported: true`` while every delivery raised. It now
ATTEMPTS the real resolution through ``resolve_signing_material``. This module mints
a key under one KEK, asks ``signing_key_backed()`` to resolve it under a DIFFERENT
one, and pins the honest answer: cannot sign. A failure here is that regression
returning.

IN-PROCESS ONLY: this grades the predicate's logic against a real database in one
process. The two-process proof -- the test runner minting under one KEK and a live
server container holding another -- is tests/e2e/test_signing_key_kek_mismatch_e2e.py.

Covers: salesagent-dn4i.
"""

from __future__ import annotations

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import (
    TEST_KEK_VARIABLE,
    install_kek,
    provision_key,
    remove_kek,
    signing_key_repo,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestKeyBackingHonorsTheActualKek:
    """KeyBacking.signs reflects whether THIS process can decrypt the row, not
    merely whether a passphrase is configured at all."""

    def test_signs_is_false_when_the_configured_kek_cannot_open_the_row(self, integration_db, monkeypatch):
        """Mint a key under KEK-A. Reconfigure to KEK-B. signing_key_backed().signs
        must be False -- the predicate has to attempt the decrypt to know that."""
        from datetime import UTC, datetime

        from src.core.signing.posture import ActiveKeyFault, signing_key_backed

        tenant_id = "dn4i_kek_mismatch"
        with BareIntegrationEnv(tenant_id=tenant_id) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=tenant_id, subdomain="dn4i-kek-mismatch")
            env.setup_default_data()
            repo = signing_key_repo(env, tenant_id)

            install_kek(monkeypatch, TEST_KEK_VARIABLE, "kek-a-correct-horse-battery-staple")
            provision_key(repo, tenant_id, "dn4i-kid-1")

            # Switch to a DIFFERENT KEK -- the row was encrypted under KEK-A, this
            # deployment now only holds KEK-B. It cannot decrypt.
            install_kek(monkeypatch, TEST_KEK_VARIABLE, "kek-b-a-totally-different-passphrase")

            backing = signing_key_backed(repo, now=datetime.now(UTC))

        assert backing.signs is False, (
            "signing_key_backed().signs must be False when the configured KEK cannot "
            "decrypt the row's private half -- it returned True, meaning the predicate "
            "stopped attempting a decrypt and only checked that SOME passphrase is "
            "configured (salesagent-dn4i)"
        )
        assert backing.active_kid == "dn4i-kid-1" and backing.active_key_fault is ActiveKeyFault.KEK_WRONG, (
            "a configured KEK that does not open the row is KEK_WRONG (restore the original KEK), not "
            f"another cause; got active_kid={backing.active_kid!r}, fault={backing.active_key_fault!r} (#2288)"
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

            install_kek(monkeypatch, TEST_KEK_VARIABLE, "the-one-true-kek-value")
            provision_key(repo, tenant_id, "dn4i-kid-2")

            backing = signing_key_backed(repo, now=datetime.now(UTC))

        assert backing.signs is True, "the correct KEK must resolve signs=True"
        assert backing.active_key_fault is None, f"a key that opens has no fault; got {backing.active_key_fault!r}"

    def test_signs_is_false_when_no_kek_configured_at_all(self, integration_db, monkeypatch):
        """Control: no passphrase configured at all must also resolve signs=False
        (this branch already worked before the fix -- pins it stays working)."""
        from datetime import UTC, datetime

        from src.core.signing.posture import ActiveKeyFault, signing_key_backed

        tenant_id = "dn4i_no_kek"
        with BareIntegrationEnv(tenant_id=tenant_id) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=tenant_id, subdomain="dn4i-no-kek")
            env.setup_default_data()
            repo = signing_key_repo(env, tenant_id)

            install_kek(monkeypatch, TEST_KEK_VARIABLE, "a-kek-for-minting-only")
            provision_key(repo, tenant_id, "dn4i-kid-3")

            remove_kek(monkeypatch, TEST_KEK_VARIABLE)

            backing = signing_key_backed(repo, now=datetime.now(UTC))

        assert backing.signs is False, "no KEK configured at all must resolve signs=False"
        assert backing.active_kid == "dn4i-kid-3" and backing.active_key_fault is ActiveKeyFault.KEK_UNSET, (
            "no KEK configured at all is KEK_UNSET, whose advice is to restore the KEK; got "
            f"active_kid={backing.active_kid!r}, fault={backing.active_key_fault!r} (#2288)"
        )
