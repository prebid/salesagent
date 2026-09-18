"""``Product.publisher_domain`` is a DOMAIN, so a tenant host's port never reaches it.

Separate from ``test_effective_properties_selection_type.py`` (which is #1162, about
selection_type INFERENCE) because this grades a different property of the same value: what
``publisher_domain`` is allowed to contain, whichever variant carries it.

In-memory ORM instances, no database — the derivation reads two tenant columns and nothing
else.
"""

from unittest.mock import MagicMock, PropertyMock

from src.core.database.models import Product


class TestPublisherDomainDropsThePort:
    """``Product.publisher_domain`` is a DOMAIN, so a tenant host's port never reaches it.

    Measured on the storyboard conformance stack: seeding the tenant's ``virtual_host`` as
    ``storyboard.adcp.test:8443`` — the origin it is genuinely reachable at — made every
    ``publisher_properties`` entry carry that string as ``publisher_domain``. The pinned
    schema fixes a domain pattern a colon fails, so the matching member of the
    ``publisher_properties`` union was knocked out and ``get_products`` answered
    ``INTERNAL_ERROR`` — a 500-class response to a well-formed request, with nothing in it
    naming the port.
    """

    @staticmethod
    def _product(virtual_host: str | None, *, subdomain: str = "ci-test", **fields: object) -> MagicMock:
        tenant = MagicMock()
        tenant.virtual_host = virtual_host
        tenant.subdomain = subdomain

        product = MagicMock(spec=Product)
        product.inventory_profile_id = None
        product.inventory_profile = None
        product.properties = None
        product.property_ids = None
        product.property_tags = None
        product.tenant = tenant
        for name, value in fields.items():
            setattr(product, name, value)

        type(product).publisher_domain = PropertyMock(side_effect=lambda: Product.publisher_domain.fget(product))
        type(product).effective_properties = PropertyMock(
            side_effect=lambda: Product.effective_properties.fget(product)
        )
        return product

    def test_port_is_dropped_from_a_virtual_host(self):
        assert self._product("storyboard.adcp.test:8443").publisher_domain == "storyboard.adcp.test"

    def test_a_portless_virtual_host_is_untouched(self):
        assert self._product("publisher.example.com").publisher_domain == "publisher.example.com"

    def test_an_ipv6_authority_keeps_its_colons(self):
        """``rpartition`` and the digit check, not ``split(':')`` — which would truncate the address."""
        assert self._product("[2001:db8::1]:8443").publisher_domain == "[2001:db8::1]"
        assert self._product("[2001:db8::1]").publisher_domain == "[2001:db8::1]"

    def test_no_virtual_host_falls_back_to_the_subdomain(self):
        assert self._product(None, subdomain="ci-test").publisher_domain == "ci-test.example.com"

    def test_every_selection_variant_reads_the_same_derivation(self):
        """The three inline copies this replaced could have been fixed one at a time."""
        assert self._product("host.example:8443").effective_properties[0]["publisher_domain"] == "host.example"
        by_id = self._product("host.example:8443", property_ids=["homepage"])
        assert by_id.effective_properties[0]["publisher_domain"] == "host.example"
        by_tag = self._product("host.example:8443", property_tags=["premium"])
        assert by_tag.effective_properties[0]["publisher_domain"] == "host.example"
