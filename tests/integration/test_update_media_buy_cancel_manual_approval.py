"""Regression coverage for cancel bypassing update manual approval (#324)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.database.models import ObjectWorkflowMapping, WorkflowStep
from src.core.exceptions import AdCPNotCancellableError
from src.core.schemas import GetMediaBuysRequest, MediaBuyStatus, UpdateMediaBuyRequest, UpdateMediaBuySuccess
from src.core.tools.media_buy_list import _get_media_buys_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import AdapterConfigFactory, MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.factories.spec_required_kwargs import required_request_kwargs


@pytest.mark.requires_db
def test_cancel_persists_when_update_media_buy_requires_manual_approval(factory_session):
    tenant = TenantFactory(ad_server="mock")
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="mock",
        mock_manual_approval_required=True,
    )
    principal = PrincipalFactory(tenant=tenant)
    media_buy = MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        status="active",
    )
    identity = PrincipalFactory.make_identity(
        tenant_id=tenant.tenant_id,
        principal_id=principal.principal_id,
        auth_token=principal.access_token,
        tenant={"tenant_id": tenant.tenant_id, "name": tenant.name, "ad_server": "mock"},
    )

    cancel_req = UpdateMediaBuyRequest(
        **required_request_kwargs(),
        media_buy_id=media_buy.media_buy_id,
        canceled=True,
        cancellation_reason="buyer requested cancellation",
    )
    cancel_response = _update_media_buy_impl(req=cancel_req, identity=identity)

    assert isinstance(cancel_response, UpdateMediaBuySuccess)
    assert cancel_response.status == "completed"
    assert cancel_response.media_buy_status == MediaBuyStatus.canceled

    factory_session.expire_all()
    mappings = list(
        factory_session.scalars(
            select(ObjectWorkflowMapping).filter_by(
                object_type="media_buy",
                object_id=media_buy.media_buy_id,
                action="update",
            )
        ).all()
    )
    assert len(mappings) == 1

    workflow_step = factory_session.scalars(select(WorkflowStep).filter_by(step_id=mappings[0].step_id)).one()
    assert workflow_step.status == "completed"

    list_response = _get_media_buys_impl(
        req=GetMediaBuysRequest(
            media_buy_ids=[media_buy.media_buy_id],
            status_filter=[MediaBuyStatus.canceled],
        ),
        identity=identity,
    )

    assert len(list_response.media_buys) == 1
    assert list_response.media_buys[0].media_buy_id == media_buy.media_buy_id
    assert list_response.media_buys[0].status == MediaBuyStatus.canceled

    second_cancel_req = UpdateMediaBuyRequest(
        **required_request_kwargs(),
        media_buy_id=media_buy.media_buy_id,
        canceled=True,
        cancellation_reason="buyer requested cancellation again",
    )
    with pytest.raises(AdCPNotCancellableError) as exc_info:
        _update_media_buy_impl(req=second_cancel_req, identity=identity)

    assert exc_info.value.error_code == "NOT_CANCELLABLE"
