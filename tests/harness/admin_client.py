"""One admin client on either transport, shared by every admin harness env.

The admin UI is reached two ways: a Flask test client against :func:`admin_test_app` in
this process, or a ``requests.Session`` against a live server. How the request is sent,
how the admin session is stated, and how the response is read differ by transport and by
nothing else, so they live here once. An env names its routes and reads its rows;
:class:`AdminClient` sends the request and :class:`AdminResponse` is what comes back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from tests.helpers.admin_session import (
    admin_session_payload,
    admin_test_app,
    drop_stated_session_cookie,
    member_session_payload,
    state_flask_session,
    state_http_session,
)

#: Where the in-process Flask client sends its requests.
_IN_PROCESS_ORIGIN = "https://localhost"

#: What a current browser sends on a page load and on a form submit. The admin app answers
#: a caller that sends neither ``Sec-Fetch-Mode: navigate`` nor an ``Accept`` preferring HTML
#: with JSON (``caller_reads_json``), so a request that stands for a browser must say so.
BROWSER_PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Mode": "navigate",
}

#: What a client that is not a browser sends: no ``Accept``, ``Origin`` or ``Sec-Fetch-*``
#: header (``requests`` would add ``Accept: */*`` itself). The admin app answers it with JSON.
NO_BROWSER_HEADERS: dict[str, str | None] = {"Accept": None}


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

    def flashes(self) -> list[tuple[str, str]]:
        """``(category, message)`` per flash ``base.html`` rendered into this page, in order.

        :meth:`AdminClient.submit` follows the redirect, so a flash arrives rendered into the
        page the browser lands on rather than waiting in the session.
        """
        alerts = BeautifulSoup(self._data, "html.parser").select(".flash-messages > .alert")
        return [
            (
                next(css.removeprefix("alert-") for css in alert["class"] if css.startswith("alert-")),
                alert.find(string=True, recursive=False).strip(),
            )
            for alert in alerts
        ]

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

    @property
    def origin(self) -> str:
        """The scheme and host requests are sent to, as a browser's ``Referer`` names a page."""
        return self._base_url or _IN_PROCESS_ORIGIN

    def authenticate(self, tenant_id: str, *, tenant_user: str | None = None, auth_method: str | None = None) -> None:
        """State a super-admin session for *tenant_id*, or *tenant_user*'s session.

        *auth_method* is how the session says its user signed in (``oidc`` for SSO).
        """
        payload = (
            member_session_payload(tenant_user, tenant_id, auth_method=auth_method)
            if tenant_user is not None
            else admin_session_payload(tenant_id, auth_method=auth_method)
        )
        self.state_session(payload)

    def state_session(self, payload: dict[str, Any]) -> None:
        """State *payload* as the session, replacing whatever the client held, through this transport's writer."""
        if self._flask_client is not None:
            state_flask_session(self._flask_client, payload)
        else:
            state_http_session(self._http, payload)

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
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str | None] | None = None,
    ) -> AdminResponse:
        """Send *method* to *path* (``/tenant/...``) with a form body or a JSON body.

        With no *headers* and no JSON body the request is a browser's (a page load or a form
        post) and carries :data:`BROWSER_PAGE_HEADERS`. A header given as ``None`` is not sent
        at all, on either transport (``requests`` otherwise sends ``Accept: */*`` itself).
        """
        if headers is None and json is None:
            headers = dict(BROWSER_PAGE_HEADERS)
        if self._flask_client is not None:
            sent = {name: value for name, value in (headers or {}).items() if value is not None}
            # Over https, because a production app marks its session cookie Secure.
            response = self._flask_client.open(
                path, method=method.upper(), data=data, json=json, headers=sent, base_url=_IN_PROCESS_ORIGIN
            )
            return AdminResponse.from_flask(response)
        response = self._http.request(
            method, f"{self._base_url}{path}", data=data, json=json, headers=headers, allow_redirects=False, timeout=60
        )
        return AdminResponse.from_requests(response)

    def submit(
        self,
        path: str,
        data: dict[str, Any],
        *,
        from_page: str | None = None,
        browser_headers: dict[str, str | None] | None = None,
    ) -> AdminResponse:
        """POST the form *data* to *path* as a browser does, and GET the page its redirect names, flash included.

        *from_page* is the page the form is on, sent as the browser's ``Referer``: the page an
        answer goes back to. A path is a page on this server; a URL with a scheme is another
        site's page. Without it the request names no page to go back to. A form that answers
        without a redirect (it re-rendered with an error) is returned as is. *browser_headers*
        say how the browser asks for a page, :data:`BROWSER_PAGE_HEADERS` by default.
        """
        headers: dict[str, str | None] = dict(BROWSER_PAGE_HEADERS if browser_headers is None else browser_headers)
        if from_page is not None:
            headers["Referer"] = from_page if urlsplit(from_page).scheme else f"{self.origin}{from_page}"
        response = self.request("post", path, data=data, headers=headers)
        location = response.headers.get("location")
        if not location:
            return response
        if self._http is not None:
            # The flash lives in the session the server just wrote; see the helper.
            drop_stated_session_cookie(self._http)
        target = urlsplit(location)
        return self.request("get", target.path + (f"?{target.query}" if target.query else ""))

    def close(self) -> None:
        if self._flask_client is not None:
            self._flask_client.__exit__(None, None, None)
            self._flask_client = None
        if self._http is not None:
            self._http.close()
            self._http = None


def guarded_admin_client(
    guard: Callable[[str, Callable[[], None]], None], base_url: str | None, tenant_id: str
) -> AdminClient:
    """An :class:`AdminClient` authenticated for *tenant_id*, closed by the env's *guard*.

    An env opens it on its first admin request, after every Given, so the in-process app is
    composed under the deployment a Given chose.
    """
    client = AdminClient(base_url)
    guard("admin_client", client.close)
    client.authenticate(tenant_id)
    return client


class AdminOperatorMixin:
    """The seller's operator, for an ``IntegrationEnv`` whose scenario includes an admin action.

    One :class:`AdminClient` on the env's OWN transport: in this process when the env has no
    ``e2e_config``, over HTTP to that live server when it has one. So an AdCP scenario whose
    Given is an operator action (authoring a product, syncing a publisher) runs that action
    against the same deployment its When then dispatches to, on every transport.

    The client is opened on the first request, after every earlier Given: the in-process
    app is composed under the settings those Givens chose. Factory rows are committed first,
    because the admin request reads them in its own session.
    """

    _admin: AdminClient | None = None

    def admin_client(self, tenant_id: str) -> AdminClient:
        """The operator's client for *tenant_id*, opened on first use, after factory rows commit."""
        self._commit_factory_data()  # type: ignore[attr-defined]
        if self._admin is None:
            e2e_config = self.e2e_config  # type: ignore[attr-defined]
            base_url = e2e_config.base_url if e2e_config is not None else None
            self._admin = guarded_admin_client(self._guard, base_url, tenant_id)  # type: ignore[attr-defined]
        return self._admin

    def admin_request(
        self,
        tenant_id: str,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str | None] | None = None,
    ) -> AdminResponse:
        """One request on the operator's client to *tenant_id*'s admin route at *path*.

        The body and *headers* are as :meth:`AdminClient.request` takes them. The client is the
        super admin's unless the env stated another caller's session on it.
        """
        return self.admin_client(tenant_id).request(
            method, f"/tenant/{tenant_id}/{path}", data=data, json=json, headers=headers
        )
