"""Contract test for SyncResponseAccount locally-owned model.

SyncResponseAccount replaced an SDK-provided type after SDK 5.7 restructured
the sync_accounts response. This contract test verifies:
  1. All 13 expected fields exist and are constructable
  2. Fields serialize correctly via model_dump
  3. None-valued fields are excluded by default

beads: salesagent-a6zc
"""

from adcp.types import Error as LibraryError
from adcp.types import Setup as LibrarySetup
from adcp.types.generated_poc.core.brand_ref import BrandReference
from adcp.types.generated_poc.core.business_entity import BusinessEntity as LibraryBusinessEntity

from src.core.schemas import SyncResponseAccount

# The 13 fields that production code (_build_sync_result / _build_failed_result)
# constructs. payment_terms added salesagent-5g8e (F1 settings-update Then needs a
# field to read). notification_configs added salesagent-ck9v (#1592 T2): the
# sync-accounts-response schema requires the applied subscriber set to be echoed on
# created/updated/unchanged results. billing_entity added salesagent-gcze: the
# response account item carries it "echoed from the request ... Bank details are
# omitted (write-only)" (v3.1.1 sync-accounts-response.json), and before that it was
# accepted on the wire by both request arms and then silently dropped.
#
# This set is an INVENTORY pin, not a behavioral assertion: it exists so a field
# cannot be added to the model without someone deciding it belongs on the buyer
# wire. Extending it is correct when the field is spec-mandated; deleting an entry
# to make a test pass is not.
EXPECTED_FIELDS = {
    "brand",
    "operator",
    "action",
    "status",
    "account_id",
    "name",
    "billing",
    "payment_terms",
    "sandbox",
    "errors",
    "setup",
    "notification_configs",
    "billing_entity",
}


class TestSyncResponseAccountFields:
    """SyncResponseAccount has all fields that production code constructs."""

    def test_has_all_expected_fields(self):
        """Model declares all 13 expected fields."""
        actual_fields = set(SyncResponseAccount.model_fields.keys())
        assert EXPECTED_FIELDS == actual_fields, (
            f"Field mismatch. Expected: {sorted(EXPECTED_FIELDS)}, got: {sorted(actual_fields)}"
        )

    def test_construct_with_all_fields(self):
        """All 13 fields can be populated without validation errors."""
        account = SyncResponseAccount(
            brand=BrandReference(domain="acme.com"),
            operator="create",
            action="created",
            status="active",
            account_id="acc_123",
            name="Test Account",
            billing="prepaid",
            payment_terms="net_45",
            sandbox=False,
            errors=[LibraryError(code="VALIDATION_ERROR", message="test error")],
            setup=LibrarySetup(message="Complete billing setup"),
            billing_entity=LibraryBusinessEntity(legal_name="Acme GmbH"),
        )
        assert account.account_id == "acc_123"
        assert account.action == "created"
        assert account.status == "active"
        assert account.name == "Test Account"
        assert account.operator == "create"
        assert account.billing == "prepaid"
        assert account.payment_terms == "net_45"
        assert account.sandbox is False
        assert len(account.errors) == 1
        assert account.errors[0].code == "VALIDATION_ERROR"
        assert account.brand.domain == "acme.com"
        assert account.setup.message == "Complete billing setup"
        assert account.billing_entity.legal_name == "Acme GmbH"

    # Required-field enforcement (brand/operator/action/status per pinned schema
    # 04f59d2d5) is verified generically in
    # tests/unit/test_pydantic_schema_alignment.py::TestResponseModelAlignment.

    def test_optional_fields_remain_optional(self):
        """Non-required fields (account_id, name, billing, payment_terms, sandbox, errors, setup) stay optional."""
        account = SyncResponseAccount(
            brand=BrandReference(domain="acme.com"),
            operator="create",
            action="created",
            status="active",
        )
        for field in EXPECTED_FIELDS - {"brand", "operator", "action", "status"}:
            assert getattr(account, field) is None


class TestSyncResponseAccountSerialization:
    """SyncResponseAccount serializes correctly for wire transport."""

    def test_model_dump_includes_set_fields(self):
        """Fields with values appear in model_dump output."""
        account = SyncResponseAccount(
            brand=BrandReference(domain="acme.com"),
            operator="create",
            account_id="acc_456",
            action="updated",
            status="active",
        )
        data = account.model_dump(exclude_none=True)
        assert data["account_id"] == "acc_456"
        assert data["action"] == "updated"
        assert data["status"] == "active"

    def test_model_dump_excludes_none_when_requested(self):
        """Unset OPTIONAL fields are excluded with exclude_none=True.

        Required fields (brand/operator/action/status) are always present; only the
        optional fields left unset are dropped.
        """
        account = SyncResponseAccount(
            brand=BrandReference(domain="acme.com"),
            operator="create",
            action="created",
            status="active",
        )
        data = account.model_dump(exclude_none=True)
        # Required fields are always present.
        assert "brand" in data
        assert "operator" in data
        assert "action" in data
        assert "status" in data
        # Unset optional fields should not appear.
        assert "account_id" not in data
        assert "name" not in data
        assert "billing" not in data
        assert "sandbox" not in data
        assert "errors" not in data
        assert "setup" not in data

    def test_roundtrip_from_dict(self):
        """SyncResponseAccount can be constructed from a dict (transport deserialization)."""
        raw = {
            "brand": {"domain": "acme.com"},
            "operator": "create",
            "account_id": "acc_rt",
            "action": "created",
            "status": "active",
            "name": "Roundtrip Account",
            "sandbox": True,
        }
        account = SyncResponseAccount.model_validate(raw)
        assert account.account_id == "acc_rt"
        assert account.sandbox is True
        assert account.name == "Roundtrip Account"

    def test_errors_field_serializes_nested_models(self):
        """Nested Error models in errors list serialize correctly."""
        account = SyncResponseAccount(
            brand=BrandReference(domain="acme.com"),
            operator="create",
            action="created",
            status="active",
            account_id="acc_err",
            errors=[
                LibraryError(code="CONFLICT", message="duplicate account"),
            ],
        )
        data = account.model_dump(exclude_none=True)
        assert len(data["errors"]) == 1
        assert data["errors"][0]["code"] == "CONFLICT"
        assert data["errors"][0]["message"] == "duplicate account"
