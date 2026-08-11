"""JSONType column serialization for account persistence.

These normalize typed models (or the dicts a wire request carries) into the plain
JSON-serializable shapes the ``accounts`` table's JSONType columns store, and they
normalize BOTH sides of a sync comparison so an unchanged field does not read as
changed (``AnyUrl != str``).

They live at the data layer, not in ``src/core/tools/accounts.py``, because that is
what they are: persistence normalization. Keeping them next to ``_sync_accounts_impl``
put ``.model_dump()`` inside the business-logic call graph, which is precisely what
``tests/unit/test_architecture_no_model_dump_in_impl.py`` exists to prevent -- and it
went unnoticed because that guard matched function NAMES rather than the call graph
(#1721 review F5).

Distinct from the write-only-field SCRUBBERS in ``src/core/tools/accounts.py``: those
take a model and return a model, shaping the response echo, and belong with the two
echo chokepoints they guard.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from adcp.types import NotificationConfig
from adcp.types.generated_poc.core.business_entity import BusinessEntity
from pydantic import BaseModel

__all__ = [
    "serialize_business_entity",
    "serialize_governance_agents",
    "serialize_notification_configs",
    "serialize_typed_list",
]


def serialize_typed_list(
    items: Iterable[BaseModel | Mapping[str, object]] | None, model: type[BaseModel]
) -> list[dict[str, object]] | None:
    """Normalize a list of typed models (or dicts) to JSON-serializable dicts.

    Both dict and model inputs go through ``model_dump(mode="json")`` so that
    comparison is type-stable — without it ``AnyUrl != str`` and an unchanged
    field reads as changed on every sync.

    ``None`` and ``[]`` are preserved as distinct results: for
    ``notification_configs`` they mean "never configured" and "explicitly
    cleared", which the wire must tell apart.
    """
    if items is None:
        return None
    result: list[dict[str, object]] = []
    for item in items:
        if isinstance(item, dict):
            # Validate through the model to normalize types (AnyUrl -> str, etc.)
            result.append(model.model_validate(item).model_dump(mode="json"))
        elif hasattr(item, "model_dump"):
            result.append(item.model_dump(mode="json"))
        else:
            result.append(dict(item))
    return result


def serialize_governance_agents(
    agents: Iterable[BaseModel | Mapping[str, object]] | None,
) -> list[dict[str, object]] | None:
    """Convert GovernanceAgent models to JSON-serializable dicts for DB storage."""
    from adcp.types.generated_poc.core.account import GovernanceAgent  # TODO: no stable alias in adcp.types

    return serialize_typed_list(agents, GovernanceAgent)


def serialize_notification_configs(
    configs: Iterable[BaseModel | Mapping[str, object]] | None,
) -> list[dict[str, object]] | None:
    """Convert NotificationConfig models to JSON-serializable dicts for DB storage."""
    return serialize_typed_list(configs, NotificationConfig)


def serialize_business_entity(entity: BusinessEntity | Mapping[str, object] | None) -> dict[str, object] | None:
    """Normalize a ``billing_entity`` (model or dict) to a JSON-serializable dict."""
    if entity is None:
        return None
    if hasattr(entity, "model_dump"):
        return entity.model_dump(mode="json", exclude_none=True)
    return dict(entity)
