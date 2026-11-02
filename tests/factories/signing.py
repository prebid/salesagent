"""Factory_boy factory for the SigningKey model.

Backs the A2 signing-key lifecycle (salesagent-z6nr.8). A factory row carries
REAL SDK-minted key material — BOTH halves, from one
``adcp.signing.generate_signing_keypair`` call — so a factory-built row is
structurally publishable at a ``jwks_uri`` and the tripwire has something honest
to compare against.

The private half is encrypted under the same test KEK
``tests.helpers.signing.deployment_kek`` configures, so a factory row resolves
exactly like a production-minted one. A row with NO private half is not buildable
at all: the column is NOT NULL, because the database is the only place that half
lives and a published key nothing can sign with is not a state worth being able
to construct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import factory
from adcp.signing import generate_signing_keypair
from factory import LazyAttribute, LazyFunction, Sequence, SubFactory

from src.core.database.models import SigningKey
from src.core.exceptions import AdCPConfigurationError
from src.core.signing.algorithms import REQUEST_SIGNING, keygen_alg
from tests.factories.core import TenantFactory
from tests.helpers.signing import TEST_KEK_PASSPHRASE

# Three alg namespaces exist. The DB column and RFC 9421 speak
# "ed25519"/"ecdsa-p256-sha256"; adcp.signing.keygen speaks "ed25519"/"es256";
# the JWK's own ``alg`` member speaks "EdDSA"/"ES256". The column -> keygen
# translation is PRODUCTION's ``keygen_alg``, imported rather than mirrored: a
# second copy here would keep minting an ed25519 JWK for a profile value it had
# not heard of, silently publishing a JWK that contradicts the ``alg`` its own
# row advertises — the exact mismatch this factory exists to make impossible.
_DEFAULT_ALG = "ed25519"

# The SDK accepts exactly these two adcp_use values (keygen._ADCP_USE_VALUES).
# NOT ``MINTABLE_PURPOSES``: production narrows to request-signing, but
# ``tests/integration/test_signing_key_repository.py``'s CHECK-constraint tests
# build a webhook-signing row on purpose and need keygen to accept it so the DB
# is what refuses it.
_SDK_PURPOSES = ("request-signing", "webhook-signing")


def _keygen_alg_or_default(alg: str) -> Literal["ed25519", "es256"]:
    """``keygen_alg``, with the profile default for a deliberately off-profile value.

    Only a value production genuinely refuses takes the fallback, so this cannot
    paper over a profile alg that keygen has simply not been taught yet.
    """
    try:
        return keygen_alg(alg)
    except AdCPConfigurationError:
        return keygen_alg(_DEFAULT_ALG)


#: One minted keypair per row, memoised so the two halves below come from the
#: SAME mint. Two ``generate_signing_keypair`` calls would publish one key's JWK
#: beside another key's PEM — precisely the disagreement the tripwire exists to
#: catch, manufactured by the fixtures. Keyed by the triple that determines the
#: mint; ``kid`` is a per-row Sequence, so entries do not collide across rows.
_MINTED: dict[tuple[str, str, str], tuple[bytes, dict[str, Any]]] = {}


def _keypair_for(obj: Any) -> tuple[bytes, dict[str, Any]]:
    """Mint (or recall) a real keypair matching the row's kid/alg/purpose.

    Falls back to the profile defaults for deliberately-invalid values (the
    CHECK-constraint tests in ``tests/integration/test_signing_key_repository.py``
    build rows with an off-profile ``alg`` or ``purpose`` on purpose — keygen must
    not raise before the DB gets a chance to reject).
    """
    alg = _keygen_alg_or_default(obj.alg)
    purpose = obj.purpose if obj.purpose in _SDK_PURPOSES else REQUEST_SIGNING
    key = (obj.kid, alg, purpose)
    if key not in _MINTED:
        _MINTED[key] = generate_signing_keypair(alg=alg, kid=obj.kid, purpose=purpose, passphrase=TEST_KEK_PASSPHRASE)
    return _MINTED[key]


def _public_jwk_for(obj: Any) -> dict[str, Any]:
    """The public half of the row's keypair."""
    return _keypair_for(obj)[1]


def _private_pem_for(obj: Any) -> bytes:
    """The private half, encrypted under the test KEK exactly as production does.

    The same ciphertext shape ``provision_signing_key`` writes — PKCS#8
    ``BEGIN ENCRYPTED PRIVATE KEY`` under the deployment passphrase — so a
    factory-built row opens under ``tests.helpers.signing.deployment_kek`` just
    like a minted one. There is no cheaper stand-in here on purpose: a sentinel or
    an unencrypted PEM would make the row a shape production cannot produce, which
    is the whole class of fixture this change exists to remove.
    """
    return _keypair_for(obj)[0]


class SigningKeyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = SigningKey
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)

    id = Sequence(lambda n: f"sk_{n:04d}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    kid = Sequence(lambda n: f"adcp-test-kid-{n:04d}")
    alg = _DEFAULT_ALG
    purpose = REQUEST_SIGNING
    public_jwk = LazyAttribute(_public_jwk_for)
    private_key_pem_encrypted = LazyAttribute(_private_pem_for)
    not_before = LazyFunction(lambda: datetime.now(UTC) - timedelta(days=1))
    not_after = None
    revoked_at = None
