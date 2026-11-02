"""MCP Tool Roundtrip Tests with Minimal Parameters.

These tests verify that MCP tools work correctly when called with only required parameters,
catching issues like the datetime.combine() bug where optional fields defaulted to None
and caused errors.

Focus: Test parameter-to-schema mapping, not business logic.
"""

import pytest

# EIGHT ROUNDTRIP CASES STOOD HERE, with the MCP client fixture that served them, and
# all of it is deleted.
#
# Seven drove one tool over MCP with its required fields and checked the answer. BDD
# grades every one of them over a2a/mcp/rest against the stack's own server, so this file
# was paying a hand-rolled subprocess server to re-derive a subset. Measured from run
# innet_200926_1903, nodeids graded over [mcp]:
#
#     create_media_buy 132   update_media_buy 75   sync_creatives 76
#     list_creatives    48   get_products     11
#
# The error case too: test_get_media_buy_delivery_invalid_date_range asserted
# VALIDATION_ERROR / correctable for start_date after end_date, and
# test_delivery_date_range_partition__partition carries exactly that obligation --
# start_after_end -> error "VALIDATION_ERROR" with suggestion -- on all three transports,
# with a boundary twin beside it.
#
# The eighth, test_get_products_content_is_summary_not_json, looked MCP-only:
# content[0].text must not be a dump of structured_content. But tests/bdd/conftest.py:4520
# admits a single-transport scenario only when the graded production is reachable on ONE
# wire transport, and it records three scenarios that claimed exactly that and were
# MEASURED false. Measured here too: src/a2a_server/adcp_a2a_server.py:295-298 emits "an
# optional TextPart then the DataPart", so A2A carries the same
# human-readable-beside-structured split. The property is not this transport's alone, so it
# belongs in BDD, parametrized, where the harness owns which wire it runs on.
#
# What is left needs no server and no database: the request schemas must construct from
# their required fields alone.


@pytest.mark.unit
class TestSchemaConstruction:
    """Every request schema constructs from its REQUIRED fields alone."""

    def test_update_media_buy_request_construction(self):
        """Test that UpdateMediaBuyRequest can be constructed with minimal params."""
        from src.core.schemas import UpdateMediaBuyRequest

        # Test with only media_buy_id (required via oneOf constraint)
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="test_buy_123"
        )

        assert req.media_buy_id == "test_buy_123"
        assert req.paused is None  # adcp 2.12.0+: replaced 'active' with 'paused'

        # No internal field survives on this DTO. ``today`` used to be asserted here, both
        # for its presence and for its absence from model_dump(); it is deleted, so the
        # question the assertions answered no longer has a subject. The rule that keeps it
        # that way is graded in
        assert not [f for f, info in req.model_fields.items() if info.exclude]

    def test_all_request_schemas_have_optional_or_default_fields(self):
        """Every request schema constructs from its REQUIRED fields alone.

        "Minimal" means the spec's own /required set, not the empty set. UpdateMediaBuyRequest
        used to appear here with only media_buy_id, which worked while account and
        idempotency_key were wrongly overridden to optional; AdCP 3.1.1 lists both in
        /required, so a request without them is not minimal, it is invalid. The obligation
        that still matters -- no schema demands MORE than the spec does -- is unchanged.
        """
        from src.core import schemas

        # Test schemas that should work with minimal params
        test_cases = [
            (schemas.GetProductsRequest, {"brand": {"domain": "testbrand.com"}}),
            (
                schemas.UpdateMediaBuyRequest,
                {
                    "media_buy_id": "test",
                    "account": {"account_id": "acct_test"},
                    "idempotency_key": "test-idem-key-0001",
                },
            ),
            (schemas.GetMediaBuyDeliveryRequest, {}),
            (schemas.ListCreativesRequest, {}),
        ]

        for schema_class, minimal_params in test_cases:
            try:
                instance = schema_class(**minimal_params)
                assert instance is not None, f"{schema_class.__name__} failed to construct with minimal params"
            except Exception as e:
                pytest.fail(f"{schema_class.__name__} raised {type(e).__name__}: {e}")


@pytest.mark.unit  # Changed from integration - these don't require server
class TestParameterToSchemaMapping:
    """Test that tool parameters map correctly to schema fields."""

    def test_update_media_buy_parameter_mapping(self):
        """Test that update_media_buy parameters map to UpdateMediaBuyRequest fields."""
        from src.core.schemas import UpdateMediaBuyRequest

        # Simulate what the tool does when constructing the request
        # Note: Tool should convert float to Budget object before passing
        # Updated: Only use valid AdCP fields (start_time/end_time, not flight_start_date/flight_end_date)
        tool_params = {
            "media_buy_id": "test_buy_123",
            "paused": True,  # adcp 2.12.0+: replaced 'active' with 'paused'
        }

        # Create request with valid fields only
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", **tool_params
        )

        # Valid fields should be set
        assert req.media_buy_id == "test_buy_123"
        assert req.paused is True  # adcp 2.12.0+: paused=True means pause

        # start_time/end_time should be None since not provided
        assert req.start_time is None
        assert req.end_time is None

        # No top-level budget to assert: AdCP 3.1.1 does not define one on
        # update-media-buy-request.json (budget is package-level), so the field was removed
        # rather than left as a convenience. `packages` is where a budget update lives.
        assert req.packages is None
