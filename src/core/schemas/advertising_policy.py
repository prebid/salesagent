"""The advertising policy a tenant saved: the shape of ``tenants.advertising_policy``.

The column is ``JSONType(model=AdvertisingPolicy)``, so every reader gets this model and no
reader gets a dict. A stored row is the complete policy the operator saved: the model
declares no defaults, so a key the operator did not save is a validation error rather
than a value the code supplies, and it forbids undeclared keys. ``None`` in the column
means the tenant has saved no policy.

The policy is implementation-only: AdCP has no advertising-policy object. The one wire
fact it feeds is ``description``, which ``get_adcp_capabilities`` echoes as
``portfolio.advertising_policies``.
"""

from pydantic import BaseModel, ConfigDict


class AdvertisingPolicy(BaseModel):
    """A tenant's advertising policy, exactly as the operator saved it."""

    model_config = ConfigDict(extra="forbid")

    #: Whether ``get_products`` checks a brief against this policy.
    enabled: bool
    #: Whether a brief the policy model flags as restricted is refused for review.
    require_manual_review: bool
    prohibited_advertisers: list[str]
    prohibited_categories: list[str]
    prohibited_tactics: list[str]
    default_prohibited_categories: list[str]
    default_prohibited_tactics: list[str]
    #: The policy in the operator's words, echoed on ``get_adcp_capabilities``.
    description: str | None
