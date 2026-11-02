"""Guard: a request DTO declares the pinned schema's fields and nothing else.

Critical pattern #1 says a DTO extends the SDK's request model rather than duplicating it.
The half that had no guard was the ADDING half: nothing stopped a local subclass from
declaring a property AdCP does not define, and once one does, the tool ADVERTISES it --
``_register_tool`` derives the announced shape from the DTO, so a hand-added field becomes a
parameter buyers can send and other sellers do not implement.

That is not hypothetical. ``ListAccountsRequest`` carried an ``idempotency_key`` for months.
It was added so one BDD step could construct the model in-process, it was documented at its
declaration as "not a spec field", and it then grew a predicate in the transport boundary
whose whole job was to detect and ignore it. Deleting the field deleted the predicate.

Membership is decided by walking the live MRO and unioning ``model_fields`` from every
ancestor defined under ``adcp`` -- so it consults no import spelling and needs no table of
which library type each DTO extends. REDECLARING an inherited field is not adding one and is
governed separately by ``test_architecture_schema_inheritance.py``; this guard sees only
names no SDK ancestor declares.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.core.tools.registry import TOOLS

#: Fields a DTO declares that no SDK ancestor does, with the reason each is allowed.
#: Ratcheting: this may only shrink. A new entry needs a reason a contributor can check.
ALLOWED_ADDITIONS: dict[str, dict[str, str]] = {
    "complete_task": {
        "task_id": "the pinned 3.1.1 tree describes no complete_task at all, so this DTO has "
        "no SDK parent to inherit from -- every field is 'added' by construction",
        "status": "ditto",
        "response_data": "ditto",
        "error_message": "ditto",
        "context": "ditto",
    },
}


def _sdk_declared_fields(model: type[BaseModel]) -> set[str]:
    """Every field name any ``adcp``-defined ancestor of ``model`` declares."""
    names: set[str] = set()
    for ancestor in model.__mro__[1:]:
        module = getattr(ancestor, "__module__", "")
        if module == "adcp" or module.startswith("adcp."):
            names |= set(getattr(ancestor, "model_fields", {}))
    return names


@pytest.mark.arch_guard
@pytest.mark.parametrize("tool_name", sorted(TOOLS))
def test_dto_declares_no_field_the_pinned_schema_lacks(tool_name: str) -> None:
    dto = TOOLS[tool_name].dto
    added = set(dto.model_fields) - _sdk_declared_fields(dto)
    unexplained = sorted(added - set(ALLOWED_ADDITIONS.get(tool_name, {})))
    assert unexplained == [], (
        f"{dto.__name__} declares {unexplained}, which no SDK ancestor does. The DTO IS the "
        f"pinned schema, and the tool advertises whatever it declares -- so a field added "
        f"here becomes a parameter buyers can send and no other seller implements. Remove it, "
        f"or add it to ALLOWED_ADDITIONS with a reason a contributor can check."
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize("tool_name", sorted(TOOLS))
def test_response_model_carries_the_protocol_envelope(tool_name: str) -> None:
    """Every response IS a ``ProtocolEnvelope`` -- the eleven fields, typed, on all fourteen.

    `core/protocol-envelope.json` is composed into every pinned response schema with `allOf`,
    so `status`, `task_id`, `message`, `replayed` and the rest are part of every response's
    contract. Nine of our models inherit the class through their SDK parent. The other five
    had to name it:

    * `sync_creatives`, `sync_accounts` -- the SDK's generated success BRANCH drops the
      composition, as it does on 19 of the 24 `*SuccessResponse` aliases at adcp 6.6, so they
      inherit it locally until adcontextprotocol/adcp-client-python#1136 is fixed;
    * `create_media_buy`, `update_media_buy` -- their wrapper `TaskResultEnvelope` is ours and
      descends from no SDK response, so it names the base directly;
    * `complete_task` -- the pin describes no such task, so there is no schema to inherit from.

    This is what lets `_boundary._response_model_for` be typed `type[ProtocolEnvelope]` and the
    boundary ASSIGN `result.replayed = True` rather than probing `model_fields` for it. A
    response model that loses the base makes that annotation a lie, and the replay marker goes
    silently missing on that tool -- which is exactly what happened before, on four of them.
    """
    from adcp.types import ProtocolEnvelope

    from src.core.tools._boundary import _response_model_for

    model = _response_model_for(TOOLS[tool_name].impl)
    assert model is not None, (
        f"{tool_name}'s implementation does not return a ProtocolEnvelope subclass, so the "
        f"boundary cannot revive a cached response for it and every retry re-executes silently"
    )
    missing = sorted(set(ProtocolEnvelope.model_fields) - set(model.model_fields))
    assert missing == [], f"{model.__name__} is missing envelope fields {missing}"


@pytest.mark.arch_guard
def test_the_allowlist_has_no_stale_entries() -> None:
    """An allowance whose field is gone must be removed, so the list can only shrink."""
    stale: list[str] = []
    for tool_name, fields in ALLOWED_ADDITIONS.items():
        dto = TOOLS[tool_name].dto
        added = set(dto.model_fields) - _sdk_declared_fields(dto)
        stale += [f"{tool_name}.{f}" for f in sorted(set(fields) - added)]
    assert stale == [], f"remove these -- the field is no longer added: {stale}"
