"""Integration tests for property-list filtering in get_products (faithful intersection).

Exercises ``_get_products_impl`` end-to-end through the real
``PropertyIntersection`` + ``AuthorizedPropertyRepository`` against a real
database. These tests are the proof of the namespace fix: a product's
``by_id``/``by_tag`` selectors reference AuthorizedProperty IDs (slugs) and
tags, which are resolved to the rows' *identifier values* (domains) and
intersected against the buyer's resolved property_list — also identifier
values. AuthorizedProperty rows are seeded so the slug→domain resolution has
something to resolve against (an earlier shortcut compared slugs to domains
directly and never matched in production).

Filtering rules:
- ``selection_type="all"``: unbounded → always included.
- product covers no buyer property (no identifier-value overlap) → excluded.
- ``property_targeting_allowed=False``: the buyer's list must select EVERY
  covered property; ``True``: any covered property matching is enough.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import PropertyListReference

from src.core.database.repositories.authorized_property import AuthorizedPropertyRepository
from src.core.tools.media_buy_create import _build_property_list_advisories
from tests.factories import (
    AuthorizedPropertyFactory,
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    TenantFactory,
)
from tests.harness.product import ProductEnv
from tests.helpers.adcp_factories import create_test_identifiers

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_property_list_ref(
    agent_url: str = "https://propertylist.example.com",
    list_id: str = "test_list",
) -> dict:
    """Build a PropertyListReference dict suitable for call_impl."""
    return {"agent_url": agent_url, "list_id": list_id}


def _by_id_props(property_ids: list[str], domain: str = "example.com") -> list[dict]:
    """A product's ``publisher_properties`` payload selecting specific property IDs."""
    return [{"publisher_domain": domain, "property_ids": property_ids, "selection_type": "by_id"}]


def _by_tag_props(tags: list[str], domain: str = "example.com") -> list[dict]:
    """A product's ``publisher_properties`` payload selecting by tag."""
    return [{"publisher_domain": domain, "property_tags": tags, "selection_type": "by_tag"}]


def _domain_ids(*domains: str) -> list[dict]:
    """AuthorizedProperty ``identifiers`` payload for one or more domain values."""
    return [{"type": "domain", "value": d} for d in domains]


# ---------------------------------------------------------------------------
# all-selector — unbounded, always included
# ---------------------------------------------------------------------------


class TestPropertyListFilteringAllSelectorE2E:
    """Products with selection_type='all' always pass through filtering."""

    @pytest.mark.asyncio
    async def test_all_selector_always_included(self, integration_db):
        """selection_type='all' is unbounded — included regardless of the resolved list."""
        with ProductEnv(tenant_id="plf-all", principal_id="plf-all-p") as env:
            tenant = TenantFactory(tenant_id="plf-all", subdomain="plf-all")
            PrincipalFactory(tenant=tenant, principal_id="plf-all-p")

            p = ProductFactory(
                tenant=tenant,
                product_id="all_selector",
                name="All Properties Product",
                property_tags=None,
                properties=[{"publisher_domain": "example.com", "selection_type": "all"}],
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Buyer's resolved list is narrow — the all-selector is still included.
            env.set_property_list(["espn.com"])

            response = await env.call_impl(brief="all selector test", property_list=_make_property_list_ref())

            assert "all_selector" in [p.product_id for p in response.products]


# ---------------------------------------------------------------------------
# by_id — slugs resolved to identifier values via AuthorizedPropertyRepository
# ---------------------------------------------------------------------------


class TestPropertyListFilteringByIdE2E:
    """by_id ``property_ids`` are AuthorizedProperty slugs resolved to identifier values."""

    @pytest.mark.asyncio
    async def test_by_id_overlap_included(self, integration_db):
        """Product whose by_id slug resolves to a domain in the buyer's list is included."""
        with ProductEnv(tenant_id="plf-byid", principal_id="plf-byid-p") as env:
            tenant = TenantFactory(tenant_id="plf-byid", subdomain="plf-byid")
            PrincipalFactory(tenant=tenant, principal_id="plf-byid-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="prop_espn",
                publisher_domain="example.com",
                identifiers=_domain_ids("espn.com"),
            )

            p = ProductFactory(
                tenant=tenant,
                product_id="byid_overlap",
                name="By Id Overlap",
                property_tags=None,
                properties=_by_id_props(["prop_espn"]),
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Buyer's list resolves to the SAME domain the slug maps to.
            env.set_property_list(["espn.com"])

            response = await env.call_impl(brief="by_id overlap", property_list=_make_property_list_ref())

            assert "byid_overlap" in [p.product_id for p in response.products]

    @pytest.mark.asyncio
    async def test_by_id_no_overlap_excluded(self, integration_db):
        """Product whose by_id domain differs from the buyer's list is excluded."""
        with ProductEnv(tenant_id="plf-byidno", principal_id="plf-byidno-p") as env:
            tenant = TenantFactory(tenant_id="plf-byidno", subdomain="plf-byidno")
            PrincipalFactory(tenant=tenant, principal_id="plf-byidno-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="prop_espn",
                publisher_domain="example.com",
                identifiers=_domain_ids("espn.com"),
            )

            p = ProductFactory(
                tenant=tenant,
                product_id="byid_noovlp",
                name="By Id No Overlap",
                property_tags=None,
                properties=_by_id_props(["prop_espn"]),
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Buyer's list resolves to a different domain — no overlap.
            env.set_property_list(["nytimes.com"])

            response = await env.call_impl(brief="by_id no overlap", property_list=_make_property_list_ref())

            assert "byid_noovlp" not in [p.product_id for p in response.products]

    @pytest.mark.asyncio
    async def test_by_id_unknown_slug_excluded(self, integration_db):
        """A by_id slug with no AuthorizedProperty row resolves to nothing → excluded.

        This is the teeth of the fix: an unknown slug is NOT compared as a value;
        it must resolve to a row's identifier values (here it resolves to none).
        """
        with ProductEnv(tenant_id="plf-ghost", principal_id="plf-ghost-p") as env:
            tenant = TenantFactory(tenant_id="plf-ghost", subdomain="plf-ghost")
            PrincipalFactory(tenant=tenant, principal_id="plf-ghost-p")
            # No AuthorizedProperty seeded for "ghost_prop".

            p = ProductFactory(
                tenant=tenant,
                product_id="byid_ghost",
                name="By Id Ghost",
                property_tags=None,
                properties=_by_id_props(["ghost_prop"]),
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            env.set_property_list(["ghost_prop"])  # even if buyer lists the slug literally, it's not a domain value

            response = await env.call_impl(brief="by_id ghost", property_list=_make_property_list_ref())

            assert "byid_ghost" not in [p.product_id for p in response.products]


# ---------------------------------------------------------------------------
# by_tag — tags resolved to rows then to identifier values
# ---------------------------------------------------------------------------


class TestPropertyListFilteringByTagE2E:
    """by_tag selectors resolve tags via the repo to rows, then to identifier values."""

    @pytest.mark.asyncio
    async def test_by_tag_overlap_included(self, integration_db):
        """A tagged property whose domain is in the buyer's list keeps the product."""
        with ProductEnv(tenant_id="plf-tag", principal_id="plf-tag-p") as env:
            tenant = TenantFactory(tenant_id="plf-tag", subdomain="plf-tag")
            PrincipalFactory(tenant=tenant, principal_id="plf-tag-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="prop_sports",
                publisher_domain="example.com",
                identifiers=_domain_ids("espn.com"),
                tags=["sports"],
            )

            p = ProductFactory(
                tenant=tenant,
                product_id="bytag_overlap",
                name="By Tag Overlap",
                property_tags=None,
                properties=_by_tag_props(["sports"]),
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            env.set_property_list(["espn.com"])

            response = await env.call_impl(brief="by_tag overlap", property_list=_make_property_list_ref())

            assert "bytag_overlap" in [p.product_id for p in response.products]

    @pytest.mark.asyncio
    async def test_by_tag_no_matching_tag_excluded(self, integration_db):
        """A by_tag product whose tag matches no AuthorizedProperty is excluded (no longer silently)."""
        with ProductEnv(tenant_id="plf-tagno", principal_id="plf-tagno-p") as env:
            tenant = TenantFactory(tenant_id="plf-tagno", subdomain="plf-tagno")
            PrincipalFactory(tenant=tenant, principal_id="plf-tagno-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="prop_sports",
                publisher_domain="example.com",
                identifiers=_domain_ids("espn.com"),
                tags=["sports"],
            )

            p = ProductFactory(
                tenant=tenant,
                product_id="bytag_nomatch",
                name="By Tag No Match",
                property_tags=None,
                properties=_by_tag_props(["news"]),  # no property carries the 'news' tag
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            env.set_property_list(["espn.com"])

            response = await env.call_impl(brief="by_tag no match", property_list=_make_property_list_ref())

            assert "bytag_nomatch" not in [p.product_id for p in response.products]


# ---------------------------------------------------------------------------
# strict vs permissive (property_targeting_allowed)
# ---------------------------------------------------------------------------


class TestPropertyListFilteringStrictModeE2E:
    """property_targeting_allowed=False requires the buyer's list to select every covered property."""

    @pytest.mark.asyncio
    async def test_strict_partial_overlap_excluded(self, integration_db):
        """Strict product covering two domains is excluded when the buyer accepts only one."""
        with ProductEnv(tenant_id="plf-strict", principal_id="plf-strict-p") as env:
            tenant = TenantFactory(tenant_id="plf-strict", subdomain="plf-strict")
            PrincipalFactory(tenant=tenant, principal_id="plf-strict-p")
            AuthorizedPropertyFactory(
                tenant=tenant, property_id="prop_a", publisher_domain="example.com", identifiers=_domain_ids("espn.com")
            )
            AuthorizedPropertyFactory(
                tenant=tenant, property_id="prop_b", publisher_domain="example.com", identifiers=_domain_ids("cnn.com")
            )

            p = ProductFactory(
                tenant=tenant,
                product_id="strict_partial",
                name="Strict Partial",
                property_tags=None,
                properties=_by_id_props(["prop_a", "prop_b"]),
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Buyer accepts only one of the two covered domains.
            env.set_property_list(["espn.com"])

            response = await env.call_impl(brief="strict partial", property_list=_make_property_list_ref())

            assert "strict_partial" not in [p.product_id for p in response.products]

    @pytest.mark.asyncio
    async def test_strict_full_subset_included(self, integration_db):
        """Strict product is included when the buyer accepts all its covered domains."""
        with ProductEnv(tenant_id="plf-subset", principal_id="plf-subset-p") as env:
            tenant = TenantFactory(tenant_id="plf-subset", subdomain="plf-subset")
            PrincipalFactory(tenant=tenant, principal_id="plf-subset-p")
            AuthorizedPropertyFactory(
                tenant=tenant, property_id="prop_a", publisher_domain="example.com", identifiers=_domain_ids("espn.com")
            )
            AuthorizedPropertyFactory(
                tenant=tenant, property_id="prop_b", publisher_domain="example.com", identifiers=_domain_ids("cnn.com")
            )

            p = ProductFactory(
                tenant=tenant,
                product_id="strict_subset",
                name="Strict Full Subset",
                property_tags=None,
                properties=_by_id_props(["prop_a", "prop_b"]),
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            env.set_property_list(["espn.com", "cnn.com", "extra.com"])

            response = await env.call_impl(brief="strict subset", property_list=_make_property_list_ref())

            assert "strict_subset" in [p.product_id for p in response.products]

    @pytest.mark.asyncio
    async def test_permissive_partial_overlap_included(self, integration_db):
        """property_targeting_allowed=True: any overlap is sufficient."""
        with ProductEnv(tenant_id="plf-perm", principal_id="plf-perm-p") as env:
            tenant = TenantFactory(tenant_id="plf-perm", subdomain="plf-perm")
            PrincipalFactory(tenant=tenant, principal_id="plf-perm-p")
            AuthorizedPropertyFactory(
                tenant=tenant, property_id="prop_a", publisher_domain="example.com", identifiers=_domain_ids("espn.com")
            )
            AuthorizedPropertyFactory(
                tenant=tenant, property_id="prop_b", publisher_domain="example.com", identifiers=_domain_ids("cnn.com")
            )

            p = ProductFactory(
                tenant=tenant,
                product_id="perm_partial",
                name="Permissive Partial",
                property_tags=None,
                properties=_by_id_props(["prop_a", "prop_b"]),
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            env.set_property_list(["espn.com"])  # only one of the two overlaps — enough in permissive mode

            response = await env.call_impl(brief="permissive partial", property_list=_make_property_list_ref())

            assert "perm_partial" in [p.product_id for p in response.products]


# ---------------------------------------------------------------------------
# empty resolved list
# ---------------------------------------------------------------------------


class TestPropertyListFilteringEmptyResolvedListE2E:
    """Empty resolved property list excludes by_id products but keeps unbounded ones."""

    @pytest.mark.asyncio
    async def test_empty_resolved_excludes_by_id_keeps_all(self, integration_db):
        """Empty buyer list: by_id product excluded, selection_type='all' kept."""
        with ProductEnv(tenant_id="plf-empty", principal_id="plf-empty-p") as env:
            tenant = TenantFactory(tenant_id="plf-empty", subdomain="plf-empty")
            PrincipalFactory(tenant=tenant, principal_id="plf-empty-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="prop_espn",
                publisher_domain="example.com",
                identifiers=_domain_ids("espn.com"),
            )

            p_all = ProductFactory(
                tenant=tenant,
                product_id="empty_all",
                name="All Selector Product",
                property_tags=None,
                properties=[{"publisher_domain": "example.com", "selection_type": "all"}],
            )
            PricingOptionFactory(product=p_all, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            p_byid = ProductFactory(
                tenant=tenant,
                product_id="empty_byid",
                name="By Id Product",
                property_tags=None,
                properties=_by_id_props(["prop_espn"]),
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p_byid, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            env.set_property_list([])

            response = await env.call_impl(brief="empty resolved", property_list=_make_property_list_ref())

            product_ids = [p.product_id for p in response.products]
            assert "empty_all" in product_ids
            assert "empty_byid" not in product_ids


# ---------------------------------------------------------------------------
# comprehensive scenario
# ---------------------------------------------------------------------------


class TestPropertyListFilteringCombinedE2E:
    """One request covering all/by_id/by_tag × overlap/strict in a single catalog."""

    @pytest.mark.asyncio
    async def test_combined_filtering_correctness(self, integration_db):
        """Five products spanning every filtering scenario, one buyer list (espn.com)."""
        with ProductEnv(tenant_id="plf-combo", principal_id="plf-combo-p") as env:
            tenant = TenantFactory(tenant_id="plf-combo", subdomain="plf-combo")
            PrincipalFactory(tenant=tenant, principal_id="plf-combo-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="combo_espn",
                publisher_domain="example.com",
                identifiers=_domain_ids("espn.com"),
                tags=["sports"],
            )
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="combo_cnn",
                publisher_domain="example.com",
                identifiers=_domain_ids("cnn.com"),
            )

            def _product(product_id: str, properties: list[dict], *, targeting_allowed: bool = False) -> None:
                p = ProductFactory(
                    tenant=tenant,
                    product_id=product_id,
                    name=product_id,
                    property_tags=None,
                    properties=properties,
                    property_targeting_allowed=targeting_allowed,
                )
                PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            _product("combo_all", [{"publisher_domain": "example.com", "selection_type": "all"}])
            _product("combo_byid_match", _by_id_props(["combo_espn"]))  # strict, exact single match → included
            _product(
                "combo_bytag_match", _by_tag_props(["sports"]), targeting_allowed=True
            )  # tag → espn.com → included
            _product("combo_no_match", _by_id_props(["combo_cnn"]))  # cnn.com not in list → excluded
            _product(
                "combo_strict_partial", _by_id_props(["combo_espn", "combo_cnn"])
            )  # strict, cnn missing → excluded

            env.set_property_list(["espn.com"])

            response = await env.call_impl(brief="combined", property_list=_make_property_list_ref())
            product_ids = [p.product_id for p in response.products]

            assert "combo_all" in product_ids, "all-selector always matches"
            assert "combo_byid_match" in product_ids, "by_id exact match"
            assert "combo_bytag_match" in product_ids, "by_tag resolves to a matching domain"
            assert "combo_no_match" not in product_ids, "by_id with no domain overlap excluded"
            assert "combo_strict_partial" not in product_ids, "strict product missing one covered domain excluded"


# ---------------------------------------------------------------------------
# AuthorizedPropertyRepository — direct read paths backing the intersection
# ---------------------------------------------------------------------------


class TestAuthorizedPropertyRepositoryE2E:
    """Direct coverage of the repository methods the faithful intersection relies on."""

    @pytest.mark.asyncio
    async def test_list_by_ids_and_by_domain_and_by_tags(self, integration_db):
        with ProductEnv(tenant_id="apr-t", principal_id="apr-p") as env:
            tenant = TenantFactory(tenant_id="apr-t", subdomain="apr-t")
            PrincipalFactory(tenant=tenant, principal_id="apr-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="apr_espn",
                publisher_domain="pub.example",
                identifiers=_domain_ids("espn.com"),
                tags=["sports", "premium"],
            )
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="apr_cnn",
                publisher_domain="pub.example",
                identifiers=_domain_ids("cnn.com"),
                tags=["news"],
            )
            # A different tenant's row must never leak into results.
            other = TenantFactory(tenant_id="apr-other", subdomain="apr-other")
            AuthorizedPropertyFactory(
                tenant=other,
                property_id="apr_espn",
                publisher_domain="pub.example",
                identifiers=_domain_ids("evil.com"),
            )

            repo = AuthorizedPropertyRepository(env._session, "apr-t")

            by_ids = repo.list_by_ids("pub.example", ["apr_espn", "apr_cnn", "missing"])
            assert {r.property_id for r in by_ids} == {"apr_espn", "apr_cnn"}
            # Tenant scoping: our espn row, not the other tenant's.
            espn = next(r for r in by_ids if r.property_id == "apr_espn")
            assert espn.identifiers == _domain_ids("espn.com")

            by_domain = repo.list_by_domain("pub.example")
            assert {r.property_id for r in by_domain} == {"apr_espn", "apr_cnn"}

            by_tags = repo.list_by_tags("pub.example", ["sports"])
            assert {r.property_id for r in by_tags} == {"apr_espn"}

            assert repo.list_by_ids("pub.example", []) == []
            assert repo.list_by_tags("pub.example", []) == []


class TestCreateAdvisoryRealProduct:
    """The create zero-overlap advisory runs the REAL intersection against a REAL ORM product.

    ``_build_property_list_advisories`` must convert each ORM product to its schema
    form so the intersection sees ``publisher_properties`` — the ORM model has no
    such attribute (only ``effective_properties``). Passing the raw ORM model
    resolves every product to an empty covered set and logs a false zero-overlap
    on every property_list buy; the advisory unit tests mock the conversion +
    intersection, so only this real-product test catches that.
    """

    @staticmethod
    def _advisory_inputs(env, tenant, *, buyer_domain: str):
        """Seed one by_id product (prop_espn → espn.com) and build the advisory call inputs."""
        AuthorizedPropertyFactory(
            tenant=tenant,
            property_id="prop_espn",
            publisher_domain="example.com",
            identifiers=_domain_ids("espn.com"),
        )
        product = ProductFactory(
            tenant=tenant,
            product_id="adv_prod",
            name="Advisory Product",
            property_tags=None,
            properties=_by_id_props(["prop_espn"]),
            property_targeting_allowed=True,
        )
        PricingOptionFactory(product=product, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

        ref = PropertyListReference(agent_url="https://propertylist.example.com", list_id="adv_list")
        package = MagicMock()
        package.product_id = "adv_prod"
        package.package_id = "adv_pkg"
        package.targeting_overlay = MagicMock()
        package.targeting_overlay.property_list = ref

        repo = AuthorizedPropertyRepository(env._session, tenant.tenant_id)
        resolved = {(str(ref.agent_url), ref.list_id): create_test_identifiers(buyer_domain)}
        return [package], {"adv_prod": product}, repo, resolved

    @pytest.mark.asyncio
    async def test_advisory_converts_orm_product_for_intersection(self, integration_db):
        """The advisory converts the ORM product to schema, and the conversion yields a
        product the intersection resolves by identifier value.

        Asserting on the conversion + intersection (rather than the WARNING log) keeps
        this robust to logging state other tests leave behind in a full-suite run. A
        regression to passing the raw ORM model makes the spy never fire (the advisory
        skipped the conversion) and resolves every product to an empty covered set.
        """
        from src.core.product_conversion import convert_product_model_to_schema as _real_convert
        from src.services.property_intersection import PropertyIntersection

        with ProductEnv(tenant_id="adv-conv", principal_id="adv-conv-p") as env:
            tenant = TenantFactory(tenant_id="adv-conv", subdomain="adv-conv")
            PrincipalFactory(tenant=tenant, principal_id="adv-conv-p")
            packages, product_map, repo, _resolved = self._advisory_inputs(env, tenant, buyer_domain="nytimes.com")

            converted: list = []

            def _spy(model, *args, **kwargs):
                schema = _real_convert(model, *args, **kwargs)
                converted.append(schema)
                return schema

            with patch("src.core.product_conversion.convert_product_model_to_schema", side_effect=_spy):
                advisories = _build_property_list_advisories(packages, product_map, repo, _resolved)

            # The advisory converted the ORM product instead of passing it raw...
            assert converted, "advisory did not convert the ORM product to schema before intersecting"
            # ...and the converted product carries the by_id selectors the intersection reads.
            schema = converted[0]
            assert schema.publisher_properties, "converted product must carry publisher_properties selectors"

            # The zero-overlap outcome is RETURNED as a buyer-visible advisory — a
            # silent exception inside the helper (its broad accept-with-context
            # swallow) would yield [] here and fail loudly, instead of hollowing
            # the test out the way a log-only contract would.
            assert [a.code for a in advisories] == ["PRODUCT_UNAVAILABLE"]
            assert advisories[0].details and advisories[0].details["reason"] == "no_property_overlap"

            # The conversion yields correct intersection behavior against the real repo:
            overlap = PropertyIntersection(repo).filter_products([schema], create_test_identifiers("espn.com"))
            mismatch = PropertyIntersection(repo).filter_products([schema], create_test_identifiers("nytimes.com"))
            assert not overlap.zero_match, "espn.com overlaps the product's resolved domain → product kept"
            assert mismatch.zero_match, "nytimes.com does not overlap the product's domain → product dropped"
