"""Contract test for SyncResponseAccount locally-owned model.

SyncResponseAccount replaced an SDK-provided type after SDK 5.7 restructured
the sync_accounts response. This contract test verifies:
  1. Optional fields stay optional
  2. Fields serialize correctly via model_dump
  3. None-valued fields are excluded by default

It does NOT compare the model's field set to a literal. That test existed and is
retired: SyncResponseAccount inherits the pinned item type, so the set is whatever the
pin declares, and re-listing it here only asks whether someone retyped the pin. CLAUDE.md
rules it out by name. EXPECTED_FIELDS survives only as the optional-field roster below.
"""

from adcp.types.generated_poc.core.brand_ref import BrandReference

from src.core.errors.codes import CODE_TABLE
from src.core.schemas import Error as LibraryError
from src.core.schemas import SyncResponseAccount

# The 13 fields that production code (_build_sync_result / _build_failed_result)
# constructs. payment_terms added salesagent-5g8e (F1 settings-update Then needs a
# field to read). notification_configs added salesagent-ck9v (#1592 T2): the
# sync-accounts-response schema requires the applied subscriber set to be echoed on
# created/updated/unchanged results. billing_entity added salesagent-gcze: the
# response account item carries it "echoed from the request ... Bank details are
# omitted (write-only)" (v3.1.1 sync-accounts-response.json), and before that it was
# accepted on the wire by both request branches and then silently dropped.
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

    # Required-field enforcement (brand/operator/action/status per pinned schema
    # 04f59d2d5) was verified generically by the alignment suite, which is deleted
    # (docs/development/building-tools.md). The model inherits the library type, so what it
    # requires is the library's; the tests in this class grade the behaviour on top.

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
        # The authored "duplicate account" is DISCARDED: the account model types this
        # field as our Error, so pydantic re-validates the SDK instance through the
        # CODE_TABLE derivation and the buyer reads the sentence the code defines
        # (ADR-010). What is specific to the account travels in details/field.
        assert data["errors"][0]["message"] == CODE_TABLE["CONFLICT"].message
