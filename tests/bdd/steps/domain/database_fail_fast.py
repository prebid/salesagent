"""Steps for the local database fail-fast feature (local-database-fail-fast.feature).

Each Given makes the database fail the seller through the env's database-fault methods
(``tests/harness/database_faults.py``); nothing in the seller is patched. The tenants, the
requests and the product assertions are BR-SECURITY-002's steps, reused.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, when

from src.core.credentials import hash_token


@given("the database will cancel the seller's next statement")
def given_cancel_next_statement(ctx: dict) -> None:
    ctx["env"].interrupt_next_statement(terminate=False)


@given("the database will drop the connection serving the seller's next statement")
def given_drop_next_connection(ctx: dict) -> None:
    ctx["env"].interrupt_next_statement(terminate=True)


def _interrupt_credential_lookup(ctx: dict, tenant: str, *, terminate: bool) -> None:
    """Interrupt the seller's principal lookup for *tenant*'s token: the row stores its hash."""
    token = ctx["token_a"] if tenant == "A" else ctx["token_b"]
    ctx["env"].interrupt_next_statement(terminate=terminate, table="principals", sending=hash_token(token))


@given(parsers.parse('the database will cancel the seller\'s credential lookup for tenant "{tenant}"'))
def given_cancel_credential_lookup(ctx: dict, tenant: str) -> None:
    _interrupt_credential_lookup(ctx, tenant, terminate=False)


@given(
    parsers.parse('the database will drop the connection serving the seller\'s credential lookup for tenant "{tenant}"')
)
def given_drop_credential_lookup(ctx: dict, tenant: str) -> None:
    _interrupt_credential_lookup(ctx, tenant, terminate=True)


@given("the database refuses new connections")
def given_database_refuses_connections(ctx: dict) -> None:
    ctx["env"].refuse_new_connections()


@when("the database accepts new connections again")
def when_database_accepts_connections(ctx: dict) -> None:
    ctx["env"].accept_new_connections()
