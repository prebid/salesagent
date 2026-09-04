"""
Global pytest configuration and fixtures for all tests.

This file provides fixtures available to all test modules.
"""

# First import in this file: tests/_worker_profile stamps its module-import time
# as t0, and everything heavy (the app, the harness, the factories) is imported
# after this point, so that stamp is early enough to bound the startup cost it
# measures. Inert unless PYTEST_WORKER_PROFILE names a directory.
import functools
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock_module
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests import _worker_profile
from tests._xdist_report_safety import sanitize_serialized_report

# ---------------------------------------------------------------------------
# xdist wire safety — an unserializable report must not void the session
# ---------------------------------------------------------------------------
# pytest's _report_to_json copies report.__dict__ wholesale onto the execnet
# wire and sanitizes only longrepr/os.PathLike/result. execnet dispatches on
# EXACT type, so anything else a plugin attached raises DumpError, which kills
# the worker and ends the session -- reporting only the tests already collected
# back, with a summary line that says zero failures.
#
# Live instance: pytest-json-report (which tox.ini passes on every suite)
# attaches report._json_report_extra carrying dict(record.__dict__) per captured
# log record, and logging merges any `extra={...}` payload straight into that
# dict. Production logs that way in ~75 places, so a mocked collaborator in a
# PASSING unit test silently truncated 416-575 of 5846 items at 4-14 workers.
# See tests/_xdist_report_safety.py for the full measurement.
#
# Registered here, at the rootdir conftest, so every suite is covered -- bdd
# and bdd_e2e already run at 16 and 8 workers and are exposed to the same hole.


@pytest.hookimpl(hookwrapper=True)
def pytest_report_to_serializable(config, report):
    """Replace anything execnet cannot dump, loudly, instead of dying."""
    outcome = yield
    data = outcome.get_result()
    safe = sanitize_serialized_report(data, nodeid=getattr(report, "nodeid", "<unknown>"))
    if safe is not data:
        outcome.force_result(safe)


# ---------------------------------------------------------------------------
# Entity marker taxonomy — auto-applied to tests by filename / path patterns
# ---------------------------------------------------------------------------
# These markers let developers run any slice of the test suite:
#   uv run pytest -m delivery          # all delivery tests across all suites
#   uv run pytest -m "creative and unit"  # creative unit tests only
#   make test-entity ENTITY=product    # convenience target

_ENTITY_MARKERS: frozenset[str] = frozenset(
    {
        "delivery",
        "creative",
        "product",
        "media_buy",
        "tenant",
        "auth",
        "adapter",
        "inventory",
        "schema",
        "admin",
        "architecture",
        "targeting",
        "transport",
        "workflow",
        "policy",
        "agent",
        "infra",
    }
)

# Maps entity markers to filename substrings. A test file whose name
# contains any of the substrings gets the corresponding entity marker.
# Tests may receive multiple entity markers when they match multiple patterns.
_ENTITY_PATTERNS: dict[str, list[str]] = {
    # --- Core domain entities ---
    "delivery": [
        "delivery",
        "webhook",
        "push_notification",
        "metrics",
    ],
    "creative": [
        "creative",
        "format_id",
        "format_cache",
        "format_display",
        "format_resolver",
        "format_template",
        "format_trailing",
        "formatid",
        "build_creative_data",
        "extract_url_from_assets",
        "normalize_agent_url",
        "list_creative_formats",
    ],
    "product": [
        "product",
        "pricing",
        "property_list",
        "property_discovery",
        "property_verification",
        "measurement_provider",
        "get_recommended_cpm",
        "duplicate_product",
    ],
    "media_buy": [
        "media_buy",
        "budget",
        "dry_run",
        "multi_package",
        "order_approval",
        "execute_approved",
        "format_conversion_approval",
        "impression_tracker",
    ],
    "tenant": [
        "tenant",
        "virtual_host",
        "domain_routing",
        "setup_checklist",
        "self_service_signup",
    ],
    "auth": [
        "auth",
        "identity",
        "token",
        "oauth",
        "resolve_identity",
        "signup_flow",
    ],
    "adapter": [
        "adapter",
        "gam_",
        "broadstreet",
        "mock_adapter",
    ],
    "inventory": [
        "inventory",
        "incremental_sync",
        "sync_job",
    ],
    "schema": [
        "adcp_contract",
        "schema",
        "adcp_",
        "pydantic_",
        "spec_compliance",
        "protocol_envelope",
        "response_shapes",
        "null_field",
        "validation_errors",
        "annotated_type",
        "all_response_str",
        "openapi_surface",
        "manual_vs_generated",
        "json_serialization",
        "version_compat",
        "signals_response",
        "discovery_endpoint",
    ],
    "admin": [
        "admin_ui",
        "dashboard",
        "form_validation",
        "signup",
        "activity_feed",
        "comprehensive_pages",
        "landing_page",
    ],
    "architecture": [
        "architecture",
        "no_toolerror_in_impl",
        "transport_agnostic_impl",
        "impl_resolved_identity",
        "no_model_dump_in_impl",
        "inspect_bdd_steps",
        "ruff_egress_bans",
    ],
    # --- Extended domain entities ---
    "targeting": [
        "targeting",
        "geo_overlap",
        "overlay_validation",
        "city_targeting",
        "device_platform",
        "axe_segment",
        "enhanced_custom_targeting",
        "validate_geo",
    ],
    "transport": [
        "a2a_",
        "mcp_",
        "rest_",
        "boundary_field",
        "shared_header",
        "parse_tool_result",
        "no_contextvar",
        "raw_function_parameter",
        "error_boundary",
        "error_format",
        "mock_server_response",
        "tool_result_format",
        "tool_registration",
    ],
    "workflow": [
        "workflow",
        "approval_error",
        "context_management",
    ],
    "policy": [
        "policy",
        "brand",
        "quiet_failure",
    ],
    "agent": [
        "ai_review",
        "ai_service",
        "naming_agent",
        "naming_parameter",
        "naming_unawaited",
        "review_agent",
        "task_management",
        "signals_agent",
    ],
    "infra": [
        "tox_config",
        "import_collision",
        "blueprint_imports",
        "warning_filters",
        "stale_docs",
        "e2e_fixture_cleanup",
        "database_health",
        "datetime_string",
        "encryption",
        "json_type",
        "pgbouncer",
        "scheduler_env",
        "slack_notification",
        "performance_index",
        "fastapi_app",
        "link_validation",
        "template_url",
        "health_route",
        "notification_url",
        "timestamptz",
        "composite_pk",
        "session_json",
        "pr1071",
        "version",
    ],
}

# Subdirectory → entity marker. Tests under these paths get the marker
# regardless of filename.
_PATH_ENTITY_MAP: dict[str, str] = {
    "/adapters/": "adapter",
    "/admin/": "admin",
}


def pytest_configure(config):
    """Register entity markers and configure test environment.

    Entity markers allow slicing the test suite by domain:
        pytest -m delivery          # all delivery tests
        pytest -m "creative and unit"  # creative unit tests only

    Also prevents fastmcp from overriding pytest's warning filters.
    """
    # --- Entity marker registration ---
    for marker in sorted(_ENTITY_MARKERS):
        config.addinivalue_line("markers", f"{marker}: Entity marker (auto-applied by filename/path)")

    # --- Environment setup ---
    os.environ.setdefault("FASTMCP_DEPRECATION_WARNINGS", "false")

    # Disable OpenTelemetry SDK during tests. Logfire (added for Pydantic AI
    # observability) initializes an OTLP exporter that tries to connect to
    # localhost:4317/4318 on teardown. With no collector running, this produces
    # noisy "Exception while exporting Span" / ConnectionError stack traces
    # after every test run. OTEL_SDK_DISABLED prevents the SDK from
    # initializing entirely, eliminating the noise with zero test impact.
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")


# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import database fixtures for all tests
from tests.conftest_db import *  # noqa: F401,F403

# Note: Environment variables are now set via fixtures to avoid global pollution
# See test_environment fixture below for configuration
# Import fixtures modules
from tests.fixtures import (
    CreativeFactory,
    MediaBuyFactory,
    MockAdapter,
    MockDatabase,
    MockOAuthProvider,
    PrincipalFactory,
    ProductFactory,
    RequestBuilder,
    ResponseBuilder,
    TargetingBuilder,
    TenantFactory,
)

# ============================================================================
# Database Fixtures
# ============================================================================


@pytest.fixture
def mock_db():
    """Provide a mock database connection."""
    return MockDatabase()


@pytest.fixture
def mock_db_with_data(mock_db):
    """Provide a mock database with sample data."""
    # Add sample tenant
    tenant = TenantFactory.create()
    mock_db.set_query_result("SELECT.*FROM tenants", [tenant])

    # Add sample principal
    principal = PrincipalFactory.create(tenant_id=tenant["tenant_id"])
    mock_db.set_query_result("SELECT.*FROM principals", [principal])

    # Add sample products
    products = ProductFactory.create_batch(3, tenant_id=tenant["tenant_id"])
    mock_db.set_query_result("SELECT.*FROM products", products)

    return mock_db


@pytest.fixture
def test_db_path():
    """Provide a temporary test database path."""
    # Create a unique temporary file for each test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        try:
            os.unlink(db_path)
        except Exception:
            pass  # Ignore cleanup errors


@pytest.fixture(autouse=True, scope="function")
def test_environment(monkeypatch, request):
    """Configure test environment variables without global pollution."""
    # Set testing flags
    monkeypatch.setenv("ADCP_TESTING", "true")
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")  # Enable test mode for auth

    # Check if this is a test that needs the database
    is_integration_test = "integration" in str(request.fspath) or "bdd" in str(request.fspath)
    has_requires_db_marker = request.node.get_closest_marker("requires_db") is not None
    database_url = os.environ.get("DATABASE_URL")

    # Check if this is a unittest.TestCase (which manages its own DATABASE_URL)
    # Pytest calls these as "UnitTestCase" in the node hierarchy
    is_unittest_class = (
        hasattr(request, "cls") and request.cls is not None and issubclass(request.cls, unittest.TestCase)
    )

    # IMPORTANT: Unit tests should NEVER use real database connections
    # Remove database-related env vars UNLESS:
    # 1. This is an integration test with DATABASE_URL set (for integration_db fixture), OR
    # 2. This is a unittest.TestCase class (manages its own DATABASE_URL in setUpClass), OR
    # 3. This test has @pytest.mark.requires_db marker (e.g. UI integration tests)
    should_preserve_db = (is_integration_test or has_requires_db_marker) and (database_url or is_unittest_class)

    if not should_preserve_db:
        if "DATABASE_URL" in os.environ:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        if "TEST_DATABASE_URL" in os.environ:
            monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    # Set test API keys and credentials
    monkeypatch.setenv("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", "test_key_for_mocking"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", os.environ.get("GOOGLE_CLIENT_ID", "test_client_id"))
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", os.environ.get("GOOGLE_CLIENT_SECRET", "test_client_secret"))
    monkeypatch.setenv("SUPER_ADMIN_EMAILS", os.environ.get("SUPER_ADMIN_EMAILS", "test@example.com"))

    yield

    # Cleanup: Reset engine to ensure clean state for next test
    # This prevents test isolation issues from module-level state
    try:
        from src.core.database.database_session import reset_engine

        reset_engine()
    except Exception:
        # Ignore errors during cleanup (e.g., if module not yet loaded)
        pass


# NOTE: db_session fixture is now imported from conftest_db.py
# This fixture is deprecated in favor of the conftest_db version
# which properly returns SQLAlchemy Session objects


# ============================================================================
# Factory Fixtures
# ============================================================================


@pytest.fixture
def tenant_factory():
    """Provide tenant factory."""
    return TenantFactory


@pytest.fixture
def principal_factory():
    """Provide principal factory."""
    return PrincipalFactory


@pytest.fixture
def product_factory():
    """Provide product factory."""
    return ProductFactory


@pytest.fixture
def media_buy_factory():
    """Provide media buy factory."""
    return MediaBuyFactory


@pytest.fixture
def creative_factory():
    """Provide creative factory."""
    return CreativeFactory


@pytest.fixture
def sample_tenant():
    """Provide a sample tenant."""
    return TenantFactory.create(tenant_id="test_tenant", name="Test Publisher", subdomain="test")


@pytest.fixture
def sample_principal(sample_tenant):
    """Provide a sample principal."""
    return PrincipalFactory.create(
        tenant_id=sample_tenant["tenant_id"],
        principal_id="test_principal",
        name="Test Advertiser",
        access_token="test_token_123",
    )


@pytest.fixture
def sample_products(sample_tenant):
    """Provide sample products."""
    return ProductFactory.create_batch(3, tenant_id=sample_tenant["tenant_id"])


# ============================================================================
# Mock Service Fixtures
# ============================================================================


@pytest.fixture
def mock_adapter():
    """Provide a mock ad server adapter."""
    return MockAdapter()


@pytest.fixture
def mock_oauth():
    """Provide a mock OAuth provider."""
    return MockOAuthProvider()


@pytest.fixture
def mock_gemini_test_scenarios():
    """Mock Gemini with realistic test scenario responses.

    Returns a fixture that accepts scenario name and returns appropriate JSON response.
    """
    scenarios = {
        "delay_10s": json.dumps({"delay_seconds": 10, "should_accept": True, "should_reject": False}),
        "reject_budget": json.dumps(
            {"should_reject": True, "rejection_reason": "Budget too high for this campaign", "should_accept": False}
        ),
        "hitl_approve": json.dumps({"simulate_hitl": True, "hitl_delay_minutes": 2, "hitl_outcome": "approve"}),
        "creative_approve": json.dumps({"creative_actions": [{"creative_index": 0, "action": "approve"}]}),
        "creative_reject": json.dumps(
            {"creative_actions": [{"creative_index": 0, "action": "reject", "reason": "Missing click URL"}]}
        ),
        "creative_ask_field": json.dumps(
            {"creative_actions": [{"creative_index": 0, "action": "ask_for_field", "field": "click_tracker"}]}
        ),
    }

    mock_model = MagicMock()

    def generate_content(prompt, **kwargs):
        # Parse prompt to determine which scenario to return
        response = MagicMock()
        prompt_str = str(prompt)

        # Extract just the message part (between quotes after "Their message:")
        message = ""
        if 'Their message: "' in prompt_str:
            start = prompt_str.find('Their message: "') + len('Their message: "')
            end = prompt_str.find('"', start)
            message = prompt_str[start:end].lower()
        else:
            message = prompt_str.lower()

        # Priority order matters - check most specific patterns first
        if "wait" in message and "seconds" in message:
            response.text = scenarios["delay_10s"]
        elif "human in the loop" in message or "hitl" in message or "simulate human" in message:
            response.text = scenarios["hitl_approve"]
        elif "ask for" in message or ("request" in message and "field" in message):
            response.text = scenarios["creative_ask_field"]
        elif "reject" in message:
            # Check if it's creative rejection or budget rejection
            if "url" in message or "missing" in message:
                response.text = scenarios["creative_reject"]
            elif "budget" in message:
                response.text = scenarios["reject_budget"]
            else:
                response.text = scenarios["creative_reject"]
        elif "approve" in message:
            response.text = scenarios["creative_approve"]
        else:
            # Default: accept without special instructions
            response.text = json.dumps({"should_accept": True, "should_reject": False})

        return response

    mock_model.generate_content = generate_content

    async def async_generate(prompt, **kwargs):
        return generate_content(prompt, **kwargs)

    mock_model.generate_content_async = async_generate

    yield mock_model


# ============================================================================
# Builder Fixtures
# ============================================================================


@pytest.fixture
def request_builder():
    """Provide request builder."""
    return RequestBuilder()


@pytest.fixture
def response_builder():
    """Provide response builder."""
    return ResponseBuilder()


@pytest.fixture
def targeting_builder():
    """Provide targeting builder."""
    return TargetingBuilder()


# ============================================================================
# Authentication Fixtures
# ============================================================================


@pytest.fixture
def auth_headers(sample_principal):
    """Provide authentication headers."""
    return {"x-adcp-auth": sample_principal["access_token"]}


@pytest.fixture
def admin_session():
    """Provide admin session data."""
    return {
        "user": {"email": "admin@example.com", "name": "Admin User", "role": "super_admin"},
        "authenticated": True,
        "role": "super_admin",
        "email": "admin@example.com",
        "name": "Admin User",
    }


@pytest.fixture
def tenant_admin_session(sample_tenant):
    """Provide tenant admin session data."""
    return {
        "user": {"email": "tenant.admin@example.com", "name": "Tenant Admin", "role": "tenant_admin"},
        "authenticated": True,
        "role": "tenant_admin",
        "tenant_id": sample_tenant["tenant_id"],
        "email": "tenant.admin@example.com",
        "name": "Tenant Admin",
    }


# ============================================================================
# Flask App Fixtures
# ============================================================================


@pytest.fixture
def flask_app():
    """Provide Flask test app."""
    # Mock database before importing admin app
    with patch("src.core.database.database_session.get_db_session") as mock_get_session:
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = None
        mock_session.query.return_value.filter_by.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_session.query.return_value.filter.return_value.all.return_value = []
        mock_session.query.return_value.all.return_value = []
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=None)
        mock_get_session.return_value = mock_session

        # Mock inventory service database session
        with patch("src.services.gam_inventory_service.db_session") as mock_inv_session:
            mock_inv_session.query.return_value.filter.return_value.all.return_value = []
            mock_inv_session.close = MagicMock()
            mock_inv_session.remove = MagicMock()

            from src.admin.app import create_app

            app = create_app()
            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret-key"
            return app


@pytest.fixture
def flask_client(flask_app):
    """Provide Flask test client."""
    return flask_app.test_client()


@pytest.fixture
def authenticated_client(flask_client, admin_session):
    """Provide authenticated Flask client."""
    with flask_client.session_transaction() as sess:
        sess.update(admin_session)
    return flask_client


# ============================================================================
# MCP Context Fixtures
# ============================================================================


@pytest.fixture
def mcp_context(auth_headers):
    """Provide MCP context object."""

    class MockContext:
        def __init__(self, headers):
            self.headers = headers

        def get_header(self, name, default=None):
            return self.headers.get(name, default)

    return MockContext(auth_headers)


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def sample_media_buy_request():
    """Provide sample media buy request."""
    return {
        "product_ids": ["prod_1", "prod_2"],
        "total_budget": 10000.0,
        "flight_start_date": "2025-02-01",
        "flight_end_date": "2025-02-28",
        "targeting_overlay": {"geo_countries": ["US"], "device_type_any_of": ["desktop", "mobile"]},
    }


@pytest.fixture
def sample_creative_content():
    """Provide sample creative content."""
    return {
        "headline": "Test Advertisement",
        "body": "This is a test ad for automated testing.",
        "cta_text": "Learn More",
        "image_url": "https://example.com/test-ad.jpg",
        "click_url": "https://example.com/landing",
        "advertiser": "Test Company",
    }


@pytest.fixture
def fixture_data_path():
    """Provide path to fixture data directory."""
    return Path(__file__).parent / "fixtures" / "data"


@pytest.fixture
def load_fixture_json():
    """Provide function to load fixture JSON files."""

    def _load(filename):
        fixture_path = Path(__file__).parent / "fixtures" / "data" / filename
        with open(fixture_path) as f:
            return json.load(f)

    return _load


# ============================================================================
# Cleanup Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def cleanup_env():
    """Clean up environment after each test."""
    # Store original env
    original_env = os.environ.copy()

    yield

    # Restore original env (but keep testing flags)
    test_vars = [
        "ADCP_TESTING",
        "DATABASE_URL",
        "GEMINI_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "SUPER_ADMIN_EMAILS",
    ]

    for key in list(os.environ.keys()):
        if key not in original_env and key not in test_vars:
            del os.environ[key]


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton instances between tests."""
    # Reset any singleton instances that might carry state
    yield

    # Add any singleton reset logic here


# ============================================================================
# Performance Fixtures
# ============================================================================


@pytest.fixture
def benchmark(request):
    """Simple benchmark fixture for performance testing."""
    import time

    start_time = time.time()

    yield

    duration = time.time() - start_time
    print(f"\n{request.node.name} took {duration:.3f}s")

    # Mark as slow if > 5 seconds
    if duration > 5:
        request.node.add_marker(pytest.mark.slow)


# ============================================================================
# Pytest Hooks
# ============================================================================


# ---------------------------------------------------------------------------
# Per-worker timing profile (opt-in) — see tests/_worker_profile.py
# ---------------------------------------------------------------------------
# Declared here rather than as a `-p` plugin because addopts cannot load
# `tests._worker_profile`: the rootdir is not on sys.path when plugins are
# registered ("No module named 'tests'"). A rootdir conftest always loads, needs
# no PYTHONPATH, and forwards to the box through one environment variable.


def pytest_collection(session):
    _worker_profile.on_collection_start()


def pytest_collection_finish(session):
    _worker_profile.on_collection_finish(len(session.items))


def pytest_runtestloop(session):
    _worker_profile.on_loop_start()


def pytest_runtest_logreport(report):
    _worker_profile.record_test_duration(report)


def pytest_sessionfinish(session, exitstatus):
    _worker_profile.on_session_finish()


# ---------------------------------------------------------------------------
# Leaked-patch guard — a test must not leave a patch running
# ---------------------------------------------------------------------------
# The #2006 root cause was a leaked patch("os.getpid"): a sabotaged patcher's
# stop() never ran, so every later LogRecord in the worker carried a MagicMock
# in .process, pytest-json-report copied it onto the report, and execnet could
# not serialize it -- killing the worker and truncating the session behind a
# summary reading "0 failed". The expensive part was the indirection: the
# symptom surfaced in tests/harness/test_harness_product.py, which is clean,
# while the defect was in tests/harness/test_harness_base.py.
#
# No AST guard can see a leaked patch, so this grades the OUTCOME. It reads
# unittest.mock's own registry rather than enumerating suspect names:
# `patch(...).start()` appends to `_patch._active_patches` and `.stop()` removes
# it, while `with patch(...)` never registers at all -- correct, since a context
# manager cannot leak. So the registry is exactly "patches someone started and
# did not stop", with no list to keep in sync. Verified against the original
# sabotage: it reports [('os', 'getpid')], target and attribute, by name.
#
# Report, never remediate. `mock.patch.stopall()` here tears down patches
# belonging to still-live enclosing scopes -- measured at 183 errors.
#
# A hookwrapper on pytest_runtest_teardown, not an autouse fixture: fixture
# finalization runs INSIDE the default teardown impl, so checking after the
# yield is guaranteed to be after every finalizer (the BDD ctx patchers at
# tests/bdd/conftest.py, the harness's env.__exit__, and so on). An autouse
# root-conftest fixture happens to work too, by being requested first and torn
# down last, but that depends on request ordering and this does not.


# Plugins legitimately hold session-long patches: pytest-mock installs 14 of
# them over NonCallableMock.assert_* / AsyncMock.assert_* at registration time
# (pytest.ini loads it via `-p pytest_mock`) so that assertion failures read
# better. Those are not leaks, and allowlisting them by name would rot. Snapshot
# whatever is already active before the first test instead, and report only what
# a test ADDS.
_known_active_patches: set[int] = set()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Baseline the patches plugins hold, before any test can add one."""
    _known_active_patches.update(id(a) for a in mock_module._patch._active_patches)


def _describe_active_patch(active: Any) -> str:
    target = getattr(active, "target", None)
    name = getattr(target, "__name__", None) or repr(target)
    return f"{name}.{getattr(active, 'attribute', '?')}"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None):
    """Fail the test that leaked a patch, at the moment it leaked it."""
    yield  # let every finalizer run first

    leaked = []
    for active in list(mock_module._patch._active_patches):
        if id(active) in _known_active_patches:
            continue
        leaked.append(_describe_active_patch(active))
        _known_active_patches.add(id(active))  # blame the culprit, not its victims

    # Secondary check for the routes `_active_patches` cannot see: a raw setattr
    # or `sys.modules[x] = MagicMock()` (live in test_incremental_sync_stale_marking,
    # test_setup_dev and test_inspect_bdd_steps). These are callables
    # logging.LogRecord reads at construction, so replacing one poisons every
    # later record and therefore every later report.
    #
    # NOT the complete set: LogRecord also reads `asyncio.current_task`,
    # `logging.getLevelName` and `os.path.basename` (patching those puts a
    # MagicMock into `taskName`, `levelname` and `filename` respectively). There
    # are 0 patch sites for those today, and `sanitize_serialized_report` catches
    # the value at the wire regardless — this list only decides whether the
    # RIGHT TEST gets blamed, so a miss costs attribution, not the session.
    for name, original in _PRISTINE_CALLABLES.items():
        if _resolve_dotted(name) is not original:
            leaked.append(f"{name} (replaced without patch())")
            _PRISTINE_CALLABLES[name] = _resolve_dotted(name)  # blame the culprit, not its victims

    if leaked:
        raise AssertionError(
            f"{item.nodeid} finished with {len(leaked)} patch(es) still active: "
            f"{', '.join(sorted(leaked))}. Every later test in this worker now sees them. "
            f"Stop the patch -- use `with patch(...)`, a fixture that stops in teardown, "
            f"or try/finally. Do not rely on a `.stop()` as the last statement of the test: "
            f"any exception before that line skips it."
        )


_PRISTINE_CALLABLES: dict[str, Any] = {
    "os.getpid": os.getpid,
    "time.time": time.time,
    "threading.get_ident": threading.get_ident,
    "threading.current_thread": threading.current_thread,
    "multiprocessing.current_process": multiprocessing.current_process,
}


def _resolve_dotted(dotted: str) -> Any:
    module_name, _, attr = dotted.rpartition(".")
    return getattr(sys.modules[module_name], attr)


# ---------------------------------------------------------------------------
# Nested-pytest modules — one at a time under xdist
# ---------------------------------------------------------------------------
# MEMBERSHIP RULE: a module belongs here if and only if it spawns a `pytest`
# SUBPROCESS. Nothing pins that, so check before adding — an earlier revision of
# this set carried four modules that spawn nothing (0.47s + 0.73s + 0.19s +
# 0.13s of work pinned to one worker for no reason), and one of them stopped
# spawning in the same change that listed it. Verify with:
#
#     grep -cE 'subprocess\.(run|Popen|check_output|check_call)' <module>
#
# Why the group: a nested subprocess re-collects the whole unit suite, and its
# timeout was calibrated against a SERIAL run. With N xdist workers you get up
# to N concurrent full-suite collections competing with the N workers that
# spawned them. Measured on a 14-core Mac at -n 12 and -n 14, the nested
# collection dies with
#
#     subprocess.TimeoutExpired: [... 'pytest','tests/unit/','--collect-only' ...]
#     timed out after 60 seconds
#
# while passing at -n 8 and serially. Raising the timeout would hide the
# contention rather than remove it; a shared cached collection across the three
# would remove the duplicated work outright and is the right follow-up. Until
# then, one xdist_group means at most ONE nested collection runs at a time,
# whatever the worker count -- which is the property that was actually missing.
#
# Requires `--dist loadgroup` (tox.ini's unit env); under any other dist mode
# the marker is inert and these simply spread out again.
_NESTED_PYTEST_MODULES: frozenset[str] = frozenset(
    {
        "test_architecture_ci_bdd_shard_manifest",
        "test_e2e_rest_ledger_fitness",
        "test_xdist_report_serialization",
    }
)


@functools.lru_cache(maxsize=4096)
def entity_markers_for_path(fspath: str) -> frozenset[str]:
    """Entity markers this path earns from its filename and directory.

    The single definition of the auto-marking rule. `pytest_collection_modifyitems`
    applies it at collection; `test_architecture_test_marker_coverage` asserts it
    directly, in-process. Two copies of this logic would drift, and anything
    grading the rule would then grade something production does not follow.

    Keyed on the FULL path, not the stem: ten stems are duplicated across
    `tests/`, and `test_authorized_properties.py` earns `admin` under
    `tests/admin/` but not under `tests/unit/`. A stem key let whichever file
    collection reached first decide for both.
    """
    filename = Path(fspath).stem  # e.g. "test_delivery_webhook_behavioral"
    markers: set[str] = set()

    # 1. Match filename against entity patterns (substring match)
    for entity, patterns in _ENTITY_PATTERNS.items():
        for pattern in patterns:
            if pattern in filename:
                markers.add(entity)
                break  # one match per entity is enough

    # 2. Match path against directory-based entity map
    for path_fragment, entity in _PATH_ENTITY_MAP.items():
        if path_fragment in fspath:
            markers.add(entity)

    return frozenset(markers)


def pytest_collection_modifyitems(config, items):
    """Auto-apply entity markers from filename/path patterns."""
    for item in items:
        fspath = str(item.fspath)

        for marker_name in entity_markers_for_path(fspath):
            item.add_marker(getattr(pytest.mark, marker_name))

        if Path(fspath).stem in _NESTED_PYTEST_MODULES:
            item.add_marker(pytest.mark.xdist_group("nested_pytest"))
