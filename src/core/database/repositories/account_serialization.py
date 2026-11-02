"""JSONType column serialization for account persistence.

These normalize typed models (or the dicts a wire request carries) into the plain
JSON-serializable shapes the ``accounts`` table's JSONType columns store, and they
normalize BOTH sides of a sync comparison so an unchanged field does not read as
changed (``AnyUrl != str``).

They live at the data layer, not in ``src/core/tools/accounts.py``, because that is
what they are: persistence normalization. Keeping them next to ``_sync_accounts_impl``
put ``.model_dump()`` inside the business-logic call graph, which is precisely what the
serialization edge rules (``ruff-serialization.toml``,
``.ast-grep/rules/serialize-only-at-the-edges.yml``) exist to prevent -- and it went
unnoticed because the guard those rules replaced matched function NAMES rather than the
call graph (#1721 review F5).

The reverse direction lives here too: ``account_from_row`` is the ONE place a persisted
``Account`` row becomes the schema ``Account``, and the write-only-field scrubbers it
applies (``scrub_notification_credentials``, ``scrub_business_entity``) sit beside it.
Three readers turn a row into the schema object -- ``list_accounts``'s echo, the
``sync_accounts`` result, and the resolver, which puts the resolved account on the
identity -- and the scrub is part of the conversion, not of any one caller, so a new
reader cannot leak a credential by forgetting it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from adcp.types.generated_poc.core.business_entity import BusinessEntity
from pydantic import BaseModel

from src.core.schemas.notification import NotificationConfig

if TYPE_CHECKING:
    from src.core.database.models import Account as AccountRow
    from src.core.schemas.account import Account

__all__ = [
    "account_from_row",
    "as_json_dict",
    "scrub_business_entity",
    "scrub_notification_credentials",
    "serialize_business_entity",
    "serialize_governance_agents",
    "serialize_notification_configs",
    "serialize_typed_list",
]


def as_json_dict(value: BaseModel | Mapping[str, object], *, exclude_none: bool = False) -> dict[str, object]:
    """A pydantic model or a mapping, as a JSON-serializable dict.

    The ONE place the "model or plain mapping?" question is answered. Four sites
    across accounts.py and this module each asked it with their own
    ``hasattr(x, "model_dump")``, which is four chances for one of them to
    forget a flag (``mode="json"``, ``exclude_none``) and serialize differently
    from the others.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=exclude_none)
    return dict(value)


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
        else:
            result.append(as_json_dict(item))
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
    return as_json_dict(entity, exclude_none=True)


def scrub_notification_credentials(
    configs: Iterable[BaseModel | Mapping[str, object]] | None,
) -> list[NotificationConfig] | None:
    """Strip write-only ``authentication.credentials`` from an echoed subscriber set.

    ``credentials`` is ``minLength: 32`` and documented write-only: the seller
    stores it to authenticate its own outbound calls and MUST NOT reflect it.
    Applied wherever a persisted config becomes a schema object -- ``account_from_row``
    and the ``sync_accounts`` result -- rather than at each call site, so a future echo
    path cannot forget it.

    Returns ``None`` for ``None`` and ``[]`` for ``[]``: "never configured" and
    "explicitly cleared" are different states to the buyer.
    """
    if configs is None:
        return None
    scrubbed: list[NotificationConfig] = []
    for config in configs:
        data = as_json_dict(config)
        auth = data.get("authentication")
        if isinstance(auth, dict) and "credentials" in auth:
            auth = {k: v for k, v in auth.items() if k != "credentials"}
            data["authentication"] = auth
        scrubbed.append(NotificationConfig.model_validate(data))
    return scrubbed


def scrub_business_entity(entity: BusinessEntity | Mapping[str, object] | None) -> BusinessEntity | None:
    """Strip write-only ``bank`` from an echoed ``billing_entity``.

    The response account item documents ``billing_entity`` as "echoed from the
    request ... **Bank details are omitted (write-only)**" (v3.1.1
    sync-accounts-response.json). Same placement rationale as
    :func:`scrub_notification_credentials`: applied where a persisted entity becomes a
    schema object, so a future echo path cannot leak by forgetting a call.
    """
    if entity is None:
        return None
    data = as_json_dict(entity, exclude_none=True)
    data.pop("bank", None)
    return BusinessEntity.model_validate(data)


def account_from_row(row: AccountRow) -> Account:
    """The schema ``Account`` for a persisted row: the one row-to-model conversion.

    Write-only fields are scrubbed here, so every consumer -- the ``list_accounts`` echo
    and the resolved account on a ``ResolvedIdentity`` alike -- holds the same shape.
    """
    from src.core.schemas.account import Account

    return Account(
        account_id=row.account_id,
        name=row.name,
        status=row.status,
        advertiser=row.advertiser,
        billing_proxy=row.billing_proxy,
        brand=row.brand,
        operator=row.operator,
        billing=row.billing,
        rate_card=row.rate_card,
        payment_terms=row.payment_terms,
        credit_limit=row.credit_limit,
        setup=row.setup,
        account_scope=row.account_scope,
        governance_agents=row.governance_agents,
        sandbox=row.sandbox,
        notification_configs=scrub_notification_credentials(row.notification_configs),
        billing_entity=scrub_business_entity(row.billing_entity),
        ext=row.ext,
    )
