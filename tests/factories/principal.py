"""Factory_boy factory for Principal model."""

from __future__ import annotations

from collections.abc import Mapping

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.credentials import hash_token, token_prefix
from src.core.database.models import Principal
from src.core.resolved_identity import AccountIdentity, PublicIdentity, ResolvedIdentity
from src.core.schemas import Principal as SchemaPrincipal
from src.core.schemas.account import Account
from src.core.tenant_context import TenantContext
from tests.factories.core import TenantFactory

_UNSET = object()


def plaintext_token_for(principal_id: str) -> str:
    """The token a test presents for the factory principal *principal_id*.

    Production stores ``sha256(token)`` and never the token, so a test cannot read a
    credential back out of the row it created. The factory derives the plaintext from the
    principal id instead, and the harness derives the same one when it builds the
    ``Authorization`` header, which is what lets the real resolver run in every test.
    """
    return f"tok_test_{principal_id}"


class PrincipalFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Principal
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = Sequence(lambda n: f"principal_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Advertiser {o.principal_id}")
    # The row stores only the hash. The plaintext a test PRESENTS is derived from the
    # principal id by ``plaintext_token_for`` -- the harness computes the same value, so a
    # credential never has to be read back out of a table that does not hold it.
    token_hash = LazyAttribute(lambda o: hash_token(plaintext_token_for(o.principal_id)))
    token_prefix = LazyAttribute(lambda o: token_prefix(plaintext_token_for(o.principal_id)))
    platform_mappings = factory.LazyFunction(lambda: {"mock": {"advertiser_id": "test_adv"}})

    @staticmethod
    def _tenant_for(tenant: object, tenant_id: str, tenant_overrides: dict[str, object]) -> object:
        """The TenantContext an identity carries: the one given, or the factory's own.

        Swallows nothing. Every override is checked against ``TenantContext.model_fields``
        and an unknown keyword is a ``TypeError`` naming it, never a key dropped on the
        floor: ``principal={...}``, ``account_id=...`` or a misspelled field fails here
        instead of building an identity that silently lacks it. An account is not an
        override: the resolver resolves the one a request names (``AccountRepository.find``)
        and builds the identity with it inside; a test that needs one calls
        ``make_account_identity``.
        """
        unknown = sorted(set(tenant_overrides) - set(TenantContext.model_fields))
        if unknown:
            raise TypeError(
                f"make_identity() got unknown keyword(s) {', '.join(unknown)}: "
                "only TenantContext fields are accepted as overrides"
            )
        if tenant is not _UNSET and tenant_overrides:
            raise TypeError(
                f"make_identity() got tenant overrides {', '.join(sorted(tenant_overrides))} "
                "alongside an explicit tenant; set the fields on the TenantContext instead"
            )
        if tenant is _UNSET:
            return TenantFactory.make_tenant(tenant_id=tenant_id, **tenant_overrides)
        if isinstance(tenant, Mapping):
            # A MAPPING IS BUILT, NOT REFUSED. The identity itself still refuses a dict —
            # ``InstanceOf(TenantContext)`` on the field — and that is the invariant worth
            # keeping: what a tool reads off ``identity.tenant`` is always the typed
            # context. Refusing to CONSTRUCT one from a mapping protects nothing extra,
            # because this factory already builds the context from keyword overrides two
            # lines up; it only meant that a caller holding a tenant dict (the shape
            # ``set_current_tenant`` takes, so a great many tests hold one) had to
            # hand-convert it, and ~150 call sites passed the dict straight through and
            # failed at ResolvedIdentity construction instead.
            #
            # An unknown key is still loud: it reaches TenantContext and fails there,
            # naming the field, exactly as a bad override does.
            return TenantFactory.make_tenant(**dict(tenant))
        return tenant

    @staticmethod
    def _principal_for(principal_id: str, platform_mappings: Mapping[str, object] | None = None) -> SchemaPrincipal:
        """The principal the resolver would have loaded for this caller.

        The factory's own shape (name and the mock platform mapping), so what a tool reads
        off ``identity.principal`` is what a row would have given it.

        ``platform_mappings`` is the one part a caller can state, because it is the one part
        a TOOL branches on: ``get_adapter`` reads the buyer's per-adapter ids off it
        (``platform_mappings["google_ad_manager"]["advertiser_id"]`` becomes the GAM
        ``company_id``), so a test driving a non-mock adapter needs the mapping its seeded
        principal row carries, not the mock default.
        """
        return SchemaPrincipal(
            principal_id=principal_id,
            name=f"Test Advertiser {principal_id}",
            platform_mappings=dict(platform_mappings) if platform_mappings else {"mock": {"advertiser_id": "test_adv"}},
        )

    @classmethod
    def make_identity(
        cls,
        principal_id: str = "test_principal",
        tenant_id: str = "test_tenant",
        tenant: TenantContext = _UNSET,  # type: ignore[assignment]
        platform_mappings: Mapping[str, object] | None = None,
        **tenant_overrides: object,
    ) -> ResolvedIdentity:
        """The AUTHENTICATED caller a protected tool takes, without DB persistence.

        A ``ResolvedIdentity`` always carries a principal and a tenant, so both parameters
        are non-optional here: the anonymous caller is ``make_public_identity``. When
        ``tenant`` is not given, ``TenantFactory.make_tenant()`` builds the TenantContext;
        pass **tenant_overrides for domain fields (approval_mode, etc).
        ``platform_mappings`` is the buyer's per-adapter ids -- state it when the tool
        under test reads one (GAM's ``advertiser_id``), since the default is mock-only.

        ``tenant`` is passed through as given. The factory normalizes nothing, and the
        identity refuses a dict at construction (``InstanceOf`` on the field), so a test
        that has a dict builds a TenantContext first. Inline ``ResolvedIdentity(...)``
        outside this factory fails
        ``.ast-grep/rules/resolved-identity-constructed-only-by-its-owners.yml``, so the
        factory's defaults are the one source of identity defaults in tests.
        """
        return ResolvedIdentity(
            principal=cls._principal_for(principal_id, platform_mappings),
            tenant=cls._tenant_for(tenant, tenant_id, tenant_overrides),  # type: ignore[arg-type]
        )

    @classmethod
    def make_account_identity(cls, identity: ResolvedIdentity, account: Account) -> AccountIdentity:
        """*identity* with *account* resolved onto it: the type a tool whose DTO requires an account takes.

        The account is per request and is passed as the schema ``Account`` it resolved to
        (in production, ``AccountRepository.find`` through ``account_from_row``); the
        factory builds the identity once with it inside, as the resolver does, and copies
        nothing.
        """
        return AccountIdentity(principal=identity.principal, tenant=identity.tenant, account=account)

    @classmethod
    def make_public_identity(
        cls,
        principal_id: str | None = None,
        tenant_id: str = "test_tenant",
        tenant: TenantContext | None = _UNSET,  # type: ignore[assignment]
        **tenant_overrides: object,
    ) -> PublicIdentity:
        """The caller of a PUBLIC tool, anonymous by default, without DB persistence.

        ``principal_id=None`` (the default) is the anonymous caller; a string is a caller
        whose credential resolved on a public tool. The TENANT is always present -- the
        identity type requires it, because a request naming no seller is refused
        CONFIGURATION_ERROR before an identity is built -- so only the caller can be absent.
        The same override rules as ``make_identity`` apply.
        """
        return PublicIdentity(
            principal=cls._principal_for(principal_id) if principal_id else None,
            tenant=cls._tenant_for(tenant, tenant_id, tenant_overrides),  # type: ignore[arg-type]
        )
