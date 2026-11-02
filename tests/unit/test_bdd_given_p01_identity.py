"""P01, the one authenticated-buyer Given, graded on both of its branches.

P01 (``tests/bdd/steps/generic/given_auth.py::given_buyer_authenticated``) carries
five sentences over one body. Four of them name no identity and are the 381 feature
lines that were already collapsed; the fifth — ``the Buyer is authenticated as
principal "P" on tenant "T"`` — names one, and no feature line uses it yet. An
unexercised branch is a claim, not a behaviour, so both are driven here:

- **unparameterized** must be byte-for-byte the collapsed body it replaced:
  ``ctx["has_auth"] = True`` plus ``ensure_tenant_principal``, and NOTHING else.
  A switch leaking into this path would silently re-point 381 scenarios.
- **parameterized** must re-point the env BEFORE seeding, because
  ``setup_default_data`` reads the env's own ids — seeding first and switching
  after would create one identity and authenticate as another.
"""

from __future__ import annotations

from typing import Any

from tests.bdd.steps.generic.given_auth import given_buyer_authenticated


class _FakeEnv:
    """Records the switch calls in order, and seeds from whatever ids are current."""

    def __init__(self) -> None:
        self.principal_id = "test_principal"
        self.tenant_id = "test_tenant"
        self.calls: list[str] = []

    def switch_principal(self, principal_id: str) -> None:
        self.principal_id = principal_id
        self.calls.append(f"switch_principal:{principal_id}")

    def switch_tenant(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.calls.append(f"switch_tenant:{tenant_id}")

    def setup_default_data(self) -> tuple[Any, Any]:
        # Mirrors IntegrationEnv.setup_default_data: the rows it creates are named
        # by the env's CURRENT ids, which is why order matters.
        self.calls.append(f"setup_default_data:{self.tenant_id}/{self.principal_id}")
        return f"tenant:{self.tenant_id}", f"principal:{self.principal_id}"


def test_unparameterized_p01_only_authenticates_and_seeds_the_default_identity() -> None:
    env = _FakeEnv()
    ctx: dict = {"env": env}

    given_buyer_authenticated(ctx)

    assert env.calls == ["setup_default_data:test_tenant/test_principal"]
    assert ctx["has_auth"] is True
    assert ctx["tenant"] == "tenant:test_tenant"
    assert ctx["principal"] == "principal:test_principal"


def test_parameterized_p01_switches_before_it_seeds() -> None:
    env = _FakeEnv()
    ctx: dict = {"env": env}

    given_buyer_authenticated(ctx, principal_id="buyer-B", tenant_id="tenant_b")

    assert env.calls == [
        "switch_tenant:tenant_b",
        "switch_principal:buyer-B",
        "setup_default_data:tenant_b/buyer-B",
    ]
    assert ctx["has_auth"] is True
    assert ctx["tenant"] == "tenant:tenant_b"
    assert ctx["principal"] == "principal:buyer-B"


def test_transport_is_accepted_and_cannot_pin_the_parametrized_transport() -> None:
    env = _FakeEnv()
    ctx: dict = {"env": env}

    given_buyer_authenticated(ctx, transport="A2A")

    assert env.calls == ["setup_default_data:test_tenant/test_principal"]
    assert "transport" not in ctx
