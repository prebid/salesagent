"""Helper functions for working with generated schemas.

This module provides convenience functions for constructing complex generated schemas
without losing type safety. Unlike adapters (which wrap schemas in dict[str, Any]),
these helpers work directly with the generated Pydantic models.

Philosophy:
- Generated schemas are the source of truth (always in sync with AdCP spec)
- Helpers make construction easier without sacrificing type safety
- Custom logic (validators, conversions) lives here, not in wrapper classes
"""

import logging
from typing import Any

# FIXME(#1388): GetProductsResponse, Product have local subclasses; import from src.core.schemas.
from adcp import CreativeFilters, GetProductsResponse, Product

# FIXME(#1388): ProductFilters has a local subclass; import from src.core.schemas.
from adcp.types import (
    BrandReference,
    ReportingWebhook,
)
from pydantic import BaseModel

from src.core.schemas.notification import PushNotificationConfig
from src.core.schemas.product import GetProductsRequest
from src.core.validation_helpers import adcp_validation_boundary

logger = logging.getLogger(__name__)


def _coerce_wire_object[ModelT: BaseModel](
    value: Any,
    model_cls: type[ModelT],
    field_prefix: str | None = None,
) -> ModelT | None:
    """Shared dict → typed-model coercion with the boundary BUILT IN.

    Single home for the ``to_*`` helpers' isinstance ladder. The internal
    ``adcp_validation_boundary`` means a malformed wire dict rejects as a
    typed ``AdCPValidationError`` (message + field + top-level suggestion)
    from EVERY call site — callers cannot forget the boundary
    (#1417; mirrors ``coerce_creative_filters``).

    Returns ``None`` for non-dict unexpected types. That fallback is why the ``to_*``
    coercion helpers are gone: on A2A it turned a request naming an account the pinned
    ``core/account-ref.json`` does not permit into a request with NO account scope --
    no authorization against that account, and a different idempotency scope -- where
    MCP and REST raised on the same bytes. The one surviving caller,
    ``to_push_notification_config``, is reached through
    ``require_push_notification_config``, which raises on a missing config rather than
    proceeding without one.
    """
    if value is None or isinstance(value, model_cls):
        return value
    if isinstance(value, dict):
        with adcp_validation_boundary(field_prefix=field_prefix):
            # model_validate handles plain models and RootModels alike
            # (AccountReference is a RootModel — field-unpacking would break it).
            return model_cls.model_validate(value)
    return None  # Fallback for unexpected types


def to_push_notification_config(
    config: dict[str, Any] | PushNotificationConfig | None,
    *,
    field_prefix: str = "push_notification_config",
) -> PushNotificationConfig | None:
    """Convert dict to PushNotificationConfig for adcp type compatibility.

    ``field_prefix`` defaults HERE rather than at the call sites: five callers
    each remembering the same string literal is the remembered-call shape this
    epic exists to delete, and the sixth caller is where the divergence comes
    back. A refusal from this funnel therefore names
    ``push_notification_config.authentication.credentials`` — the path into the
    document the buyer actually sent — which is what FastMCP already emits (it
    validates the whole argument model, so its pydantic loc carries the parameter
    name) and what the registration gate raises. This converges REST and A2A onto
    the spelling MCP and the ingest gate already use; it is not a third one.

    Scope note: the broader prefix inconsistency across every field this
    validator reports is gh-#1895 and stays open — this narrows exactly one
    helper's one field.
    """
    return _coerce_wire_object(
        config,
        PushNotificationConfig,
        field_prefix=field_prefix,
    )


def require_push_notification_config(
    config: dict[str, Any] | PushNotificationConfig,
    *,
    field_prefix: str = "push_notification_config",
) -> PushNotificationConfig:
    """:func:`to_push_notification_config` for a caller that HAS a config.

    Same funnel, same refusals, same field paths -- the only difference is that
    ``None`` is not in the domain, so the result is not ``| None`` and a caller
    has nothing to narrow.

    The optional version exists because some callers legitimately hold "maybe a
    config"; the trouble was that callers who did NOT then had to prove the
    absence away, and two of them did it with a bare ``assert``. Under
    ``python -O`` an assert is deleted, so a function annotated as never
    returning ``None`` returned it. Stating the requirement in the SIGNATURE is
    what removes the narrowing rather than making it survive an interpreter
    flag.
    """
    coerced = to_push_notification_config(config, field_prefix=field_prefix)
    if coerced is None:
        # Unreachable via the annotated domain; a runtime guard rather than an
        # assert so it cannot be optimised away, and so a caller that passed
        # ``None`` through an ``Any`` gets a named failure instead of one
        # deferred to whatever first dereferences the result.
        raise ValueError(f"{field_prefix} is required but resolved to None")
    return coerced


# Re-export commonly used generated types for convenience


__all__ = [
    "require_push_notification_config",
    "to_push_notification_config",
    # Re-export types for type hints
    "BrandReference",
    "CreativeFilters",
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
    "ReportingWebhook",
]
