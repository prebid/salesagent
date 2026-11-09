"""The publishers' adagents.json files, the seller's ingest of them, and the operator's views.

A publisher authorizes this agent only through its own adagents.json, which the seller
fetches and stores (``src/services/publisher_ingest.py``). A scenario chooses what each
publisher serves -- a document it writes, or a failure status -- through
:class:`PublisherAdagentsMixin`, and the seller then ingests it.

Two ways the file reaches the seller:

* :class:`PublisherAuthorizationEnv` drives the operator's admin routes (add a publisher,
  sync every publisher, open a publisher's properties) on both admin transports.
  ``admin_integration`` replaces ``fetch_adagents`` where the ingest service binds it --
  the SDK refuses to dial a private address, and this process can only serve one.
  ``e2e_admin`` drives the routes over HTTP on the live server, which dials the publisher
  for real: a TLS origin in THIS runner, at :data:`PUBLISHER_ORIGIN_HOST` (the runner's
  network alias on the e2e stack's non-private subnet, under the shared test leaf's
  ``*.adcp-e2e.dev`` SAN) on a port of its own, so concurrent workers never share one.
* An env composing the mixin with ``ingest_in_runner = True`` (the product envs) runs the
  ingest service in this process on every transport, e2e included, against the database
  the server reads. A buyer reads the result over the transport under test. It cannot be
  the admin route over e2e: the runner's origin has a port, and a port-qualified
  ``publisher_domain`` is one ``core/product.json``'s pattern refuses, so no product could
  ever be offered for it. The network fetch itself is graded by the admin feature.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import patch

from adcp import AdagentsHTTPError, AdagentsNotFoundError

from src.core.database.models import Publisher, PublisherProperty
from src.core.http_utils import canonical_publisher_domain
from tests.harness._base import IntegrationEnv
from tests.harness.admin_client import AdminOperatorMixin, AdminResponse

if TYPE_CHECKING:
    from tests.helpers.local_http_origin import LocalOrigin

#: Where the ingest service binds ``fetch_adagents``: the one place the seller reads a
#: publisher's file.
_FETCH_ADAGENTS_SITE = "src.services.publisher_ingest.fetch_adagents"

#: Where the live server reaches a publisher origin this runner serves: the ``tests``
#: service's network alias in docker-compose.e2e.yml, which ``run_all_tests.sh`` applies
#: with ``dc run --use-aliases``. Change the two together.
PUBLISHER_ORIGIN_HOST = "publisher.adcp-e2e.dev"


def _empty_adagents() -> dict[str, Any]:
    return {"authorized_agents": [], "properties": []}


class PublisherAdagentsMixin:
    """Serves each publisher's adagents.json from a document (or a failure) the scenario chose.

    A scenario names a publisher; :meth:`publisher_address` says where the seller reaches
    it. A publisher no scenario gave a document answers as a missing file does (404, which
    the SDK raises as ``AdagentsNotFoundError``), never as an empty authorization.

    Host env must be a ``BaseTestEnv`` (relies on ``_guard`` so both release paths stop
    the patcher and close the origins, and on ``is_e2e``).
    """

    if TYPE_CHECKING:
        # Declared, not implemented: composed only with BaseTestEnv, which owns the
        # cleanup registry (the same declaration EgressHatchMixin makes).
        def _guard(self, label: str, cleanup: Callable[[], None]) -> None: ...

        @property
        def is_e2e(self) -> bool: ...

    #: Run the ingest service in this process on every transport (see the module docstring).
    ingest_in_runner: ClassVar[bool] = False

    _adagents_documents: dict[str, dict[str, Any]]
    _failure_statuses: dict[str, int]
    _publisher_origins: dict[str, LocalOrigin]

    def _enter_pre(self) -> None:
        super()._enter_pre()  # type: ignore[misc]
        self._adagents_documents = {}
        self._failure_statuses = {}
        self._publisher_origins = {}
        if self._fetches_in_process:
            patcher = patch(_FETCH_ADAGENTS_SITE, side_effect=self._serve_adagents)
            patcher.start()
            self._guard("adagents:fetch", patcher.stop)

    @property
    def _fetches_in_process(self) -> bool:
        return self.ingest_in_runner or not self.is_e2e

    # ── what the publisher serves ──────────────────────────────────────────

    def adagents_document(self, publisher: str) -> dict[str, Any]:
        """The document *publisher* serves, created empty on first use.

        Keyed by the host the seller fetches (``canonical_publisher_domain``), so a scenario
        may name a publisher in any spelling an operator could type.
        """
        return self._adagents_documents.setdefault(canonical_publisher_domain(publisher), _empty_adagents())

    def answer_with_status(self, publisher: str, status: int) -> None:
        """Make *publisher* answer *status* instead of its document (404, or a 5xx)."""
        self._failure_statuses[canonical_publisher_domain(publisher)] = status

    def publisher_address(self, publisher: str) -> str:
        """The domain the seller fetches *publisher*'s adagents.json from.

        The name itself, except on e2e when the live server dials: then the runner's TLS
        origin for *publisher*, started on first use.
        """
        if self._fetches_in_process:
            return canonical_publisher_domain(publisher)
        publisher = canonical_publisher_domain(publisher)
        origin = self._publisher_origins.get(publisher)
        if origin is None:
            from tests.helpers.tls_material import start_tls_origin

            # 0.0.0.0, not loopback: the server dialing it is another container.
            origin = start_tls_origin(self._guard, f"publisher_origin:{publisher}", listen_host="0.0.0.0")
            self._publisher_origins[publisher] = origin
        return f"{PUBLISHER_ORIGIN_HOST}:{origin.port}"

    def serve_adagents(self) -> None:
        """Make what each publisher serves the document (or status) as it stands now.

        Called before every fetch the seller makes. In process there is nothing to do: the
        patched ``fetch_adagents`` reads the documents when the ingest calls it.
        """
        for publisher, origin in self._publisher_origins.items():
            status = self._failure_statuses.get(publisher)
            document = self._adagents_documents.get(publisher)
            if status is not None or document is None:
                origin.respond_with(status or 404, body=b"")
            else:
                origin.respond_with(200, body=json.dumps(document).encode())

    async def _serve_adagents(self, publisher_domain: str, **_options: Any) -> dict[str, Any]:
        status = self._failure_statuses.get(publisher_domain)
        document = self._adagents_documents.get(publisher_domain)
        if status == 404 or (status is None and document is None):
            raise AdagentsNotFoundError(publisher_domain)
        if status is not None:
            raise AdagentsHTTPError(status_code=status, url=f"https://{publisher_domain}/.well-known/adagents.json")
        return json.loads(json.dumps(document))

    # ── the seller's state ─────────────────────────────────────────────────

    def stored_publisher_or_none(self, publisher: str) -> Publisher | None:
        """The seller's row for *publisher* as the last request committed it, or ``None``."""
        session = self.get_session()  # type: ignore[attr-defined]
        session.expire_all()
        return session.get(Publisher, (self._tenant_id, self.publisher_address(publisher)))  # type: ignore[attr-defined]

    def stored_publisher(self, publisher: str) -> Publisher:
        """The seller's row for *publisher*, as the last request committed it."""
        stored = self.stored_publisher_or_none(publisher)
        assert stored is not None, f"the seller holds no publisher {publisher!r}"
        return stored

    def stored_properties(self, publisher: str) -> list[PublisherProperty]:
        """The properties the seller derived from *publisher*'s stored document."""
        return list(self.stored_publisher(publisher).properties)

    def move_last_fetch_back(self, publisher: str, hours: float) -> None:
        """Move the seller's last successful fetch of *publisher* *hours* into the past.

        The harness clock: freshness is evaluated at read time from ``fetched_at``, so
        moving it back is the time that has passed since, on every transport, with no sleep.
        """
        stored = self.stored_publisher(publisher)
        assert stored.fetched_at is not None, f"the seller never fetched {publisher!r}"
        stored.fetched_at -= timedelta(hours=hours)
        self.get_session().commit()  # type: ignore[attr-defined]

    def ingest_publisher(self, publisher: str) -> None:
        """The seller ingests *publisher*'s adagents.json in this process (``ingest_in_runner``)."""
        from src.services.publisher_ingest import ingest_sync
        from tests.factories.publisher import PublisherFactory

        tenant, _principal = self.setup_default_data()  # type: ignore[attr-defined]
        PublisherFactory(tenant=tenant, domain=self.publisher_address(publisher))
        self._commit_factory_data()  # type: ignore[attr-defined]
        self.serve_adagents()
        ingest_sync(self._tenant_id, [self.publisher_address(publisher)])  # type: ignore[attr-defined]

    # ── what the document says ─────────────────────────────────────────────

    def list_property(self, publisher: str, property_id: str, tags: Iterable[str] = ()) -> None:
        """*publisher*'s file lists a website property *property_id* on its own domain."""
        prop: dict[str, Any] = {
            "property_id": property_id,
            "property_type": "website",
            "name": property_id,
            # The domain the file lists is the domain it is served from.
            "identifiers": [{"type": "domain", "value": self.publisher_address(publisher)}],
        }
        if tags:
            prop["tags"] = list(tags)
        self.adagents_document(publisher)["properties"].append(prop)

    def unlist_property(self, publisher: str, property_id: str) -> None:
        """*publisher*'s file no longer lists *property_id*, nor authorizes any agent for it."""
        document = self.adagents_document(publisher)
        document["properties"] = [p for p in document["properties"] if p.get("property_id") != property_id]
        for entry in document["authorized_agents"]:
            if property_id in (entry.get("property_ids") or ()):
                entry["property_ids"] = [i for i in entry["property_ids"] if i != property_id]
        document["authorized_agents"] = [
            e
            for e in document["authorized_agents"]
            if e.get("authorization_type") != "property_ids" or e["property_ids"]
        ]

    def authorize(self, publisher: str, entry_url: str, **selector: Any) -> None:
        """*publisher*'s file adds an ``authorized_agents[]`` entry for *entry_url*.

        *selector* is the entry's variant and qualifiers: ``authorization_type`` plus
        ``property_ids`` / ``property_tags`` / ``properties``, ``effective_until``, ...
        """
        self.adagents_document(publisher)["authorized_agents"].append(
            {"url": entry_url, "authorized_for": "Display inventory", **selector}
        )

    def declare_format_option(self, publisher: str, declaration: dict[str, Any]) -> None:
        """*publisher*'s file declares *declaration* in its ``formats[]``, replacing one with the same id."""
        formats = self.adagents_document(publisher).setdefault("formats", [])
        formats[:] = [f for f in formats if f.get("format_option_id") != declaration["format_option_id"]]
        formats.append(declaration)


class PublisherAuthorizationEnv(AdminOperatorMixin, PublisherAdagentsMixin, IntegrationEnv):
    """Drive the operator's publisher routes, and read what they wrote."""

    def run_ad_server(self, adapter_type: str) -> None:
        """Give the tenant an adapter configuration of *adapter_type*."""
        from tests.factories import AdapterConfigFactory

        AdapterConfigFactory(tenant=self._tenant(), adapter_type=adapter_type)

    # ── the operator's actions ────────────────────────────────────────────

    def add_publisher(self, publisher: str) -> AdminResponse:
        return self._admin_request(
            "post", "publisher-partners", json={"publisher_domain": self.publisher_address(publisher)}
        )

    def sync_publisher_partners(self) -> AdminResponse:
        return self._admin_request("post", "publisher-partners/sync")

    def open_partner_properties(self, publisher: str) -> AdminResponse:
        return self._admin_request("get", f"publisher-partners/{self.publisher_address(publisher)}/properties")

    # ── internals ─────────────────────────────────────────────────────────

    def _tenant(self) -> Any:
        tenant, _principal = self.setup_default_data()
        return tenant

    def _admin_request(self, method: str, path: str, **kwargs: Any) -> AdminResponse:
        """One authenticated request to the tenant's admin route at *path*, on either transport.

        The publisher's file is served first, so the action reads what the scenario chose.
        """
        self._commit_factory_data()
        self.serve_adagents()
        return self.admin_request(self._tenant_id, method, path, **kwargs)
