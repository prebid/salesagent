"""The account natural key cannot be duplicated by a second create.

Regression for salesagent-0njj — the create-side sibling of salesagent-8sfr.

8sfr closed the UPDATE path: ``operator``/``sandbox``/``brand`` are immutable, so
an existing account cannot be re-keyed. The CREATE path was still open. Nothing
enforced uniqueness of (tenant_id, operator, brand.domain, brand.brand_id,
sandbox) at either layer — ``Account.__table_args__`` carried only a NON-unique
``idx_accounts_operator``, and ``AccountRepository.create`` did no collision
check — so the admin create form could plant a SECOND account on a natural key a
buyer had already provisioned.

The fix is both layers: ``AccountRepository.create`` refuses an occupied key (the
message), and ``uq_accounts_natural_key`` enforces it (the invariant, and the only
thing that closes the race between two concurrent creates).

Why that is the same harm from the other side: every natural-key resolver reads
that tuple. ``sync_accounts`` resolves each entry with
``get_by_natural_key(...)``, which is ``.first()`` over an unordered query, and
``_resolve_by_natural_key`` detects ambiguity with
``list_by_natural_key(limit=2)``. Once two rows share a key, the first resolver
answers non-deterministically and the second reports the key as unresolvable.
The buyer cannot fix either from their side — they do not own the row the
operator added, and the natural key is the only handle their ``sync_accounts``
entry has.

The tests assert on the KEY being unambiguous rather than on "the re-sync still
returns the buyer's account_id". That second assertion passes today by accident:
with two rows present, ``.first()`` happens to return the older one under a
sequential scan, so it would grade the storage engine's row order rather than
our invariant, and would go green while the defect is fully present. The
ambiguity of the key is the thing production actually cannot survive.

Measured note for whoever writes the fix: the admin form passes ``sandbox or
None``, but the rows land as ``sandbox = false``, not NULL — the column carries
``default=False`` and SQLAlchemy applies a column default for an attribute whose
value is None. So NULL ``sandbox`` cannot be produced through the ORM today, and
the NULL-vs-false hazard the ticket flagged applies to LEGACY rows only (which is
also why ``get_by_natural_key`` filters ``sandbox IS NULL OR sandbox = false``).
That is why the index keys on ``COALESCE(sandbox, false)`` rather than the column:
a plain ``UNIQUE (..., sandbox)`` would treat a legacy NULL and a false as distinct
when every resolver treats them as one.
"""

import pytest

from src.core.database.repositories.account import AccountRepository, NaturalKey
from src.core.schemas.account import SyncAccountsRequest
from tests.factories.request import fresh_idempotency_key
from tests.harness.account_sync import AccountSyncEnv
from tests.harness.admin_accounts import AdminAccountEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_DOMAIN = "acme.com"
_OPERATOR = "example.com"


def _action(result) -> str:
    """Extract the string action from an enum-or-str response field."""
    action = result.action
    return action.value if hasattr(action, "value") else str(action)


def _provision(env, *, domain: str = _DOMAIN, operator: str = _OPERATOR) -> str:
    """Provision one account through the real sync path; return its account_id."""
    req = SyncAccountsRequest(
        idempotency_key=fresh_idempotency_key(),
        accounts=[{"brand": {"domain": domain}, "operator": operator, "billing": "operator"}],
    )
    result = env.call_impl(req=req).accounts[0]
    assert _action(result) == "created", f"setup precondition: expected a fresh create, got {_action(result)!r}"
    return result.account_id


def _admin_create(admin, *, name: str, domain: str = _DOMAIN, operator: str = _OPERATOR, **extra) -> object:
    """POST the REAL admin create form — the surface the bug names."""
    form = {"name": name, "brand_domain": domain, "operator": operator, "billing": "operator"}
    form.update(extra)
    return admin.post_create(form)


def _accounts_on_key(env, tenant_id: str, *, sandbox: bool | None = None) -> list:
    """Every account the natural-key resolvers see for the shared key.

    ``limit=5`` rather than the production ``limit=2``: the production call only
    needs to know "more than one", but a failure message that says how many rows
    actually collide is what tells a reader whether the fix under-shot.
    """
    repo = AccountRepository(env.get_session(), tenant_id=tenant_id)
    return repo.list_by_natural_key(
        operator=_OPERATOR,
        brand_domain=_DOMAIN,
        sandbox=sandbox,
        limit=5,
    )


def _describe(accounts) -> str:
    return str([(a.account_id, a.operator, a.brand, a.sandbox) for a in accounts])


class TestAdminCreateCannotDuplicateANaturalKey:
    """The create path must refuse a key that already resolves to an account."""

    def test_admin_create_on_an_existing_key_leaves_the_key_unambiguous(self, integration_db):
        """The bug as the buyer reaches it: their key stops resolving to one account.

        The assertion is on the surviving row count for the key, not on the HTTP
        status, because the fix may legitimately refuse with a flash-and-redirect
        rather than an error code. What is NOT negotiable is that the buyer's key
        still names exactly one account afterwards.
        """
        with (
            AccountSyncEnv(tenant_id="nku_t1", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t1") as admin,
        ):
            env.setup_default_data()
            original_id = _provision(env)

            admin.authenticate()
            _admin_create(admin, name="Acme (operator copy)")

            on_key = _accounts_on_key(env, "nku_t1")
            assert len(on_key) == 1, (
                f"admin create manufactured an ambiguous natural key: {len(on_key)} accounts now "
                f"match brand={_DOMAIN!r} operator={_OPERATOR!r} — {_describe(on_key)}. "
                "sync_accounts resolves this key with get_by_natural_key().first() and "
                "_resolve_by_natural_key reports it AMBIGUOUS; the buyer cannot repair either."
            )
            assert on_key[0].account_id == original_id, (
                f"the surviving account on the key is {on_key[0].account_id!r}, not the buyer's "
                f"{original_id!r} — the operator's create displaced the buyer's account"
            )

    def test_admin_create_collides_on_the_sandbox_key_too(self, integration_db):
        """``sandbox`` is the fifth key component, so the sandbox key collides too.

        Guards the fix against being written as a four-component check: a
        collision detector that ignored ``sandbox`` would pass the first test and
        still leave every sandbox account duplicable.
        """
        with (
            AccountSyncEnv(tenant_id="nku_t2", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t2") as admin,
        ):
            env.setup_default_data()
            # The seller must DECLARE sandbox support before it can provision a
            # sandbox account (#1721 flipped tenants.account_sandbox to default
            # False: support is opted into, never assumed from an unset column).
            env.configure_tenant_field("account_sandbox", True)
            req = SyncAccountsRequest(
                idempotency_key=fresh_idempotency_key(),
                accounts=[
                    {"brand": {"domain": _DOMAIN}, "operator": _OPERATOR, "billing": "operator", "sandbox": True}
                ],
            )
            result = env.call_impl(req=req).accounts[0]
            assert _action(result) == "created", f"setup precondition: got {_action(result)!r}"
            original_id = result.account_id

            admin.authenticate()
            _admin_create(admin, name="Acme sandbox (operator copy)", sandbox="on")

            on_key = _accounts_on_key(env, "nku_t2", sandbox=True)
            assert len(on_key) == 1, (
                f"admin create manufactured an ambiguous SANDBOX key: {len(on_key)} accounts match "
                f"brand={_DOMAIN!r} operator={_OPERATOR!r} sandbox=True — {_describe(on_key)}"
            )
            assert on_key[0].account_id == original_id

    def test_ticking_sandbox_is_a_different_key_and_must_still_be_creatable(self, integration_db):
        """A sandbox account is legitimately distinct from the production one.

        The fifth key component cuts both ways: the refusal must not collapse
        sandbox into the production key, or an operator could never stand up a
        sandbox account alongside a live one.
        """
        with (
            AccountSyncEnv(tenant_id="nku_t5", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t5") as admin,
        ):
            env.setup_default_data()
            _provision(env)

            admin.authenticate()
            response = _admin_create(admin, name="Acme sandbox", sandbox="on")

            assert response.status_code in (302, 303), (
                f"a sandbox account is a DIFFERENT natural key and must still be creatable, got {response.status_code}"
            )
            sandbox_rows = _accounts_on_key(env, "nku_t5", sandbox=True)
            assert len(sandbox_rows) == 1, (
                f"expected the sandbox key to hold exactly the new account, found {_describe(sandbox_rows)}"
            )

    def test_admin_create_on_a_free_key_still_succeeds(self, integration_db):
        """The refusal is scoped to a COLLIDING key — ordinary creates must work.

        Without this, refusing every admin create would satisfy the two tests
        above while breaking the create form outright.
        """
        with (
            AccountSyncEnv(tenant_id="nku_t3", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t3") as admin,
        ):
            env.setup_default_data()
            _provision(env)

            admin.authenticate()
            response = _admin_create(admin, name="Beta", domain="beta.com", operator="other.com")

            assert response.status_code in (302, 303), (
                f"a create on a FREE natural key must still succeed, got {response.status_code}"
            )
            repo = AccountRepository(env.get_session(), tenant_id="nku_t3")
            beta = repo.get_by_natural_key(NaturalKey.from_parts("beta.com", None, "other.com", None))
            assert beta is not None, "the non-colliding admin create did not persist an account"


class TestTheRefusalIsScopedToTheWholeKey:
    """brand_id is the fourth component — it must separate keys, not be ignored.

    Both accounts here are provisioned through the ADMIN form rather than through
    sync_accounts, unlike every other test in this file. That is not a shortcut:
    sync_accounts stringifies the ``BrandId`` RootModel with ``str()`` at four
    sites, so it PERSISTS ``brand->>'brand_id'`` as the literal ``"root='x'"``
    (filed as salesagent-myhs, P1, out of scope here). A sync-provisioned
    brand_id account therefore sits under a mangled key, and using it as the
    fixture would grade that defect instead of this one. When salesagent-myhs
    lands, these two can move back onto ``_provision``.
    """

    def test_a_different_brand_id_is_a_different_account(self, integration_db):
        """Two brands on one domain are legitimately two accounts.

        The AdCP key is brand.domain + brand.brand_id + operator + sandbox, so a
        collision check that compared only the domain would refuse a create the
        database itself accepts — a check disagreeing with its own index.
        """
        with (
            AccountSyncEnv(tenant_id="nku_t6", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t6") as admin,
        ):
            env.setup_default_data()
            admin.authenticate()
            first = _admin_create(admin, name="Acme brand one", brand_id="brand_one")
            assert first.status_code in (302, 303), f"setup precondition: got {first.status_code}"

            response = _admin_create(admin, name="Acme brand two", brand_id="brand_two")

            assert response.status_code in (302, 303), (
                f"a different brand_id is a DIFFERENT natural key and must be creatable, got {response.status_code}"
            )
            repo = AccountRepository(env.get_session(), tenant_id="nku_t6")
            assert len(repo.list_all()) == 2, (
                f"both brands must persist as separate accounts, found {_describe(repo.list_all())}"
            )

    def test_the_same_brand_id_collides(self, integration_db):
        """The mirror of the test above: same brand_id, same key, refused."""
        with (
            AccountSyncEnv(tenant_id="nku_t7", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t7") as admin,
        ):
            env.setup_default_data()
            admin.authenticate()
            first = _admin_create(admin, name="Acme brand one", brand_id="brand_one")
            assert first.status_code in (302, 303), f"setup precondition: got {first.status_code}"

            _admin_create(admin, name="Acme brand one (copy)", brand_id="brand_one")

            repo = AccountRepository(env.get_session(), tenant_id="nku_t7")
            on_key = repo.list_by_natural_key(operator=_OPERATOR, brand_domain=_DOMAIN, brand_id="brand_one", limit=5)
            assert len(on_key) == 1, f"same brand_id is the SAME key and must collide: {_describe(on_key)}"


class TestTheOperatorSeesAMessageNotACrash:
    """A refused create is an operator mistake with an obvious remedy."""

    def test_a_colliding_create_redirects_with_an_error_instead_of_500(self, integration_db):
        with (
            AccountSyncEnv(tenant_id="nku_t8", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t8") as admin,
        ):
            env.setup_default_data()
            original_id = _provision(env)

            admin.authenticate()
            response = _admin_create(admin, name="Acme (operator copy)")

            assert response.status_code in (302, 303), (
                f"the refusal must be a form error, not a crash — got {response.status_code}"
            )
            follow_up = admin.get_create_page()
            assert original_id.encode() in follow_up.data, (
                "the flashed error must name the account the operator should edit instead; "
                f"{original_id!r} was not rendered on the create page"
            )


class TestAClosedAccountStillOwnsItsKey:
    """Recorded on purpose, because the alternative is a silent behavior change.

    sync_accounts' get_by_natural_key does not filter on status, so a re-sync of
    a CLOSED account's key updates that row rather than creating a fresh one.
    The create refusal follows the same rule — closing an account does not free
    its natural key. If that is ever meant to change, it changes in both places
    and this test is what says so.
    """

    def test_closing_an_account_does_not_free_its_natural_key(self, integration_db):
        with (
            AccountSyncEnv(tenant_id="nku_t9", principal_id="agent_nku") as env,
            AdminAccountEnv(mode="integration", tenant_id="nku_t9") as admin,
        ):
            env.setup_default_data()
            original_id = _provision(env)

            repo = AccountRepository(env.get_session(), tenant_id="nku_t9")
            repo.update_status(original_id, "closed")

            admin.authenticate()
            _admin_create(admin, name="Acme (after close)")

            on_key = _accounts_on_key(env, "nku_t9")
            assert len(on_key) == 1, (
                f"a closed account still occupies its natural key — sync would resolve to it, "
                f"so a create must not add a second: {_describe(on_key)}"
            )
            assert on_key[0].account_id == original_id


class TestRepositoryRefusesADuplicateNaturalKey:
    """The mechanism, so a future caller that bypasses the admin form is refused too.

    8sfr put the natural-key protection in the repository rather than the form
    for exactly this reason: the sync create path and the admin create path are
    two callers today, and protection that lives in one form is protection only
    against that form.
    """

    def test_create_refuses_an_account_whose_natural_key_already_exists(self, integration_db):
        from src.core.database.models import Account

        with AccountSyncEnv(tenant_id="nku_t4", principal_id="agent_nku") as env:
            env.setup_default_data()
            _provision(env)

            repo = AccountRepository(env.get_session(), tenant_id="nku_t4")
            duplicate = Account(
                tenant_id="nku_t4",
                account_id="acc_manual_duplicate",
                name="Acme (bypassing the form)",
                status="active",
                brand={"domain": _DOMAIN},
                operator=_OPERATOR,
            )
            # NaturalKeyConflict is now an AdCPConflictError as well as a ValueError
            # (salesagent-rys3u.5). str(exc) is CODE_TABLE's buyer-facing sentence, so
            # the specifics are asserted where they now live: the operator text and
            # the structured account id. Both bases are still pinned -- ValueError
            # because the admin blueprint catches it, the AdCP code because it escapes
            # to the buyer when the conflict names a row that has since vanished.
            with pytest.raises(ValueError) as exc_info:
                repo.create(duplicate)
            assert "natural key" in exc_info.value.operator_message
            assert exc_info.value.error_code == "CONFLICT"


class TestTheDatabaseHoldsTheInvariant:
    """The check is the message; the index is the invariant.

    Two concurrent creates can both pass the repository check — only the unique
    index stops the second insert. The check is therefore not a substitute for
    the index, and an index that silently lost a key component (or its NULL
    mechanics) would leave the race open while every other test in this file
    stayed green, because they all stop at the check.
    """

    def test_the_natural_key_index_exists_with_every_component(self, integration_db):
        from sqlalchemy import text

        with AccountSyncEnv(tenant_id="nku_t10", principal_id="agent_nku") as env:
            env.setup_default_data()
            indexdef = (
                env.get_session()
                .execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_accounts_natural_key'"))
                .scalar()
            )

            assert indexdef is not None, (
                "uq_accounts_natural_key is missing — the repository check alone leaves the check-then-insert race open"
            )
            assert "UNIQUE INDEX" in indexdef, f"the natural-key index is not unique: {indexdef}"
            for component in ("tenant_id", "operator", "'domain'", "'brand_id'", "COALESCE"):
                assert component in indexdef, (
                    f"key component {component!r} missing from uq_accounts_natural_key: {indexdef}"
                )
            assert "NULLS NOT DISTINCT" in indexdef, (
                f"without NULLS NOT DISTINCT a NULL operator or NULL brand_id escapes uniqueness entirely: {indexdef}"
            )
            assert "WHERE" in indexdef and "domain" in indexdef.split("WHERE")[-1], (
                "the index must stay PARTIAL on brand.domain — an account with no brand has no "
                f"natural key, and constraining those forbids a shape the admin form allows: {indexdef}"
            )

    def test_keyless_accounts_are_not_constrained(self, integration_db):
        """Several accounts with no brand may coexist — they have no key to collide on."""
        from tests.factories import AccountFactory, TenantFactory

        with AccountSyncEnv(tenant_id="nku_t11", principal_id="agent_nku") as env:
            env.setup_default_data()
            tenant = TenantFactory(tenant_id="nku_keyless")
            AccountFactory(tenant=tenant, account_id="acc_keyless_1", name="Keyless 1")
            AccountFactory(tenant=tenant, account_id="acc_keyless_2", name="Keyless 2")

            repo = AccountRepository(env.get_session(), tenant_id="nku_keyless")
            assert len(repo.list_all()) == 2, (
                "brand-less accounts carry no natural key, so uniqueness must not apply to them"
            )


class TestHarnessSeedingObeysTheSameInvariant:
    """The BDD harness seeds through the repository, not around it."""

    def test_admin_harness_cannot_seed_a_duplicate_natural_key(self, integration_db):
        """``AdminAccountEnv.create_account`` refuses what production refuses.

        The helper used to build an ``Account`` and ``session.add`` it, which made
        it the one seam capable of establishing a state the production write path
        forbids: two accounts on one natural key. A scenario seeded that way would
        assert against a database no buyer could ever produce — green, and proving
        nothing. Routing it through ``AccountUoW`` -> ``AccountRepository.create``
        closes the seam, and this pins that it stays closed.
        """
        from src.core.database.repositories.account import NaturalKeyConflict

        with AdminAccountEnv(mode="integration", tenant_id="nku_t12") as admin:
            first = admin.create_account(name="Seeded First", brand_domain=_DOMAIN, operator=_OPERATOR)

            with pytest.raises(NaturalKeyConflict) as excinfo:
                admin.create_account(name="Seeded Duplicate", brand_domain=_DOMAIN, operator=_OPERATOR)

            assert first, "the first seed must succeed — only the duplicate is refused"
            assert excinfo.value.existing_account_id == first, (
                f"the conflict must name the account already holding the key ({first!r}), "
                f"got {excinfo.value.existing_account_id!r}"
            )

    def test_admin_harness_still_seeds_distinct_keys_freely(self, integration_db):
        """The refusal is scoped to a genuine collision, not to seeding in general.

        Without this, the test above would pass just as well if ``create_account``
        raised unconditionally.
        """
        with AdminAccountEnv(mode="integration", tenant_id="nku_t13") as admin:
            one = admin.create_account(name="Distinct A", brand_domain=_DOMAIN, operator=_OPERATOR)
            two = admin.create_account(name="Distinct B", brand_domain=_DOMAIN, operator="other-operator")
            three = admin.create_account(name="Keyless")

            assert len({one, two, three}) == 3, "three distinct-key seeds must all succeed"
