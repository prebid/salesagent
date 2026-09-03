"""Bind production's buy-keyed sandbox seam onto a mocked UnitOfWork.

Four test modules built this stub independently under four names (``_uow_with``,
``_seam_for``, ``_make_uow``, and inline in the update harness). The repo stubs each
one wires differ legitimately; the seam binding at the centre was the same four lines
plus four restatements of the same rationale.

The rationale, stated once: left as a plain ``MagicMock``, ``sandbox_mode*`` returns a
``MagicMock`` — truthy, not a ``bool``. Every downstream sandbox assertion then reads
"sandbox" no matter what the account says, so the test grades nothing while looking
green. ``sandbox_modes()`` in the sibling module rejects a non-bool for the same
reason, at the other end of the same pipe. Binding production's own methods keeps
these tests oracles for the seam rather than for a reimplementation of it.
"""

from __future__ import annotations

from types import MethodType


def bind_real_sandbox_seam[T](uow: T) -> T:
    """Attach the real ``sandbox_mode`` / ``sandbox_mode_by_id`` to *uow*; returns it.

    The mixin is imported here rather than at module scope because several callers
    patch the UoW classes in ``src.core.database.repositories.uow`` at their source, and
    a module-scope import would bind the pre-patch object.

    Bound off ``BuyKeyedSandboxMixin`` rather than a concrete UoW deliberately: the
    deferred creative-push path holds an ``AdminCreativeUoW`` and the admin detail route
    holds no UoW at all, so naming a concrete class here would reintroduce exactly the
    coupling the mixin exists to remove.
    """
    from src.core.database.repositories.uow import BuyKeyedSandboxMixin

    uow.sandbox_mode = MethodType(BuyKeyedSandboxMixin.sandbox_mode, uow)
    uow.sandbox_mode_by_id = MethodType(BuyKeyedSandboxMixin.sandbox_mode_by_id, uow)
    return uow
