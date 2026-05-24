import pytest

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


def test_deactivate_active_for_principal_purpose_preserves_except_config(integration_db):
    from src.core.database.repositories.push_notification import PushNotificationConfigRepository
    from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

    with _RepoEnv() as env:
        tenant = TenantFactory(tenant_id="push_repo_t1")
        principal = PrincipalFactory(tenant=tenant, principal_id="agent_1")
        keep = PushNotificationConfigFactory(
            id="pnc_keep",
            tenant=tenant,
            principal=principal,
            purpose="catalog_changes",
            is_active=True,
        )
        old = PushNotificationConfigFactory(
            id="pnc_old",
            tenant=tenant,
            principal=principal,
            purpose="catalog_changes",
            is_active=True,
        )
        async_task = PushNotificationConfigFactory(
            id="pnc_task",
            tenant=tenant,
            principal=principal,
            purpose="async_task",
            is_active=True,
        )
        session = env.get_session()
        repo = PushNotificationConfigRepository(session, tenant.tenant_id)

        count = repo.deactivate_active_for_principal_purpose(
            principal_id=principal.principal_id,
            purpose="catalog_changes",
            except_config_id=keep.id,
        )
        session.flush()
        states = {
            "keep": keep.is_active,
            "old": old.is_active,
            "async_task": async_task.is_active,
        }

    assert count == 1
    assert states == {"keep": True, "old": False, "async_task": True}
