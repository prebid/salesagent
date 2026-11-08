"""Which publisher catalog a product format declaration may name.

AdCP 3.2.7 identifies a format by ``format_kind`` plus ``params``. ``format_option_id`` is
optional, and ``publisher_domain`` only namespaces an option taken from a real publisher's
catalog: ``core/product-format-declaration.json`` ``publisher_domain`` is the "Namespace for
``format_option_id`` when this declaration references or narrows a publisher-declared format
option from that publisher's adagents.json top-level ``formats[]``. Product-local options omit
this field". ``core/format-option-ref.json`` resolves a ``scope: "publisher"`` reference
"against the named publisher's adagents.json top-level ``formats[]`` catalog", and
``core/product.json`` ``format_options`` says an unresolved format-option reference "MUST fail
closed".

That catalog is the publisher's to declare. ``adagents.json#/properties/formats`` is the
"Publisher-authoritative format catalog"; ``canonical-formats.mdx`` § Where to declare a format
gives it authority "Publisher"; and ``core/placement.json`` says a seller's attribution "does
not grant the seller authority to mint IDs in that publisher's catalog namespace". So this
seller publishes no ``formats[]`` (``src/core/signing/trust_root.py``), and a declaration
naming the host this agent serves its own adagents.json at names a catalog that does not
exist: no buyer can resolve it. The product and inventory-profile forms refuse one.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import TypeAdapter


@functools.cache
def format_declarations_type() -> TypeAdapter:
    """The pinned type of a list of ``core/product-format-declaration.json`` documents.

    Read off the DTO that serves a product's ``format_options`` rather than restated, so an
    authoring surface refuses exactly what ``convert_product_model_to_schema`` would refuse
    later.
    """
    from src.core.schemas.product import Product as ProductSchema

    return TypeAdapter(ProductSchema.model_fields["format_options"].annotation)


def agent_host_refusal(declarations: Iterable[Mapping[str, Any]] | None, agent_host: str) -> str | None:
    """Why *declarations* cannot be served, when one names *agent_host* as its publisher; else ``None``.

    *agent_host* is the hostname this agent serves its adagents.json at
    (``Tenant.virtual_host_name``), and that file carries no ``formats[]``.
    """
    named = sorted(
        {
            str(declaration.get("format_option_id"))
            for declaration in declarations or ()
            if declaration.get("publisher_domain") == agent_host
        }
    )
    if not named:
        return None
    return (
        f"format_options {', '.join(named)} name {agent_host}, the host this sales agent answers at, "
        f"as their publisher_domain. publisher_domain names the publisher whose adagents.json "
        f"declares the option, and this agent publishes no format options. Remove publisher_domain "
        f"to declare them product-local."
    )


def refuse_options_under_agent_host(declarations: Iterable[Mapping[str, Any]] | None, agent_host: str) -> None:
    """``ValueError`` when a declaration names *agent_host* as the publisher of its option."""
    refusal = agent_host_refusal(declarations, agent_host)
    if refusal:
        raise ValueError(refusal)
