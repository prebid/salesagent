"""Factories for the publishers a tenant sells for, and the documents that authorize it.

A property is authorized only by a publisher's stored adagents.json, so a test that needs
an authorized property stores a DOCUMENT -- :func:`authorize_properties` -- through the
ingest service's apply half (``publisher_ingest.apply_document``), exactly as a seed does.
No factory writes a ``PublisherProperty`` row directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

import factory
from factory import LazyAttribute, SubFactory
from factory import Sequence as FactorySequence

from src.core.database.models import Publisher
from src.core.database.repositories.publisher import PublisherRepository
from src.core.http_utils import canonical_publisher_domain
from src.services.publisher_ingest import apply_document, website_property_id
from tests.factories.core import TenantFactory


class PublisherFactory(factory.alchemy.SQLAlchemyModelFactory):
    """A publisher the tenant holds, never fetched: it authorizes nothing until a document is stored."""

    class Meta:
        model = Publisher
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        sqlalchemy_get_or_create = ("tenant_id", "domain")
        # The model has no ``tenant`` relationship (publishers are read through the
        # tenant's viewonly ``publishers``); the row is keyed by tenant_id.
        exclude = ("tenant",)

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    domain = FactorySequence(lambda n: f"publisher-{n:04d}.example.com")
    display_name = LazyAttribute(lambda o: o.domain)

    @classmethod
    def _create(cls, model_class: type, *args: Any, **kwargs: Any) -> Any:
        kwargs["domain"] = canonical_publisher_domain(kwargs["domain"])
        return super()._create(model_class, *args, **kwargs)


def website(property_id: str, domain: str, tags: Sequence[str] = ()) -> dict[str, Any]:
    """One ``core/property.json`` website on *domain*."""
    prop: dict[str, Any] = {
        "property_id": property_id,
        "property_type": "website",
        "name": property_id,
        "identifiers": [{"type": "domain", "value": domain}],
    }
    if tags:
        prop["tags"] = list(tags)
    return prop


def store_document(
    tenant: Any, domain: str, document: dict[str, Any], *, fetched_at: datetime | None = None
) -> Publisher:
    """Store *document* as *tenant*'s publisher *domain*'s adagents.json, through the apply half, and commit."""
    publisher = PublisherFactory(tenant=tenant, domain=domain)
    session = PublisherFactory._meta.sqlalchemy_session
    apply_document(
        PublisherRepository(session, tenant.tenant_id),
        publisher,
        document,
        agent_url=tenant.agent_url,
        fetched_at=fetched_at or datetime.now(UTC),
    )
    session.commit()
    return publisher


def authorize_properties(tenant: Any, domain: str, properties: Iterable[tuple[str, Sequence[str]]]) -> Publisher:
    """Store a document in which publisher *domain* authorizes *tenant*'s agent for *properties*.

    *properties* are ``(property_id, tags)``. What the publisher's stored document already
    lists, authorizes and declares in ``formats[]`` is kept, so successive calls accumulate.
    """
    domain = canonical_publisher_domain(domain)
    existing = dict(PublisherFactory(tenant=tenant, domain=domain).adagents or {})
    listed = {prop["property_id"]: prop for prop in existing.get("properties") or ()}
    for property_id, tags in properties:
        listed.setdefault(property_id, website(property_id, domain, tags))
    document = {
        **existing,
        "authorized_agents": [
            {
                "url": tenant.agent_url,
                "authorized_for": "Test inventory",
                "authorization_type": "property_ids",
                "property_ids": list(listed),
            }
        ],
        "properties": list(listed.values()),
    }
    return store_document(tenant, domain, document)


def declare_format_options(tenant: Any, domain: str, entries: Iterable[dict[str, Any]]) -> Publisher:
    """Store publisher *domain*'s document with *entries* in its ``formats[]``, replacing entries of the same id.

    Everything else the stored document says is kept; a publisher with none gets a
    catalog-only document (``authorized_agents: []``), which authorizes nothing.
    """
    domain = canonical_publisher_domain(domain)
    existing = dict(PublisherFactory(tenant=tenant, domain=domain).adagents or {"authorized_agents": []})
    formats = {entry["format_option_id"]: entry for entry in existing.get("formats") or ()}
    formats.update({entry["format_option_id"]: entry for entry in entries})
    return store_document(tenant, domain, {**existing, "formats": list(formats.values())})


def declare_referenced_formats(tenant: Any, declarations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """*declarations* as a product stores them, each publisher-scoped one declared by its publisher first.

    A test that authors a publisher-scoped declaration in full means the publisher's
    catalog declares it: the publisher's document gets that ``formats[]`` entry, and the
    product keeps the key alone (``publisher_format_catalog.stored_declaration``), as every
    product does. A key with nothing more references the catalog as it stands. The host this
    agent serves its own adagents.json at is never given a catalog: that file carries no
    ``formats[]`` (``src/core/signing/trust_root.py``).
    """
    from src.core.publisher_format_catalog import option_key, stored_declaration

    stored = []
    for declaration in declarations:
        key = option_key(declaration)
        if key is not None and set(declaration) - {"publisher_domain", "format_option_id"}:
            if key[0] != canonical_publisher_domain(tenant.virtual_host_name):
                entry = {k: v for k, v in declaration.items() if k != "publisher_domain"}
                declare_format_options(tenant, key[0], [entry])
        stored.append(dict(stored_declaration(declaration)))
    return stored


def authorize_publishers(tenant: Any, *domains: str) -> None:
    """Each publisher of *domains* authorizes *tenant* for one untagged website named after the domain."""
    for domain in domains:
        authorize_properties(tenant, domain, [(domain_property_id(domain), ())])


def authorize_selected_properties(tenant: Any, selectors: Iterable[dict]) -> None:
    """Each publisher *selectors* name authorizes *tenant* for every id and tag they name.

    A ``by_id`` selector's ids become properties of its publisher; a ``by_tag`` selector's
    tags each become a property ``<tag>_carrier`` carrying it; an ``all`` selector's
    publisher authorizes one untagged website.
    """
    for selector in selectors:
        domain = selector["publisher_domain"]
        properties: list[tuple[str, Sequence[str]]] = [(pid, ()) for pid in selector.get("property_ids") or ()]
        properties += [(f"{tag}_carrier", (tag,)) for tag in selector.get("property_tags") or ()]
        authorize_properties(tenant, domain, properties or [(domain_property_id(domain), ())])


def domain_property_id(domain: str) -> str:
    """The property id of the one website :func:`authorize_publishers` gives *domain* (as a seed lists it)."""
    return website_property_id(domain)


class SellsPublishersMixin:
    """For a factory whose model stores ``publisher_selections`` and ``format_options``.

    A selection references its publisher by foreign key, so each publisher a selection
    names is created (never fetched, authorizing nothing) before the owning row is
    inserted. Authorization is a separate, explicit step (:func:`authorize_properties`).
    A publisher-scoped format declaration authored in full is declared in its publisher's
    catalog and stored as its key (:func:`declare_referenced_formats`).
    """

    @classmethod
    def _create(cls, model_class: type, *args: Any, **kwargs: Any) -> Any:
        tenant = kwargs["tenant"]
        for domain in dict.fromkeys(row.publisher_domain for row in kwargs.get("publisher_selections") or ()):
            PublisherFactory(tenant=tenant, domain=domain)
        if kwargs.get("format_options"):
            kwargs["format_options"] = declare_referenced_formats(tenant, kwargs["format_options"])
        return super()._create(model_class, *args, **kwargs)  # type: ignore[misc]


def held_selections(session: Any, model: type, tenant_id: str, selectors: Iterable[dict]) -> list[Any]:
    """Selection rows of *model* storing *selectors*, for a row a test builds by hand on *session*.

    Each publisher the selectors name is held first (never fetched, authorizing nothing),
    so the selection's foreign key holds. Call it before adding the owning row.
    """
    # An entry naming no publisher is no selector (the old JSON column admitted anything).
    selectors = [s for s in selectors if isinstance(s, dict) and s.get("publisher_domain")]
    publishers = PublisherRepository(session, tenant_id)
    for domain in dict.fromkeys(canonical_publisher_domain(s["publisher_domain"]) for s in selectors):
        publishers.add_publisher(domain)
    return model.from_selectors(tenant_id, selectors)
