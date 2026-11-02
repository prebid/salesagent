"""Regression tests for the local pricing option schemas (src.core.schemas.pricing).

The SDK's pricing union members carry ``extra="allow"``, which leaked internal
annotations (``supported`` / ``unsupported_reason``) onto the buyer-facing wire
and silently accepted pre-V3 keywords such as ``rate=``. The local subclasses
close both holes: this project's extra policy on every member, and the internal
annotations declared once with ``exclude=True`` so they never serialize.
"""

import pytest
from pydantic import ValidationError

from src.core.config import get_pydantic_extra_mode
from src.core.schemas.pricing import _MEMBER_TYPES, CpmPricingOption, PricingOption


def _cpm_kwargs(**overrides):
    kwargs = {
        "pricing_option_id": "cpm_usd_fixed",
        "pricing_model": "cpm",
        "currency": "USD",
        "fixed_price": 5.0,
    }
    kwargs.update(overrides)
    return kwargs


class TestMemberExtraPolicy:
    def test_every_member_applies_project_extra_policy(self):
        """All nine members must carry get_pydantic_extra_mode(), not the SDK's "allow".

        The mixin's config wins pydantic's base-config merge only when it is the
        LAST base; this pins that ordering for every member.
        """
        expected = get_pydantic_extra_mode()
        for member in _MEMBER_TYPES:
            assert member.model_config.get("extra") == expected, (
                f"{member.__name__}.model_config['extra'] is "
                f"{member.model_config.get('extra')!r}, expected {expected!r} — "
                "the _AdapterSupportAnnotations mixin must be the last base"
            )

    def test_union_covers_all_nine_spec_members(self):
        """The local union mirrors the nine-member oneOf of 3.1.1 core/pricing-option.json."""
        discriminators = {member.model_fields["pricing_model"].default for member in _MEMBER_TYPES}
        assert discriminators == {"cpm", "vcpm", "cpc", "cpcv", "cpv", "cpp", "cpa", "flat_rate", "time"}

    def test_pre_v3_rate_keyword_is_rejected_outside_production(self):
        """A pre-V3 ``rate=`` keyword is drift and must fail loud, not be echoed.

        Tests always run with the non-production extra policy ("forbid",
        Pattern #7); the assertion below pins that precondition.
        """
        assert get_pydantic_extra_mode() == "forbid"
        with pytest.raises(ValidationError, match="rate"):
            CpmPricingOption(**_cpm_kwargs(rate=5.0))


class TestInternalAnnotationsStayOffTheWire:
    # Two tests here wrote ``supported`` / ``unsupported_reason`` and then asserted they
    # stayed off the wire. Both fields are DELETED from _AdapterSupportAnnotations, which
    # now declares no field at all -- only the extra policy and the derived ``is_fixed``
    # property -- so the mixin has nothing it could put on the wire and the write itself
    # is a ValueError. What is left to grade is that the mixin adds nothing, which is
    # exactly the equality below.

    def test_wire_shape_matches_sdk_member(self):
        """The local member serializes exactly like the SDK's -- the mixin adds no wire field."""
        from adcp.types import CpmPricingOption as LibraryCpmPricingOption

        local = CpmPricingOption(**_cpm_kwargs()).model_dump(mode="json", exclude_none=True)
        sdk = LibraryCpmPricingOption(**_cpm_kwargs()).model_dump(mode="json", exclude_none=True)
        assert local == sdk


class TestWrapperCoercion:
    def test_kwargs_construct_local_member(self):
        wrapped = PricingOption(**_cpm_kwargs())
        assert type(wrapped.root) is CpmPricingOption
        assert wrapped.fixed_price == 5.0  # proxied attribute access

    def test_local_member_instance_passes_through_by_identity(self):
        member = CpmPricingOption(**_cpm_kwargs())
        assert PricingOption(member).root is member

    # The two tests that lived here graded a coercion that no longer exists: an SDK member
    # instance and the SDK's own wrapper were each revalidated into a local member by a
    # mode="before" validator that dumped them. The validator is deleted, so an SDK instance
    # is refused instead of coerced, and a test asserting the coercion would be asserting
    # against the rule. The refusal is graded below.

    def test_sdk_instance_is_refused_rather_than_coerced(self):
        """An SDK-typed member is not a local member, and nothing converts it.

        This replaces an assertion that could not fail. It used to construct the SDK
        instance with an undeclared ``rate`` and demand ``ValidationError`` matching
        "rate", on the theory that the deleted validator's dump re-applied this project's
        extra policy. But the input's repr appears inside pydantic's model_type error, so
        "rate" matched whether the rejection came from extra=forbid or from the union
        refusing the instance outright -- the test passed either way and could not tell
        which mechanism it had exercised.

        What actually holds is stronger and is asserted by TYPE: the root is a discriminated
        union over the LOCAL members, so an SDK instance fails the union's instance check
        with ``model_type``. That check precedes any extra handling, which is why extras are
        irrelevant here and why the refusal does not depend on the extra mode -- the
        undeclared field cannot leak because the object carrying it is never admitted.
        """
        assert get_pydantic_extra_mode() == "forbid"
        from adcp.types import CpmPricingOption as LibraryCpmPricingOption

        for kwargs in (_cpm_kwargs(), _cpm_kwargs(rate=5.0)):
            sdk_member = LibraryCpmPricingOption(**kwargs)
            with pytest.raises(ValidationError) as exc_info:
                PricingOption(sdk_member)
            assert [e["type"] for e in exc_info.value.errors()] == ["model_type"], (
                f"expected the union to refuse the SDK instance, got {exc_info.value.errors()}"
            )


# tests/unit/test_pricing_option_rootmodel.py is DELETED -- all four of its tests were
# already graded, on the wire, by BR-UC-GET-PRODUCTS-pricing-options.feature, whose
# outlines assert the EXACT pricing option dict the buyer receives per pricing model:
# MEASURED passed:27 (fixed) + passed:9 (auction) + passed:3 (id lowercasing) + passed:3
# (two options, distinct ids) in-process across a2a/mcp/rest, and passed:9 + 3 + 1 + 1
# in-network, in the box run test-results/innet_150926_1232.
#
# The four were test_pricing_option_rootmodel_unwrapping, test_pricing_option_unwrap_helper,
# test_legacy_pricing_option_id_generation and test_legacy_pricing_option_id_auction.
#
# Two of them could not have failed for the reason they claimed. Both built the value they
# asserted IN THE TEST BODY -- one defined its own `def unwrap_po(po): return getattr(po,
# "root", po)` and the other recomputed the id as
# `f"{pricing_model}_{currency}_{is_fixed}"` -- and then asserted that the local
# computation produced "cpm_usd_fixed". They graded the test's own arithmetic, not
# production's; their docstrings pointed at "lines 1527-1531 of media_buy_create.py", which
# no longer compute an id at all (it is a stored column now -- see that feature's own
# comment on why).
#
# The remaining two asserted that the adcp library's PricingOption RootModel proxies
# attributes from .root. That is library behavior, and the outcome that matters -- what the
# buyer actually receives -- is pinned byte-for-byte by the scenarios above.


class TestProductIntegration:
    @staticmethod
    def _product(pricing_options):
        from src.core.product_conversion import default_reporting_capabilities
        from src.core.schemas import Product

        return Product(
            product_id="p1",
            name="P1",
            description="d",
            delivery_type="guaranteed",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            publisher_properties=[{"selection_type": "all", "publisher_domain": "example.com"}],
            delivery_measurement={"provider": "test"},
            pricing_options=pricing_options,
            reporting_capabilities=default_reporting_capabilities(),
            is_custom=False,
        )

    def test_product_wraps_options_in_local_wrapper(self):
        product = self._product([_cpm_kwargs()])
        assert type(product.pricing_options[0]) is PricingOption
        assert type(product.pricing_options[0].root) is CpmPricingOption

    def test_product_wire_pricing_options_exact_shape(self):
        """A nested option reaches the wire as exactly the pinned dict, no local extras.

        This asserted the same dict after writing ``supported`` / ``unsupported_reason``
        onto the inner member, to grade that the get_products annotation path never
        reached the buyer. Those fields and that path are both deleted, so the write is
        gone; the exact-dict assertion is kept because it grades something else that is
        live -- the nested member re-serializing through its own serializer inside the
        PricingOption wrapper inside Product (critical pattern #4).
        """
        product = self._product([_cpm_kwargs()])

        wire = product.model_dump(mode="json")
        assert wire["pricing_options"] == [
            {
                "pricing_option_id": "cpm_usd_fixed",
                "pricing_model": "cpm",
                "currency": "USD",
                "fixed_price": 5.0,
                "max_bid": False,
            }
        ]
