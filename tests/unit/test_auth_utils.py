"""``get_principal_from_token``: which bearer resolves to which principal, and which to nobody.

The merged module accepts TWO bearer forms and answers both with a ``Principal`` or ``None``
(``src/core/auth_utils.py``). Both are opaque strings the database holds the ``sha256`` of, and
both are graded here against real objects: a first-party MCP OAuth access token whose row is
written by the real ``OAuthAccessTokenRepository.mint``, and a buyer token (``tok_``) whose hash
the ``principals`` row stores. Nothing about the credential is faked — the token row, the OAuth
client row (``OAuthClientRepository``) and the principal row (``PrincipalFactory``) are the rows
production reads — so a rejection here is the rejection production performs.

What is absent is the engine, because a unit test has no database: ``get_db_session`` hands back
an in-memory session that answers the repositories' statements from rows held in a list, applying
every criterion including ``find_live``'s expiry and revocation predicates. The repositories, the
client and tenant checks and ``Principal.from_row`` all run for real on top of it, and a
tenant-scoped query really is scoped.

What is absent for a second reason is the environment. The verify path reads none at all now —
no signing secret, no issuer, no audience — so there is nothing to set up and nothing a
misconfigured deployment can turn into a wrongly-accepted bearer. The ``resource`` the row
carries is settled at mint time by ``src/routes/oauth.py``, which refuses any value but the
deployment's own MCP audience before a row is written.

Raising is NOT this module's job: ``None`` means "this bearer resolved to nobody", and
``src/core/resolved_identity.py`` turns that into AUTH_INVALID.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, False_, Null, True_

from src.core.auth_utils import get_principal_from_token
from src.core.credentials import hash_token
from src.core.database.repositories.oauth_access_token import OAuthAccessTokenRepository
from src.core.database.repositories.oauth_client import OAuthClientRepository
from src.core.oauth_service import DEFAULT_MCP_OAUTH_SCOPE, generate_oauth_client_credentials
from src.core.schemas import Principal
from tests.factories import PrincipalFactory, TenantFactory
from tests.factories.principal import plaintext_token_for

_TENANT_ID = "tenant_oauth"
_PRINCIPAL_ID = "principal_oauth"
_OTHER_TENANT_ID = "tenant_other"

#: The RFC 8707 resource every row carries. The verify path never reads it — the authorization
#: and token endpoints refuse any other value before the row exists — so it is one constant.
_MCP_RESOURCE = "https://seller.example.com/mcp"

#: The principal the seeded row names, which is what both bearer forms must resolve to.
_EXPECTED_PRINCIPAL = Principal(
    principal_id=_PRINCIPAL_ID,
    name="Acme Buying Desk",
    platform_mappings={"mock": {"advertiser_id": "adv_oauth"}},
    agent_url="https://buyer.example.com",
)

#: How a SQLAlchemy comparison is evaluated against a row held in memory. ``find_live`` compares
#: four ways and the in-memory session must honour all four, because three of them ARE the
#: definition of a live token.
_COMPARISONS = {
    operator.eq: operator.eq,
    operators.gt: operator.gt,
    operators.is_: lambda value, expected: value is expected,
}


class _RowsResult:
    """What ``Session.scalars`` returns, for the one method the repositories call."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _InMemorySession:
    """A session that answers a repository's ``select()`` from rows held in memory.

    Every criterion is applied, so ``OAuthAccessTokenRepository``'s and
    ``PrincipalRepository``'s tenant scoping, ``find_live``'s ``expires_at > now`` and
    ``revoked_at IS NULL``, and ``OAuthClientRepository.get_active``'s ``is_active=True`` are
    really in force rather than assumed. ``add`` is here because the token row and the OAuth
    client row are both created through their repositories.
    """

    def __init__(self, *rows: Any) -> None:
        self._rows = list(rows)

    def __enter__(self) -> _InMemorySession:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def commit(self) -> None:
        """``execute_with_retry`` commits the read it just ran; the rows are the fixture."""

    def add(self, row: Any) -> None:
        self._rows.append(row)

    def scalars(self, stmt: Any) -> _RowsResult:
        entity = stmt.column_descriptions[0]["entity"]
        criteria = _criteria(stmt)
        return _RowsResult(
            [
                row
                for row in self._rows
                if isinstance(row, entity)
                and all(compare(getattr(row, column), value) for column, compare, value in criteria)
            ]
        )


def _criteria(stmt: Any) -> list[tuple[str, Any, Any]]:
    """The ``(column, comparison, value)`` triples a repository's statement put on *stmt*."""
    whereclause = stmt.whereclause
    clauses = getattr(whereclause, "clauses", (whereclause,))
    return [(clause.left.key, _COMPARISONS[clause.operator], _compared_value(clause.right)) for clause in clauses]


def _compared_value(node: Any) -> Any:
    if isinstance(node, BindParameter):
        return node.value
    if isinstance(node, Null):
        return None
    if isinstance(node, True_):
        return True
    if isinstance(node, False_):
        return False
    raise AssertionError(f"unsupported comparison operand: {node!r}")


@dataclass(frozen=True)
class _Seller:
    """One tenant with one principal and one registered OAuth client, plus their session."""

    tenant: Any
    principal: Any
    oauth_client: Any
    session: _InMemorySession


def _mint_access_token(
    session: _InMemorySession,
    *,
    client_id: str,
    tenant_id: str = _TENANT_ID,
    principal_id: str = _PRINCIPAL_ID,
    expires_at: datetime | None = None,
) -> tuple[str, Any]:
    """Issue a real stored access token: the string a request presents, and its row.

    Minted exactly as the token endpoint does — through the repository, which stores
    ``hash_token`` of a plaintext it never keeps. The row is returned so a test can revoke it.
    """
    plaintext = f"mcp_at_{tenant_id}_{principal_id}_vQmJ6r8kXn2T"
    access_token = OAuthAccessTokenRepository(session, tenant_id).mint(
        token_hash=hash_token(plaintext),
        client_id=client_id,
        principal_id=principal_id,
        scopes=[DEFAULT_MCP_OAUTH_SCOPE],
        resource=_MCP_RESOURCE,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )
    return plaintext, access_token


@pytest.fixture
def seller(monkeypatch: pytest.MonkeyPatch) -> _Seller:
    tenant = TenantFactory.build(tenant_id=_TENANT_ID, virtual_host="seller.example.com", is_active=True)
    principal = PrincipalFactory.build(
        tenant=tenant,
        principal_id=_PRINCIPAL_ID,
        name=_EXPECTED_PRINCIPAL.name,
        platform_mappings=_EXPECTED_PRINCIPAL.platform_mappings,
        agent_url=_EXPECTED_PRINCIPAL.agent_url,
    )
    session = _InMemorySession(tenant, principal)
    oauth_client = OAuthClientRepository(session).create_for_principal(
        tenant_id=_TENANT_ID,
        principal_id=_PRINCIPAL_ID,
        credentials=generate_oauth_client_credentials(),
    )

    # The only absent part: the engine. ``execute_with_retry`` still opens, commits and
    # retries around the real repositories. Nothing else needs arranging — the verify path
    # reads no environment, so there is no secret, issuer or audience to configure.
    monkeypatch.setattr("src.core.database.database_session.get_db_session", lambda: session)

    return _Seller(tenant=tenant, principal=principal, oauth_client=oauth_client, session=session)


def test_live_access_token_resolves_to_the_principal_its_row_names(seller: _Seller) -> None:
    token, _ = _mint_access_token(seller.session, client_id=seller.oauth_client.client_id)

    assert get_principal_from_token(token, _TENANT_ID) == _EXPECTED_PRINCIPAL


def test_access_token_minted_for_another_tenant_resolves_to_nobody(seller: _Seller) -> None:
    """The tenant match, now structural: the row is minted inside another seller's scope.

    Every other fact is in place — the client is active, the tenant is active, the principal
    id names this tenant's row — so the ONLY thing refusing the bearer is that
    ``OAuthAccessTokenRepository(session, _TENANT_ID)`` cannot see a row minted under
    ``_OTHER_TENANT_ID``. There is no spelling of the lookup that could.
    """
    token, _ = _mint_access_token(
        seller.session,
        client_id=seller.oauth_client.client_id,
        tenant_id=_OTHER_TENANT_ID,
    )

    assert get_principal_from_token(token, _TENANT_ID) is None


def test_revoking_the_oauth_client_stops_a_token_already_issued_under_it(seller: _Seller) -> None:
    """The revocation point for a whole client, asserted before and after so it is not vacuous."""
    token, _ = _mint_access_token(seller.session, client_id=seller.oauth_client.client_id)
    assert get_principal_from_token(token, _TENANT_ID) == _EXPECTED_PRINCIPAL

    seller.oauth_client.is_active = False

    assert get_principal_from_token(token, _TENANT_ID) is None


def test_revoked_access_token_resolves_to_nobody(seller: _Seller) -> None:
    """Revocation is exact: ``revoked_at`` on the row ends the token at the next request."""
    token, access_token = _mint_access_token(seller.session, client_id=seller.oauth_client.client_id)
    assert get_principal_from_token(token, _TENANT_ID) == _EXPECTED_PRINCIPAL

    access_token.revoked_at = datetime.now(UTC)

    assert get_principal_from_token(token, _TENANT_ID) is None


def test_expired_access_token_resolves_to_nobody(seller: _Seller) -> None:
    """Expired an hour ago. ``find_live`` compares ``expires_at`` against now, with no leeway."""
    token, _ = _mint_access_token(
        seller.session,
        client_id=seller.oauth_client.client_id,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    assert get_principal_from_token(token, _TENANT_ID) is None


def test_access_token_resolves_to_nobody_once_the_tenant_is_deactivated(seller: _Seller) -> None:
    token, _ = _mint_access_token(seller.session, client_id=seller.oauth_client.client_id)
    assert get_principal_from_token(token, _TENANT_ID) == _EXPECTED_PRINCIPAL

    seller.tenant.is_active = False

    assert get_principal_from_token(token, _TENANT_ID) is None


def test_access_token_whose_principal_is_not_a_row_resolves_to_nobody(seller: _Seller) -> None:
    """The token row names a principal; the ``principals`` row is the fact.

    Postgres cascades a principal's live tokens away with it, so this state is the one the
    cascade is there to prevent rather than one a deployment reaches — the check is what makes
    the row, and not the token's own claim about itself, the authority.
    """
    vanished_principal_id = "principal_vanished"
    oauth_client = OAuthClientRepository(seller.session).create_for_principal(
        tenant_id=_TENANT_ID,
        principal_id=vanished_principal_id,
        credentials=generate_oauth_client_credentials(),
    )
    token, _ = _mint_access_token(
        seller.session,
        client_id=oauth_client.client_id,
        principal_id=vanished_principal_id,
    )

    assert get_principal_from_token(token, _TENANT_ID) is None


def test_opaque_buyer_token_resolves_by_token_hash(seller: _Seller) -> None:
    """The fallback form: an opaque ``tok_`` value whose ``sha256`` the row stores."""
    assert get_principal_from_token(plaintext_token_for(_PRINCIPAL_ID), _TENANT_ID) == _EXPECTED_PRINCIPAL


def test_opaque_buyer_token_does_not_resolve_inside_another_tenant(seller: _Seller) -> None:
    assert get_principal_from_token(plaintext_token_for(_PRINCIPAL_ID), _OTHER_TENANT_ID) is None


def test_bearer_that_is_neither_form_resolves_to_nobody(seller: _Seller) -> None:
    """One hash, two misses. Neither table holds it, and the two are indistinguishable."""
    assert get_principal_from_token("tok_this_token_was_never_issued", _TENANT_ID) is None
