"""The principal lookups: the resolver's database primitives.

Three readers, one per way a principal is identified. A request identifies its principal
by the token it presents (``get_principal_from_token``) or, presenting no token, by the
request-signing keyid its onboarding record maps (``get_principal_by_signing_keyid``);
server-initiated work identifies the owner of a stored row by id (``get_principal_by_id``).
All three are scoped to a tenant, because a principal is a row in exactly one tenant, and
all three hand back the model built inside the session so the resolver keeps what was
loaded.

The keyid lookup's answer is UNVERIFIED: the keyid is read off a header before any
signature is checked. The resolver hands the lookup to the verifier, which verifies the
signature against that principal's keys and returns the principal only when it verified.
"""

import logging

from src.core.credentials import hash_token
from src.core.database.database_session import execute_with_retry
from src.core.database.repositories.principal import PrincipalRepository
from src.core.schemas import Principal

logger = logging.getLogger(__name__)


def get_principal_from_token(token: str, tenant_id: str) -> Principal | None:
    """The principal *token* authenticates inside *tenant_id*, or ``None``.

    A buyer credential is a ``Principal`` row and nothing else, and the lookup is always
    scoped to the tenant the request addressed, so a token minted for one tenant never
    acts on another. The row stores ``sha256(token)``, so the presented value is hashed
    here and compared by equality on the hash; the plaintext is never written anywhere.
    """

    token_hash = hash_token(token)

    def _lookup_principal(session):
        row = PrincipalRepository(session, tenant_id).find_by_token_hash(token_hash)
        return Principal.from_row(row) if row else None

    return execute_with_retry(_lookup_principal)


def get_principal_by_signing_keyid(keyid: str, tenant_id: str) -> Principal | None:
    """The counterparty *tenant_id* onboarded *keyid* for, or ``None``.

    security.mdx @ v3.1.1 L1088-1090, checklist step 1: the verifier resolves a keyid to the
    signing agent's brand.json ``agents[]`` entry, and "Discovery MAY come from prior
    onboarding". The answer is a CANDIDATE: only the verifier, by verifying the signature
    against this principal's keys, establishes it as the caller.
    """

    def _lookup_principal(session):
        row = PrincipalRepository(session, tenant_id).find_by_signing_keyid(keyid)
        return Principal.from_row(row) if row else None

    return execute_with_retry(_lookup_principal)


def get_principal_by_id(tenant_id: str, principal_id: str) -> Principal | None:
    """The principal *principal_id* names inside *tenant_id*, or ``None``.

    For resolution from stored ids (``resolved_identity.identity_of``): the owner of a
    media buy or creative a server-initiated job acts on.
    """

    def _lookup_principal(session):
        row = PrincipalRepository(session, tenant_id).get(principal_id)
        return Principal.from_row(row) if row else None

    return execute_with_retry(_lookup_principal)
