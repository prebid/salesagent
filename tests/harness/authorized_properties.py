"""AuthorizedPropertiesEnv — integration test environment for list_authorized_properties.

Minimal harness — discovery operation, pure DB read, no adapter calls.
A2A dispatch goes through the REAL pipeline (``on_message_send`` →
``_handle_list_authorized_properties_skill``); the tool has no ``*_raw``
production surface reachable from tests (salesagent-klkg dead-path rule).

Requires: integration_db fixture.
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import ListAuthorizedPropertiesRequest
from src.core.schemas._base import ListAuthorizedPropertiesResponse
from tests.harness._base import IntegrationEnv


class AuthorizedPropertiesEnv(IntegrationEnv):
    """Integration test environment for list_authorized_properties.

    No patches — discovery is read-only, no external service calls.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/authorized-properties"

    def _configure_mocks(self) -> None:
        """No mocks needed for read-only discovery operation."""

    def call_impl(self, **kwargs: Any) -> ListAuthorizedPropertiesResponse:
        """Call _list_authorized_properties_impl with real DB."""
        from src.core.tools.properties import _list_authorized_properties_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None:
            req = ListAuthorizedPropertiesRequest(**kwargs)
        return _list_authorized_properties_impl(req=req, identity=identity)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Dispatch list_authorized_properties through the REAL A2A pipeline."""
        return self._run_a2a_handler("list_authorized_properties", ListAuthorizedPropertiesResponse, **kwargs)
