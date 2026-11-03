"""The reverse proxy's on-demand TLS question, asked of a seller over HTTP.

``GET /tls/ask`` is mounted only when the deployment sets ``TLS_ASK_ENABLED``, and the app
reads that when it is composed. So both sides of this env run an app composed with it:
``docker-compose.e2e.yml`` sets it on the live stack, and ``tests/bdd/conftest.py`` sets it
before the in-process ``src.app`` is imported.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from src.core.config import get_settings
from tests.harness._base import IntegrationEnv
from tests.harness._realize import realize_e2e
from tests.harness.database_faults import DatabaseFaultMixin


def _domain_already_on_the_live_stack(env: TlsGateEnv, domain: str) -> None:
    """E2E: already realized. ``docker-compose.e2e.yml`` sets ``SALES_AGENT_DOMAIN`` on adcp-server.

    The feature names that same value, so the two change together. Without this function
    the e2e branch would patch a settings object in a process that is not the server's.
    """


class TlsGateEnv(DatabaseFaultMixin, IntegrationEnv):
    """Ask the gate about a host, against real tenant rows and a real database."""

    #: A database failure has to come back as the 500 the proxy reads, not as a raise in the test.
    REST_RAISE_SERVER_EXCEPTIONS = False

    @realize_e2e(_domain_already_on_the_live_stack)
    def answer_on_domain(self, domain: str) -> None:
        """Make *domain* this deployment's own, with the admin domain derived from it.

        The app is composed first: importing ``src.app`` calls ``load_settings()``, which
        replaces the cached settings, so a field patched before the first import in a worker
        is patched on an object no reader consults any more.
        """
        self.get_rest_client()
        for field, value in (("sales_agent_domain", domain), ("admin_domain", None)):
            patcher = patch.object(get_settings().runtime, field, value)
            patcher.start()
            self._guard(f"runtime:{field}", patcher.stop)

    def declare_host(self, *, label: str = "tenant", port: int | None = None, **tenant_fields: Any) -> tuple[str, str]:
        """A new tenant declaring a host of its own: that host without any port, and its subdomain."""
        from tests.factories import TenantFactory

        unique = uuid4().hex[:12]
        host = f"{label}.{unique}.example"
        subdomain = f"t{unique}"
        TenantFactory(subdomain=subdomain, virtual_host=f"{host}:{port}" if port else host, **tenant_fields)
        self._commit_factory_data()
        return host, subdomain

    def ask_tls_gate(self, params: dict[str, str]) -> Any:
        """GET ``/tls/ask`` the way the proxy does, with *params* as its query string."""
        return self.get_root("/tls/ask", params=params)
