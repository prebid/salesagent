"""Non-wire creative callbacks persist sandbox results without shared live IDs."""

import pytest

from src.core.tools.media_buy_create import push_creative_to_existing_buy
from tests.harness.sandbox_lifecycle import SandboxLifecycleEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.parametrize("pricing_model,expected_outcome", [("cpcv", "failed"), ("cpc", "executed")])
def test_submitted_gam_buy_validates_pricing_before_deferred_execution(integration_db, pricing_model, expected_outcome):
    from src.core.tools.media_buy_create import execute_approved_media_buy
    from tests.harness.transport import Transport

    with SandboxLifecycleEnv() as env:
        env.seed_lifecycle(approval=True, adapter_type="google_ad_manager")
        submitted = env.call_via(Transport.REST, req=env.seed_pricing_request(pricing_model))
        body = submitted.require_wire()
        assert body["status"] == "submitted"
        assert isinstance(body["task_id"], str) and body["task_id"]
        assert "media_buy_id" not in body and "packages" not in body
        buy_id = env.submitted_buy_id()
        before = env.execution_state(buy_id)
        assert before[0] == "pending_approval"
        assert before[1] is None

        result = execute_approved_media_buy(buy_id, env.tenant_id)

        assert result.outcome.value == expected_outcome
        status, confirmed_at, packages = env.execution_state(buy_id)
        if expected_outcome == "failed":
            assert (status, confirmed_at, packages) == before
            assert all(
                "platform_order_id" not in package and "platform_line_item_id" not in package for package in packages
            )
            assert env.workflow_status(body["task_id"]) == "requires_approval"
        else:
            assert status == "scheduled"
            assert confirmed_at is not None
            assert all(package["platform_order_id"].startswith("sandbox_") for package in packages)
        assert env.vendor_calls() == 0


def test_later_creative_push_uses_buy_account_and_preserves_shared_live_id(integration_db):
    with SandboxLifecycleEnv() as env:
        env.seed_lifecycle(adapter_type="google_ad_manager")
        creative_id, _ = env.seed_shared_creative()
        ok, error = push_creative_to_existing_buy(
            creative_id=creative_id, media_buy_id="sandbox-creative-buy", tenant_id=env.tenant_id
        )
        assert (ok, error) == (True, None)
        creative, sandbox_package, live_package = env.creative_persistence(creative_id)
        assert creative["platform_creative_id"] == "live-creative-123"
        assert sandbox_package["sandbox_creative_ids"][creative_id].startswith("sandbox_creative_")
        assert "sandbox_creative_ids" not in live_package
        assert env.vendor_calls() == 0
        before = sandbox_package["sandbox_creative_ids"]
        assert push_creative_to_existing_buy(
            creative_id=creative_id, media_buy_id="sandbox-creative-buy", tenant_id=env.tenant_id
        ) == (True, None)
        assert env.creative_persistence(creative_id)[1]["sandbox_creative_ids"] == before
