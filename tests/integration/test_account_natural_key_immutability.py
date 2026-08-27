"""The account natural key cannot be mutated out from under sync_accounts.

Regression for salesagent-8sfr.

``AccountRepository.get_by_natural_key`` resolves a buyer's ``sync_accounts``
entry to an existing account by (tenant_id, operator, brand.domain[, brand_id],
sandbox). Two of those components — ``operator`` and ``sandbox`` — were writable
through ``update_fields``, and the admin edit form wrote both. Changing either
silently RE-KEYS the account: the next buyer sync carrying the ORIGINAL natural
key no longer matches, so it provisions a DUPLICATE account for the same
brand+operator, and the buyer's existing account_id quietly stops being the one
their syncs maintain.

The consequence tests below assert that the buyer still gets ONE account rather
than only that the repository raises, because "the repository raises" is a claim
about our code while "the buyer's account still resolves" is the claim that
matters. They drive the REAL admin endpoint through ``AdminAccountEnv``: calling
``update_fields`` directly inside ``pytest.raises`` would go vacuous the moment
the fix landed, proving "no duplicate" by an exception rather than by the admin
operation being safe. The mechanism gets its own tests so a future caller that
bypasses the form is still refused.

Scope note: ``brand`` is the third natural-key component. No write path mutates
it (neither create path goes through ``update_fields``), so it has no
consequence test here — but it IS protected, and the guard in
tests/unit/test_architecture_natural_key_immutability.py is what keeps the
protection complete as the key evolves.
"""

import pytest

from src.core.database.repositories.account import AccountRepository
from src.core.schemas.account import SyncAccountsRequest
from tests.harness.account_sync import AccountSyncEnv
from tests.harness.admin_accounts import AdminAccountEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _action(result) -> str:
    """Extract the string action from an enum-or-str response field."""
    action = result.action
    return action.value if hasattr(action, "value") else str(action)


def _provision(env, *, domain: str, operator: str) -> str:
    """Provision one account through the real sync path; return its account_id."""
    req = SyncAccountsRequest(accounts=[{"brand": {"domain": domain}, "operator": operator, "billing": "operator"}])
    response = env.call_impl(req=req)
    result = response.accounts[0]
    assert _action(result) == "created", f"setup precondition: expected a fresh create, got {_action(result)!r}"
    return result.account_id


def _resync(env, *, domain: str, operator: str):
    """Re-send the SAME natural key a buyer originally provisioned with."""
    req = SyncAccountsRequest(accounts=[{"brand": {"domain": domain}, "operator": operator, "billing": "operator"}])
    return env.call_impl(req=req).accounts[0]


def _assert_edit_succeeded(response, tenant_id: str, account_id: str) -> None:
    """The admin edit must redirect to the account detail page.

    Asserting the LOCATION, not just a 3xx: "Account not found" also redirects,
    so a bare status check cannot tell a successful edit from a no-op — and a
    no-op would make the re-key assertions below pass for the wrong reason.
    """
    assert response.status_code in (302, 303), (
        f"admin edit must still succeed for an operator, got {response.status_code}"
    )
    location = response.headers.get("Location", "")
    assert f"/accounts/{account_id}" in location, (
        f"expected a redirect to the account detail page, got Location={location!r} "
        "(the 'Account not found' path also 302s -- a bare status check would not notice)"
    )


class TestNaturalKeyComponentsAreImmutable:
    """update_fields refuses the natural-key components (the fix's mechanism)."""

    def test_sandbox_cannot_be_updated(self, integration_db):
        with AccountSyncEnv(tenant_id="nk_t1", principal_id="agent_nk") as env:
            env.setup_default_data()
            account_id = _provision(env, domain="acme.com", operator="example.com")

            repo = AccountRepository(env.get_session(), tenant_id="nk_t1")
            with pytest.raises(ValueError, match="sandbox"):
                repo.update_fields(account_id, sandbox=True)

    def test_operator_cannot_be_updated(self, integration_db):
        with AccountSyncEnv(tenant_id="nk_t2", principal_id="agent_nk") as env:
            env.setup_default_data()
            account_id = _provision(env, domain="acme.com", operator="example.com")

            repo = AccountRepository(env.get_session(), tenant_id="nk_t2")
            with pytest.raises(ValueError, match="operator"):
                repo.update_fields(account_id, operator="other.com")

    def test_brand_cannot_be_updated(self, integration_db):
        """The third key component. No caller writes it today -- that is the point.

        The Account bug was latent for exactly as long as nothing wrote sandbox.
        Protection that depends on the absence of a caller is not protection.
        """
        with AccountSyncEnv(tenant_id="nk_t7", principal_id="agent_nk") as env:
            env.setup_default_data()
            account_id = _provision(env, domain="acme.com", operator="example.com")

            repo = AccountRepository(env.get_session(), tenant_id="nk_t7")
            with pytest.raises(ValueError, match="brand"):
                repo.update_fields(account_id, brand={"domain": "other.com"})

    def test_mutable_settings_still_update(self, integration_db):
        """The guard is scoped to the KEY -- ordinary settings must still write.

        Without this, making every field immutable would pass the tests above
        while breaking the admin edit form entirely.
        """
        with AccountSyncEnv(tenant_id="nk_t3", principal_id="agent_nk") as env:
            env.setup_default_data()
            account_id = _provision(env, domain="acme.com", operator="example.com")

            repo = AccountRepository(env.get_session(), tenant_id="nk_t3")
            updated = repo.update_fields(account_id, payment_terms="net_45", name="Renamed")

            assert updated.payment_terms == "net_45"
            assert updated.name == "Renamed"


class TestAdminEditDoesNotOrphanTheAccount:
    """The consequence, driven through the surface the bug actually names.

    Uses ``AdminAccountEnv`` -- the same harness the BR-ADMIN-ACCOUNTS BDD
    scenarios drive -- rather than a private Flask client, so the login and the
    edit POST have exactly one implementation.
    """

    def test_toggling_sandbox_in_the_admin_form_does_not_rekey(self, integration_db):
        """An operator ticking the sandbox checkbox must not orphan the account.

        The defect as the buyer experiences it: after that toggle the next sync
        stopped matching and provisioned a SECOND account for the same
        brand+operator, silently stranding the account_id the buyer holds.
        """
        with (
            AccountSyncEnv(tenant_id="nk_t4", principal_id="agent_nk") as env,
            AdminAccountEnv(mode="integration", tenant_id="nk_t4") as admin,
        ):
            env.setup_default_data()
            original_id = _provision(env, domain="acme.com", operator="example.com")

            admin.authenticate()
            response = admin.post_edit(
                original_id,
                {"name": "Acme", "operator": "example.com", "billing": "operator", "sandbox": "on"},
            )
            _assert_edit_succeeded(response, "nk_t4", original_id)

            result = _resync(env, domain="acme.com", operator="example.com")

            assert _action(result) in ("unchanged", "updated"), (
                f"re-sync of the original natural key must resolve to the SAME account, "
                f"got action {_action(result)!r} (a 'created' here IS the duplicate defect)"
            )
            assert result.account_id == original_id, (
                f"buyer's account_id changed: {original_id!r} -> {result.account_id!r}; "
                "the account was re-keyed and orphaned from its own sync"
            )

            # list_all, not list_by_natural_key: a duplicate born of a re-key sits
            # under a DIFFERENT key by construction, so a key-scoped query is exactly
            # the query that cannot see it.
            repo = AccountRepository(env.get_session(), tenant_id="nk_t4")
            surviving = repo.list_all()
            assert len(surviving) == 1, (
                f"expected exactly ONE account for acme.com c/o example.com, found {len(surviving)}: "
                f"{[(a.account_id, a.operator, a.sandbox) for a in surviving]}"
            )

    def test_changing_operator_in_the_admin_form_does_not_rekey(self, integration_db):
        """Same defect via the second natural-key component the form wrote.

        Lower live severity than the sandbox arm: the template already rendered
        ``operator`` readonly in edit mode, so reaching this needed a crafted
        POST rather than ordinary UI use. Readonly is client-side only, and the
        handler accepted the field -- which is why the guard belongs server-side.
        """
        with (
            AccountSyncEnv(tenant_id="nk_t5", principal_id="agent_nk") as env,
            AdminAccountEnv(mode="integration", tenant_id="nk_t5") as admin,
        ):
            env.setup_default_data()
            original_id = _provision(env, domain="acme.com", operator="example.com")

            admin.authenticate()
            response = admin.post_edit(
                original_id,
                {"name": "Acme", "operator": "renamed.com", "billing": "operator"},
            )
            _assert_edit_succeeded(response, "nk_t5", original_id)

            result = _resync(env, domain="acme.com", operator="example.com")

            assert result.account_id == original_id, (
                f"buyer's account_id changed: {original_id!r} -> {result.account_id!r}"
            )
            repo = AccountRepository(env.get_session(), tenant_id="nk_t5")
            surviving = repo.list_all()
            assert len(surviving) == 1, (
                f"expected exactly ONE account, found {len(surviving)}: "
                f"{[(a.account_id, a.operator, a.sandbox) for a in surviving]}"
            )

    def test_admin_can_still_edit_the_settings_it_owns(self, integration_db):
        """The guard is scoped to the KEY, not to the form.

        Owner decision (2026-07-27): `billing` STAYS editable here. The AdCP
        settings-update restriction on it
        (sync-accounts-request.json #/properties/accounts/items/oneOf/1/allOf/2)
        binds the BUYER wire, not the seller operating on their own account.
        Without this test, "fix the re-key" could quietly be implemented by
        freezing the whole form.

        This also proves the admin app writes to the same database the sync path
        reads, which is what stops the two tests above from passing on a silent
        no-op.
        """
        with (
            AccountSyncEnv(tenant_id="nk_t6", principal_id="agent_nk") as env,
            AdminAccountEnv(mode="integration", tenant_id="nk_t6") as admin,
        ):
            env.setup_default_data()
            account_id = _provision(env, domain="acme.com", operator="example.com")

            admin.authenticate()
            response = admin.post_edit(
                account_id,
                {"name": "Renamed Account", "billing": "agent", "payment_terms": "net_45"},
            )
            _assert_edit_succeeded(response, "nk_t6", account_id)

            repo = AccountRepository(env.get_session(), tenant_id="nk_t6")
            account = repo.get_by_id(account_id)
            assert account.name == "Renamed Account"
            assert account.billing == "agent"
            assert account.payment_terms == "net_45"
