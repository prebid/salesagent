"""Broadstreet Ads API client wrapper.

Handles authentication and HTTP requests to the Broadstreet API.
Base URL: https://api.broadstreetads.com/api/0/
Auth: Access token passed as query parameter.
"""

import logging
from types import MappingProxyType
from typing import Any, NoReturn

from pydantic import JsonValue

from src.adapters.vendor_http import VendorHttpClient
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAdapterResourceNotFoundError,
    AdCPAuthorizationError,
    AdCPSalesAgentError,
)
from src.core.security.outbound_http import (
    OperatorEndpoint,
    OutboundDeliveryFailed,
    OutboundError,
    QueryParams,
)

logger = logging.getLogger(__name__)


def _raise_broadstreet_error(exc: OutboundError) -> NoReturn:
    """Re-raise an egress-seam failure as the AdCP error the upstream status warrants.

    AdCP 3.1.1 ``transport-errors.mdx`` Rule 1 mandates translating a vendor's
    HTTP status into an AdCP code. Three rows are Broadstreet's own, because a
    vendor ad server's 4xx carries RESOURCE semantics the shared
    operator-endpoint table cannot express -- it reads every non-429 4xx as
    ``CONFIGURATION_ERROR``, "this deployment is misconfigured":

    * 403 is the access token being denied -> ``PERMISSION_DENIED``.
    * 404 is an advertiser, campaign, advertisement or zone the ad server says
      does not exist -> ``REFERENCE_NOT_FOUND``, the case
      :class:`AdCPAdapterResourceNotFoundError` was minted for.
    * any other terminal 4xx -> ``AdCPAdapterError``.

    Every remaining row delegates to
    :func:`~src.core.helpers.outbound_error_mapping.raise_mapped_outbound_error`,
    which already produces exactly the classes this client wants -- 429 ->
    ``AdCPRateLimitError`` carrying the clamped ``retry_after``; a 5xx or a dial
    that never reached the wire -> the seam's own
    ``AdCPServiceUnavailableError``, re-raised with its ``attempts``/
    ``last_status`` intact; an egress-policy refusal ->
    ``AdCPConfigurationError``. Copying those rows here is the drift that module
    exists to prevent, so they are not copied. 429 is tested BEFORE the 4xx
    range below for the same reason: the range then needs no second copy of the
    retryable-status set to exclude it.

    The vendor's response BODY appears in none of these errors. It used to ride
    in ``internal_detail`` (non-wire by construction, because a third party's
    body has no provenance guarantee -- AdCP 3.1.1 Security Considerations
    MUST-NOT list); the egress seam now declines to carry a counterparty's error
    body back at all, so operators keep the status and lose the vendor's message
    text.

    Imported inside the function, not at module level: ``src.core.helpers``'s
    package ``__init__`` pulls in ``adapter_helpers``, which imports the
    adapters -- including the one that owns this client.
    """
    status = exc.http_status if isinstance(exc, OutboundDeliveryFailed) else None

    error: AdCPSalesAgentError[Any] | None = None
    if status == 403:
        error = AdCPAuthorizationError(internal_detail=exc)
    elif status == 404:
        error = AdCPAdapterResourceNotFoundError(internal_detail=exc)
    elif status == 429:
        # Owned by the shared table (see the docstring): delegating keeps the
        # clamped retry_after a locally-built AdCPRateLimitError would drop.
        pass
    elif status is not None and 400 <= status < 500:
        error = AdCPAdapterError(internal_detail=exc)

    if error is not None:
        raise error from exc

    from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error

    raise_mapped_outbound_error(exc, provenance=OperatorEndpoint("Broadstreet"), logger=logger)


class BroadstreetClient:
    """Client for interacting with the Broadstreet Ads API.

    Attributes:
        access_token: API access token for authentication
        network_id: Broadstreet network ID
        base_url: API base URL (default: https://api.broadstreetads.com/api/0)
        timeout: Request timeout in seconds
    """

    DEFAULT_BASE_URL = "https://api.broadstreetads.com/api/0"
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        access_token: str,
        network_id: str,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        """Initialize the Broadstreet client.

        Args:
            access_token: API access token
            network_id: Broadstreet network ID
            base_url: Optional custom API base URL
            timeout: Request timeout in seconds
        """
        self.access_token = access_token
        self.network_id = network_id
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        # Broadstreet authenticates by query parameter, so the token is a
        # client-level dial coordinate — fixed here, never reassembled per call.
        self._vendor = VendorHttpClient(
            base_url=self.base_url,
            headers={},
            params=MappingProxyType({"access_token": access_token}),
            timeout=float(timeout),
        )

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, JsonValue] | None = None,
        query_params: QueryParams | None = None,
    ) -> Any:
        """Make an API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API endpoint path
            data: Request body data
            query_params: Query parameters

        Returns:
            Parsed response body

        Raises:
            AdCPSalesAgentError: The subclass for the upstream failure — see
                :func:`_raise_broadstreet_error`.
        """
        try:
            result = self._vendor.call(method, path, json=data if data else None, params=query_params)
        except OutboundError as e:
            # One branch, not two: OutboundDeliveryFailed is an OutboundError, and
            # the status it carries is the only thing the classifier reads.
            _raise_broadstreet_error(e)

        body = result.json() if result.content else None
        return body

    def get(self, path: str, query_params: QueryParams | None = None) -> Any:
        """Make a GET request."""
        return self._request("GET", path, query_params=query_params)

    def post(self, path: str, data: dict[str, JsonValue]) -> Any:
        """Make a POST request."""
        return self._request("POST", path, data=data)

    def put(self, path: str, data: dict[str, JsonValue]) -> Any:
        """Make a PUT request."""
        return self._request("PUT", path, data=data)

    def delete(self, path: str) -> Any:
        """Make a DELETE request."""
        return self._request("DELETE", path)

    # =========================================================================
    # Network Operations
    # =========================================================================

    def get_network(self) -> dict[str, Any]:
        """Get network details."""
        result = self.get(f"/networks/{self.network_id}")
        return result.get("network", result) if result else {}

    def get_networks(self) -> list[dict[str, Any]]:
        """Get all networks this token has access to."""
        result = self.get("/networks")
        return result.get("networks", []) if result else []

    # =========================================================================
    # Advertiser Operations
    # =========================================================================

    def get_advertisers(self) -> list[dict[str, Any]]:
        """Get all advertisers for the network."""
        result = self.get(f"/networks/{self.network_id}/advertisers")
        return result.get("advertisers", []) if result else []

    def get_advertiser(self, advertiser_id: str) -> dict[str, Any]:
        """Get a specific advertiser."""
        result = self.get(f"/networks/{self.network_id}/advertisers/{advertiser_id}")
        return result.get("advertiser", result) if result else {}

    def create_advertiser(self, name: str) -> dict[str, Any]:
        """Create a new advertiser."""
        result = self.post(f"/networks/{self.network_id}/advertisers", {"name": name})
        return result.get("advertiser", result) if result else {}

    # =========================================================================
    # Campaign Operations
    # =========================================================================

    def create_campaign(
        self,
        advertiser_id: str,
        name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Create a new campaign.

        Args:
            advertiser_id: Advertiser ID
            name: Campaign name
            start_date: Optional start date (ISO 8601)
            end_date: Optional end date (ISO 8601)

        Returns:
            Created campaign data
        """
        data: dict[str, JsonValue] = {"name": name}
        if start_date:
            data["start_date"] = start_date
        if end_date:
            data["end_date"] = end_date

        result = self.post(
            f"/networks/{self.network_id}/advertisers/{advertiser_id}/campaigns",
            data,
        )
        return result.get("campaign", result) if result else {}

    def delete_campaign(self, advertiser_id: str, campaign_id: str) -> dict[str, Any]:
        """Delete a campaign."""
        return self.delete(f"/networks/{self.network_id}/advertisers/{advertiser_id}/campaigns/{campaign_id}")

    # =========================================================================
    # Advertisement Operations
    # =========================================================================

    def create_advertisement(
        self,
        advertiser_id: str,
        name: str,
        ad_type: str,
        params: dict[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        """Create a new advertisement.

        Args:
            advertiser_id: Advertiser ID
            name: Advertisement name
            ad_type: Type of ad (html, static, text)
            params: Additional parameters (html, image, image_base64, etc.)

        Returns:
            Created advertisement data
        """
        data: dict[str, JsonValue] = {"name": name, "type": ad_type, "active": 1}
        if params:
            data.update(params)

        result = self.post(
            f"/networks/{self.network_id}/advertisers/{advertiser_id}/advertisements",
            data,
        )
        return result.get("advertisement", result) if result else {}

    def get_advertisement(self, advertiser_id: str, advertisement_id: str) -> dict[str, Any]:
        """Get a specific advertisement."""
        result = self.get(f"/networks/{self.network_id}/advertisers/{advertiser_id}/advertisements/{advertisement_id}")
        return result.get("advertisement", result) if result else {}

    def update_advertisement(
        self, advertiser_id: str, advertisement_id: str, params: dict[str, JsonValue]
    ) -> dict[str, Any]:
        """Update an advertisement."""
        result = self.put(
            f"/networks/{self.network_id}/advertisers/{advertiser_id}/advertisements/{advertisement_id}",
            params,
        )
        return result.get("advertisement", result) if result else {}

    def set_advertisement_source(
        self,
        advertiser_id: str,
        advertisement_id: str,
        source_type: str,
        params: dict[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        """Set the source/template for an advertisement.

        This is used for special Broadstreet templates like 3D Cube, YouTube, etc.
        The source type determines which template is used, and params provide
        the template-specific assets (images, captions, etc.).

        Args:
            advertiser_id: Advertiser ID
            advertisement_id: Advertisement ID
            source_type: Template source type (e.g., 'cube', 'youtube', 'gallery')
            params: Template-specific parameters (images, captions, URLs, etc.)

        Returns:
            Updated advertisement data
        """
        data: dict[str, JsonValue] = {"type": source_type}
        if params:
            data.update(params)

        result = self.post(
            f"/networks/{self.network_id}/advertisers/{advertiser_id}/advertisements/{advertisement_id}/source",
            data,
        )
        return result.get("advertisement", result) if result else {}

    def delete_advertisement(self, advertiser_id: str, advertisement_id: str) -> dict[str, Any]:
        """Delete an advertisement."""
        return self.delete(f"/networks/{self.network_id}/advertisers/{advertiser_id}/advertisements/{advertisement_id}")

    def get_advertisement_report(
        self,
        advertiser_id: str,
        advertisement_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get delivery report for an advertisement.

        Args:
            advertiser_id: Advertiser ID
            advertisement_id: Advertisement ID
            start_date: Report start date (ISO 8601)
            end_date: Report end date (ISO 8601)

        Returns:
            List of report records
        """
        query_params: dict[str, str | int | float | bool] = {}
        if start_date:
            query_params["start_date"] = start_date
        if end_date:
            query_params["end_date"] = end_date

        result = self.get(
            f"/networks/{self.network_id}/advertisers/{advertiser_id}/advertisements/{advertisement_id}/records",
            query_params=query_params if query_params else None,
        )
        return result.get("records", []) if result else []

    # =========================================================================
    # Placement Operations
    # =========================================================================

    def create_placement(
        self,
        advertiser_id: str,
        campaign_id: str,
        zone_id: str,
        advertisement_id: str,
    ) -> dict[str, Any]:
        """Create a placement linking an ad to a zone in a campaign.

        Args:
            advertiser_id: Advertiser ID
            campaign_id: Campaign ID
            zone_id: Zone ID
            advertisement_id: Advertisement ID

        Returns:
            Created placement data
        """
        data: dict[str, JsonValue] = {
            "zone_id": zone_id,
            "advertisement_id": advertisement_id,
        }
        return self.post(
            f"/networks/{self.network_id}/advertisers/{advertiser_id}/campaigns/{campaign_id}/placements",
            data,
        )

    # =========================================================================
    # Zone Operations
    # =========================================================================

    def get_zones(self) -> list[dict[str, Any]]:
        """Get all zones for the network."""
        result = self.get(f"/networks/{self.network_id}/zones")
        return result.get("zones", []) if result else []

    def create_zone(
        self,
        name: str,
        alias: str | None = None,
        self_serve: bool = False,
    ) -> dict[str, Any]:
        """Create a new zone.

        Args:
            name: Zone name
            alias: Optional zone alias
            self_serve: Whether zone is self-serve

        Returns:
            Created zone data
        """
        data: dict[str, JsonValue] = {"name": name}
        if alias:
            data["alias"] = alias
        data["self_serve"] = self_serve

        result = self.post(f"/networks/{self.network_id}/zones", data)
        return result.get("zone", result) if result else {}

    def delete_zone(self, zone_id: str) -> dict[str, Any]:
        """Delete a zone."""
        return self.delete(f"/networks/{self.network_id}/zones/{zone_id}")
