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
    """Deleting the pops in token-mode prepare must redden this oracle.

    Sequence: unit-mode prepare installs identity lambdas on the shared
    handler; token-mode prepare must drop them so a later real-token dispatch
    cannot silently reuse the previous caller's identity.

    Also asserts no callable instance-dict shadows remain over class methods —
    a third injected attribute (e.g. ``_authenticate``) must redden even when
    the two named pops stay green.
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
        # Third shadow: a two-name pop list stays green without this inject;
        # structural cleanup must drop every callable instance override.
        handler._authenticate = lambda *a, **k: unit_identity  # type: ignore[method-assign]
        assert "_authenticate" in handler.__dict__

        env._prepare_a2a_server_context(handler, token_identity)
        assert "_resolve_a2a_identity" not in handler.__dict__
        assert "_get_auth_token" not in handler.__dict__
        assert "_authenticate" not in handler.__dict__
        # Any instance-dict callable that shadows a class method defeats the gate.
        assert {k for k in handler.__dict__ if callable(getattr(type(handler), k, None))} == set()


def test_a2a_wire_task_slot_populated_without_proto_slot() -> None:
    """Served wire Task lands in ``last_a2a_wire_task``; protobuf slot stays empty.

    Replaces the tautological "two attributes are distinct storage" asserts with a
    real dispatch read-back so a crossed wire/proto assignment reddens.
    """
    from a2a.types import Task, TaskState, TaskStatus

    from src.a2a_server.adcp_a2a_server import _TaskOwner
    from tests.a2a_helpers import OWNED_TASK_ID, owned_task_owner_identity

    with _A2APrepareEnv(use_real_db=False) as env:
        handler = env.a2a_handler
        owner = owned_task_owner_identity()
        handler.tasks[OWNED_TASK_ID] = Task(
            id=OWNED_TASK_ID,
            context_id="ctx",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        handler._task_owners[OWNED_TASK_ID] = _TaskOwner(
            tenant_id=owner.tenant_id or "",
            principal_id=owner.principal_id or "",
        )
        served = env.run_a2a_task_method("tasks/get", OWNED_TASK_ID, identity=owner)
        assert served is not None
        assert env.last_a2a_wire_task is not None
        assert env.last_a2a_wire_task.id == OWNED_TASK_ID
        assert env.last_a2a_task is None
