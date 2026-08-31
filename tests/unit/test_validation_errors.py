"""Unit tests for validation error handling in create_media_buy."""

import pytest
from pydantic import BaseModel, ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.schemas import CreateMediaBuyRequest
from src.core.validation_helpers import first_validation_error_field, format_validation_error
from tests.helpers import assert_redacted, extra_forbidden_error


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


def test_first_validation_error_field_strips_generated_union_variant_segments():
    """A codegen union-variant class name must not reach the buyer-facing field path.

    Pydantic inserts the matched union member class name (e.g. AccountReference1) as a loc
    segment; the buyer's payload has no such path. The field must be the real dotted path
    (account.account_id), not account.AccountReference1.account_id (#1329).
    """
    from tests.helpers.governance import governance_request

    with pytest.raises(ValidationError) as exc_info:
        # A non-string account_id fails the AccountReferenceById arm; the loc carries the
        # generated variant tag AccountReference1 between `account` and `account_id`. Built via
        # the shared request/agent builders (#1329) — the only deviation is the int account_id.
        governance_request(account_ref={"account_id": 123}, url="https://agent.example.com/hook", credentials="c" * 32)

    field = first_validation_error_field(exc_info.value)
    assert field is not None
    assert "AccountReference" not in field, field
    # The stripped path is a real dotted path into the buyer's request.
    assert field.startswith("accounts[0].account"), field


def test_extra_forbidden_header_style_key_survives_in_field():
    """An extra_forbidden key with a header-style name must stay in the buyer field path.

    The union-variant strip keys on capitalization (uppercase initial, no underscore),
    which also matches a buyer's own header-style key (Authorization, X-Api-Key, Token)
    when it is the TERMINAL loc segment of an extra_forbidden rejection. That key is the
    one actionable pointer the buyer has (the echoed value is always [redacted]), so the
    strip must never drop the terminal segment (#1329).
    """
    from tests.helpers.governance import account_entry, governance_agent_dict, governance_request

    # Header-style extra key: capitalized, no underscore, terminal. Built via the shared
    # agent/request builders (#1329); the authentication override carries the extra key.
    agent = governance_agent_dict(
        "https://agent.example.com/hook",
        authentication={"schemes": ["Bearer"], "credentials": "c" * 32, "Authorization": "Bearer secret-value"},
    )
    with pytest.raises(ValidationError) as exc_info:
        governance_request(accounts=[account_entry({"account_id": "acct_1"}, agents=[agent])])

    field = first_validation_error_field(exc_info.value)
    assert field is not None
    # The buyer's own key must survive as the terminal segment, not be stripped.
    assert field.endswith(".Authorization"), field


def test_create_media_buy_boundary_validation_preserves_field_suggestion():
    """Boundary request construction keeps the current field-specific hint."""
    from src.core.tools.media_buy_create import _build_create_media_buy_request

    with pytest.raises(AdCPValidationError) as exc_info:
        _build_create_media_buy_request(
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
        )

    error = exc_info.value
    assert error.field == "idempotency_key"
    assert error.suggestion == ("Provide the required 'idempotency_key' field and resend the request.")


def test_brand_target_audience_must_be_string():
    """Test Brand target_audience field accepts strings (adcp 3.12: Brand replaced BrandManifest)."""
    from adcp.types.generated_poc.brand import Brand, LocalizedName  # TODO: no stable alias in adcp.types

    brand = Brand(
        id="test_brand",
        names=[LocalizedName(name="Test Brand", language="en")],
        target_audience="spiritual seekers interested in unexplained phenomena",
    )
    assert brand.target_audience == "spiritual seekers interested in unexplained phenomena"


def test_brand_accepts_extra_fields():
    """Test that Brand accepts arbitrary extra fields (extra=allow)."""
    from adcp.types.generated_poc.brand import Brand, LocalizedName  # TODO: no stable alias in adcp.types

    brand = Brand(
        id="test_brand",
        names=[LocalizedName(name="Test Brand", language="en")],
        custom_field="custom_value",
    )
    # Brand accepts extra fields with extra="allow"
    assert brand is not None


def test_create_media_buy_request_invalid_brand_manifest():
    """Test that CreateMediaBuyRequest accepts brand field (adcp 3.6.0: brand replaced brand_manifest)."""
    # In adcp 3.6.0, brand is a BrandReference with optional domain field
    # Missing domain does not raise an error since domain is optional
    req = CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        end_time="2026-02-01T00:00:00Z",
        start_time="2026-01-01T00:00:00Z",
        idempotency_key="unit-test-key-invalid-brand-mfst",
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
        error_msg = format_validation_error(e, context="test request")

        # Check that we got a helpful error message. The codegen union-variant tag
        # (BrandManifest) is stripped from the message path too — the message reports
        # the same buyer path as `field`/`details.loc` (#1329).
        assert "Invalid test request:" in error_msg
        assert "brand_manifest.target_audience" in error_msg
        assert "brand_manifest.BrandManifest" not in error_msg
        assert "Expected string, got object" in error_msg
        assert "AdCP spec requires this field to be a simple string" in error_msg
        assert "https://adcontextprotocol.org/schemas/v1/" in error_msg


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


def test_validation_error_formatting_extra_field_redacts_innocuous_scalar():
    """Even an innocuous scalar extra field is redacted — the echo is redact-ALL.

    The value is withheld for EVERY extra_forbidden rejection, not only
    credential-shaped ones: a deny-list cannot enumerate buyer-invented names, so the
    only safe policy is to never echo (#1329). The actionable field PATH
    always survives.
    """
    err = extra_forbidden_error("CreateMediaBuyRequest", ("unknown_field",), "some_value")
    # Grade BOTH halves off the one helper: the value is withheld ([redacted], secret absent)
    # AND the actionable field path survives — even for an innocuous scalar.
    assert_redacted(format_validation_error(err), field_path="unknown_field", secret="some_value")


def test_validation_error_formatting_extra_field_with_dict_redacted():
    """An extra field with a dict value is redacted too — the structure could nest a secret.

    A value scan cannot prove a dict is credential-free (a list-of-pairs or a
    buyer-invented key escapes it), so the whole value is withheld (#1329).
    """
    err = extra_forbidden_error(
        "Package",
        ("format_ids", "agent_url"),
        {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_300x250"},
    )
    # Both halves off the one helper: the whole dict value is withheld (each fragment absent)
    # AND the field path survives.
    assert_redacted(
        format_validation_error(err),
        field_path="format_ids.agent_url",
        secret=["https://creative.adcontextprotocol.org/", "display_300x250"],
    )


def test_validation_error_redacts_declared_field_name_misplaced():
    """A DECLARED field name misplaced as an extra (e.g. keywords) is redacted uniformly.

    Redact-all makes the former deny-list's over-match moot: declared field names that
    happened to contain a fragment (``keywords`` -> ``key``, ``idempotency_key``) no
    longer get special treatment — every extra_forbidden value is withheld the same way
    (#1329).
    """
    err = extra_forbidden_error("SyncAccountsRequest", ("keywords",), ["news", "sports"])
    assert_redacted(format_validation_error(err), field_path="keywords", secret=["news", "sports"])


def test_validation_error_redacts_credential_under_authentication():
    """A typo'd extra field under authentication must NOT echo the secret value.

    format_validation_error feeds errors[0].message, which reaches the buyer wire
    (REST/A2A) and the error-log + audit sinks. A buyer typo (`credential` for
    `credentials`) still carries the bearer token; redact-all withholds the value while
    the actionable field PATH is preserved (#1329, was BLOCKER A).
    """
    secret = "SUPERSECRETcredential00000000000000"
    err = extra_forbidden_error(
        "SyncGovernanceRequest",
        ("accounts", 0, "governance_agents", 0, "authentication", "credential"),
        secret,
    )
    assert_redacted(format_validation_error(err), field_path="authentication.credential", secret=secret)


def test_validation_error_redacts_nested_secret_under_unknown_field():
    """An unknown top-level field carrying a nested credential must be redacted.

    The offending loc segment is innocuous and the value nests a sensitive key several
    levels deep — redact-all withholds it without needing to detect the nesting
    (#1329).
    """
    secret = "NESTEDbearerSECRET00000000000000000"
    err = extra_forbidden_error("SyncGovernanceRequest", ("extra_config",), {"authentication": {"credentials": secret}})
    # Routing through assert_redacted also grades that the innocuous top-level field path
    # (extra_config) survives — a strengthening over the prior [redacted]-only check.
    assert_redacted(format_validation_error(err), field_path="extra_config", secret=secret)


@pytest.mark.parametrize(
    "field_name",
    [
        "api_key",
        "access_token",
        "client_secret",
        "auth_token",
        "private_key",
        "bearer_token",
        "secret_key",
        "refresh_token",
        "session_token",
        "password",
    ],
)
def test_validation_error_redacts_credential_shaped_sibling_of_url(field_name):
    """Realistic credential field names as a SCALAR SIBLING of ``url`` are redacted.

    Names like api_key/access_token/client_secret/private_key placed NOT under
    ``authentication`` but as a plain scalar sibling would defeat any nested-key scan;
    redact-all withholds them regardless. A deny-list could never enumerate this open
    set, which is why the policy redacts every value (#1329).
    """
    secret = "sk-live-" + "z" * 40
    err = extra_forbidden_error(
        "SyncGovernanceRequest",
        ("accounts", 0, "governance_agents", 0, field_name),
        secret,
    )
    assert_redacted(format_validation_error(err), field_path=field_name, secret=secret)
