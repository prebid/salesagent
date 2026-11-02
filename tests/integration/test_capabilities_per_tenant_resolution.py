"""Integration tests for the two capability facts get_adcp_capabilities derives per tenant.

Core Invariant: a seller's advertised channels and the degradation it reports come
from REAL per-tenant production state, read on the ordinary path -- never from a
test-only control surface in ``src/``.

That invariant replaced one written the other way. salesagent-689e made
``AdapterConfig.config_json["test_behavior"]`` the per-tenant fault-injection seam
and had ``src/core/helpers/adapter_helpers.py`` read it to override the seller's
channels, targeting and availability; a1b79d22d deleted those three reads as the
testing-hook channel they were, and prebid/salesagent#1891 asks whether this repo
should carry such surfaces at all. Two facts settled it: ``test_behavior`` is not
in ``MockConnectionConfig`` (which is ``extra="forbid"``), and
``save_adapter_config`` replaces ``config_json`` wholesale -- so an operator
saving mock config through the admin UI silently deletes it.

What replaced it needs no such surface:

* **channels** -- ``media_buy.portfolio.primary_channels`` is the union of each
  product's effective channels (``src/core/helpers/channel_helpers.py``), the same
  rule ``get_products`` applies per product. ``Product.channels`` is a real
  per-tenant column, admin-editable and AdCP-public. A tenant with no catalog
  falls back to the adapter class's defaults.
* **degradation** -- an ``adapter_type`` outside ``ADAPTER_REGISTRY`` is ordinary
  operator misconfiguration, and ``get_adapter_class`` already refuses it. Nothing
  simulates an outage.

Targeting granularity has no per-tenant surface and deliberately gets none:
``get_targeting_capabilities`` is adapter-class-level by documented design
(``src/adapters/base.py``), and the declaration store's absence-is-enforcement rule
forbids adding a field for a posture production cannot back. The three scenarios
that need it are recorded in ``tests/bdd/e2e_rest_known_failures.txt``.

These tests run the real path unpatched -- real Postgres via ``IntegrationEnv``,
real repositories, real ``MockAdServer`` -- so each fails if the resolution regresses.

Covers: salesagent-689e (superseded), #1871, salesagent-piyo (the portfolio
precondition these scenarios have to satisfy -- see ``_tenant_with_a_portfolio``).
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.channels import MediaChannel

from src.adapters.mock_ad_server import MockAdServer
from tests.factories import ProductFactory
from tests.factories.core import PublisherPartnerFactory, set_adapter_type
from tests.harness._base import IntegrationEnv


def _tenant_with_a_portfolio(env: IntegrationEnv) -> tuple:
    """Seed a tenant that has a ``media_buy.portfolio`` at all, then return it.

    ``primary_channels`` lives on ``Portfolio``, and ``Portfolio`` is only emitted
    for a tenant with at least one real ``PublisherPartner`` row: ``publisher_domains``
    is REQUIRED with ``minItems: 1`` there (pinned v3.1.1
    get-adcp-capabilities-response.json), so ``_get_adcp_capabilities_impl`` omits the
    whole section rather than fabricate a ``<subdomain>.example.com`` domain the seller
    does not own (salesagent-piyo; graded by
    ``tests/integration/test_capabilities_publisher_domain_honesty.py``, which depends on
    ``setup_default_data`` seeding NO partner -- so the row belongs here, not in the harness).

    A partnership is ordinary per-tenant data and is orthogonal to both facts under test:
    it fixes ``publisher_domains``, never ``primary_channels`` (the catalog does that) and
    never the degradation advisory (the adapter lookup does that). Seeding it is the same
    move ``given_adapter_unavailable`` makes for the UC-010 degradation rows
    (tests/bdd/steps/domain/uc010_capabilities.py), and for the same reason.
    """
    tenant, principal = env.setup_default_data()
    PublisherPartnerFactory(tenant=tenant)
    env._commit_factory_data()
    return tenant, principal


@pytest.mark.requires_db
class TestMisconfiguredAdapterDegrades:
    """An adapter_type production cannot resolve degrades discovery, advisory and all."""

    def test_unresolvable_adapter_type_degrades_to_display_only(self, integration_db):
        """A tenant pointed at an ad server that does not exist still gets an answer.

        ``get_adapter_class`` raises ``AdCPConfigurationError`` for an
        ``adapter_type`` outside ``ADAPTER_REGISTRY``; that call sits inside the
        capabilities degradation boundary, so the response degrades to the
        ``[display]`` fallback and records an advisory rather than failing. This is
        the state the two UC-010 degradation scenarios describe, reached the way a
        real operator reaches it -- by typing the adapter name wrong.
        """
        with IntegrationEnv(tenant_id="t_bad_adapter", principal_id="p_bad_adapter") as env:
            tenant, _principal = _tenant_with_a_portfolio(env)
            set_adapter_type(env, tenant.tenant_id, "__no_such_ad_server__")

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        assert response.media_buy is not None
        channels = response.media_buy.portfolio.primary_channels
        assert channels == [MediaChannel.display], (
            "expected the [display]-only degraded fallback for a tenant whose adapter_type "
            f"cannot be resolved, got {channels!r} -- a real adapter answered, so the "
            "misconfiguration was not refused"
        )
        assert response.errors, (
            "expected a top-level advisory recording the degradation "
            "(get-adcp-capabilities-response.json#/properties/errors); got none, so the "
            "buyer cannot tell a missing section from an empty one"
        )

    def test_a_resolvable_adapter_is_unaffected(self, integration_db):
        """The control: a normally-configured tenant degrades nothing and advises nothing."""
        with IntegrationEnv(tenant_id="t_ok_adapter", principal_id="p_ok_adapter") as env:
            tenant, _principal = _tenant_with_a_portfolio(env)
            set_adapter_type(env, tenant.tenant_id, "mock")

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        assert response.media_buy is not None
        assert response.media_buy.portfolio.primary_channels == [
            CHANNEL for CHANNEL in MediaChannel if CHANNEL.value in MockAdServer.default_channels
        ], (
            "a tenant on the mock adapter with no catalog must report the adapter class's "
            f"own default_channels, got {response.media_buy.portfolio.primary_channels!r}"
        )
        assert not response.errors, f"a healthy tenant recorded a degradation advisory: {response.errors!r}"


@pytest.mark.requires_db
class TestPortfolioChannelsComeFromTheCatalog:
    """primary_channels is the union of each product's effective channels."""

    def test_union_across_products_reaches_the_wire(self, integration_db):
        """Three products declaring one channel each produce all three on the wire.

        One product per channel on purpose: the production rule is a UNION across
        the catalog, and a single product declaring all three would pass even if the
        union were dropped for a first-product-wins read.
        """
        with IntegrationEnv(tenant_id="t_catalog_ch", principal_id="p_catalog_ch") as env:
            tenant, _principal = _tenant_with_a_portfolio(env)
            for channel in ("display", "social", "ctv"):
                ProductFactory(tenant=tenant, channels=[channel])
            env._commit_factory_data()

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        channels = response.media_buy.portfolio.primary_channels
        assert channels == [MediaChannel.display, MediaChannel.social, MediaChannel.ctv], (
            f"expected the catalog's three channels in pinned enum order, got {channels!r} -- "
            "the mock adapter's class default_channels leaking through means the catalog was "
            "never read"
        )

    def test_a_product_declaring_nothing_inherits_the_adapter_defaults(self, integration_db):
        """An undeclared catalog entry is the absence of a claim, not "no channels".

        The product contributes the adapter's defaults, which is the same rule
        ``get_products`` applies when filtering -- so a seller cannot be advertised
        one set and filtered by another.
        """
        with IntegrationEnv(tenant_id="t_catalog_bare", principal_id="p_catalog_bare") as env:
            tenant, _principal = _tenant_with_a_portfolio(env)
            ProductFactory(tenant=tenant, channels=None)
            env._commit_factory_data()

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        channels = {channel.value for channel in response.media_buy.portfolio.primary_channels}
        assert channels == set(MockAdServer.default_channels), (
            f"expected the adapter defaults {sorted(MockAdServer.default_channels)} for a product "
            f"declaring no channels, got {sorted(channels)}"
        )

    def test_an_empty_catalog_falls_back_to_the_adapter_defaults(self, integration_db):
        """No catalog is also not a claim of "no channels".

        This is the path every UC-010 scenario that says nothing about channels
        takes, and the path the alias/unrecognized-name mapping is still graded
        through -- ``Product.channels`` is enum-typed and cannot carry ``"video"``
        or ``"hologram"``, but ``default_channels`` is a raw string list and can.
        """
        with IntegrationEnv(tenant_id="t_catalog_empty", principal_id="p_catalog_empty") as env:
            _tenant_with_a_portfolio(env)

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        channels = {channel.value for channel in response.media_buy.portfolio.primary_channels}
        assert channels == set(MockAdServer.default_channels), (
            f"expected the adapter defaults for a tenant with no catalog, got {sorted(channels)}"
        )

    def test_all_twenty_canonical_channels_survive_the_mapping(self, integration_db):
        """Every pinned channels-enum value round-trips from catalog to wire.

        Grades CHANNEL_MAPPING's completeness (channels.json#/enum, 20 values), not
        a claim that a seller must offer all 20 -- the catalog is what fixes the set.
        """
        with IntegrationEnv(tenant_id="t_catalog_all", principal_id="p_catalog_all") as env:
            tenant, _principal = _tenant_with_a_portfolio(env)
            for channel in MediaChannel:
                ProductFactory(tenant=tenant, channels=[channel.value])
            env._commit_factory_data()

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        assert response.media_buy.portfolio.primary_channels == list(MediaChannel), (
            "not every channels-enum value survived catalog -> CHANNEL_MAPPING -> wire: missing "
            f"{sorted(c.value for c in MediaChannel if c not in response.media_buy.portfolio.primary_channels)}"
        )
