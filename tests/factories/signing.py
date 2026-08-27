"""Factory_boy factory for the SigningKey model.

Backs the A2 signing-key lifecycle (salesagent-z6nr.8). A factory row carries
REAL SDK-minted public key material by default — ``public_jwk`` is whatever
``adcp.signing.generate_signing_keypair`` emits for the row's ``kid``/``alg`` —
so a factory-built row is structurally publishable at a ``jwks_uri`` and the
tripwire has something honest to compare against. The private half is
deliberately dropped: rows that need a resolvable private key are built by
``src.core.signing.keys.provision_signing_key``, never by this factory.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import factory
from adcp.signing import generate_signing_keypair
from factory import LazyAttribute, LazyFunction, Sequence, SubFactory

from src.core.database.models import SigningKey
from tests.factories.core import TenantFactory

# Three alg namespaces exist. The DB column and RFC 9421 speak
# "ed25519"/"ecdsa-p256-sha256"; adcp.signing.keygen speaks "ed25519"/"es256";
# the JWK's own ``alg`` member speaks "EdDSA"/"ES256". This maps column -> keygen,
# mirroring production's ``_KEYGEN_ALG`` so a factory row's JWK actually matches
# the ``alg`` it advertises.
_KEYGEN_ALG = {"ed25519": "ed25519", "ecdsa-p256-sha256": "es256"}

# The SDK accepts exactly these two adcp_use values (keygen._ADCP_USE_VALUES).
_SDK_PURPOSES = ("request-signing", "webhook-signing")


def _public_jwk_for(obj: Any) -> dict[str, Any]:
    """Mint a real public JWK matching the row's kid/alg/purpose.

    Falls back to the profile defaults for deliberately-invalid values (the
    CHECK-constraint tests build rows with an off-profile ``alg`` or ``purpose``
    on purpose — keygen must not raise before the DB gets a chance to reject).
    """
    _, jwk = generate_signing_keypair(
        alg=_KEYGEN_ALG.get(obj.alg, "ed25519"),
        kid=obj.kid,
        purpose=obj.purpose if obj.purpose in _SDK_PURPOSES else "request-signing",
    )
    return jwk


class SigningKeyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = SigningKey
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)

    id = Sequence(lambda n: f"sk_{n:04d}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    kid = Sequence(lambda n: f"adcp-test-kid-{n:04d}")
    alg = "ed25519"
    purpose = "request-signing"
    public_jwk = LazyAttribute(_public_jwk_for)
    private_key_ref = Sequence(lambda n: f"env:ADCP_SIGNING_TEST_KEY_{n:04d}")
    # NULL by default, matching the dropped private half above: an env: row's
    # material lives outside the database. Rows that need resolvable ciphertext
    # are minted by provision_signing_key, which is the only thing that may write
    # this column in production.
    private_key_pem_encrypted = None
    not_before = LazyFunction(lambda: datetime.now(UTC) - timedelta(days=1))
    not_after = None
    revoked_at = None
