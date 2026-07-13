"""Broadstreet Ads API client wrapper.

Handles authentication and HTTP requests to the Broadstreet API.
Base URL: https://api.broadstreetads.com/api/0/
Auth: Access token passed as query parameter.
"""

import logging
from typing import Any

import requests
from requests.exceptions import RequestException

from src.core.exceptions import AdCPAdapterError, ad_server_error_attrs

logger = logging.getLogger(__name__)


class BroadstreetAPIError(AdCPAdapterError):
    """Broadstreet API failure, typed into the AdCP recovery taxonomy.

    Subclasses ``AdCPAdapterError`` so a Broadstreet write failure surfaces a
    spec recovery hint at the transport boundary instead of normalizing to
    ``INTERNAL_ERROR``/``terminal`` — which would tell a buyer agent to escalate
    to a human on a transient ad-server outage, the exact recovery-taxonomy
    contradiction the sibling adapters (Kevel/Triton/Xandr) were just fixed to
    avoid. ``error_code``/``recovery``/``status_code`` ALL come from the shared
    ad-server table (``ad_server_error_attrs``), so this configures-self-in-``__init__``
    error type produces the SAME buyer-facing envelope AND REST status as the
    ``wrap_request_errors`` factory path for the same status — one ad-server event,
    one buyer-facing result, regardless of which adapter the tenant runs:

    - transport outage (no status) / 5xx -> ``SERVICE_UNAVAILABLE``, transient, HTTP 502
    - 429 (rate limited) -> ``RATE_LIMITED``, transient (retry with backoff), HTTP 429
    - 403 (operator ``access_token`` denied) -> ``CONFIGURATION_ERROR``, terminal, HTTP 500
    - other 4xx -> ``VALIDATION_ERROR``, correctable (fix the request/reference), HTTP 400

    The buyer-facing ``status_code`` is the mapped AdCP class default, NOT the upstream
    ad-server status (which would leak the upstream's 403/404 to the buyer REST status
    line and diverge by adapter); the upstream status stays in the message text
    (``"... (HTTP 403)"``) and ``response_body``.
    """

    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.response_body = response_body
        # Derive the FULL (error_code, recovery, status_code) verdict from the shared
        # ad-server table so Broadstreet matches the wrap_request_errors factory path
        # exactly. When status_code is None (a transport outage) the AdCPAdapterError
        # defaults stand: status_code=502, SERVICE_UNAVAILABLE / transient. The attrs
        # are set as instance attributes (not error_code=/recovery= kwargs, which the
        # src/adapters error-construction guard forbids).
        if status_code is not None:
            self.error_code, self.recovery, self.status_code = ad_server_error_attrs(status_code)


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

    def _build_url(self, path: str, query_params: dict[str, Any] | None = None) -> str:
        """Build full URL with access token.

        Args:
            path: API endpoint path (e.g., "/networks/123/advertisers")
            query_params: Optional additional query parameters

        Returns:
            Full URL with access token
        """
        from urllib.parse import urlencode

        url = f"{self.base_url}{path}"
        params = {"access_token": self.access_token}
        if query_params:
            params.update(query_params)

        # Filter None values and properly URL-encode all parameters
        query_string = urlencode({k: v for k, v in params.items() if v is not None})
        return f"{url}?{query_string}"

    def _handle_response(self, response: requests.Response) -> Any:
        """Handle API response and raise errors if needed.

        Args:
            response: Requests response object

        Returns:
            Parsed JSON response body

        Raises:
            BroadstreetAPIError: If response indicates an error
        """
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text

        if response.status_code == 403:
            raise BroadstreetAPIError(
                "Broadstreet API Auth Denied (HTTP 403)",
                status_code=403,
                response_body=body,
            )

        if response.status_code == 404:
            raise BroadstreetAPIError(
                "Resource not found (HTTP 404)",
                status_code=404,
                response_body=body,
            )

        if response.status_code >= 500:
            raise BroadstreetAPIError(
                f"Broadstreet API server error (HTTP {response.status_code})",
                status_code=response.status_code,
                response_body=body,
            )

        if response.status_code >= 400:
            # Do NOT echo the response body into the buyer-facing message (unbounded
            # reflected ad-server output); keep it server-side on response_body only.
            raise BroadstreetAPIError(
                f"Broadstreet API error (HTTP {response.status_code})",
                status_code=response.status_code,
                response_body=body,
            )

        return body

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
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
            BroadstreetAPIError: If request fails
        """
        url = self._build_url(path, query_params)

        try:
            response = requests.request(
                method=method,
                url=url,
                json=data if data else None,
                timeout=self.timeout,
            )
            return self._handle_response(response)
        except RequestException as e:
            # Do NOT interpolate str(e): a requests error stringifies the request URL,
            # which carries the operator's access_token query param (client.py builds
            # the URL with ?access_token=...). That message reaches the buyer wire via
            # the typed-error boundary. Keep the credentialed detail server-side only
            # (the chained `from e` preserves it for the operator's traceback/logs).
            raise BroadstreetAPIError(f"Broadstreet request failed (network {self.network_id})") from e

    def get(self, path: str, query_params: dict[str, Any] | None = None) -> Any:
        """Make a GET request."""
        return self._request("GET", path, query_params=query_params)

    def post(self, path: str, data: dict[str, Any]) -> Any:
        """Make a POST request."""
        return self._request("POST", path, data=data)

    def put(self, path: str, data: dict[str, Any]) -> Any:
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
        data: dict[str, Any] = {"name": name}
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
        params: dict[str, Any] | None = None,
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
        data: dict[str, Any] = {"name": name, "type": ad_type, "active": 1}
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

    def update_advertisement(self, advertiser_id: str, advertisement_id: str, params: dict[str, Any]) -> dict[str, Any]:
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
        params: dict[str, Any] | None = None,
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
        data: dict[str, Any] = {"type": source_type}
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
        query_params: dict[str, Any] = {}
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
        data = {
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
        data: dict[str, Any] = {"name": name}
        if alias:
            data["alias"] = alias
        data["self_serve"] = self_serve

        result = self.post(f"/networks/{self.network_id}/zones", data)
        return result.get("zone", result) if result else {}

    def delete_zone(self, zone_id: str) -> dict[str, Any]:
        """Delete a zone."""
        return self.delete(f"/networks/{self.network_id}/zones/{zone_id}")
