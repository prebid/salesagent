"""The pinned SDK type a tool's response is DECLARED as, and the domain that declares it.

ONE implementation, two consumers. ``tests/helpers/response_schemas.py`` wants the
domain NAME (it is the schema directory); ``tests/harness/spec_models.py`` wants the
declared OBJECT (it parses the wire body back through it). Both answer the same
question — "where does the pinned SDK declare this tool's response?" — and both used
to answer it themselves, with the same wrong lookup, and were wrong about the same
four tools.

THE DOMAIN TREE IS THE SOURCE, NOT THE FLAT ``adcp.types`` NAMESPACE. The SDK says
why in ``adcp/types/domains/__init__.py``: codegen writes that tree in the schema
bundle's own layout and "defines every class here, so a type name more than one
schema declares is unambiguous BY PATH rather than by a mangled name".

The flat namespace cannot answer either question. It is one ``dir()`` with every
domain's names collapsed into it, so a name two schemas declare arrives mangled, and
the mangling is not a convention a caller could follow. Measured against adcp 9.0.0,
the flat ``<Camel>Response`` name:

- resolves to ``adcp.types.canonical_creative`` — a module with no domain in its
  path — for ``get_products``, ``get_media_buys``, ``get_media_buy_delivery``,
  ``list_creatives``, ``create_media_buy`` and ``update_media_buy``;
- does not exist at all for ``list_creative_formats``, renamed
  ``LegacyListCreativeFormatsResponse`` because the tool is deprecated in AdCP 3.2.

Each consumer failed differently on that, which is why one copy was loud and the
other silent:

- the schema-category consumer lost the category for all seven. Six fell back to a
  bare filename that happened to hit the right file; only
  ``list-creative-formats-response.json`` collides across two categories, so only
  that one refused.
- the parse-back consumer got a class that PARSES and is not the one production
  answers with. ``canonical_creative.GetProductsResponse`` is not in the MRO of
  ``src.core.schemas.product.GetProductsResponse``, so the harness graded the seller
  against a contract the seller never claimed, and the canonical model refuses
  legacy ``format_ids`` outright.

A fallback onto ``Legacy<Camel>Response`` is NOT the fix and was measured before
being rejected: for six of the seven the plain name EXISTS, so
``getattr(x) or getattr(legacy)`` never evaluates its right operand. It fires for
``list_creative_formats`` alone.
"""

from __future__ import annotations

import importlib
from functools import cache
from types import ModuleType

from adcp.types import domains as adcp_domains


def tool_camel(tool: str) -> str:
    """``get_products`` -> ``GetProducts``."""
    return "".join(part.title() for part in tool.split("_"))


@cache
def _domain_package(domain: str) -> ModuleType:
    return importlib.import_module(f"{adcp_domains.__name__}.{domain}")


@cache
def _declarations(tool: str) -> tuple[tuple[str, object], ...]:
    """Every ``(domain, declared object)`` pair for *tool*'s response type.

    Reads ``adcp_domains.DOMAINS``, the SDK's own list, so a domain added upstream is
    searched without this module being told about it.
    """
    name = tool_camel(tool) + "Response"
    found = []
    for domain in adcp_domains.DOMAINS:
        declared = getattr(_domain_package(domain), name, None)
        if declared is not None:
            found.append((domain, declared))
    return tuple(found)


def response_declaration(tool: str) -> tuple[str, object] | tuple[None, None]:
    """The one domain declaring *tool*'s response, and what it declares.

    ``(None, None)`` for a tool the pinned SDK does not type at all — a seller may
    expose more tools than the standard, and ``complete_task`` is the live case.

    Two domains declaring one response name RAISES rather than returning the first.
    Choosing between two live declarations is the guess this module exists to not
    make: picking wrong gives a caller a category for another actor's schema, or a
    model the seller never claimed to satisfy, and both look green while doing it.
    """
    found = _declarations(tool)
    if not found:
        return (None, None)
    if len(found) > 1:
        raise ValueError(
            f"{tool}: {tool_camel(tool)}Response is declared by {len(found)} domain packages "
            f"({', '.join(domain for domain, _ in found)}), so neither its schema category nor "
            f"its parse-back model can be derived. Decide which one this seller's response "
            f"actually conforms to and say so here; do not let it resolve to whichever package "
            f"sorts first."
        )
    return found[0]
