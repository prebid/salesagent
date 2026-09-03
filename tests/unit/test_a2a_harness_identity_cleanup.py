"""Harness-contract: token-mode prepare drops unit-mode identity lambdas (#1780)."""

from __future__ import annotations

from tests.factories.principal import PrincipalFactory
from tests.harness._base import BaseTestEnv


class _A2APrepareEnv(BaseTestEnv):
    """Minimal env so ``_prepare_a2a_server_context`` is reachable without a domain."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def call_impl(self, **kwargs: object) -> None:
        raise NotImplementedError


def test_token_mode_prepare_clears_unit_mode_identity_lambdas() -> None:
    """Deleting the two pops in token-mode prepare must redden this oracle.

    Sequence: unit-mode prepare installs identity lambdas on the shared
    handler; token-mode prepare must drop them so a later real-token dispatch
    cannot silently reuse the previous caller's identity.
    """
    with _A2APrepareEnv(use_real_db=False) as env:
        handler = env.a2a_handler
        unit_identity = PrincipalFactory.make_identity(
            principal_id="unit_principal",
            tenant_id="unit_tenant",
            protocol="a2a",
            auth_token=None,
        )
        token_identity = PrincipalFactory.make_identity(
            principal_id="token_principal",
            tenant_id="token_tenant",
            protocol="a2a",
            auth_token="harness-contract-tok",
        )

        env._prepare_a2a_server_context(handler, unit_identity)
        assert "_resolve_a2a_identity" in handler.__dict__
        assert "_get_auth_token" in handler.__dict__

        env._prepare_a2a_server_context(handler, token_identity)
        assert "_resolve_a2a_identity" not in handler.__dict__
        assert "_get_auth_token" not in handler.__dict__
