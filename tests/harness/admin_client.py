"""One admin client on either transport, shared by every admin harness env.

The admin UI is reached two ways: a Flask test client against :func:`admin_test_app` in
this process, or a ``requests.Session`` against a live server. How the request is sent,
how the admin session is stated, and how the response is read differ by transport and by
nothing else, so they live here once. An env names its routes and reads its rows;
:class:`AdminClient` sends the request and :class:`AdminResponse` is what comes back.
"""

from __future__ import annotations

import json
from typing import Any

from tests.helpers.admin_session import admin_auth_session, admin_test_app, authenticate_http_session


class _CaseInsensitiveHeaders(dict):
    """Header mapping that looks up regardless of case, on either transport.

    HTTP header names are case-insensitive (RFC 9110 §5.1), and the two
    transports genuinely differ: werkzeug hands back canonical ``Location``,
    while the live server emits lowercase ``location`` (ASGI normalizes header
    names). Both source objects model that correctly — werkzeug ``Headers`` and
    requests ``CaseInsensitiveDict`` — but ``dict(response.headers)`` threw the
    property away, so ``headers.get("Location")`` silently returned "" over real
    HTTP and every redirect assertion read as "no redirect happened".
    Six BR-ADMIN-ACCOUNTS scenarios failed on this the first
    time they ran over the wire.
    """

    def __init__(self, headers: Any) -> None:
        super().__init__({str(k).lower(): v for k, v in dict(headers).items()})

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key.lower(), default)

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key.lower())

    def __contains__(self, key: object) -> bool:
        return super().__contains__(str(key).lower())


class AdminResponse:
    """Unified response wrapper for Flask test_client and requests.Session.

    Normalizes the response interface so step definitions don't need to
    know which transport is active.
    """

    def __init__(self, status_code: int, data: bytes, headers: Any, json_data: Any = None) -> None:
        self.status_code = status_code
        self._data = data
        self.headers = _CaseInsensitiveHeaders(headers)
        self._json_data = json_data

    @property
    def data(self) -> bytes:
        return self._data

    def get_json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self._data)

    @classmethod
    def from_flask(cls, response: Any) -> AdminResponse:
        """Wrap a Flask/werkzeug test response."""
        return cls(
            status_code=response.status_code,
            data=response.data,
            headers=dict(response.headers),
        )

    @classmethod
    def from_requests(cls, response: Any) -> AdminResponse:
        """Wrap a requests.Response."""
        return cls(
            status_code=response.status_code,
            data=response.content,
            headers=dict(response.headers),
            json_data=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else None,
        )


class AdminClient:
    """An admin's client on one transport: a Flask test client, or an HTTP session.

    *base_url* picks the transport, and the caller supplies it: ``None`` drives
    :func:`admin_test_app` in this process, a URL drives the live server there. The
    in-process app is composed here, on construction, so a deployment fact a Given patched
    onto the settings before the first request is the one the app serves under.

    Redirects are never followed on either transport: a step reads the redirect itself.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url
        self._flask_client: Any = None
        self._http: Any = None
        if base_url is None:
            self._flask_client = admin_test_app().test_client().__enter__()
        else:
            import requests

            self._http = requests.Session()

    def authenticate(self, tenant_id: str) -> None:
        """State an authenticated super-admin session for *tenant_id*."""
        if self._flask_client is not None:
            admin_auth_session(self._flask_client, tenant_id)
        else:
            authenticate_http_session(self._http, self._base_url or "", tenant_id)

    def clear_auth(self) -> None:
        """Drop the admin session, so the next request arrives anonymous."""
        if self._flask_client is not None:
            with self._flask_client.session_transaction() as session:
                session.clear()
        else:
            import requests

            self._http.close()
            self._http = requests.Session()

    def request(
        self, method: str, path: str, *, data: dict[str, str] | None = None, json: dict[str, Any] | None = None
    ) -> AdminResponse:
        """Send *method* to *path* (``/tenant/...``) with a form body or a JSON body."""
        if self._flask_client is not None:
            # Over https, because a production app marks its session cookie Secure.
            response = self._flask_client.open(
                path, method=method.upper(), data=data, json=json, base_url="https://localhost"
            )
            return AdminResponse.from_flask(response)
        response = self._http.request(
            method, f"{self._base_url}{path}", data=data, json=json, allow_redirects=False, timeout=60
        )
        return AdminResponse.from_requests(response)

    def close(self) -> None:
        if self._flask_client is not None:
            self._flask_client.__exit__(None, None, None)
            self._flask_client = None
        if self._http is not None:
            self._http.close()
            self._http = None
