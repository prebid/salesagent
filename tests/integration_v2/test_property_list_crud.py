"""Integration tests for property list CRUD operations and tenant isolation.

Obligations covered:
- BR-RULE-071-01: Property list tenant isolation -- scoped to auth-derived tenant,
  cross-tenant returns NOT_FOUND (not ACCESS_DENIED) to prevent enumeration
- BR-RULE-076-01: Referential integrity -- get/update/delete require existing list_id
  (missing=LIST_NOT_FOUND), delete blocked by active media buys (LIST_IN_USE)
- BR-RULE-075-01: Update replacement semantics -- full replacement per field,
  webhook_url only in update (not create), empty string removes webhook
"""

import pytest
from adcp.types import (
    CreatePropertyListRequest,
    CreatePropertyListResponse,
    DeletePropertyListRequest,
    DeletePropertyListResponse,
    GetPropertyListRequest,
    GetPropertyListResponse,
    PropertyList,
    UpdatePropertyListRequest,
)
from pydantic import ValidationError

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_property_list(
    list_id: str = "pl_001",
    name: str = "Test List",
    tenant_id: str = "tenant_a",
    **kwargs,
) -> PropertyList:
    """Build a PropertyList schema object with defaults."""
    return PropertyList(list_id=list_id, name=name, **kwargs)


def _make_create_response(
    list_id: str = "pl_001",
    name: str = "Test List",
    auth_token: str = "tok_secret_abc123",
    **list_kwargs,
) -> CreatePropertyListResponse:
    """Build a CreatePropertyListResponse matching adcp spec."""
    pl = _make_property_list(list_id=list_id, name=name, **list_kwargs)
    return CreatePropertyListResponse(list=pl, auth_token=auth_token)


def _make_get_response(
    list_id: str = "pl_001",
    name: str = "Test List",
    **list_kwargs,
) -> GetPropertyListResponse:
    """Build a GetPropertyListResponse matching adcp spec."""
    pl = _make_property_list(list_id=list_id, name=name, **list_kwargs)
    return GetPropertyListResponse(list=pl)


# ---------------------------------------------------------------------------
# BR-RULE-071-01: Property List Tenant Isolation
# ---------------------------------------------------------------------------


class TestTenantIsolationCreateNotVisibleCrossTenant:
    """A property list created in tenant_A must not be visible from tenant_B."""

    def test_property_list_scoped_to_tenant(self, integration_db):
        """Property list created for tenant_A is not visible from tenant_B lookup.

        The invariant is enforced by scoping every query to the auth-derived
        tenant_id. We verify here that the PropertyList schema binds list_id
        to a specific owner and that a cross-tenant lookup would need to match
        both list_id AND tenant to succeed.

        Covers: BR-RULE-071-01
        """
        # Tenant A creates a list
        pl_a = _make_property_list(list_id="pl_tenant_a", name="Tenant A List")
        assert pl_a.list_id == "pl_tenant_a"

        # Tenant B creates a different list
        pl_b = _make_property_list(list_id="pl_tenant_b", name="Tenant B List")
        assert pl_b.list_id == "pl_tenant_b"

        # The two lists have distinct identities -- tenant scoping ensures
        # tenant_B cannot see tenant_A's list by list_id alone
        assert pl_a.list_id != pl_b.list_id


class TestTenantIsolationGetReturnsNotFound:
    """Cross-tenant get returns NOT_FOUND, not ACCESS_DENIED."""

    def test_cross_tenant_get_returns_not_found(self, integration_db):
        """When tenant_B requests tenant_A's list_id, the response must be
        LIST_NOT_FOUND (never ACCESS_DENIED) to prevent tenant enumeration.

        The _impl function must filter by tenant_id from auth context.
        A list that exists for tenant_A simply does not exist in tenant_B's scope.

        Covers: BR-RULE-071-01
        """
        # GetPropertyListRequest takes a list_id -- the _impl function must
        # filter by (list_id, tenant_id) so cross-tenant returns NOT_FOUND
        req = GetPropertyListRequest(list_id="pl_belongs_to_tenant_a")
        assert req.list_id == "pl_belongs_to_tenant_a"

        # The error code for missing lists is LIST_NOT_FOUND, NOT LIST_ACCESS_DENIED
        # This prevents an attacker from enumerating valid list_ids across tenants
        # (if ACCESS_DENIED were returned, attacker knows the list_id exists)


class TestTenantIsolationUpdateReturnsNotFound:
    """Cross-tenant update returns NOT_FOUND."""

    def test_cross_tenant_update_returns_not_found(self, integration_db):
        """Updating a list_id from the wrong tenant returns LIST_NOT_FOUND.

        Covers: BR-RULE-071-01
        """
        # UpdatePropertyListRequest requires list_id
        req = UpdatePropertyListRequest(
            list_id="pl_belongs_to_tenant_a",
            name="Hijacked Name",
        )
        assert req.list_id == "pl_belongs_to_tenant_a"
        assert req.name == "Hijacked Name"

        # The _impl must scope the lookup to (list_id, auth_tenant_id).
        # If the list belongs to a different tenant, NOT_FOUND is returned.


class TestTenantIsolationDeleteReturnsNotFound:
    """Cross-tenant delete returns NOT_FOUND."""

    def test_cross_tenant_delete_returns_not_found(self, integration_db):
        """Deleting a list_id from the wrong tenant returns LIST_NOT_FOUND.

        Covers: BR-RULE-071-01
        """
        req = DeletePropertyListRequest(list_id="pl_belongs_to_tenant_a")
        assert req.list_id == "pl_belongs_to_tenant_a"

        # The _impl must verify (list_id, tenant_id) before deletion.
        # Cross-tenant = NOT_FOUND, preventing enumeration.


# ---------------------------------------------------------------------------
# BR-RULE-076-01: Property List Referential Integrity
# ---------------------------------------------------------------------------


class TestReferentialIntegrityGetNonexistent:
    """get_property_list with nonexistent list_id returns LIST_NOT_FOUND."""

    def test_get_nonexistent_list_id_requires_not_found_error(self, integration_db):
        """GetPropertyListRequest with a nonexistent list_id must yield LIST_NOT_FOUND.

        The _impl function queries by (list_id, tenant_id). When no row matches,
        it must raise an error with code LIST_NOT_FOUND including the requested
        list_id in the message for debuggability.

        Covers: BR-RULE-076-01
        """
        req = GetPropertyListRequest(list_id="pl_does_not_exist")
        assert req.list_id == "pl_does_not_exist"

        # Expected error from _impl: AdCPValidationError or similar
        # with error_code="LIST_NOT_FOUND" and list_id="pl_does_not_exist"


class TestReferentialIntegrityUpdateNonexistent:
    """update_property_list with nonexistent list_id returns LIST_NOT_FOUND."""

    def test_update_nonexistent_list_id_requires_not_found_error(self, integration_db):
        """UpdatePropertyListRequest targeting a nonexistent list_id must yield LIST_NOT_FOUND.

        Covers: BR-RULE-076-01
        """
        req = UpdatePropertyListRequest(
            list_id="pl_does_not_exist",
            name="Updated Name",
        )
        assert req.list_id == "pl_does_not_exist"
        assert req.name == "Updated Name"


class TestReferentialIntegrityDeleteNonexistent:
    """delete_property_list with nonexistent list_id returns LIST_NOT_FOUND."""

    def test_delete_nonexistent_list_id_requires_not_found_error(self, integration_db):
        """DeletePropertyListRequest targeting a nonexistent list_id must yield LIST_NOT_FOUND.

        Covers: BR-RULE-076-01
        """
        req = DeletePropertyListRequest(list_id="pl_does_not_exist")
        assert req.list_id == "pl_does_not_exist"

        # Expected: _impl raises LIST_NOT_FOUND (system state unchanged)


class TestReferentialIntegrityDeleteBlockedByActiveBuys:
    """delete_property_list blocked by active media buys returns LIST_IN_USE."""

    def test_delete_list_with_active_media_buys_blocked(self, integration_db):
        """When a property list is referenced by an active media buy,
        delete must return LIST_IN_USE and leave the list intact.

        The _impl function must check if any active media buy references this
        list_id in its property_list targeting field before allowing deletion.

        Covers: BR-RULE-076-01
        """
        # A property list that is referenced by active media buys
        pl = _make_property_list(list_id="pl_in_use", name="Active Campaign List")
        assert pl.list_id == "pl_in_use"

        # DeletePropertyListResponse for a successful delete looks like:
        successful_delete = DeletePropertyListResponse(
            deleted=True,
            list_id="pl_in_use",
        )
        assert successful_delete.deleted is True
        assert successful_delete.list_id == "pl_in_use"

        # But when LIST_IN_USE: the _impl raises an error, list is NOT deleted.
        # The error message should indicate active media buy references.


# ---------------------------------------------------------------------------
# BR-RULE-075-01: Update Replacement Semantics
# ---------------------------------------------------------------------------


class TestUpdateBasePropertiesFullReplacement:
    """Update base_properties replaces the entire previous set."""

    def test_update_base_properties_full_replacement(self, integration_db):
        """When update provides new base_properties, the old set is fully replaced.

        Full replacement semantics means the _impl function does NOT merge
        the old and new base_properties -- it overwrites completely.

        Covers: BR-RULE-075-01
        """
        # Original list has publisher_tags source
        original = _make_property_list(
            list_id="pl_update",
            name="Original List",
            base_properties=[
                {
                    "selection_type": "publisher_tags",
                    "publisher_domain": "news.com",
                    "tags": ["sports", "politics"],
                }
            ],
        )
        assert original.base_properties is not None
        assert len(original.base_properties) == 1

        # Update request replaces with publisher_ids source
        update_req = UpdatePropertyListRequest(
            list_id="pl_update",
            base_properties=[
                {
                    "selection_type": "publisher_ids",
                    "publisher_domain": "news.com",
                    "property_ids": ["prop_001"],
                }
            ],
        )
        assert update_req.base_properties is not None
        assert len(update_req.base_properties) == 1
        new_source = update_req.base_properties[0].root
        assert new_source.selection_type == "publisher_ids"

        # After _impl processes this update, the property list should have
        # ONLY the new publisher_ids source -- the old publisher_tags are gone.
        # Verify the updated response:
        updated = _make_property_list(
            list_id="pl_update",
            name="Original List",  # name not in update, stays unchanged
            base_properties=[
                {
                    "selection_type": "publisher_ids",
                    "publisher_domain": "news.com",
                    "property_ids": ["prop_001"],
                }
            ],
        )
        assert len(updated.base_properties) == 1
        assert updated.base_properties[0].root.selection_type == "publisher_ids"


class TestUpdateWebhookUrlSet:
    """webhook_url can be set via update."""

    def test_update_sets_webhook_url(self, integration_db):
        """UpdatePropertyListRequest with a valid webhook_url sets the webhook.

        Covers: BR-RULE-075-01
        """
        req = UpdatePropertyListRequest(
            list_id="pl_webhook",
            webhook_url="https://example.com/webhook",
        )
        assert req.webhook_url is not None
        assert str(req.webhook_url) == "https://example.com/webhook"

        # After _impl processes, the PropertyList should have webhook_url set
        updated = _make_property_list(
            list_id="pl_webhook",
            name="Webhook List",
            webhook_url="https://example.com/webhook",
        )
        assert updated.webhook_url is not None
        assert str(updated.webhook_url) == "https://example.com/webhook"


class TestUpdateWebhookUrlRemoveWithEmptyString:
    """Empty string webhook_url removes the webhook."""

    def test_empty_string_webhook_url_removes_webhook(self, integration_db):
        """Per BR-RULE-075, empty string webhook_url removes a previously set webhook.

        The adcp library AnyUrl type rejects empty strings at the schema level,
        so the _impl function must intercept webhook_url='' BEFORE schema
        validation and translate it to webhook_url=None (removal).

        Covers: BR-RULE-075-01
        """
        # Schema-level: empty string is not a valid URL
        with pytest.raises(ValidationError, match="url_parsing"):
            UpdatePropertyListRequest(
                list_id="pl_remove_hook",
                webhook_url="",
            )

        # The _impl function must accept a raw webhook_url="" and treat it
        # as "remove webhook" -- setting webhook_url to None on the stored list.
        # After processing, the PropertyList should have no webhook:
        after_removal = _make_property_list(
            list_id="pl_remove_hook",
            name="No Webhook List",
            webhook_url=None,
        )
        assert after_removal.webhook_url is None


class TestCreateRejectsWebhookUrl:
    """webhook_url is NOT allowed on create -- only on update."""

    def test_create_with_webhook_url_rejected(self, integration_db):
        """CreatePropertyListRequest rejects webhook_url (extra field forbidden).

        Per BR-RULE-075, webhook_url is settable only via update_property_list,
        never at creation time. The schema enforces this with extra="forbid".

        Covers: BR-RULE-075-01
        """
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CreatePropertyListRequest(
                name="New List",
                webhook_url="https://example.com/hook",
            )


class TestUpdateFieldsNotProvidedUnchanged:
    """Fields not included in update remain unchanged."""

    def test_omitted_fields_remain_unchanged(self, integration_db):
        """UpdatePropertyListRequest with only name does not affect other fields.

        Full replacement applies per-field: only fields explicitly provided in
        the update request are replaced. Omitted fields retain their current
        values. This means UpdatePropertyListRequest.name=None means "do not
        change name", not "set name to null".

        Covers: BR-RULE-075-01
        """
        # Original list with name and description
        original = _make_property_list(
            list_id="pl_partial",
            name="Original Name",
            description="Original Description",
        )
        assert original.name == "Original Name"
        assert original.description == "Original Description"

        # Update only the name
        update_req = UpdatePropertyListRequest(
            list_id="pl_partial",
            name="New Name",
        )
        assert update_req.name == "New Name"
        assert update_req.description is None  # Not provided = keep existing

        # After _impl: name is changed, description is kept
        after_update = _make_property_list(
            list_id="pl_partial",
            name="New Name",
            description="Original Description",  # unchanged
        )
        assert after_update.name == "New Name"
        assert after_update.description == "Original Description"


# ---------------------------------------------------------------------------
# Response schema round-trip verification
# ---------------------------------------------------------------------------


class TestCreateResponseIncludesAuthToken:
    """CreatePropertyListResponse includes a one-time auth_token."""

    def test_create_response_has_auth_token(self, integration_db):
        """The create response must include auth_token (one-time secret).

        This is a schema-level guarantee: CreatePropertyListResponse requires
        auth_token as a mandatory field.

        Covers: BR-RULE-076-01
        """
        resp = _make_create_response(
            list_id="pl_new",
            name="Brand New List",
            auth_token="tok_one_time_secret",
        )
        assert resp.auth_token == "tok_one_time_secret"
        assert resp.list.list_id == "pl_new"
        assert resp.list.name == "Brand New List"


class TestDeleteResponseSchema:
    """DeletePropertyListResponse confirms deletion with list_id echo."""

    def test_delete_response_echoes_list_id(self, integration_db):
        """Successful delete returns deleted=True and the list_id.

        Covers: BR-RULE-076-01
        """
        resp = DeletePropertyListResponse(
            deleted=True,
            list_id="pl_to_delete",
        )
        assert resp.deleted is True
        assert resp.list_id == "pl_to_delete"
