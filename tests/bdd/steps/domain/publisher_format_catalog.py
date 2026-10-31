"""Steps for the local publisher-format-catalog feature (RFC S3).

WHAT MAKES THESE NON-VACUOUS. The catalog is written to the tenant row through
``configure_tenant_field`` and read back only off the SERVED document, so a builder that
never publishes it fails the first scenario. The reference scenario resolves the way a buyer
does (formats.mdx § Discovery flow, step 2): it reads the product's option off the
get_products wire, fetches the adagents.json of the publisher THAT option names, and matches
the option there. Nothing the steps seeded is used as the expected value of the resolution.

``When the buyer requests products`` is the shared step in ``uc_get_products_inventory``.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from src.core.agent_identity import ADAGENTS_JSON_PATH
from src.core.database.models import Tenant
from tests.bdd.steps._outcome_helpers import wire_field
from tests.factories import PricingOptionFactory, ProductFactory
from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION
from tests.helpers.pinned_schema import validator_for

_CATALOG_FIELD = "publisher_format_options"


def _declaration(option_id: str, kind: str, width: int, height: int) -> dict[str, Any]:
    """One ``core/product-format-declaration.json`` document, as a scenario line names it."""
    return {"format_kind": kind, "format_option_id": option_id, "params": {"width": width, "height": height}}


def _tenant_row(ctx: dict) -> Tenant:
    """The env's tenant row as stored, after every Given's write is committed."""
    tenant = ctx["env"].get_one(Tenant, tenant_id=ctx["tenant"].tenant_id)
    assert tenant is not None, f"tenant {ctx['tenant'].tenant_id!r} is not stored"
    return tenant


def _fetched_document(ctx: dict) -> dict[str, Any]:
    """The served adagents.json body, or a loud failure naming what came back instead."""
    response = ctx["adagents_response"]
    assert response.status_code == 200, (
        f"the adagents.json fetch returned {response.status_code}, so there is no document to read: "
        f"{response.text[:300]!r}"
    )
    return dict(response.json())


def _announced_product(ctx: dict) -> dict[str, Any]:
    """The seeded product as the buyer received it on the wire."""
    product_id = ctx["catalog_product"].product_id
    products: list[dict[str, Any]] = wire_field(ctx, "products")
    matches = [product for product in products if product["product_id"] == product_id]
    assert len(matches) == 1, f"product {product_id!r} was announced {len(matches)} times: {products!r}"
    return matches[0]


def _announced_option(ctx: dict, option_id: str) -> dict[str, Any]:
    """The announced product's ``format_options`` entry carrying *option_id*."""
    options = _announced_product(ctx).get("format_options") or []
    matches = [option for option in options if option.get("format_option_id") == option_id]
    assert len(matches) == 1, f"the product announces option {option_id!r} {len(matches)} times: {options!r}"
    return matches[0]


# ── Given steps ─────────────────────────────────────────────────────


@given(
    parsers.parse(
        'the seller\'s publisher format catalog declares option "{option_id}" of kind "{kind}" at {width:d}x{height:d}'
    )
)
def given_catalog_declares(ctx: dict, option_id: str, kind: str, width: int, height: int) -> None:
    ctx["env"].configure_tenant_field(_CATALOG_FIELD, [_declaration(option_id, kind, width, height)])


@given("the seller has no publisher format catalog")
def given_no_catalog(ctx: dict) -> None:
    # Written explicitly, not left at the default: over e2e_rest every scenario shares one
    # tenant row, so a catalog an earlier scenario wrote would otherwise still be there.
    ctx["env"].configure_tenant_field(_CATALOG_FIELD, None)


@given(
    parsers.parse('the seller stores a product declaring publisher option "{option_id}" under its own publisher domain')
)
def given_product_declaring_publisher_option(ctx: dict, option_id: str) -> None:
    """A canonical-only product naming *option_id* in the seller's own publisher namespace.

    ``primary_domain`` is the domain the product's ``publisher_properties`` announce
    (``Product.publisher_domain``); the Then reads both back off the wire rather than trusting
    this value. The declaration copies the catalog entry's kind and size, which is the
    narrowing the pin allows (``canonical-formats.mdx`` § Where to declare a format: "Each entry is a
    full ProductFormatDeclaration"; a catalog-backed one adds ``{publisher_domain,
    format_option_id}``).
    """
    tenant = _tenant_row(ctx)
    entry = next(option for option in tenant.publisher_format_options or () if option["format_option_id"] == option_id)
    declaration = {**entry, "publisher_domain": tenant.primary_domain}
    product = ProductFactory(tenant=tenant, format_ids=None, format_options=[declaration])
    PricingOptionFactory(product=product)
    ctx["catalog_product"] = product
    ctx["catalog_option_id"] = option_id


# ── When steps ──────────────────────────────────────────────────────


@when("the buyer fetches the adagents.json the seller serves at its own publisher domain")
def when_fetch_own_adagents(ctx: dict) -> None:
    ctx["adagents_response"] = ctx["env"].fetch_agent_card(
        path=ADAGENTS_JSON_PATH, host=_tenant_row(ctx).primary_domain
    )


@when("the buyer fetches the adagents.json of the publisher the product's option names")
def when_fetch_named_publisher_adagents(ctx: dict) -> None:
    """Resolve the way a buyer does: the host is the option's ``publisher_domain`` on the wire."""
    option = _announced_option(ctx, ctx["catalog_option_id"])
    publisher_domain = option.get("publisher_domain")
    assert publisher_domain, f"the announced option names no publisher, so there is no catalog to resolve: {option!r}"
    ctx["resolved_option"] = option
    ctx["adagents_response"] = ctx["env"].fetch_agent_card(path=ADAGENTS_JSON_PATH, host=publisher_domain)


# ── Then steps ──────────────────────────────────────────────────────


@then(parsers.parse('the document\'s formats[] declares option "{option_id}" of kind "{kind}" at {width:d}x{height:d}'))
def then_formats_declares(ctx: dict, option_id: str, kind: str, width: int, height: int) -> None:
    formats = _fetched_document(ctx).get("formats")
    assert formats, "the served adagents.json publishes no formats[], so no product reference can resolve in it"
    matches = [entry for entry in formats if entry.get("format_option_id") == option_id]
    assert len(matches) == 1, f"formats[] declares {option_id!r} {len(matches)} times: {formats!r}"
    entry = matches[0]
    assert entry["format_kind"] == kind, f"formats[] declares {option_id!r} as {entry['format_kind']!r}, not {kind!r}"
    assert (entry["params"].get("width"), entry["params"].get("height")) == (width, height), (
        f"formats[] declares {option_id!r} at {entry['params']!r}, not {width}x{height}"
    )


@then("the document is valid against the pinned adagents.json schema")
def then_document_valid(ctx: dict) -> None:
    errors = [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in validator_for(f"{EXPECTED_SPEC_VERSION}/adagents.json").iter_errors(_fetched_document(ctx))
    ]
    assert errors == [], f"the served adagents.json is not valid against the pinned schema: {errors}"


@then("the document carries no formats[]")
def then_document_has_no_formats(ctx: dict) -> None:
    document = _fetched_document(ctx)
    assert "formats" not in document, f"a seller with no catalog published formats[]: {document['formats']!r}"


@then(parsers.parse('the product announces option "{option_id}" under the domain of the publisher it sells'))
def then_product_announces_publisher_option(ctx: dict, option_id: str) -> None:
    """The option's namespace is the publisher whose inventory the product sells.

    Both values come off the wire: the option's ``publisher_domain`` and the product's own
    ``publisher_properties[].publisher_domain``. An option stamped with any other domain would
    name a catalog this seller does not serve.
    """
    option = _announced_option(ctx, option_id)
    publishers = {selector["publisher_domain"] for selector in _announced_product(ctx)["publisher_properties"]}
    assert option.get("publisher_domain") in publishers, (
        f"the option names publisher {option.get('publisher_domain')!r}, but the product sells {sorted(publishers)}"
    )


@then("that publisher's formats[] declares the product's option with the same format_kind")
def then_publisher_catalog_resolves_option(ctx: dict) -> None:
    option = ctx["resolved_option"]
    formats = _fetched_document(ctx).get("formats") or []
    matches = [entry for entry in formats if entry.get("format_option_id") == option["format_option_id"]]
    assert len(matches) == 1, (
        f"the publisher's formats[] does not resolve {option['format_option_id']!r} exactly once: {formats!r}"
    )
    assert matches[0]["format_kind"] == option["format_kind"], (
        f"the publisher declares {option['format_option_id']!r} as {matches[0]['format_kind']!r}, "
        f"but the product announces it as {option['format_kind']!r}"
    )
