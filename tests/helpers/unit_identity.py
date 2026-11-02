"""The identity a MOCKED-DATABASE unit test hands an ``_impl``. Backed by no rows.

The name says what the value is: a FABRICATED caller. In a unit test whose database is
mocked there are no rows at all, so a fabricated tenant is internally consistent — there
is nothing for it to disagree with. That is the one place fabrication is legitimate. An
integration test is the opposite case: rows exist for everything else, so a fabricated
tenant there points at nothing, and the identity must be derived from the row instead
(``TenantContext.load(tenant_id)`` — see the ``env._identity =`` sites in the
get_products modules, and ``tests/CLAUDE.md``).

What this fixes at the call sites is the identity TYPE. ``PrincipalFactory.make_identity``
returns a ``ResolvedIdentity``, whose ``account`` is optional and defaults to ``None``.
``_create_media_buy_impl`` and ``_sync_creatives_impl`` both declare ``AccountIdentity``,
where ``account`` is REQUIRED, because their DTOs put ``account`` in ``/required`` and the
boundary resolves it before either runs. So a bare ``ResolvedIdentity`` is a caller those
controllers can never receive: the ``account is None`` branch it selects is unreachable in
production, and the sites that go on to read ``identity.account.account_id`` get an
AttributeError instead of a test result. mypy does not catch it — ``make quality`` runs
mypy over ``src/`` only.
"""

from __future__ import annotations

from typing import Any

from src.core.resolved_identity import AccountIdentity, ResolvedIdentity
from src.core.schemas.account import Account
from tests.factories.principal import PrincipalFactory

#: The account these identities carry. Active and non-sandbox, which is what the resolver
#: would have loaded for an ordinary buyer: ``sandbox`` unset keeps
#: ``_sync_creatives_impl``'s sandbox branch on the same answer it gave with no account.
UNIT_ACCOUNT_ID = "acct_unit"


def fabricated_identity(
    *,
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
    **tenant_fields: Any,
) -> ResolvedIdentity:
    """A fabricated ``ResolvedIdentity`` — for an ``_impl`` whose DTO needs no account.

    The sibling of :func:`fabricated_account_identity` for the tools that declare
    ``ResolvedIdentity`` or ``PublicIdentity`` (``get_products`` and friends), and for pure
    predicates that take an identity and read one tenant field off it. Same rule: mocked
    database only.
    """
    return PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id, **tenant_fields)


def fabricated_account_identity(
    *,
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
    account_id: str = UNIT_ACCOUNT_ID,
    **tenant_fields: Any,
) -> AccountIdentity:
    """A fabricated ``AccountIdentity`` — the type an account-taking ``_impl`` declares.

    ``tenant_fields`` are TenantContext fields the code under test reads off
    ``identity.tenant`` (``approval_mode``, ``human_review_required``, ...). They are read
    from the identity in production too — the resolver puts the row's values there — so
    stating them here substitutes for the row a mocked-DB test does not have. Use this
    ONLY where the database is mocked.
    """
    return PrincipalFactory.make_account_identity(
        PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id, **tenant_fields),
        Account(account_id=account_id, name="Unit Test Account", status="active"),
    )
