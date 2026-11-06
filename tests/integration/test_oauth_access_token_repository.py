"""``OAuthAccessTokenRepository`` against real PostgreSQL: the one verification query, graded.

MCP OAuth access tokens are no longer JWTs. The token endpoint returns a high-entropy opaque
string once and stores ``sha256`` of it as a row in ``oauth_access_tokens``; what used to be
four signed claims checked in Python is now ONE statement —
``OAuthAccessTokenRepository.find_live`` — whose ``WHERE`` clause decides tenant, expiry and
revocation together. ``exp`` became ``expires_at > now``; the ``tenant_id`` claim match became
the repository's constructor-bound scope; a revocation that a signature could not carry became
``revoked_at IS NULL``.

WHY THIS FILE IS AN INTEGRATION TEST
------------------------------------
Because the subject IS the SQL. Every condition that makes a presented bearer usable is a
predicate in one ``select()``, so a test that substitutes the session grades the substitute:
the unit suite runs without ``DATABASE_URL`` by construction and therefore cannot execute this
query at all. There is no wire here either — ``find_live`` is below ``_resolve_identity``, and
a BDD scenario reaching it through ``/mcp`` would grade the whole resolver rather than the
predicate. That is the repository-contract case ``tests/integration/CLAUDE.md`` admits, and
PostgreSQL is the only thing that can answer three of the questions below at all: whether
``jsonb`` preserves a scope list, whether a timestamptz comparison is strict at the boundary,
and whether the composite ``ON DELETE CASCADE`` the model docstring claims actually fires.

WHAT MAKES THESE ASSERTIONS NON-VACUOUS
---------------------------------------
Nothing in this file is mocked; there are no mocks at all. Every row is written by production
code — tenants and principals by the factories, the OAuth client by
``OAuthClientRepository.create_for_principal``, the token rows by
``OAuthAccessTokenRepository.mint`` — and every assertion reads a value back out of
PostgreSQL.

Three habits keep a ``None`` from passing for the wrong reason:

- **``_fresh`` after every mint.** ``find_live`` returns the identity-mapped instance, whose
  ``scopes`` would otherwise be the very Python list ``mint`` was handed. Committing and
  expunging forces every attribute to come back from the column, so the round trip through
  ``jsonb`` and ``timestamptz`` is what gets asserted.
- **Every refusal is held against a control.** An expired token is also asked for at a time
  before its expiry; a cross-tenant miss is paired with the owning tenant's hit on the SAME
  hash; a revoked lookup asserts the live answer first. Without those, a repository that found
  nothing ever would satisfy half this file.
- **Column reads, not attribute reads, where the question is what is STORED.**
  ``select(OAuthAccessToken.revoked_at)`` goes to the server; ``row.revoked_at`` after an
  ``UPDATE`` issued through ``session.execute`` may be whatever the identity map still holds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.core.credentials import hash_token
from src.core.database.models import OAuthAccessToken, OAuthClient, Principal
from src.core.database.repositories.oauth_access_token import OAuthAccessTokenRepository
from src.core.database.repositories.oauth_client import OAuthClientRepository
from src.core.oauth_service import generate_oauth_client_credentials
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: The RFC 8707 audience the row records. Every production writer of the column refuses any
#: value but this deployment's own MCP audience before writing it, so one value is enough —
#: what is graded here is that the column carries it back unchanged.
_RESOURCE = "https://seller.example.test/mcp"

#: Deliberately ORDER-SENSITIVE and REPEATING. A two-distinct-element set would compare equal
#: to a ``jsonb`` round trip that sorted or de-duplicated the list, which is exactly the class
#: of silent corruption a stored scope list has to be proved against.
_SCOPES = ["mcp:tools", "mcp:principal", "mcp:tools"]

#: The instant every lookup in this file is made at. Fixed, because the subject is a
#: comparison against a stored timestamp and a wall-clock ``now`` makes the boundary cases
#: below unrepeatable.
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_HOUR = timedelta(hours=1)


def _hash(label: str) -> str:
    """The stored form of a token, derived the way production derives it.

    ``hash_token`` is the one producer of what this column holds (``src/core/credentials.py``),
    so the fixed-width hex the tests mint and look up is the same shape a real bearer reduces
    to — ``String(128)`` is not silently truncating a hand-written sentinel.
    """
    return hash_token(f"oat-plaintext-{label}")


def _registered_client(env: BareIntegrationEnv, *, tenant_id: str, principal_id: str) -> str:
    """A tenant, a principal and a registered OAuth client; returns the client id.

    All three are required by the row's two composite foreign keys, and all three are written
    by production: the factories for the identity rows, ``OAuthClientRepository`` for the
    client. ``token_hash`` is qualified with the tenant because the column is globally UNIQUE
    while the factory derives it from the principal id alone — the isolation cases below seed
    the same principal id in two tenants, which would otherwise collide on insert. Nothing
    here presents a credential, so the value only has to be distinct.
    """
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=tenant_id)
    PrincipalFactory(
        tenant=tenant,
        principal_id=principal_id,
        token_hash=hash_token(f"tok_test_{tenant_id}_{principal_id}"),
    )
    session = env.get_session()
    credentials = generate_oauth_client_credentials()
    OAuthClientRepository(session).create_for_principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        credentials=credentials,
    )
    session.commit()
    return credentials.client_id


def _fresh(session: Any) -> None:
    """Commit the caller's mint, then forget every loaded instance.

    Both halves matter. ``mint`` does not commit, so this is the caller doing its half of the
    contract; and the expunge is what makes the following assertions read COLUMNS rather than
    the Python objects the mint was handed. Without it, ``found.scopes == _SCOPES`` compares a
    list to itself and the ``jsonb`` round trip is never exercised.
    """
    session.commit()
    session.expunge_all()


def _mint(
    repo: OAuthAccessTokenRepository,
    label: str,
    *,
    client_id: str,
    principal_id: str,
    expires_at: datetime,
    scopes: list[str] | None = None,
) -> str:
    """Record one token through the repository and return the hash it is keyed by."""
    token_hash = _hash(label)
    repo.mint(
        token_hash=token_hash,
        client_id=client_id,
        principal_id=principal_id,
        scopes=_SCOPES if scopes is None else scopes,
        resource=_RESOURCE,
        expires_at=expires_at,
    )
    return token_hash


def _stored_hashes(session: Any, tenant_id: str) -> set[str]:
    """Every token hash PostgreSQL still holds for *tenant_id*, read off the column."""
    return set(session.scalars(select(OAuthAccessToken.token_hash).where(OAuthAccessToken.tenant_id == tenant_id)))


def _stored_revoked_at(session: Any, token_hash: str) -> datetime | None:
    """``revoked_at`` as the server has it — a column read, never the identity map's copy."""
    return session.scalars(select(OAuthAccessToken.revoked_at).where(OAuthAccessToken.token_hash == token_hash)).one()


def _row_count(session: Any, token_hash: str) -> int:
    """Whether the row still exists at all, independent of whether it is usable."""
    return session.scalars(
        select(func.count()).select_from(OAuthAccessToken).where(OAuthAccessToken.token_hash == token_hash)
    ).one()


class TestFindLiveResolvesAUsableToken:
    """The verify path's one query, on the facts it is supposed to carry back."""

    def test_a_live_token_resolves_to_every_fact_the_mint_recorded(self, integration_db):
        """Each column is a claim the JWT used to carry, so each is asserted by VALUE.

        The verifier reads the client, the principal, the scopes and the RFC 8707 resource off
        this row instead of trusting a signed copy of them, which is the whole reason the row
        replaced the JWT. ``found is not None`` would grade none of that.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_live_t", principal_id="oat_live_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_live_t")
            token_hash = _mint(
                repo,
                "live",
                client_id=client_id,
                principal_id="oat_live_p",
                expires_at=_NOW + _HOUR,
            )
            _fresh(session)

            found = repo.find_live(token_hash, now=_NOW)

            assert found is not None, "a minted, unexpired, unrevoked token of this tenant must resolve"
            assert found.token_hash == token_hash
            assert found.tenant_id == "oat_live_t", "mint binds the row to the repository's constructor tenant"
            assert found.client_id == client_id
            assert found.principal_id == "oat_live_p"
            assert found.resource == _RESOURCE
            assert found.scopes == _SCOPES
            assert found.expires_at == _NOW + _HOUR
            assert found.revoked_at is None, "a freshly minted token is not revoked"

    def test_scopes_round_trip_through_jsonb_with_order_and_repeats_intact(self, integration_db):
        """``scopes`` is a ``JSONType`` list, so the column is ``jsonb`` and the order is a claim.

        Asserted twice from two directions: off the re-loaded instance, and off a bare column
        select that constructs no ORM object at all. A granted scope set that came back sorted
        or de-duplicated would be a silent authorization change, and the list is chosen so
        either would fail the comparison.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_jsonb_t", principal_id="oat_jsonb_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_jsonb_t")
            ordered = _mint(repo, "jsonb", client_id=client_id, principal_id="oat_jsonb_p", expires_at=_NOW + _HOUR)
            # A grant carrying no scope at all: ``jsonb '[]'`` is a value, SQL NULL is not, and
            # ``JSONType`` maps ``None`` to NULL — so an empty list has to come back as a list.
            empty = _mint(
                repo,
                "jsonb-empty",
                client_id=client_id,
                principal_id="oat_jsonb_p",
                expires_at=_NOW + _HOUR,
                scopes=[],
            )
            _fresh(session)

            found = repo.find_live(ordered, now=_NOW)
            assert found is not None
            assert found.scopes == _SCOPES, f"the stored scope list changed in transit: {found.scopes!r}"

            column_value = session.scalars(
                select(OAuthAccessToken.scopes).where(OAuthAccessToken.token_hash == ordered)
            ).one()
            assert column_value == _SCOPES, f"jsonb holds {column_value!r}, not what was granted"

            empty_token = repo.find_live(empty, now=_NOW)
            assert empty_token is not None, "a scope-less grant is still a usable token"
            assert empty_token.scopes == [], f"an empty grant must read back as [], got {empty_token.scopes!r}"


class TestTenantScopingIsStructural:
    """The invariant that replaced the JWT's ``tenant_id`` claim check.

    Scoping is constructor-bound and applied by every ``select()`` in the repository
    (``tests/unit/test_cross_tenant_query_isolation.py`` states the rule; this executes it),
    so a token minted for one seller has no spelling of a lookup that reaches it from another.
    That is what lets the verifier drop the tenant claim rather than re-check it.
    """

    def test_a_token_minted_for_one_tenant_is_invisible_to_another_tenants_repository(self, integration_db):
        """The hash alone is not a credential: the same hash resolves in A and not in B.

        Every other fact about the row is in order — it is unexpired, unrevoked, and its
        client and principal are registered — so the only thing refusing the lookup is the
        tenant predicate. The owning tenant's hit on the SAME hash is what proves that:
        without it, a repository that never found anything would pass.
        """
        with BareIntegrationEnv() as env:
            client_a = _registered_client(env, tenant_id="oat_iso_a", principal_id="oat_iso_p")
            _registered_client(env, tenant_id="oat_iso_b", principal_id="oat_iso_p")
            session = env.get_session()

            repo_a = OAuthAccessTokenRepository(session, "oat_iso_a")
            token_hash = _mint(
                repo_a, "isolated", client_id=client_a, principal_id="oat_iso_p", expires_at=_NOW + _HOUR
            )
            _fresh(session)

            repo_b = OAuthAccessTokenRepository(session, "oat_iso_b")

            assert repo_b.find_live(token_hash, now=_NOW) is None, (
                "tenant B resolved a token minted for tenant A — the tenant predicate is the "
                "only thing standing in for the JWT claim this design deleted"
            )
            assert repo_a.find_live(token_hash, now=_NOW) is not None, (
                "the owning tenant must still resolve the very same hash, or the miss above says nothing about scoping"
            )
            assert _stored_hashes(session, "oat_iso_a") == {token_hash}, "the row belongs to tenant A"
            assert _stored_hashes(session, "oat_iso_b") == set(), "tenant B was never given a row"


class TestExpiryIsDecidedInSql:
    """``expires_at > now``, evaluated by PostgreSQL rather than returned to the caller."""

    def test_an_expired_token_is_indistinguishable_from_an_absent_one(self, integration_db):
        """A row whose expiry has passed is not found, and the same row IS found earlier.

        The control is the point: the row is present and unrevoked throughout, so the only
        variable between the two lookups is the ``now`` the predicate compares against.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_exp_t", principal_id="oat_exp_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_exp_t")
            token_hash = _mint(repo, "expired", client_id=client_id, principal_id="oat_exp_p", expires_at=_NOW - _HOUR)
            _fresh(session)

            assert repo.find_live(token_hash, now=_NOW) is None, (
                "an expired token resolved — expiry must be refused in SQL, not handed back "
                "for a caller to remember to check"
            )
            assert repo.find_live(token_hash, now=_NOW - 2 * _HOUR) is not None, (
                "the same row must resolve at a time before its expiry, or the miss above is "
                "about the row's existence rather than about expiry"
            )
            assert _row_count(session, token_hash) == 1, "an expired token is refused, not deleted"

    def test_a_token_expiring_exactly_at_now_is_already_dead(self, integration_db):
        """The boundary is EXCLUSIVE: ``expires_at > now``, so ``expires_at == now`` is refused.

        Pinned as the code behaves. One microsecond earlier the same row is live, which both
        fixes the sense of the inequality and shows PostgreSQL is keeping microsecond
        resolution on the column — a timestamp rounded to the second would make the two
        lookups indistinguishable and this case meaningless.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_bound_t", principal_id="oat_bound_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_bound_t")
            token_hash = _mint(repo, "boundary", client_id=client_id, principal_id="oat_bound_p", expires_at=_NOW)
            _fresh(session)

            assert repo.find_live(token_hash, now=_NOW) is None, (
                "a token whose expires_at equals now must NOT be live: the predicate is "
                "strictly greater-than, so the instant of expiry already belongs to the dead side"
            )
            assert repo.find_live(token_hash, now=_NOW - timedelta(microseconds=1)) is not None, (
                "one microsecond before expiry the token must still be live — if this fails the "
                "column is not keeping microsecond resolution and the boundary above is untested"
            )


class TestRevocationIsDecidedInSql:
    """``revoked_at IS NULL``: the exactness a signature could not offer."""

    def test_revocation_ends_the_token_at_the_next_lookup(self, integration_db):
        """Live before, ``None`` after, with the row still in the table.

        Both halves are asserted so the case cannot pass vacuously: a token that never
        resolved would satisfy the second assertion alone. The row surviving is the other
        promise — a revoked token is REFUSED until its own expiry, not erased.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_rev_t", principal_id="oat_rev_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_rev_t")
            token_hash = _mint(repo, "revoked", client_id=client_id, principal_id="oat_rev_p", expires_at=_NOW + _HOUR)
            _fresh(session)

            assert repo.find_live(token_hash, now=_NOW) is not None, "the token must be live before it is revoked"

            assert repo.revoke(token_hash, at=_NOW) is True, "revoking a live row must report that one turned"
            session.commit()

            assert repo.find_live(token_hash, now=_NOW) is None, (
                "a revoked token resolved — revocation must be refused by the same query that "
                "decides expiry, so no call site can forget it"
            )
            assert _row_count(session, token_hash) == 1, (
                "the row must survive its revocation: it is what keeps the token refused until its own expiry"
            )
            assert _stored_revoked_at(session, token_hash) == _NOW, "revoked_at records when it turned"

    def test_revoke_turns_one_row_once_and_keeps_the_first_timestamp(self, integration_db):
        """``True`` once, ``False`` afterwards, and the FIRST ``revoked_at`` is not overwritten.

        A second revocation is reported rather than silently succeeding, and the recorded
        instant stays the one the token actually stopped working at — a later call must not
        move it forward.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_rev2_t", principal_id="oat_rev2_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_rev2_t")
            token_hash = _mint(
                repo, "twice", client_id=client_id, principal_id="oat_rev2_p", expires_at=_NOW + 2 * _HOUR
            )
            _fresh(session)

            assert repo.revoke(token_hash, at=_NOW) is True
            assert repo.revoke(token_hash, at=_NOW + _HOUR) is False, (
                "a double revocation must be reported False, not silently re-applied"
            )
            session.commit()

            assert _stored_revoked_at(session, token_hash) == _NOW, (
                "the second call rewrote revoked_at; the column must keep the time of the FIRST "
                "revocation, because that is when the token stopped working"
            )
            assert repo.revoke(_hash("never-minted"), at=_NOW) is False, (
                "a hash naming nothing in this tenant must report False rather than claiming a turn"
            )

    def test_revoke_cannot_reach_another_tenants_token(self, integration_db):
        """The write is scoped exactly like the read, so one seller cannot end another's token.

        Asserted on both sides of the attempt: the return value, and the victim row, which
        must still be live and still carry ``revoked_at IS NULL``.
        """
        with BareIntegrationEnv() as env:
            client_a = _registered_client(env, tenant_id="oat_revx_a", principal_id="oat_revx_p")
            _registered_client(env, tenant_id="oat_revx_b", principal_id="oat_revx_p")
            session = env.get_session()

            repo_a = OAuthAccessTokenRepository(session, "oat_revx_a")
            token_hash = _mint(
                repo_a, "cross-revoke", client_id=client_a, principal_id="oat_revx_p", expires_at=_NOW + _HOUR
            )
            _fresh(session)

            assert OAuthAccessTokenRepository(session, "oat_revx_b").revoke(token_hash, at=_NOW) is False, (
                "tenant B revoked a hash it cannot even see"
            )
            session.commit()

            assert _stored_revoked_at(session, token_hash) is None, "the victim row must still be unrevoked"
            assert repo_a.find_live(token_hash, now=_NOW) is not None, (
                "tenant A's token must still be usable after tenant B's attempt"
            )


class TestDeleteExpiredPrunes:
    """The pruning job: tenant-scoped, past the cutoff only, and blind to revocation."""

    def test_only_this_tenants_rows_past_the_cutoff_are_deleted(self, integration_db):
        """Three rows, one deletion: the other tenant's expired row and this tenant's fresh
        row both survive.

        The returned count and the surviving hashes are both asserted. The count alone would
        not catch a statement that deleted the right number of the wrong rows.
        """
        with BareIntegrationEnv() as env:
            client_a = _registered_client(env, tenant_id="oat_del_a", principal_id="oat_del_p")
            client_b = _registered_client(env, tenant_id="oat_del_b", principal_id="oat_del_p")
            session = env.get_session()

            repo_a = OAuthAccessTokenRepository(session, "oat_del_a")
            expired_a = _mint(
                repo_a, "del-expired-a", client_id=client_a, principal_id="oat_del_p", expires_at=_NOW - _HOUR
            )
            fresh_a = _mint(
                repo_a, "del-fresh-a", client_id=client_a, principal_id="oat_del_p", expires_at=_NOW + _HOUR
            )
            expired_b = _mint(
                OAuthAccessTokenRepository(session, "oat_del_b"),
                "del-expired-b",
                client_id=client_b,
                principal_id="oat_del_p",
                expires_at=_NOW - _HOUR,
            )
            _fresh(session)

            deleted = repo_a.delete_expired(before=_NOW)
            session.commit()

            assert deleted == 1, f"exactly tenant A's one expired row must go, got {deleted}"
            assert _stored_hashes(session, "oat_del_a") == {fresh_a}, (
                "tenant A must keep its unexpired token and lose only the expired one"
            )
            assert _stored_hashes(session, "oat_del_b") == {expired_b}, (
                "tenant B's expired row survived tenant A's prune — cross-tenant cleanup must be "
                "impossible from a single repository"
            )

    def test_a_revoked_but_unexpired_row_is_deliberately_left(self, integration_db):
        """Revocation is not a reason to prune: the row is what keeps the token refused.

        It stays until its own expiry says it is dead for everyone, so a prune at a cutoff
        inside its lifetime must leave it — while ``find_live`` still answers ``None``.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_delrev_t", principal_id="oat_delrev_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_delrev_t")
            token_hash = _mint(
                repo, "revoked-fresh", client_id=client_id, principal_id="oat_delrev_p", expires_at=_NOW + _HOUR
            )
            _fresh(session)
            assert repo.revoke(token_hash, at=_NOW) is True
            session.commit()

            deleted = repo.delete_expired(before=_NOW)
            session.commit()

            assert deleted == 0, f"a revoked-but-unexpired row must not be pruned, {deleted} were deleted"
            assert _row_count(session, token_hash) == 1, "the revoked row must still be in the table"
            assert repo.find_live(token_hash, now=_NOW) is None, "and must still be refused"

    def test_a_token_expiring_exactly_at_the_cutoff_is_not_pruned(self, integration_db):
        """``expires_at < before``, so the cutoff instant itself survives the prune.

        Worth pinning because it is the mirror image of ``find_live``'s strict ``>``: at
        exactly ``_NOW`` the row is already unusable AND not yet prunable. Both predicates
        are exclusive, so the instant of expiry belongs to neither side, and the two
        assertions here record that the gap is deliberate rather than a drifting inequality.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_delb_t", principal_id="oat_delb_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_delb_t")
            token_hash = _mint(repo, "cutoff", client_id=client_id, principal_id="oat_delb_p", expires_at=_NOW)
            _fresh(session)

            assert repo.delete_expired(before=_NOW) == 0, "a row expiring exactly at the cutoff is not past it"
            session.commit()
            assert _row_count(session, token_hash) == 1
            assert repo.find_live(token_hash, now=_NOW) is None, (
                "the same row is nonetheless already unusable — find_live's > and delete_expired's < are both exclusive"
            )
            assert repo.delete_expired(before=_NOW + timedelta(microseconds=1)) == 1, (
                "one microsecond past the expiry the row IS prunable, which is what makes the "
                "refusal above about the boundary rather than about the tenant or the clause"
            )


class TestForeignKeyCascadesFire:
    """Schema behaviour only a real database can answer, and the model docstring claims it.

    ``oauth_access_tokens`` carries two composite foreign keys — to
    ``oauth_clients(tenant_id, client_id)`` and ``principals(tenant_id, principal_id)`` —
    both ``ON DELETE CASCADE``, so deleting a client or a principal takes its tokens with it
    rather than leaving bearers that outlive the identity they name.
    """

    def test_deleting_the_oauth_client_takes_its_tokens_with_it(self, integration_db):
        """The client FK pair, isolated: the principal and the tenant both survive the delete.

        There is no ORM relationship from ``OAuthClient`` to ``OAuthAccessToken``, so
        SQLAlchemy emits one ``DELETE FROM oauth_clients`` and knows nothing about the token
        row. Whatever removes it is PostgreSQL acting on the constraint.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_casc_t", principal_id="oat_casc_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_casc_t")
            token_hash = _mint(
                repo, "cascade-client", client_id=client_id, principal_id="oat_casc_p", expires_at=_NOW + _HOUR
            )
            _fresh(session)
            assert repo.find_live(token_hash, now=_NOW) is not None, "the token must be live before the delete"

            oauth_client = OAuthClientRepository(session).get_active(tenant_id="oat_casc_t", client_id=client_id)
            assert oauth_client is not None, "the registered client must be the row we are about to delete"
            session.delete(oauth_client)
            session.commit()

            assert _row_count(session, token_hash) == 0, (
                "deleting the oauth_clients row left its access token behind — the composite FK "
                "the model documents is not CASCADE in the schema this database was built from"
            )
            surviving_principal = session.scalars(
                select(func.count())
                .select_from(Principal)
                .where(Principal.tenant_id == "oat_casc_t", Principal.principal_id == "oat_casc_p")
            ).one()
            assert surviving_principal == 1, (
                "the principal must survive, so it was the CLIENT foreign key that took the token"
            )

    def test_deleting_the_principal_takes_its_tokens_with_it(self, integration_db):
        """The other half of the claim, graded on the outcome.

        ``Principal.oauth_clients`` is declared ``cascade="all, delete-orphan"``, so the ORM
        deletes the client row on its way to the principal and the two token foreign keys are
        not separable from here. The outcome is what the docstring promises and what matters:
        deleting the identity leaves no usable bearer naming it.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_cascp_t", principal_id="oat_cascp_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_cascp_t")
            token_hash = _mint(
                repo, "cascade-principal", client_id=client_id, principal_id="oat_cascp_p", expires_at=_NOW + _HOUR
            )
            _fresh(session)
            assert repo.find_live(token_hash, now=_NOW) is not None, "the token must be live before the delete"

            principal = session.scalars(
                select(Principal).where(Principal.tenant_id == "oat_cascp_t", Principal.principal_id == "oat_cascp_p")
            ).one()
            session.delete(principal)
            session.commit()

            assert _row_count(session, token_hash) == 0, (
                "deleting the principal left a bearer naming it — a token that outlives the "
                "identity it authenticates is exactly what the CASCADE exists to prevent"
            )
            assert (
                session.scalars(
                    select(func.count()).select_from(OAuthClient).where(OAuthClient.client_id == client_id)
                ).one()
                == 0
            ), "the principal's OAuth client went with it, which is the path the token row's deletion took"

    def test_minting_against_an_unregistered_client_is_refused_by_the_database(self, integration_db):
        """The precondition for the CASCADE above: the foreign key is really there.

        Without this, a schema missing the constraint would make the cascade tests fail for a
        reason nobody could tell from a cascade that simply did not fire. A client id naming no
        ``oauth_clients`` row cannot be written at all.
        """
        with BareIntegrationEnv() as env:
            _registered_client(env, tenant_id="oat_fk_t", principal_id="oat_fk_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_fk_t")

            with pytest.raises(IntegrityError):
                _mint(
                    repo,
                    "orphan",
                    client_id="mcp_client_never_registered",
                    principal_id="oat_fk_p",
                    expires_at=_NOW + _HOUR,
                )
                session.flush()
            session.rollback()


class TestTheCallerCommits:
    """``mint`` writes through the session and does NOT commit; the mint path does that."""

    def test_an_unflushed_mint_is_visible_in_its_own_transaction_and_lost_on_rollback(self, integration_db):
        """Both halves of "the caller commits", on one row.

        Inside the transaction the pending row is autoflushed by the following ``select``, so
        ``find_live`` already answers it — that is what lets the token endpoint hand back the
        plaintext beside a response it is about to commit. Roll back instead and nothing was
        issued: no row, and no hash that would resolve for a bearer the client may already
        hold.
        """
        with BareIntegrationEnv() as env:
            client_id = _registered_client(env, tenant_id="oat_tx_t", principal_id="oat_tx_p")
            session = env.get_session()
            repo = OAuthAccessTokenRepository(session, "oat_tx_t")
            token_hash = _mint(
                repo, "uncommitted", client_id=client_id, principal_id="oat_tx_p", expires_at=_NOW + _HOUR
            )

            assert repo.find_live(token_hash, now=_NOW) is not None, (
                "the mint's own transaction must see the row it added, uncommitted"
            )

            session.rollback()

            assert _row_count(session, token_hash) == 0, "a rolled-back mint must leave no row"
            assert repo.find_live(token_hash, now=_NOW) is None, (
                "a token whose mint was rolled back must not resolve — mint does not commit, so "
                "the row and the plaintext handed to the client stand or fall together"
            )
