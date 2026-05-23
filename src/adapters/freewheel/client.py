"""High-level FreeWheel client.

Public facade composing :class:`FreeWheelInventoryClient` (v4 JSON inventory
taxonomy), :class:`FreeWheelCommercialClient` (v3 XML commercial entities),
and :class:`FreeWheelCreativeClient` (v4 creative_resources) behind a single
object so adapter code has one client to wire up.

Construct with either OAuth2 password-grant credentials (``username`` +
``password`` — canonical, auto-refreshing) or a pre-minted bearer
(``api_token`` — escape hatch for partner-provisioned tokens). See
:mod:`._transport` for HTTP details and exception classes.
"""

from __future__ import annotations

from typing import Any

import requests

from src.adapters.freewheel._commercial import FreeWheelCommercialClient
from src.adapters.freewheel._creatives import FreeWheelCreativeClient
from src.adapters.freewheel._forecasting import FreeWheelForecastingClient
from src.adapters.freewheel._inventory import FreeWheelInventoryClient
from src.adapters.freewheel._transport import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    STAGING_BASE_URL,
    FreeWheelAuthError,
    FreeWheelError,
    FreeWheelForbiddenError,
    FreeWheelNotFoundError,
    FreeWheelServerError,
    FreeWheelTransport,
    FreeWheelValidationError,
)

# Kept for callers that catch on the older name.
FreeWheelAPIError = FreeWheelError

__all__ = [
    "FreeWheelAPIError",
    "FreeWheelAuthError",
    "FreeWheelClient",
    "FreeWheelError",
    "FreeWheelForbiddenError",
    "FreeWheelNotFoundError",
    "FreeWheelServerError",
    "FreeWheelTransport",
    "FreeWheelValidationError",
    "DEFAULT_BASE_URL",
    "STAGING_BASE_URL",
]


class FreeWheelClient:
    """Composed FreeWheel API client.

    Use ``client.inventory`` for v4 inventory and ``client.commercial`` for
    v3 commercial entities. The top-level convenience methods cover the few
    operations consumed directly by the adapter layer (status checks); for
    anything else, reach into the namespaced clients.
    """

    def __init__(
        self,
        api_token: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ):
        self._transport = FreeWheelTransport(
            api_token=api_token,
            username=username,
            password=password,
            base_url=base_url,
            timeout=timeout,
            session=session,
        )
        self.inventory = FreeWheelInventoryClient(self._transport)
        self.commercial = FreeWheelCommercialClient(self._transport)
        self.creatives = FreeWheelCreativeClient(self._transport)
        self.forecasting = FreeWheelForecastingClient(self._transport)

    # ----- connectivity -----

    def token_info(self) -> dict[str, Any]:
        """Validate the bearer and return its metadata.

        200 here proves the token is recognised by FreeWheel's gateway and
        is associated with a real user. ``expires_in`` is seconds remaining
        before the token rolls.
        """
        return self._transport.token_info()

    # ----- adapter shims -----
    # Thin wrappers around the namespaced clients for code paths that just
    # need a single call. New code should prefer ``client.commercial.*`` and
    # ``client.inventory.*`` directly for clarity.

    def get_campaign(self, campaign_id: int | str) -> dict[str, Any]:
        """Fetch a campaign by id. Returns the Pydantic model dumped as a dict
        for compatibility with the existing adapter integration points."""
        return self.commercial.get_campaign(int(campaign_id)).model_dump(mode="json")
