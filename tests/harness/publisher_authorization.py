"""The operator's publisher-authorization actions, and the publishers' files they read.

Three admin actions read a publisher's adagents.json to decide whether it authorizes THIS
agent: syncing publisher partners, verifying pending authorized properties, and opening a
partner's properties. :class:`PublisherAuthorizationEnv` drives each through its admin
route, with the real tenant row, URL derivation, SDK resolution and database writes. The
one thing a scenario chooses is what the publisher's origin serves, through
:class:`PublisherAdagentsMixin`.

Both admin transports run every scenario:

* ``admin_integration`` drives the route on a Flask test client in this process. The SDK
  refuses to dial a private address, and this process can only serve one, so the
  publisher's origin is the one seam: ``fetch_adagents`` is replaced where the three
  actions bind it.
* ``e2e_admin`` drives the route over HTTP on the live server, which dials the publisher
  for real. The publisher's origin is a TLS origin in THIS runner, reached at
  :data:`PUBLISHER_ORIGIN_HOST` -- the runner's network alias on the e2e stack's
  non-private subnet, under the shared test leaf's ``*.adcp-e2e.dev`` SAN -- on a port
  of its own, so concurrent workers never share a publisher.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from adcp.exceptions import AdagentsNotFoundError

from src.core.config import get_settings
from src.core.database.models import AuthorizedProperty, PublisherPartner
from tests.harness._base import IntegrationEnv
from tests.harness._realize import realize_e2e
from tests.harness.admin_client import AdminClient, AdminResponse

if TYPE_CHECKING:
    from tests.harness.transport import E2EConfig
    from tests.helpers.local_http_origin import LocalOrigin

#: Every module that binds ``fetch_adagents`` for this seller's own authorization check.
#: Patched together in process, so whichever action a scenario drives reads the same
#: publisher.
_FETCH_ADAGENTS_SITES = (
    "src.admin.blueprints.publisher_partners.fetch_adagents",
    "src.services.property_verification_service.fetch_adagents",
    "src.services.property_discovery_service.fetch_adagents",
)

#: Where the live server reaches a publisher origin this runner serves: the ``tests``
#: service's network alias in docker-compose.e2e.yml, which ``run_all_tests.sh`` applies
#: with ``dc run --use-aliases``. Change the two together.
PUBLISHER_ORIGIN_HOST = "publisher.adcp-e2e.dev"


def _empty_adagents() -> dict[str, Any]:
    return {"authorized_agents": [], "properties": []}


def _publisher_origin_address(env: PublisherAdagentsMixin, publisher: str) -> str:
    """E2E: the address of the TLS origin serving *publisher*, started on first use."""
    origin = env._publisher_origins.get(publisher)
    if origin is None:
        from tests.helpers.tls_material import start_tls_origin

        # 0.0.0.0, not loopback: the server dialing it is another container.
        origin = start_tls_origin(env._guard, f"publisher_origin:{publisher}", listen_host="0.0.0.0")
        env._publisher_origins[publisher] = origin
    return f"{PUBLISHER_ORIGIN_HOST}:{origin.port}"


def _serve_from_origins(env: PublisherAdagentsMixin) -> None:
    """E2E: each origin answers with its publisher's document as the scenario left it."""
    for publisher, origin in env._publisher_origins.items():
        document = env._adagents_documents.get(publisher)
        if document is None:
            origin.respond_with(404, body=b"")
        else:
            origin.respond_with(200, body=json.dumps(document).encode())


class PublisherAdagentsMixin:
    """Serves each publisher's adagents.json from a document the scenario wrote.

    A scenario names a publisher; :meth:`publisher_address` says where the seller reaches
    it, which is the name itself in process and the runner's origin over e2e. A publisher
    no scenario gave a document answers as a missing file does (404, which the SDK raises
    as ``AdagentsNotFoundError``), never as an empty authorization.

    Host env must be a ``BaseTestEnv`` (relies on ``_guard`` so both release paths stop
    the patchers and close the origins, and on ``is_e2e`` to pick the realization).
    """

    if TYPE_CHECKING:
        # Declared, not implemented: composed only with BaseTestEnv, which owns the
        # cleanup registry (the same declaration EgressHatchMixin makes).
        def _guard(self, label: str, cleanup: Callable[[], None]) -> None: ...

        @property
        def is_e2e(self) -> bool: ...

    _adagents_documents: dict[str, dict[str, Any]]
    _publisher_origins: dict[str, LocalOrigin]

    def _enter_pre(self) -> None:
        super()._enter_pre()  # type: ignore[misc]
        self._adagents_documents = {}
        self._publisher_origins = {}
        if not self.is_e2e:
            for site in _FETCH_ADAGENTS_SITES:
                patcher = patch(site, side_effect=self._serve_adagents)
                patcher.start()
                self._guard(f"adagents:{site}", patcher.stop)

    def adagents_document(self, publisher: str) -> dict[str, Any]:
        """The document *publisher* serves, created empty on first use."""
        return self._adagents_documents.setdefault(publisher, _empty_adagents())

    @realize_e2e(_publisher_origin_address)
    def publisher_address(self, publisher: str) -> str:
        """The domain the seller fetches *publisher*'s adagents.json from."""
        return publisher

    @realize_e2e(_serve_from_origins)
    def serve_adagents(self) -> None:
        """Make what each publisher serves the document as it stands now.

        Called before every admin action. In process there is nothing to do: the patched
        ``fetch_adagents`` reads the documents when the action calls it.
        """

    async def _serve_adagents(self, publisher_domain: str, **_options: Any) -> dict[str, Any]:
        document = self._adagents_documents.get(publisher_domain)
        if document is None:
            raise AdagentsNotFoundError(publisher_domain)
        return document


def _auto_verify_off_on_the_live_stack(env: PublisherAuthorizationEnv) -> None:
    """E2E: already realized. The live stack's posture is fixed where it starts.

    ``docker-compose.e2e.yml`` sets ``PUBLISHER_AUTO_VERIFY=false`` on adcp-server, which
    the storyboard server and every per-worker server inherit, so the server under test
    reads the publisher's file and asking for that is a true no-op. Without this function
    the e2e branch would patch a settings object in a process that is not the server's.
    """


class PublisherAuthorizationEnv(PublisherAdagentsMixin, IntegrationEnv):
    """Drive the three admin actions that read a publisher's file, and read what they wrote."""

    def __init__(self, *, e2e_config: E2EConfig | None = None, **kwargs: Any) -> None:
        super().__init__(e2e_config=e2e_config, **kwargs)
        self._admin: AdminClient | None = None
        # None drives the admin app in this process; a URL drives that live server.
        self._admin_base_url: str | None = e2e_config.base_url if e2e_config is not None else None

    # ── seller and tenant state ───────────────────────────────────────────

    @realize_e2e(_auto_verify_off_on_the_live_stack)
    def disable_publisher_auto_verify(self) -> None:
        """Read every publisher partner's file for this env's lifetime (``PUBLISHER_AUTO_VERIFY=false``).

        Left unset, partner sync verifies every partner without reading its file anywhere
        but production (``Settings.publisher_auto_verify_allowed``), so the check under test
        would not run. In process the settings field is patched on the object every reader
        reads, which is the object :func:`admin_test_app` composes the app from.
        """
        patcher = patch.object(get_settings().runtime, "publisher_auto_verify", False)
        patcher.start()
        self._guard("publisher_auto_verify", patcher.stop)

    def run_ad_server(self, adapter_type: str) -> None:
        """Give the tenant an adapter configuration of *adapter_type*."""
        from tests.factories import AdapterConfigFactory

        AdapterConfigFactory(tenant=self._tenant(), adapter_type=adapter_type)

    def pending_property(self, *, property_id: str, publisher: str) -> None:
        """An authorized website property of *publisher*, waiting for verification."""
        from tests.factories import AuthorizedPropertyFactory

        AuthorizedPropertyFactory(
            tenant=self._tenant(),
            property_id=property_id,
            publisher_domain=self.publisher_address(publisher),
            verification_status="pending",
        )

    # ── the operator's actions ────────────────────────────────────────────

    def sync_publisher_partners(self) -> AdminResponse:
        return self._admin_request("post", "publisher-partners/sync")

    def verify_pending_properties(self) -> AdminResponse:
        return self._admin_request("post", "authorized-properties/verify-all")

    def open_partner_properties(self, publisher: str) -> AdminResponse:
        partner = self.partner(publisher)
        return self._admin_request("get", f"publisher-partners/{partner.id}/properties")

    # ── read-backs ────────────────────────────────────────────────────────

    def partner(self, publisher: str) -> PublisherPartner:
        (partner,) = self._fresh(PublisherPartner, publisher_domain=self.publisher_address(publisher))
        return partner

    def properties_from(self, publisher: str) -> list[AuthorizedProperty]:
        return self._fresh(AuthorizedProperty, publisher_domain=self.publisher_address(publisher))

    def authorized_property(self, property_id: str) -> AuthorizedProperty:
        (prop,) = self._fresh(AuthorizedProperty, property_id=property_id)
        return prop

    # ── internals ─────────────────────────────────────────────────────────

    def _tenant(self) -> Any:
        tenant, _principal = self.setup_default_data()
        return tenant

    def _fresh(self, model: type, **filters: Any) -> list:
        """Rows the admin request committed in its own session, not this session's cache."""
        self.get_session().expire_all()
        return self.query(model, tenant_id=self._tenant_id, **filters)

    def _admin_request(self, method: str, path: str) -> AdminResponse:
        """One authenticated request to the tenant's admin route at *path*, on either transport."""
        self._commit_factory_data()
        self.serve_adagents()
        if self._admin is None:
            # Opened on the first action, after every Given: the in-process app is composed
            # under the settings a Given chose.
            self._admin = AdminClient(self._admin_base_url)
            self._guard("admin_client", self._admin.close)
            self._admin.authenticate(self._tenant_id)
        return self._admin.request(method, f"/tenant/{self._tenant_id}/{path}")
