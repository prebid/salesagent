"""Test harness package — shared test environments for obligation tests.

Two variants available:
- **Integration (default)**: Real database, only mocks external services.
  Requires ``integration_db`` fixture.
- **Unit**: Full mocking for fast unit tests (backward compat).

Usage (integration — preferred)::

    from tests.harness._base import IntegrationEnv

    class MyDomainEnv(IntegrationEnv):
        EXTERNAL_PATCHES = {"adapter": "src.adapters.get_adapter"}

        def _configure_mocks(self):
            self.mock["adapter"].return_value = MagicMock()

        def call_impl(self, **kwargs):
            req = MyRequest(**kwargs)
            return _my_impl(req, self.identity)

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with MyDomainEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            response = env.call_impl(...)

Usage (unit — backward compat)::

    from tests.harness._base import BaseTestEnv

    class MyDomainEnvUnit(BaseTestEnv):
        EXTERNAL_PATCHES = {"db": "src.core.database.database_session.get_db_session", ...}
        ...

    def test_something(self):
        with MyDomainEnvUnit() as env:
            ...
"""

from tests.harness._mock_uow import make_mock_uow
from tests.harness.creative_formats import CreativeFormatsEnv
from tests.harness.creative_list import CreativeListEnv
from tests.harness.creative_sync import CreativeSyncEnv

__all__ = [
    "make_mock_uow",
    "CreativeFormatsEnv",
    "CreativeListEnv",
    "CreativeSyncEnv",
]
