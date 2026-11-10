"""Unit tests for validation error handling in create_media_buy."""

import pytest
from pydantic import BaseModel, ValidationError

from src.core.schemas import CreateMediaBuyRequest
from src.core.validation_helpers import first_validation_error_field, format_validation_error
from tests.factories.request import PackageRequestFactory
from tests.helpers import assert_construction_rejects
from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION


def test_first_validation_error_field_uses_bracket_notation():
    """first_validation_error_field renders list indices as [i] (bracket form).

    The boundary-derived field path must match the hand-rolled field= strings
    raised inside the _impl layer (e.g. packages[].budget), so the wire
    envelope's `field` attribute has one consistent shape regardless of where
    the validation error originated.
    """

    class _Pkg(BaseModel):
        budget: float

    class _Req(BaseModel):
        packages: list[_Pkg]

    with pytest.raises(ValidationError) as exc_info:
        _Req(packages=[{"budget": "not-a-number"}])

    assert first_validation_error_field(exc_info.value) == "packages[0].budget"


def test_first_validation_error_field_is_owned_by_exception_leaf_module():
    """The field-path helper must not recreate an exceptions/helpers import cycle."""
    assert first_validation_error_field.__module__ == "src.core.exceptions"


def test_create_media_buy_boundary_validation_names_the_offending_field():
    """Boundary request construction tells the buyer WHICH field, via the field slot.

    The specificity lives on ``field``, not in prose: the suggestion is a function of the
    code and is asserted nowhere, since comparing it to CODE_TABLE would grade the table
    against itself. This test previously pinned an authored sentence that named the same
    field ``field=`` already carries.
    """

    assert_construction_rejects(
        lambda: CreateMediaBuyRequest(
            brand={"domain": "wiretest.example"},
            packages=None,
            start_time=None,
            end_time=None,
            po_number=None,
            reporting_webhook=None,
            context=None,
            ext=None,
            account=None,
            idempotency_key=None,
            paused=None,
        ),
        field="idempotency_key",
    )


def test_brand_target_audience_must_be_string():
    """Test Brand target_audience field accepts strings (adcp 3.12: Brand replaced BrandManifest)."""
    from adcp.types.domains.brand_discovery import Brand, LocalizedName  # TODO: no stable alias in adcp.types

    brand = Brand(
        id="test_brand",
        # A LOCALE-KEYED map, which is what ``LocalizedName`` is: a RootModel over
        # ``dict[str, str]`` holding exactly one locale -> name entry (the spec's own
        # examples are ``{"en_US": "Tide"}``). The two-key ``name=``/``language=`` form
        # here read as if it had those FIELDS and never did; adcp 6.6.0 constrained the
        # dict with ``MinLen(1)`` alone so the wrong shape validated, and 3.2.1 adds
        # ``MaxLen(1)``, which refuses it.
        names=[LocalizedName({"en": "Test Brand"})],
        target_audience="spiritual seekers interested in unexplained phenomena",
    )
    assert brand.target_audience == "spiritual seekers interested in unexplained phenomena"


def test_brand_accepts_extra_fields():
    """Test that Brand accepts arbitrary extra fields (extra=allow)."""
    from adcp.types.domains.brand_discovery import Brand, LocalizedName  # TODO: no stable alias in adcp.types

    brand = Brand(
        id="test_brand",
        # A LOCALE-KEYED map, which is what ``LocalizedName`` is: a RootModel over
        # ``dict[str, str]`` holding exactly one locale -> name entry (the spec's own
        # examples are ``{"en_US": "Tide"}``). The two-key ``name=``/``language=`` form
        # here read as if it had those FIELDS and never did; adcp 6.6.0 constrained the
        # dict with ``MinLen(1)`` alone so the wrong shape validated, and 3.2.1 adds
        # ``MaxLen(1)``, which refuses it.
        names=[LocalizedName({"en": "Test Brand"})],
        custom_field="custom_value",
    )
    # Brand accepts extra fields with extra="allow"
    assert brand is not None


def test_create_media_buy_request_invalid_brand_manifest():
    """Test that CreateMediaBuyRequest accepts brand field (adcp 3.6.0: brand replaced brand_manifest)."""
    # In adcp 3.6.0, brand is a BrandReference with optional domain field
    # Missing domain does not raise an error since domain is optional
    req = CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        end_time="2026-02-01T00:00:00Z",
        start_time="2026-01-01T00:00:00Z",
        idempotency_key="unit-test-key-invalid-brand-mfst",
        # ``packages`` is here because create-media-buy-request.json's ROOT requires one of
        # ``packages`` | ``packages+total_budget+budget_allocation`` | ``proposal_id+total_budget``,
        # and adcp 9 enforces that group (``enforce_root_required_groups``) where 6.6.0
        # generated the fields and ignored the root anyOf. A request with neither packages
        # nor a proposal was never an accepted shape. This test is about ``brand``, so the
        # packages come from the factory bound to the accepted model rather than hand-rolled.
        # PackageRequestFactory, NOT ``default_request_packages``: the latter builds the
        # ``MediaBuy.raw_request`` shape, which carries a ``package_id`` the REQUEST model
        # does not declare, so strict mode rejects it at ``/packages/0/package_id``.
        packages=[PackageRequestFactory.payload()],
    )
    assert req.brand is not None


def test_validation_error_formatting():
    """Test that our validation error formatting provides helpful messages."""
    # Test the format_validation_error helper function
    try:
        raise ValidationError.from_exception_data(
            "CreateMediaBuyRequest",
            [
                {
                    "type": "string_type",
                    "loc": ("brand_manifest", "BrandManifest", "target_audience"),
                    "msg": "Input should be a valid string",
                    "input": {"demographics": ["test"], "interests": ["test"]},
                }
            ],
        )
    except ValidationError as e:
        # Use the shared helper function
        error_msg = format_validation_error(e, label="test request")

        # Check that we got a helpful error message
        assert "Invalid test request:" in error_msg
        assert "brand_manifest.BrandManifest.target_audience" in error_msg
        assert "Expected string, got object" in error_msg
        assert "AdCP spec requires this field to be a simple string" in error_msg
        # Asserted against EXPECTED_SPEC_VERSION, not a literal and not
        # get_adcp_spec_version(). Production derives the segment from the SDK at
        # runtime; this constant is hand-authored in tests/helpers/adcp_pin.py with its
        # own drift guard, so the two reach the same string by independent paths and
        # must agree. A literal here would go stale at the next bump -- which is the
        # defect this assertion previously carried: it pinned `/schemas/v1/`, a version
        # this project has never used, and kept asserting it after production started
        # deriving.
        assert f"https://adcontextprotocol.org/schemas/{EXPECTED_SPEC_VERSION}/" in error_msg


def test_validation_error_formatting_missing_field():
    """Test formatting for missing required fields."""
    try:
        raise ValidationError.from_exception_data(
            "CreateMediaBuyRequest",
            [{"type": "missing", "loc": ("brand",), "msg": "Field required", "input": {}}],
        )
    except ValidationError as e:
        error_msg = format_validation_error(e)

        assert "brand: Required field is missing" in error_msg
        assert "Invalid request:" in error_msg


def test_validation_error_formatting_extra_field():
    """Test formatting for extra forbidden fields shows the actual value."""
    try:
        raise ValidationError.from_exception_data(
            "CreateMediaBuyRequest",
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("unknown_field",),
                    "msg": "Extra inputs are not permitted",
                    "input": "some_value",
                }
            ],
        )
    except ValidationError as e:
        error_msg = format_validation_error(e)

        assert "unknown_field: Extra field not allowed by AdCP spec" in error_msg
        # Now we show the actual value for debugging
        assert "some_value" in error_msg
        assert "Received value:" in error_msg


def test_validation_error_formatting_extra_field_with_dict():
    """Test formatting for extra forbidden fields with dict values shows full structure."""
    # This tests the scenario from the bug where format_ids had an agent_url key
    # that was incorrectly placed, and Pydantic truncated it
    try:
        raise ValidationError.from_exception_data(
            "Package",
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("format_ids", "agent_url"),
                    "msg": "Extra inputs are not permitted",
                    "input": {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_300x250"},
                }
            ],
        )
    except ValidationError as e:
        error_msg = format_validation_error(e)

        # Error message should show the full value, not truncated
        assert "format_ids.agent_url: Extra field not allowed by AdCP spec" in error_msg
        assert "Received value:" in error_msg
        # The full URL should be visible, not truncated like "ht...id"
        assert "https://creative.adcontextprotocol.org/" in error_msg
        assert "display_300x250" in error_msg
