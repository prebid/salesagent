"""HTTP transport for the FreeWheel Publisher API.

Knows about bearer-token auth, content-type negotiation (v3 paths return XML,
v4 paths return JSON), and HTTP status -> exception mapping. Does not know
about pagination, entity shapes, or specific endpoints — those live in
:mod:`_inventory`, :mod:`_commercial`, and :mod:`_creatives`.

Two authentication paths are supported (per the FreeWheel docs at
https://api-docs.freewheel.tv/demand/docs/demand-api-authentication):

1. **OAuth2 password grant (canonical)**: pass ``username`` + ``password`` and
   the transport mints a bearer at ``POST /auth/token`` on first use, caches
   it with TTL tracking, and refreshes on 401 or expiry.

2. **Pre-minted bearer (escape hatch)**: pass ``api_token`` directly. Used
   when a partner provides a token out-of-band. No refresh — 401 propagates
   to the caller, who must rotate the token.

Exactly one of the two paths must be provided.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

import requests

from src.adapters._token_cache import BearerTokenCache

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.freewheel.tv"
STAGING_BASE_URL = "https://api.stg.freewheel.tv"
DEFAULT_TIMEOUT = 30.0

# Refresh a minted token slightly before its documented expiry so an
# in-flight request never crosses the boundary. FreeWheel issues 7-day
# tokens; one hour of headroom is comfortable.
_REFRESH_LEEWAY_SECONDS = 60 * 60


class FreeWheelError(Exception):
    """Base exception for FreeWheel API errors.

    Carries the HTTP status code and raw response body so callers can
    inspect them without re-reading the response.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class FreeWheelAuthError(FreeWheelError):
    """401 — the bearer token is invalid, expired, or revoked."""


class FreeWheelForbiddenError(FreeWheelError):
    """403 — the bearer is valid but lacks entitlements for this resource."""


class FreeWheelNotFoundError(FreeWheelError):
    """404 — the requested resource does not exist."""


class FreeWheelValidationError(FreeWheelError):
    """4xx (other than 401/403/404) — typically a malformed request body."""


class FreeWheelServerError(FreeWheelError):
    """5xx — FreeWheel's side is unhappy."""


class FreeWheelTransport:
    """Low-level HTTP layer for the FreeWheel Publisher API.

    Construct with either a pre-minted ``api_token`` (escape hatch) or
    ``username`` + ``password`` (canonical OAuth2 password grant). The
    password path caches and auto-refreshes; the token path does not.
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
        has_password_grant = bool(username) and bool(password)
        has_token = bool(api_token)
        if not has_password_grant and not has_token:
            raise ValueError("FreeWheelTransport requires either api_token or (username + password)")

        self._username = username
        self._password = password
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._token_cache = BearerTokenCache(
            static_token=api_token if has_token else None,
            mint_fn=self._mint_token if has_password_grant else None,
            refresh_leeway_seconds=_REFRESH_LEEWAY_SECONDS,
        )

    # ----- public methods -----

    def get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a JSON resource. Used for v4 (inventory) endpoints."""
        response = self._request("GET", path, accept="application/json", params=params or None)
        return response.json() if response.content else {}

    def get_xml(self, path: str, **params: Any) -> ET.Element:
        """GET an XML resource. Used for v3 (commercial) endpoints."""
        response = self._request("GET", path, accept="application/xml", params=params or None)
        return ET.fromstring(response.text)

    def post_xml(self, path: str, body: str) -> ET.Element:
        """POST an XML body and parse the XML response. v3 only."""
        response = self._request(
            "POST",
            path,
            accept="application/xml",
            body=body,
            content_type="application/xml",
        )
        return ET.fromstring(response.text)

    def put_xml(self, path: str, body: str) -> ET.Element:
        """PUT an XML body and parse the XML response. v3 uses PUT (not PATCH)
        for partial updates — only the fields included in the body are
        modified server-side."""
        response = self._request(
            "PUT",
            path,
            accept="application/xml",
            body=body,
            content_type="application/xml",
        )
        return ET.fromstring(response.text)

    def delete_xml(self, path: str) -> None:
        """DELETE a v3 resource. Response body (if any) is discarded."""
        self._request("DELETE", path, accept="application/xml")

    def post_json(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON body, parse JSON response. Used for v4 + Reporting."""
        import json as _json

        response = self._request(
            "POST",
            path,
            accept="application/json",
            body=_json.dumps(json_body),
            content_type="application/json",
        )
        return response.json() if response.content else {}

    def delete_json(self, path: str) -> None:
        """DELETE a v4/JSON resource. Response body (if any) is discarded."""
        self._request("DELETE", path, accept="application/json")

    def token_info(self) -> dict[str, Any]:
        """Connectivity probe — returns ``{user_id, expires_in, created_at}``.

        A 200 here proves the bearer is valid; the ``expires_in`` field is
        useful for surfacing remaining TTL in admin UIs.
        """
        return self.get_json("/auth/token/info")

    def probe(self, method: str, path: str, *, accept: str = "application/json") -> tuple[int, str]:
        """Cheap permission-check probe — return ``(status_code, body)`` without
        raising on non-2xx. Used by ``check_permissions()`` so a single 403
        on one endpoint doesn't kill the whole probe pass.

        Auth/token-mint failures still raise (the probe can't run at all
        without a valid token) — callers should treat that as a fatal
        precondition and surface it as a transport-level error.
        """
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self._current_token()}", "accept": accept}
        response = self._session.request(method=method, url=url, headers=headers, timeout=self.timeout)
        return response.status_code, (response.text[:200] if response.text else "")

    # ----- internals -----

    def _current_token(self) -> str:
        """Return a valid bearer, minting/refreshing if needed."""
        return self._token_cache.current()

    def _mint_token(self) -> tuple[str, float]:
        """OAuth2 password grant: POST /auth/token to mint a fresh bearer.

        Returns ``(token, ttl_seconds)`` for :class:`BearerTokenCache` to
        cache with. FreeWheel issues 7-day tokens by default; the actual
        TTL is read from the ``expires_in`` field of the response.
        """
        assert self._username and self._password  # enforced in __init__
        url = f"{self.base_url}/auth/token"
        response = self._session.post(
            url,
            data={
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise FreeWheelAuthError(
                f"FreeWheel /auth/token rejected credentials: HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        body = response.json() if response.content else {}
        token = body.get("access_token")
        if not token:
            raise FreeWheelAuthError(
                "FreeWheel /auth/token response missing access_token",
                status_code=response.status_code,
                body=response.text,
            )
        expires_in = float(body.get("expires_in", 7 * 24 * 60 * 60))
        logger.info("FreeWheel: minted bearer token (expires_in=%s)", int(expires_in))
        return token, expires_in

    def _request(
        self,
        method: str,
        path: str,
        *,
        accept: str,
        params: dict[str, Any] | None = None,
        body: str | None = None,
        content_type: str | None = None,
    ) -> requests.Response:
        response = self._do_request(method, path, accept, params, body, content_type)
        # If we're using a minted token and got a 401, the token might have
        # rolled prematurely. Try one refresh + retry before propagating.
        if response.status_code == 401 and self._token_cache.has_mint:
            logger.info("FreeWheel: 401 with cached token; minting fresh and retrying")
            self._token_cache.invalidate()
            response = self._do_request(method, path, accept, params, body, content_type)
        self._raise_for_status(response, method, path)
        return response

    def _do_request(
        self,
        method: str,
        path: str,
        accept: str,
        params: dict[str, Any] | None,
        body: str | None,
        content_type: str | None,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "Authorization": f"Bearer {self._current_token()}",
            "accept": accept,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return self._session.request(
            method=method,
            url=url,
            headers=headers,
            data=body,
            timeout=self.timeout,
        )

    def _raise_for_status(self, response: requests.Response, method: str, path: str) -> None:
        if response.ok:
            return
        status = response.status_code
        body = response.text
        message = f"FreeWheel {method} {path} -> HTTP {status}"
        if status == 401:
            raise FreeWheelAuthError(message, status_code=status, body=body)
        if status == 403:
            raise FreeWheelForbiddenError(message, status_code=status, body=body)
        if status == 404:
            raise FreeWheelNotFoundError(message, status_code=status, body=body)
        if 400 <= status < 500:
            raise FreeWheelValidationError(message, status_code=status, body=body)
        raise FreeWheelServerError(message, status_code=status, body=body)
