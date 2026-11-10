"""Ingest a publisher's adagents.json: fetch it, validate it, store it, derive from it.

A tenant sells for publishers, and each publisher states in its own
``https://{domain}/.well-known/adagents.json`` whether it authorizes this agent, for which
properties, and which format options its inventory accepts. This is the one place that
file is read. :func:`ingest` fetches it and :func:`apply_document` stores a validated
document and replaces everything derived from it -- the authorized properties and the
``formats[]`` catalog -- in one unit of work (``PublisherRepository.store_document``). The
apply half takes no network, so a seed stores a document the same way an ingest does.

Failure keeps what is stored. managed-networks.mdx: on a 5xx or timeout "serve the
previously cached file for up to 24 hours"; "repeated failures MUST NOT extend the cache";
and a ``last_updated`` older than the cached one is an invalid response. So any failed fetch
-- 404, 5xx, timeout, a schema-invalid file, a non-monotonic ``last_updated`` -- records the
attempt and leaves the document and ``fetched_at`` alone; past the freshness window the
publisher authorizes nothing (``Publisher.is_fresh``). adagents.mdx: a file-level failure
"MUST abort processing for that domain". A failed fetch is retried on the next refresh and
never cached as a negative.

The fetch is the SDK's ``fetch_adagents``, the sanctioned self-pinning dialer -- see
``src/core/security/outbound_http.py``'s module docstring.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from adcp import (
    AdagentsHTTPError,
    AdagentsNotFoundError,
    AdagentsTimeoutError,
    AdagentsValidationError,
    fetch_adagents,
    get_properties_by_agent,
)
from sqlalchemy.orm import Session

from src.core.agent_identity import identifies_agent
from src.core.database.models import Publisher
from src.core.database.repositories.publisher import PublisherRepository, every_publisher
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.uow import PublisherUoW
from src.core.http_utils import canonical_publisher_domain
from src.services.adagents_error_messages import describe_adagents_error

logger = logging.getLogger(__name__)

#: The keys of a ``formats[]`` entry that scope it within the file; the rest is the declaration.
_FORMAT_SCOPE_KEYS = ("applies_to_property_ids", "applies_to_property_tags")


class StaleDocumentError(AdagentsValidationError):
    """A fetched document whose ``last_updated`` is older than the stored one's.

    managed-networks.mdx treats a non-monotonic ``last_updated`` as an invalid response, so
    it is refused like any other invalid file.
    """


class MissingAuthorizedAgentsError(AdagentsValidationError):
    """A document with no top-level ``authorized_agents`` array.

    adagents.mdx: a file-level failure such as a "missing required top-level
    authorized_agents" "MUST abort processing for that domain". The SDK's fetch refuses
    such a file before it reaches here; :func:`apply_document` refuses it too, so no path
    stores one.
    """


@dataclass(frozen=True)
class IngestOutcome:
    """What one ingest did: ``fetch_status`` as stored, and the non-disclosing error of a failure."""

    domain: str
    fetch_status: str
    error: str | None = None
    property_count: int = 0
    format_option_count: int = 0


def _timestamp(value: object) -> datetime | None:
    """An ISO 8601 date-time from the document, or ``None`` when absent or unreadable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _belongs_to(prop: Mapping[str, Any], domain: str, revoked: set[str]) -> bool:
    """Whether *prop*, listed in *domain*'s file, is a property of *domain* that is not revoked.

    ``core/property.json`` ``publisher_domain``: "Optional in adagents.json (file location
    implies domain)". A property naming ANOTHER publisher is a managed network's, and is out
    of scope here: it would be authorized by that publisher's own file.
    """
    own = canonical_publisher_domain(str(prop.get("publisher_domain") or domain))
    return own == domain and own not in revoked


def _property_values(
    prop: Mapping[str, Any], entry: Mapping[str, Any], *, domain: str, entry_index: int, position: int
) -> dict[str, Any] | None:
    """The ``PublisherProperty`` columns for *prop* authorized by *entry*, or ``None`` for a malformed one.

    adagents.mdx: a per-property failure "MUST NOT prevent processing of remaining
    properties" (skip, log, continue), so a property missing what ``core/property.json``
    requires is logged with the publisher's domain, the index of the authorizing entry, the
    property's position and the fields it lacks, and skipped.
    """
    missing = [field for field in ("property_type", "name") if not prop.get(field)]
    if not isinstance(prop.get("identifiers"), list):
        missing.append("identifiers")
    if missing:
        logger.warning(
            "adagents.json of %s: skipping property %d authorized by authorized_agents[%d]: it lacks %s",
            domain,
            position,
            entry_index,
            ", ".join(missing),
        )
        return None
    return {
        "property_id": prop.get("property_id"),
        "property_type": str(prop["property_type"]),
        "name": str(prop["name"]),
        "identifiers": prop["identifiers"],
        "tags": list(prop.get("tags") or []) or None,
        "effective_from": _timestamp(entry.get("effective_from")),
        "effective_until": _timestamp(entry.get("effective_until")),
        "placement_ids": list(entry["placement_ids"]) if entry.get("placement_ids") else None,
        "countries": list(entry["countries"]) if entry.get("countries") else None,
    }


def authorized_property_values(document: Mapping[str, Any], domain: str, agent_url: str) -> list[dict[str, Any]]:
    """The properties *domain*'s *document* authorizes the agent at *agent_url* for, as row values.

    One row per ``authorized_agents[]`` entry naming this agent (``identifies_agent``: the
    canonical URL, path significant) and per property it authorizes, so each row carries its
    entry's qualifiers. Each entry is resolved by the SDK's ``get_properties_by_agent`` over
    a copy of the document holding that entry alone: ``property_ids`` (resolved against the
    top-level ``properties``), ``property_tags`` (tag match on them) and
    ``inline_properties`` (the entry's own ``properties``). Only properties of this file's own
    domain are kept (:func:`_belongs_to`), so the ``publisher_properties`` variant, which names
    OTHER publishers' properties (a managed network's), records nothing: those publishers' own
    files authorize them, and that is out of scope here. The signal variants authorize no
    property.

    ``revoked_publisher_domains``: validators "MUST treat any publisher domain listed here as
    no-longer-authorized", so a file revoking its own domain authorizes nothing.
    ``authorized_agents: []`` authorizes nothing and is not an error (its formats are still
    stored by :func:`format_option_values`).
    """
    revoked = {
        canonical_publisher_domain(str(item["publisher_domain"]))
        for item in document.get("revoked_publisher_domains") or ()
        if isinstance(item, Mapping) and item.get("publisher_domain")
    }
    values: list[dict[str, Any]] = []
    for entry_index, entry in enumerate(document.get("authorized_agents") or ()):
        if not isinstance(entry, Mapping) or not identifies_agent(entry.get("url"), agent_url):
            continue
        scoped = {**document, "authorized_agents": [{**entry, "url": agent_url}]}
        for position, prop in enumerate(get_properties_by_agent(scoped, agent_url)):
            if not _belongs_to(prop, domain, revoked):
                continue
            row = _property_values(prop, entry, domain=domain, entry_index=entry_index, position=position)
            if row is not None and row not in values:
                values.append(row)
    return values


def format_option_values(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ``PublisherFormatOption`` rows of *document*'s ``formats[]``, by ``format_option_id``.

    An entry with no ``format_option_id`` cannot be referenced by a product (the key is
    ``{publisher_domain, format_option_id}``), so it is skipped; of two entries sharing an id
    the first is kept. Each is logged.
    """
    values: dict[str, dict[str, Any]] = {}
    for entry in document.get("formats") or ():
        option_id = entry.get("format_option_id") if isinstance(entry, Mapping) else None
        if not option_id or option_id in values:
            logger.warning("Skipping a formats[] entry with a missing or repeated format_option_id: %r", entry)
            continue
        values[str(option_id)] = {
            "format_option_id": str(option_id),
            "declaration": {key: value for key, value in entry.items() if key not in _FORMAT_SCOPE_KEYS},
            "applies_to_property_ids": list(entry.get("applies_to_property_ids") or []) or None,
            "applies_to_property_tags": list(entry.get("applies_to_property_tags") or []) or None,
        }
    return list(values.values())


def apply_document(
    publishers: PublisherRepository,
    publisher: Publisher,
    document: dict[str, Any],
    *,
    agent_url: str,
    fetched_at: datetime,
) -> None:
    """Store a validated *document* as *publisher*'s, and replace its derived properties and format options.

    Raises :class:`MissingAuthorizedAgentsError` for a document with no ``authorized_agents``
    array and :class:`StaleDocumentError` when its ``last_updated`` is older than the stored
    one's, before anything is written.
    """
    if not isinstance(document.get("authorized_agents"), list):
        raise MissingAuthorizedAgentsError("adagents.json has no authorized_agents array")
    last_updated = _timestamp(document.get("last_updated"))
    if publisher.last_updated is not None and last_updated is not None and last_updated < publisher.last_updated:
        raise StaleDocumentError(
            f"adagents.json last_updated {last_updated.isoformat()} is older than the stored "
            f"{publisher.last_updated.isoformat()}"
        )
    publishers.store_document(
        publisher,
        document,
        fetched_at=fetched_at,
        last_updated=last_updated or publisher.last_updated,
        catalog_etag=document.get("catalog_etag") if isinstance(document.get("catalog_etag"), str) else None,
        properties=authorized_property_values(document, publisher.domain, agent_url),
        format_options=format_option_values(document),
    )


def website_property_id(domain: str) -> str:
    """The ``property_id`` a seeded website on *domain* is listed under: ``core/property-id.json``'s ``^[a-z0-9_]+$``."""
    return re.sub(r"[^a-z0-9]+", "_", canonical_publisher_domain(domain)).strip("_")


def website_document(
    agent_url: str, *, domain: str, property_id: str, name: str, tags: list[str] | None = None
) -> dict[str, Any]:
    """The adagents.json of a publisher authorizing the agent at *agent_url* for one website on *domain*.

    What a seed stores for a publisher it controls (:func:`seed_document`).
    """
    prop: dict[str, Any] = {
        "property_id": property_id,
        "property_type": "website",
        "name": name,
        "identifiers": [{"type": "domain", "value": domain}],
    }
    if tags:
        prop["tags"] = tags
    return {
        "authorized_agents": [
            {
                "url": agent_url,
                "authorized_for": f"Advertising on {name}"[:500],
                "authorization_type": "property_ids",
                "property_ids": [property_id],
            }
        ],
        "properties": [prop],
    }


def seed_document(
    session: Session, tenant_id: str, domain: str, document: dict[str, Any], *, display_name: str | None = None
) -> bool:
    """Store *document* as *tenant_id*'s publisher at *domain* without fetching it, through :func:`apply_document`.

    For a seed that controls the publisher. ``False``, and nothing written, when the tenant
    already holds the publisher. The caller owns the transaction.
    """
    session.flush()  # a tenant the seed has just added is read back below
    publishers = PublisherRepository(session, tenant_id)
    tenant = TenantConfigRepository(session, tenant_id).get_tenant()
    if tenant is None or not publishers.add_publisher(domain, display_name):
        return False
    publisher = publishers.get(domain)
    assert publisher is not None
    apply_document(publishers, publisher, document, agent_url=tenant.agent_url, fetched_at=datetime.now(UTC))
    return True


async def ingest(tenant_id: str, domain: str) -> IngestOutcome:
    """Fetch *domain*'s adagents.json for *tenant_id*'s publisher, and store it or record why not.

    The publisher must already exist (``PublisherRepository.add_publisher``); the fetch runs before the
    unit of work opens, so no transaction is held across the network.
    """
    domain = canonical_publisher_domain(domain)
    document: dict[str, Any] | None = None
    # (fetch_status, non-disclosing error) of a failed fetch. A transport failure the SDK
    # reports as a plain AdagentsValidationError (connection refused, a DNS miss) is
    # recorded "invalid": only the exception's type may be read here, never its text.
    failure: tuple[str, str] | None = None
    try:
        document = await fetch_adagents(domain)
    except AdagentsNotFoundError as exc:
        logger.warning("adagents.json of %s for tenant %s: %s", domain, tenant_id, exc)
        failure = ("not_found", describe_adagents_error(exc))
    except (AdagentsTimeoutError, AdagentsHTTPError) as exc:
        logger.warning("adagents.json of %s for tenant %s: %s", domain, tenant_id, exc)
        failure = ("unreachable", describe_adagents_error(exc))
    except AdagentsValidationError as exc:
        logger.warning("adagents.json of %s for tenant %s: %s", domain, tenant_id, exc)
        failure = ("invalid", describe_adagents_error(exc))
    now = datetime.now(UTC)
    with PublisherUoW(tenant_id) as uow:
        assert uow.publishers is not None and uow.tenant_config is not None
        publisher = uow.publishers.get(domain)
        tenant = uow.tenant_config.get_tenant()
        if publisher is None or tenant is None:
            raise LookupError(f"tenant {tenant_id!r} has no publisher {domain!r}")
        if document is not None:
            try:
                apply_document(uow.publishers, publisher, document, agent_url=tenant.agent_url, fetched_at=now)
            except AdagentsValidationError as exc:
                logger.warning("adagents.json of %s for tenant %s refused: %s", domain, tenant_id, exc)
                failure = ("invalid", describe_adagents_error(exc))
        if failure is not None:
            status, error = failure
            uow.publishers.record_failed_fetch(publisher, status=status, error=error, attempted_at=now)
            return IngestOutcome(domain, status, error)
        return IngestOutcome(domain, "ok", None, len(publisher.properties), len(publisher.format_options))


async def ingest_all(tenant_id: str, domains: Iterable[str]) -> list[IngestOutcome]:
    """:func:`ingest` each of *domains*, one after another, each in its own unit of work."""
    return [await ingest(tenant_id, domain) for domain in domains]


def ingest_sync(tenant_id: str, domains: Iterable[str]) -> list[IngestOutcome]:
    """:func:`ingest_all` from synchronous code (the admin routes, the scheduled refresh)."""
    return asyncio.run(ingest_all(tenant_id, list(domains)))


def refresh_every_publisher() -> list[IngestOutcome]:
    """Ingest every publisher of every tenant: what the scheduled refresh runs.

    A failure of one publisher is recorded on its row and does not stop the others.
    """
    outcomes: list[IngestOutcome] = []
    for tenant_id, domain in every_publisher():
        outcomes.extend(ingest_sync(tenant_id, [domain]))
    return outcomes
