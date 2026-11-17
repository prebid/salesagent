"""The publishers' adagents.json files, the seller's ingest of them, and the operator's views.

A publisher authorizes this agent only through its own adagents.json, which the seller
fetches and stores (``src/services/publisher_ingest.py``). A scenario chooses what each
publisher serves -- a document it writes, or a failure status -- through
:class:`PublisherAdagentsMixin`, and the seller then ingests it through the operator's
admin routes (add a publisher, sync every publisher) on the env's own transport.

Where the seller reads the file:

* In process (a2a, mcp, rest, admin_integration) ``fetch_adagents`` is replaced where the
  ingest service binds it: the SDK refuses to dial a private address, and this process
  can only serve one.
* On e2e the live server dials the publisher for real, at the domain the scenario names,
  on 443 (a publisher domain carries no port). Each such domain is a tls-proxy alias, an
  SNI map row routed to ``publisher-origin`` and a SAN of the shared leaf, like every other
  e2e origin (docker-compose.e2e.yml, config/nginx/nginx-tls-test.conf.template,
  scripts/dev/gen_test_tls.py). ``publisher-origin`` serves the files this runner wrote
  under :data:`PUBLISHER_FILES_DIR` -- keyed by the address of this worker's own server, so
  concurrent workers serving the same domain never read each other's files.
"""

from __future__ import annotations

import json
import shutil
import socket
from collections.abc import Callable, Iterable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from adcp import AdagentsHTTPError, AdagentsNotFoundError

from src.core.database.models import Publisher, PublisherProperty
from src.core.http_utils import canonical_publisher_domain
from tests.harness._base import IntegrationEnv
from tests.harness.admin_client import AdminOperatorMixin, AdminResponse

if TYPE_CHECKING:
    from tests.harness.transport import E2EConfig

#: Where the ingest service binds ``fetch_adagents``: the one place the seller reads a
#: publisher's file.
_FETCH_ADAGENTS_SITE = "src.services.publisher_ingest.fetch_adagents"

#: The directory ``publisher-origin`` serves, as this runner sees it (the repository is
#: bind-mounted at the same path in both). ``run_all_tests.sh`` creates and empties it.
PUBLISHER_FILES_DIR = Path(__file__).resolve().parents[2] / ".test-publishers"


def _empty_adagents() -> dict[str, Any]:
    return {"authorized_agents": [], "properties": []}


class PublisherAdagentsMixin:
    """Serves each publisher's adagents.json from a document (or a failure) the scenario chose.

    A scenario names a publisher; :meth:`publisher_address` says where the seller reaches
    it. A publisher no scenario gave a document answers as a missing file does (404, which
    the SDK raises as ``AdagentsNotFoundError``), never as an empty authorization.

    Host env must be a ``BaseTestEnv`` composed with :class:`AdminOperatorMixin` (relies on
    ``_guard`` for cleanup, on ``is_e2e`` and ``e2e_config``, and on ``admin_request`` for
    the ingest).
    """

    if TYPE_CHECKING:
        # Declared, not implemented: composed only with BaseTestEnv and AdminOperatorMixin.
        e2e_config: E2EConfig | None

        def _guard(self, label: str, cleanup: Callable[[], None]) -> None: ...

        def admin_request(self, tenant_id: str, method: str, path: str, **kwargs: Any) -> AdminResponse: ...

        @property
        def is_e2e(self) -> bool: ...

    _adagents_documents: dict[str, dict[str, Any]]
    _failure_statuses: dict[str, int]
    _server_files: Path | None

    def _enter_pre(self) -> None:
        super()._enter_pre()  # type: ignore[misc]
        self._adagents_documents = {}
        self._failure_statuses = {}
        self._server_files = None
        if not self.is_e2e:
            patcher = patch(_FETCH_ADAGENTS_SITE, side_effect=self._serve_adagents)
            patcher.start()
            self._guard("adagents:fetch", patcher.stop)

    # ── what the publisher serves ──────────────────────────────────────────

    def adagents_document(self, publisher: str) -> dict[str, Any]:
        """The document *publisher* serves, created empty on first use.

        Keyed by the host the seller fetches (``canonical_publisher_domain``), so a scenario
        may name a publisher in any spelling an operator could type.
        """
        return self._adagents_documents.setdefault(self.publisher_address(publisher), _empty_adagents())

    def answer_with_status(self, publisher: str, status: int) -> None:
        """Make *publisher* answer *status* instead of its document (404, or a 5xx)."""
        self._failure_statuses[self.publisher_address(publisher)] = status

    def publisher_address(self, publisher: str) -> str:
        """The domain the seller fetches *publisher*'s adagents.json from: its canonical domain."""
        return canonical_publisher_domain(publisher)

    def serve_adagents(self) -> None:
        """Make what each publisher serves the document (or status) as it stands now.

        Called before every fetch the seller makes. In process there is nothing to do: the
        patched ``fetch_adagents`` reads the documents when the ingest calls it. On e2e each
        publisher's directory is rewritten for ``publisher-origin``, under this worker's
        server's address.
        """
        if not self.is_e2e:
            return
        files = self._registered_server_files()
        for address in {*self._adagents_documents, *self._failure_statuses}:
            self._write_origin_files(files / address, address)

    def _registered_server_files(self) -> Path:
        """This worker's directory at ``publisher-origin``: the address of the server it drives.

        Resolved from the server's own container name (``E2EConfig.server_host``), never
        written or computed. Creating it is the registration ``publisher-origin`` checks: a
        request from an address no runner registered is refused, naming the address.
        """
        if self._server_files is None:
            server_host = self.e2e_config.server_host if self.e2e_config is not None else None
            assert server_host, "the e2e stack names no server container (E2E_SERVER_HOST), so no file can be served"
            files = PUBLISHER_FILES_DIR / socket.gethostbyname(server_host)
            files.mkdir(parents=True, exist_ok=True)
            self._guard(f"publisher_origin:{files.name}", lambda: shutil.rmtree(files, ignore_errors=True))
            self._server_files = files
        return self._server_files

    def _write_origin_files(self, directory: Path, address: str) -> None:
        shutil.rmtree(directory, ignore_errors=True)
        status = self._failure_statuses.get(address)
        document = self._adagents_documents.get(address)
        if status == 503:
            directory.mkdir(parents=True)
            (directory / "unavailable").touch()
        elif status is None and document is not None:
            well_known = directory / ".well-known"
            well_known.mkdir(parents=True)
            (well_known / "adagents.json").write_text(json.dumps(document))
        else:
            # A missing file is how a 404 answers.
            assert status in (None, 404), f"publisher-origin answers 404 or 503, not {status}"

    async def _serve_adagents(self, publisher_domain: str, **_options: Any) -> dict[str, Any]:
        status = self._failure_statuses.get(publisher_domain)
        document = self._adagents_documents.get(publisher_domain)
        if status == 404 or (status is None and document is None):
            raise AdagentsNotFoundError(publisher_domain)
        if status is not None:
            raise AdagentsHTTPError(status_code=status, url=f"https://{publisher_domain}/.well-known/adagents.json")
        return json.loads(json.dumps(document))

    # ── the operator's actions ────────────────────────────────────────────

    def add_publisher(self, publisher: str) -> AdminResponse:
        """The operator adds *publisher*, spelled as the scenario wrote it; the route ingests its file at once."""
        return self._publisher_route("post", "publisher-partners", json={"publisher_domain": publisher})

    def sync_publisher_partners(self) -> AdminResponse:
        """The operator syncs every publisher; the route ingests each one's file."""
        return self._publisher_route("post", "publisher-partners/sync")

    def remove_publisher(self, publisher: str) -> AdminResponse:
        """The operator removes *publisher*: its derived rows and every selection of it go with it."""
        return self._publisher_route("delete", f"publisher-partners/{self.publisher_address(publisher)}")

    def open_publishers(self) -> AdminResponse:
        """The operator's publishers list: each publisher with what its stored file authorizes."""
        return self._publisher_route("get", "publisher-partners")

    def open_authorized_properties(self) -> AdminResponse:
        """The operator's authorized-properties page."""
        return self._publisher_route("get", "authorized-properties")

    def open_setup_checklist(self) -> AdminResponse:
        """The operator's setup checklist page."""
        return self._publisher_route("get", "setup-checklist")

    def open_partner_properties(self, publisher: str) -> AdminResponse:
        return self._publisher_route("get", f"publisher-partners/{self.publisher_address(publisher)}/properties")

    def ingest_publisher(self, publisher: str) -> None:
        """The seller ingests *publisher*'s adagents.json, through the operator's routes.

        Added when the seller does not hold it yet, synced when it does (the scheduled
        refresh ingests every publisher the same way).
        """
        added = self.stored_publisher_or_none(publisher) is None
        response = self.add_publisher(publisher) if added else self.sync_publisher_partners()
        assert response.status_code == (201 if added else 200), (
            f"the seller's ingest of {publisher!r} answered {response.status_code}: {response.data[:300]!r}"
        )

    def _publisher_route(self, method: str, path: str, **kwargs: Any) -> AdminResponse:
        """One authenticated request to the tenant's admin route at *path*, on the env's transport.

        The publishers' files are served first, so the route reads what the scenario chose.
        """
        tenant, _principal = self.setup_default_data()  # type: ignore[attr-defined]
        self._commit_factory_data()  # type: ignore[attr-defined]
        self.serve_adagents()
        return self.admin_request(tenant.tenant_id, method, path, **kwargs)

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

        tenant, _principal = self.setup_default_data()
        AdapterConfigFactory(tenant=tenant, adapter_type=adapter_type)
