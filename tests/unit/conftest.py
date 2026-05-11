"""
Unit test specific fixtures.

These fixtures are only available to unit tests.
"""

import sys
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest


def _build_scalars_dispatch(model_to_result: dict[type, Any]):
    """Build a callable side_effect for ``Session.scalars(stmt)`` that dispatches by target model.

    The admin blueprints query several distinct models in one view body (Tenant,
    TenantAuthConfig, User). A flat ``side_effect = [...]`` list breaks as soon
    as the call order or count drifts from the test's assumption — and pretty
    much every test that issues two requests trips that. Inspecting the select
    statement's target entity is cheap and order-independent.
    """

    def _dispatch(stmt: Any) -> Any:
        result_mock = MagicMock()
        # SQLAlchemy ``select(Model)`` exposes the target via column_descriptions.
        target = None
        try:
            target = stmt.column_descriptions[0]["entity"]
        except (AttributeError, IndexError, KeyError, TypeError):
            pass
        if target is not None and target in model_to_result:
            result_mock.first.return_value = model_to_result[target]
            result_mock.all.return_value = [model_to_result[target]] if model_to_result[target] is not None else []
        else:
            # Unknown model — return empty so the view's `if result` guard short-circuits
            # instead of getting a MagicMock that pretends to be every shape.
            result_mock.first.return_value = None
            result_mock.all.return_value = []
        return result_mock

    return _dispatch


@pytest.fixture(autouse=True)
def mock_all_external_dependencies():
    """Automatically mock all external dependencies for unit tests."""
    # Mock database connections - create a proper context manager mock
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=None)
    # Configure mock to return None for tenant-specific attributes that would otherwise
    # return MagicMock objects and cause type validation errors (e.g., Pydantic validation)
    # The .first() result is a MagicMock, but these specific attributes are set to None
    mock_first_result = MagicMock()
    mock_first_result.gemini_api_key = None  # Prevents str type validation errors in naming
    mock_first_result.order_name_template = None
    mock_session.scalars.return_value.first.return_value = mock_first_result

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_db.return_value = mock_session

        # Mock external services
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}

            yield


@pytest.fixture(autouse=True)
def _default_workflow_repo_misses_idempotency_key(request):
    """Default WorkflowRepository.find_by_idempotency_key to return None.

    The idempotency-replay branch in _update_media_buy_impl reads
    WorkflowRepository(session, tenant_id).find_by_idempotency_key(...) and
    branches on the result. Tests that mock the surrounding session/UoW but
    don't explicitly patch WorkflowRepository would get a bare MagicMock —
    truthy by default — which sends every test through the replay path and
    fails on `UpdateMediaBuySuccess.model_validate({})`.

    Tests that exercise the replay path patch WorkflowRepository themselves
    (their patch wins because they enter inside this fixture's scope).

    Skip when test marks itself ``no_default_workflow_repo`` (rare).
    """
    if request.node.get_closest_marker("no_default_workflow_repo"):
        yield
        return
    instance = MagicMock()
    instance.find_by_idempotency_key.return_value = None
    cls_mock = MagicMock(return_value=instance)
    with patch("src.core.database.repositories.workflow.WorkflowRepository", cls_mock):
        yield


@pytest.fixture
def standard_mocks():
    """Context manager that patches all common dependencies for _update_media_buy_impl.

    Patches MediaBuyUoW to provide a mock session and repository,
    and patches all other common dependencies.

    Yields a dict of mock objects keyed by short name.
    """
    mock_session = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = Mock(return_value=mock_session)
    mock_cm.__exit__ = Mock(return_value=False)

    mock_cl = MagicMock()
    mock_cl.max_daily_package_spend = Decimal("100000")
    mock_cl.min_package_budget = Decimal("0")

    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.media_buys = MagicMock()
    # Default idempotency lookup misses — tests exercising the replay path
    # override per-test. Without this, the bare MagicMock returns a truthy
    # default and every request looks like an idempotency hit.
    mock_uow.media_buys.find_by_idempotency_key.return_value = None
    mock_currency_limits_repo = MagicMock()
    mock_currency_limits_repo.get_for_currency.return_value = mock_cl
    mock_uow.currency_limits = mock_currency_limits_repo
    mock_uow.__enter__ = Mock(return_value=mock_uow)
    mock_uow.__exit__ = Mock(return_value=False)

    MODULE = "src.core.tools.media_buy_update"
    DB_MODULE = "src.core.database.database_session"

    with (
        patch("src.core.helpers.context_helpers.ensure_tenant_context") as m_tenant,
        patch(f"{MODULE}.get_principal_object") as m_principal_obj,
        patch(f"{MODULE}._verify_principal") as m_verify,
        patch(f"{MODULE}.get_context_manager") as m_ctx_mgr,
        patch(f"{MODULE}.get_adapter") as m_adapter,
        patch(f"{MODULE}.get_audit_logger") as m_audit,
        patch(f"{MODULE}.MediaBuyUoW") as m_uow,
        patch(f"{DB_MODULE}.get_db_session") as m_db,
    ):
        m_tenant.return_value = {"tenant_id": "tenant_test", "name": "Test"}
        m_principal_obj.return_value = MagicMock(
            principal_id="principal_test",
            name="Test Principal",
            platform_mappings={},
        )

        m_uow.return_value = mock_uow

        mock_step = MagicMock()
        mock_step.step_id = "step_001"
        mock_ctx_mgr_instance = MagicMock()
        mock_ctx_mgr_instance.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
        mock_ctx_mgr_instance.create_workflow_step.return_value = mock_step
        m_ctx_mgr.return_value = mock_ctx_mgr_instance

        mock_adapter_instance = MagicMock()
        mock_adapter_instance.manual_approval_required = False
        mock_adapter_instance.manual_approval_operations = []
        m_adapter.return_value = mock_adapter_instance

        m_audit.return_value = MagicMock()
        m_db.return_value = mock_cm

        yield {
            "tenant": m_tenant,
            "principal_obj": m_principal_obj,
            "verify_principal": m_verify,
            "ctx_mgr": m_ctx_mgr,
            "ctx_mgr_instance": mock_ctx_mgr_instance,
            "adapter": m_adapter,
            "adapter_instance": mock_adapter_instance,
            "audit": m_audit,
            "uow": m_uow,
            "uow_instance": mock_uow,
            "db": m_db,
            "db_session": mock_session,
            "step": mock_step,
        }


@pytest.fixture
def isolated_imports():
    """Provide isolated imports for testing."""
    # Store original modules
    original_modules = sys.modules.copy()

    yield

    # Restore original modules
    sys.modules = original_modules


@pytest.fixture
def mock_time():
    """Mock time for deterministic tests."""
    with patch("time.time") as mock_time:
        mock_time.return_value = 1640995200  # 2022-01-01 00:00:00
        with patch("datetime.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value.isoformat.return_value = "2022-01-01T00:00:00"
            mock_datetime.now.return_value.isoformat.return_value = "2022-01-01T00:00:00"
            yield mock_time


@pytest.fixture
def mock_uuid():
    """Mock UUID generation for deterministic tests."""
    with patch("uuid.uuid4") as mock_uuid:
        mock_uuid.return_value.hex = "1234567890abcdef1234567890abcdef"
        yield mock_uuid


@pytest.fixture
def mock_secrets():
    """Mock secrets generation for deterministic tests."""
    with patch("secrets.token_urlsafe") as mock_token:
        mock_token.return_value = "test_token_123456"
        with patch("secrets.token_hex") as mock_hex:
            mock_hex.return_value = "abcdef123456"
            yield mock_token


@pytest.fixture
def fast_password_hashing():
    """Speed up password hashing for tests."""
    with patch("werkzeug.security.generate_password_hash") as mock_hash:
        mock_hash.side_effect = lambda x: f"hashed_{x}"
        with patch("werkzeug.security.check_password_hash") as mock_check:
            mock_check.side_effect = lambda h, p: h == f"hashed_{p}"
            yield


@pytest.fixture
def make_auth_test_client():
    """Factory fixture: context manager yielding (client, mock_session) with auth DB patched.

    The tenant is a real ``Tenant`` ORM instance built via ``TenantFactory.build()``
    (no DB session required) — so ``tenant.tenant_id``, ``tenant.is_embedded``, etc.
    have real types and template rendering works without MagicMock fallout.

    Usage::

        with make_auth_test_client(auth_setup_mode=True) as (client, mock_session):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true", ...}):
                response = client.post("/test/auth", ...)
    """
    from contextlib import contextmanager

    from src.admin.app import create_app
    from src.core.database.models import Tenant, TenantAuthConfig
    from tests.factories import TenantAuthConfigFactory, TenantFactory

    @contextmanager
    def _factory(auth_setup_mode: bool = True, oidc_enabled: bool = False):
        app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})
        client = app.test_client()
        tenant = TenantFactory.build(
            tenant_id="default",
            name="Test Tenant",
            auth_setup_mode=auth_setup_mode,
            is_embedded=False,
            embed_breadcrumb_root=None,
        )
        auth_config = TenantAuthConfigFactory.build(tenant_id="default", oidc_enabled=oidc_enabled)
        # View-side DB mock — what the auth blueprint sees. The login() view
        # queries Tenant then TenantAuthConfig; tenant_login and /test/auth
        # query Tenant only; some tests issue multiple requests in one block.
        # Dispatch on the query's target model so order and call count don't
        # matter — every Tenant query returns ``tenant``, every TenantAuthConfig
        # query returns ``auth_config``.
        dispatch = _build_scalars_dispatch({Tenant: tenant, TenantAuthConfig: auth_config})
        view_session = MagicMock()
        view_session.scalars.side_effect = dispatch
        # Context-processor DB mock — what inject_context sees when populating
        # ``tenant`` for templates. Separate from view_session so the two paths
        # don't share call accounting. tenant_login renders login.html without
        # passing tenant explicitly, so the processor's value is what base.html's
        # subnav reads.
        context_session = MagicMock()
        context_session.scalars.side_effect = dispatch
        with (
            patch("src.admin.blueprints.auth.get_db_session") as view_db,
            patch("src.core.database.database_session.get_db_session") as context_db,
        ):
            view_db.return_value.__enter__ = MagicMock(return_value=view_session)
            view_db.return_value.__exit__ = MagicMock(return_value=False)
            context_db.return_value.__enter__ = MagicMock(return_value=context_session)
            context_db.return_value.__exit__ = MagicMock(return_value=False)
            yield client, view_session

    return _factory


@pytest.fixture
def make_users_test_client():
    """Factory fixture: context manager yielding (client, mock_session) for users blueprint.

    Sets up ADCP_AUTH_TEST_MODE=true + an admin-role test session so
    @require_tenant_access(role=("admin",)) passes without a DB call. tenant_id used in routes is "default".

    The session role is ``"admin"`` — not super_admin — so a regression that
    drops ``role=("admin",)`` from the decorator would fail the test.

    Tenant and auth_config are real ORM instances built via ``TenantFactory.build()``
    / ``TenantAuthConfigFactory.build()`` (no DB session required) — gives templates
    real ``str``/``bool`` attribute values without MagicMock surprises.

    Usage::

        with make_users_test_client(auth_setup_mode=True) as (client, mock_session):
            response = client.get("/tenant/default/users")
    """
    import os
    from contextlib import contextmanager

    from src.admin.app import create_app
    from src.core.database.models import Tenant, TenantAuthConfig
    from tests.factories import TenantAuthConfigFactory, TenantFactory

    @contextmanager
    def _factory(
        auth_setup_mode: bool = True,
        oidc_enabled: bool = False,
        auth_config_exists: bool = True,
        is_embedded: bool = False,
    ):
        app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})
        client = app.test_client()

        tenant = TenantFactory.build(
            tenant_id="default",
            name="Test Tenant",
            auth_setup_mode=auth_setup_mode,
            is_embedded=is_embedded,
            embed_breadcrumb_root=None,
            authorized_domains=[],
        )
        auth_config = (
            TenantAuthConfigFactory.build(tenant_id="default", oidc_enabled=oidc_enabled)
            if auth_config_exists
            else None
        )

        # Dispatch on the query's target model so call order doesn't matter —
        # list_users queries Tenant then TenantAuthConfig; enable/disable query
        # Tenant only; the User .all() listing returns []. inject_context (the
        # context processor) also queries Tenant; same dispatch covers it.
        dispatch = _build_scalars_dispatch({Tenant: tenant, TenantAuthConfig: auth_config})
        view_session = MagicMock()
        view_session.scalars.side_effect = dispatch
        context_session = MagicMock()
        context_session.scalars.side_effect = dispatch

        with (
            patch("src.admin.blueprints.users.get_db_session") as view_db,
            patch("src.core.database.database_session.get_db_session") as context_db,
        ):
            view_db.return_value.__enter__ = MagicMock(return_value=view_session)
            view_db.return_value.__exit__ = MagicMock(return_value=False)
            context_db.return_value.__enter__ = MagicMock(return_value=context_session)
            context_db.return_value.__exit__ = MagicMock(return_value=False)
            with client.session_transaction() as sess:
                sess["test_user"] = "admin@test.com"
                sess["test_tenant_id"] = "default"
                sess["test_user_role"] = "admin"
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                yield client, view_session

    return _factory
