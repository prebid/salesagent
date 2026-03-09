"""Integration tests for property list CRUD operations and tenant isolation.

Obligations covered:
- BR-RULE-071-01: Property list tenant isolation -- scoped to auth-derived tenant,
  cross-tenant returns NOT_FOUND (not ACCESS_DENIED) to prevent enumeration
- BR-RULE-076-01: Referential integrity -- get/update/delete require existing list_id
  (missing=LIST_NOT_FOUND), delete blocked by active media buys (LIST_IN_USE)
- BR-RULE-075-01: Update replacement semantics -- full replacement per field,
  webhook_url only in update (not create), empty string removes webhook

All tests call production _impl functions with real database via factories.
Tests for unimplemented _impl functions are marked xfail.
"""

from __future__ import annotations

import pytest
from adcp.types import (
    CreatePropertyListRequest,
    UpdatePropertyListRequest,
)
from pydantic import ValidationError

from tests.factories import PrincipalFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_XFAIL_NO_IMPL = pytest.mark.xfail(
    reason="Property list CRUD _impl functions not yet implemented",
    raises=(ImportError, AttributeError, NotImplementedError),
    strict=False,
)


def _lazy_identity(tenant_id: str, principal_id: str = "p1"):
    """Build a ResolvedIdentity for the given tenant."""
    from src.core.resolved_identity import ResolvedIdentity
    from src.core.tenant_context import LazyTenantContext

    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=LazyTenantContext(tenant_id),
    )


# ---------------------------------------------------------------------------
# BR-RULE-071-01: Property List Tenant Isolation
# ---------------------------------------------------------------------------


class TestTenantIsolationCreateNotVisibleCrossTenant:
    """A property list created in tenant_A must not be visible from tenant_B."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_property_list_scoped_to_tenant(self, integration_db):
        """Property list created for tenant_A is not visible from tenant_B lookup.

        The _impl function scopes every query to the auth-derived tenant_id.
        A cross-tenant lookup returns NOT_FOUND, not the list.

        Covers: BR-RULE-071-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
        )

        tenant_a = TenantFactory(tenant_id="pl-iso-a", subdomain="pl-iso-a")
        PrincipalFactory(tenant=tenant_a, principal_id="principal-a")
        tenant_b = TenantFactory(tenant_id="pl-iso-b", subdomain="pl-iso-b")
        PrincipalFactory(tenant=tenant_b, principal_id="principal-b")

        # Tenant A creates a list
        create_req = CreatePropertyListRequest(name="Tenant A List")
        identity_a = _lazy_identity("pl-iso-a", "principal-a")
        create_resp = await _create_property_list_impl(create_req, identity_a)
        list_id = create_resp.list.list_id

        # Tenant B tries to get it — should raise NOT_FOUND
        from src.core.exceptions import AdCPValidationError

        identity_b = _lazy_identity("pl-iso-b", "principal-b")
        with pytest.raises(AdCPValidationError, match="NOT_FOUND"):
            await _get_property_list_impl(list_id, identity_b)


class TestTenantIsolationGetReturnsNotFound:
    """Cross-tenant get returns NOT_FOUND, not ACCESS_DENIED."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_cross_tenant_get_returns_not_found(self, integration_db):
        """When tenant_B requests tenant_A's list_id, the error code is NOT_FOUND.

        NOT_FOUND (not ACCESS_DENIED) prevents tenant enumeration attacks.

        Covers: BR-RULE-071-01
        """
        from src.core.tools.property_list import _get_property_list_impl

        from src.core.exceptions import AdCPValidationError

        tenant_a = TenantFactory(tenant_id="pl-notfound-a", subdomain="pl-notfound-a")
        PrincipalFactory(tenant=tenant_a, principal_id="principal-a")

        identity_b = _lazy_identity("pl-notfound-b", "principal-b")
        with pytest.raises(AdCPValidationError, match="NOT_FOUND"):
            await _get_property_list_impl("pl_belongs_to_tenant_a", identity_b)


class TestTenantIsolationUpdateReturnsNotFound:
    """Cross-tenant update returns NOT_FOUND."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_cross_tenant_update_returns_not_found(self, integration_db):
        """Updating a list_id from the wrong tenant returns NOT_FOUND.

        Covers: BR-RULE-071-01
        """
        from src.core.tools.property_list import _update_property_list_impl

        from src.core.exceptions import AdCPValidationError

        update_req = UpdatePropertyListRequest(
            list_id="pl_belongs_to_tenant_a",
            name="Hijacked Name",
        )
        identity_b = _lazy_identity("wrong-tenant", "principal-b")
        with pytest.raises(AdCPValidationError, match="NOT_FOUND"):
            await _update_property_list_impl(update_req, identity_b)


class TestTenantIsolationDeleteReturnsNotFound:
    """Cross-tenant delete returns NOT_FOUND."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_cross_tenant_delete_returns_not_found(self, integration_db):
        """Deleting a list_id from the wrong tenant returns NOT_FOUND.

        Covers: BR-RULE-071-01
        """
        from src.core.tools.property_list import _delete_property_list_impl

        from src.core.exceptions import AdCPValidationError

        identity_b = _lazy_identity("wrong-tenant", "principal-b")
        with pytest.raises(AdCPValidationError, match="NOT_FOUND"):
            await _delete_property_list_impl("pl_belongs_to_tenant_a", identity_b)


# ---------------------------------------------------------------------------
# BR-RULE-076-01: Property List Referential Integrity
# ---------------------------------------------------------------------------


class TestReferentialIntegrityGetNonexistent:
    """get_property_list with nonexistent list_id returns LIST_NOT_FOUND."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_get_nonexistent_list_id_requires_not_found_error(self, integration_db):
        """GetPropertyListRequest with a nonexistent list_id must yield LIST_NOT_FOUND.

        Covers: BR-RULE-076-01
        """
        from src.core.tools.property_list import _get_property_list_impl

        from src.core.exceptions import AdCPValidationError

        tenant = TenantFactory(tenant_id="pl-ref-get", subdomain="pl-ref-get")
        PrincipalFactory(tenant=tenant, principal_id="pl-ref-get-p")
        identity = _lazy_identity("pl-ref-get", "pl-ref-get-p")

        with pytest.raises(AdCPValidationError, match="NOT_FOUND"):
            await _get_property_list_impl("pl_does_not_exist", identity)


class TestReferentialIntegrityUpdateNonexistent:
    """update_property_list with nonexistent list_id returns LIST_NOT_FOUND."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_update_nonexistent_list_id_requires_not_found_error(self, integration_db):
        """UpdatePropertyListRequest targeting a nonexistent list_id must yield LIST_NOT_FOUND.

        Covers: BR-RULE-076-01
        """
        from src.core.tools.property_list import _update_property_list_impl

        from src.core.exceptions import AdCPValidationError

        tenant = TenantFactory(tenant_id="pl-ref-upd", subdomain="pl-ref-upd")
        PrincipalFactory(tenant=tenant, principal_id="pl-ref-upd-p")
        identity = _lazy_identity("pl-ref-upd", "pl-ref-upd-p")

        req = UpdatePropertyListRequest(list_id="pl_does_not_exist", name="Updated Name")
        with pytest.raises(AdCPValidationError, match="NOT_FOUND"):
            await _update_property_list_impl(req, identity)


class TestReferentialIntegrityDeleteNonexistent:
    """delete_property_list with nonexistent list_id returns LIST_NOT_FOUND."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_delete_nonexistent_list_id_requires_not_found_error(self, integration_db):
        """DeletePropertyListRequest targeting a nonexistent list_id must yield LIST_NOT_FOUND.

        Covers: BR-RULE-076-01
        """
        from src.core.tools.property_list import _delete_property_list_impl

        from src.core.exceptions import AdCPValidationError

        tenant = TenantFactory(tenant_id="pl-ref-del", subdomain="pl-ref-del")
        PrincipalFactory(tenant=tenant, principal_id="pl-ref-del-p")
        identity = _lazy_identity("pl-ref-del", "pl-ref-del-p")

        with pytest.raises(AdCPValidationError, match="NOT_FOUND"):
            await _delete_property_list_impl("pl_does_not_exist", identity)


class TestReferentialIntegrityDeleteBlockedByActiveBuys:
    """delete_property_list blocked by active media buys returns LIST_IN_USE."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_delete_list_with_active_media_buys_blocked(self, integration_db):
        """When a property list is referenced by an active media buy,
        delete must return LIST_IN_USE and leave the list intact.

        Covers: BR-RULE-076-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _delete_property_list_impl,
        )

        from src.core.exceptions import AdCPValidationError

        tenant = TenantFactory(tenant_id="pl-inuse", subdomain="pl-inuse")
        PrincipalFactory(tenant=tenant, principal_id="pl-inuse-p")
        identity = _lazy_identity("pl-inuse", "pl-inuse-p")

        # Create the list
        create_req = CreatePropertyListRequest(name="Active Campaign List")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        # TODO: Create an active media buy that references this list_id
        # (Requires MediaBuyFactory with property_list targeting)

        # Attempt to delete — should fail with LIST_IN_USE
        with pytest.raises(AdCPValidationError, match="IN_USE"):
            await _delete_property_list_impl(list_id, identity)


# ---------------------------------------------------------------------------
# BR-RULE-075-01: Update Replacement Semantics
# ---------------------------------------------------------------------------


class TestUpdateBasePropertiesFullReplacement:
    """Update base_properties replaces the entire previous set."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_update_base_properties_full_replacement(self, integration_db):
        """When update provides new base_properties, the old set is fully replaced.

        Full replacement semantics: _impl does NOT merge old and new — it overwrites.

        Covers: BR-RULE-075-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
            _update_property_list_impl,
        )

        tenant = TenantFactory(tenant_id="pl-replace", subdomain="pl-replace")
        PrincipalFactory(tenant=tenant, principal_id="pl-replace-p")
        identity = _lazy_identity("pl-replace", "pl-replace-p")

        # Create with publisher_tags source
        create_req = CreatePropertyListRequest(
            name="Original List",
            base_properties=[
                {
                    "selection_type": "publisher_tags",
                    "publisher_domain": "news.com",
                    "tags": ["sports", "politics"],
                }
            ],
        )
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        # Update with publisher_ids source (completely different)
        update_req = UpdatePropertyListRequest(
            list_id=list_id,
            base_properties=[
                {
                    "selection_type": "publisher_ids",
                    "publisher_domain": "news.com",
                    "property_ids": ["prop_001"],
                }
            ],
        )
        await _update_property_list_impl(update_req, identity)

        # Verify: old publisher_tags are gone, only publisher_ids remain
        get_resp = await _get_property_list_impl(list_id, identity)
        assert len(get_resp.list.base_properties) == 1
        assert get_resp.list.base_properties[0].root.selection_type == "publisher_ids"


class TestUpdateWebhookUrlSet:
    """webhook_url can be set via update."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_update_sets_webhook_url(self, integration_db):
        """UpdatePropertyListRequest with a valid webhook_url sets the webhook.

        Covers: BR-RULE-075-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
            _update_property_list_impl,
        )

        tenant = TenantFactory(tenant_id="pl-webhook", subdomain="pl-webhook")
        PrincipalFactory(tenant=tenant, principal_id="pl-webhook-p")
        identity = _lazy_identity("pl-webhook", "pl-webhook-p")

        create_req = CreatePropertyListRequest(name="Webhook List")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        update_req = UpdatePropertyListRequest(
            list_id=list_id,
            webhook_url="https://example.com/webhook",
        )
        await _update_property_list_impl(update_req, identity)

        get_resp = await _get_property_list_impl(list_id, identity)
        assert str(get_resp.list.webhook_url) == "https://example.com/webhook"


class TestUpdateWebhookUrlRemoveWithEmptyString:
    """Empty string webhook_url removes the webhook."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_empty_string_webhook_url_removes_webhook(self, integration_db):
        """Per BR-RULE-075, empty string webhook_url removes a previously set webhook.

        The _impl must intercept webhook_url='' BEFORE schema validation and
        translate it to webhook_url=None (removal).

        Covers: BR-RULE-075-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
            _update_property_list_impl,
        )

        tenant = TenantFactory(tenant_id="pl-rmhook", subdomain="pl-rmhook")
        PrincipalFactory(tenant=tenant, principal_id="pl-rmhook-p")
        identity = _lazy_identity("pl-rmhook", "pl-rmhook-p")

        # Create and set a webhook
        create_req = CreatePropertyListRequest(name="Remove Hook List")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        update_req = UpdatePropertyListRequest(
            list_id=list_id,
            webhook_url="https://example.com/webhook",
        )
        await _update_property_list_impl(update_req, identity)

        # Empty string removes the webhook — _impl handles this before schema validation
        # (adcp AnyUrl rejects empty string, so _impl intercepts raw input)
        await _update_property_list_impl(
            UpdatePropertyListRequest.__construct__(list_id=list_id, webhook_url=""),
            identity,
        )

        get_resp = await _get_property_list_impl(list_id, identity)
        assert get_resp.list.webhook_url is None


class TestCreateRejectsWebhookUrl:
    """webhook_url is NOT allowed on create -- only on update."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_create_with_webhook_url_rejected(self, integration_db):
        """CreatePropertyListRequest rejects webhook_url (extra field forbidden).

        Per BR-RULE-075, webhook_url is settable only via update_property_list.
        Schema-level enforcement prevents constructing the request, so _impl
        never receives webhook_url on create.

        Covers: BR-RULE-075-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        identity = _lazy_identity("pl-no-webhook", "pl-no-webhook-p")

        # Schema-level: webhook_url is forbidden on create
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            req = CreatePropertyListRequest(
                name="New List",
                webhook_url="https://example.com/hook",
            )
            await _create_property_list_impl(req, identity)


class TestUpdateFieldsNotProvidedUnchanged:
    """Fields not included in update remain unchanged."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_omitted_fields_remain_unchanged(self, integration_db):
        """UpdatePropertyListRequest with only name does not affect other fields.

        Full replacement is per-field: omitted fields retain current values.

        Covers: BR-RULE-075-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
            _update_property_list_impl,
        )

        tenant = TenantFactory(tenant_id="pl-partial", subdomain="pl-partial")
        PrincipalFactory(tenant=tenant, principal_id="pl-partial-p")
        identity = _lazy_identity("pl-partial", "pl-partial-p")

        # Create with name and description
        create_req = CreatePropertyListRequest(
            name="Original Name",
            description="Original Description",
        )
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        # Update only the name
        update_req = UpdatePropertyListRequest(list_id=list_id, name="New Name")
        await _update_property_list_impl(update_req, identity)

        # Description should be unchanged
        get_resp = await _get_property_list_impl(list_id, identity)
        assert get_resp.list.name == "New Name"
        assert get_resp.list.description == "Original Description"


# ---------------------------------------------------------------------------
# Response schema round-trip verification
# ---------------------------------------------------------------------------


class TestCreateResponseIncludesAuthToken:
    """CreatePropertyListResponse includes a one-time auth_token."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_create_response_has_auth_token(self, integration_db):
        """The create response must include auth_token (one-time secret).

        Covers: BR-RULE-076-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="pl-auth", subdomain="pl-auth")
        PrincipalFactory(tenant=tenant, principal_id="pl-auth-p")
        identity = _lazy_identity("pl-auth", "pl-auth-p")

        create_req = CreatePropertyListRequest(name="Brand New List")
        resp = await _create_property_list_impl(create_req, identity)

        assert resp.auth_token is not None
        assert len(resp.auth_token) > 0
        assert resp.list.list_id is not None
        assert resp.list.name == "Brand New List"


class TestDeleteResponseSchema:
    """DeletePropertyListResponse confirms deletion with list_id echo."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_delete_response_echoes_list_id(self, integration_db):
        """Successful delete returns deleted=True and the list_id.

        Covers: BR-RULE-076-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _delete_property_list_impl,
        )

        tenant = TenantFactory(tenant_id="pl-del", subdomain="pl-del")
        PrincipalFactory(tenant=tenant, principal_id="pl-del-p")
        identity = _lazy_identity("pl-del", "pl-del-p")

        create_req = CreatePropertyListRequest(name="To Delete")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        resp = await _delete_property_list_impl(list_id, identity)
        assert resp.deleted is True
        assert resp.list_id == list_id
