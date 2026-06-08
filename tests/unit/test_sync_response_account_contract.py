"""Contract test for SyncResponseAccount locally-owned model.

SyncResponseAccount replaced an SDK-provided type after SDK 5.7 restructured
the sync_accounts response. This contract test verifies:
  1. All 10 expected fields exist and are constructable
  2. Fields serialize correctly via model_dump
  3. None-valued fields are excluded by default

beads: salesagent-a6zc
"""

from adcp.types import Error as LibraryError
from adcp.types import Setup as LibrarySetup
from adcp.types.generated_poc.core.brand_ref import BrandReference

from src.core.schemas import SyncResponseAccount

# The 10 fields that production code (_build_per_account_result) constructs.
EXPECTED_FIELDS = {
    "brand",
    "operator",
    "action",
    "status",
    "account_id",
    "name",
    "billing",
    "sandbox",
    "errors",
    "setup",
}


class TestSyncResponseAccountFields:
    """SyncResponseAccount has all fields that production code constructs."""

    def test_has_all_expected_fields(self):
        """Model declares all 10 expected fields."""
        actual_fields = set(SyncResponseAccount.model_fields.keys())
        assert EXPECTED_FIELDS == actual_fields, (
            f"Field mismatch. Expected: {sorted(EXPECTED_FIELDS)}, got: {sorted(actual_fields)}"
        )

    def test_construct_with_all_fields(self):
        """All 10 fields can be populated without validation errors."""
        account = SyncResponseAccount(
            brand=BrandReference(domain="acme.com"),
            operator="create",
            action="created",
            status="active",
            account_id="acc_123",
            name="Test Account",
            billing="prepaid",
            sandbox=False,
            errors=[LibraryError(code="VALIDATION_ERROR", message="test error")],
            setup=LibrarySetup(message="Complete billing setup"),
        )
        assert account.account_id == "acc_123"
        assert account.action == "created"
        assert account.status == "active"
        assert account.name == "Test Account"
        assert account.operator == "create"
        assert account.billing == "prepaid"
        assert account.sandbox is False
        assert len(account.errors) == 1
        assert account.errors[0].code == "VALIDATION_ERROR"
        assert account.brand.domain == "acme.com"
        assert account.setup.message == "Complete billing setup"

    def test_construct_minimal(self):
        """Model can be constructed with no fields (all optional)."""
        account = SyncResponseAccount()
        for field in EXPECTED_FIELDS:
            assert getattr(account, field) is None


class TestSyncResponseAccountSerialization:
    """SyncResponseAccount serializes correctly for wire transport."""

    def test_model_dump_includes_set_fields(self):
        """Fields with values appear in model_dump output."""
        account = SyncResponseAccount(
            account_id="acc_456",
            action="updated",
            status="active",
        )
        data = account.model_dump(exclude_none=True)
        assert data["account_id"] == "acc_456"
        assert data["action"] == "updated"
        assert data["status"] == "active"

    def test_model_dump_excludes_none_when_requested(self):
        """None-valued fields are excluded with exclude_none=True."""
        account = SyncResponseAccount(
            account_id="acc_789",
            action="created",
        )
        data = account.model_dump(exclude_none=True)
        assert "account_id" in data
        assert "action" in data
        # Unset fields should not appear
        assert "brand" not in data
        assert "operator" not in data
        assert "billing" not in data
        assert "sandbox" not in data
        assert "errors" not in data
        assert "setup" not in data

    def test_roundtrip_from_dict(self):
        """SyncResponseAccount can be constructed from a dict (transport deserialization)."""
        raw = {
            "account_id": "acc_rt",
            "action": "created",
            "status": "active",
            "operator": "create",
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
            account_id="acc_err",
            errors=[
                LibraryError(code="CONFLICT", message="duplicate account"),
            ],
        )
        data = account.model_dump(exclude_none=True)
        assert len(data["errors"]) == 1
        assert data["errors"][0]["code"] == "CONFLICT"
        assert data["errors"][0]["message"] == "duplicate account"
