"""
End-to-end test specific fixtures.

These fixtures are for complete system tests that exercise the full AdCP protocol.
Implements testing hooks from https://github.com/adcontextprotocol/adcp/pull/34
"""

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
import requests

# Bind real os.getpid at import so a later patch("os.getpid") in-process cannot
# leak a non-int into the pid-is-None default path.
_GETPID = os.getpid

# Import contract validation - this automatically validates tool calls at test collection time
from tests.e2e.conftest_contract_validation import pytest_collection_modifyitems  # noqa: F401
from tests.e2e.stack_readiness import (
    DEFAULT_E2E_COMPOSE_FILES,
    _compose_argv,
    _compose_available,
    wait_for_e2e_stack,
)


def e2e_host() -> str:
    """Host for e2e server URLs.

    'localhost' on the host path (Docker publishes host ports); a compose service
    name (e.g. 'proxy') when the runner is in-network and reaches the server over
    the compose network with no published ports (ADCP_TEST_HOST).
    """
    return os.getenv("ADCP_TEST_HOST", "localhost")


def e2e_db_url(fallback_port: int) -> str:
    """Server-side Postgres URL for direct-DB e2e helpers — the DB counterpart of
    :func:`e2e_host`.

    Single source for the host/port/dbname the e2e stack's database lives at, so
    every direct-DB helper resolves it the same way. Preference order::

        E2E_DATABASE_URL  — in-network DB by compose service name (postgres:5432/adcp)
        DATABASE_URL      — host path: the e2e stack's localhost:<published>/adcp
        localhost:<fallback_port>/adcp — last-resort fallback

    Replaces the ad-hoc ``ADCP_TEST_DB_HOST``/``ADCP_TEST_DB_PORT`` split, which
    ignored ``E2E_DATABASE_URL`` and so pointed the post-reset diagnostic at
    localhost in-network instead of the compose service.
    """
    return (
        os.getenv("E2E_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or f"postgresql://adcp_user:secure_password_change_me@localhost:{fallback_port}/adcp"
    )


def port_scan_start(start_port: int, end_port: int, pid: int | None = None) -> int:
    """Deterministic per-process scan origin inside [start_port, end_port).

    Root cause of the mass e2e 502 failures (salesagent-18h.12): every port
    allocator on the host scanned the same range from the same low bound and
    returned the first free port. Two sibling worktree agents racing between
    probe-close and ``docker run -p`` deterministically picked the *identical*
    lowest free port and collided -- one stack half-started and nginx served
    502 on the contended port.

    Scattering the scan origin by PID makes independent processes diverge:
    they no longer all converge on the low bound, so the collision window
    effectively disappears even though the probe itself is still TOCTOU.

    The shell allocators (``scripts/test-stack.sh``, ``agent-db.sh``) mirror
    this exact algorithm in their Python heredocs -- keep them in sync.
    """
    if pid is None:
        pid = _GETPID()
    span = end_port - start_port
    if span <= 1:
        return start_port
    # Test suites may temporarily monkeypatch process metadata; production callers
    # must pass a real PID (os.getpid() is always int).
    if not isinstance(pid, int):
        msg = f"port_scan_start pid must be int, got {type(pid).__name__}"
        raise TypeError(msg)
    normalized_pid = pid
    # Spread the origin across the whole span. PID modulo span gives a
    # stable, well-distributed offset; distinct PIDs land on distinct
    # origins so parallel agents start scanning different sub-ranges.
    return start_port + (normalized_pid % span)


def find_free_port(start_port: int = 10000, end_port: int = 60000) -> int:
    """Find an available port in [start_port, end_port).

    Hardened for parallel worktree execution (salesagent-18h.12):

    * Scan starts at a per-process scattered origin (see ``port_scan_start``)
      and wraps around, so concurrent agents do not converge on the same
      lowest free port. The full range is still searched.
    * The probe binds the *all-interfaces* address ("") -- the same way
      Docker publishes ``-p host:container`` -- so a port already taken by
      another stack on 0.0.0.0 is correctly detected. A 127.0.0.1-only
      probe used to miss those and hand out an already-bound port.
    * ``SO_REUSEADDR`` is left at 0 on the probe so the check is
      conservative (it never reports a contended port as free).
    """
    span = end_port - start_port
    if span <= 0:
        raise RuntimeError(f"No free ports found in range {start_port}-{end_port}")
    origin = port_scan_start(start_port, end_port)
    for i in range(span):
        port = start_port + ((origin - start_port + i) % span)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free ports found in range {start_port}-{end_port}")


def pytest_addoption(parser):
    """Add custom command line options for E2E tests."""
    parser.addoption(
        "--skip-docker",
        action="store_true",
        default=False,
        help="Skip Docker setup and assume services are already running",
    )
    parser.addoption(
        "--offline-schemas",
        action="store_true",
        default=False,
        help="Use cached AdCP schemas only (no network requests for schema validation)",
    )


@pytest.fixture(scope="session")
def docker_services_e2e(request):
    """
    Provide service port information for E2E tests.

    If ADCP_TESTING=true (set by run_all_tests.sh), uses existing services.
    Otherwise, starts its own Docker Compose stack for standalone testing.
    """
    # Check if running from run_all_tests.sh (services already started)
    use_existing_services = os.getenv("ADCP_TESTING") == "true" or request.config.getoption("--skip-docker")

    if use_existing_services:
        print("Using existing Docker services (ADCP_TESTING=true or --skip-docker)")
        # Get ports from environment (set by run_all_tests.sh)
        # All services (MCP, A2A, Admin) run on a single port in the unified FastAPI process
        mcp_port = int(os.getenv("ADCP_SALES_PORT", "8092"))
        a2a_port = mcp_port  # A2A is on same port as MCP (unified FastAPI process)
        admin_port = mcp_port  # Admin is on same port as MCP (unified FastAPI process)
        postgres_port = int(os.getenv("POSTGRES_PORT", "5435"))

        # Export resolved ports so later wrappers (e.g. wait_for_server_readiness)
        # see the same values the fixture used — same contract as standalone.
        os.environ["ADCP_SALES_PORT"] = str(mcp_port)
        os.environ["POSTGRES_PORT"] = str(postgres_port)

        print(f"✓ Using ports: Server={mcp_port} (MCP+A2A+Admin), Postgres={postgres_port}")

        # Shared ordered readiness (postgres → creative-agent → adcp /health).
        # CI already compose --wait'd; 60s is a safety net, not a cold start budget.
        wait_for_e2e_stack(
            ports={"mcp": mcp_port, "postgres": postgres_port},
            compose_files=DEFAULT_E2E_COMPOSE_FILES,
            host=e2e_host(),
            timeout_s=60,
        )

    else:
        # Check if Docker is available
        try:
            subprocess.run(["docker", "--version"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip("Docker not available")

        # Always clean up existing services and volumes to ensure fresh state
        print("Cleaning up any existing Docker services and volumes...")
        subprocess.run(
            [*_compose_argv(("docker-compose.e2e.yml",)), "down", "-v"],
            capture_output=True,
            check=False,
        )

        # Explicitly remove volumes in case docker-compose down -v didn't work
        print("Explicitly removing Docker volumes...")
        subprocess.run(["docker", "volume", "prune", "-f"], capture_output=True, check=False)

        # Ensure .env file exists (docker-compose env_file requires it)
        # In CI, environment variables are set directly, but .env file must exist
        env_file = Path(".env")
        if not env_file.exists():
            print("Creating empty .env file for docker-compose...")
            env_file.touch()

        # Use environment variable ports if set, otherwise allocate dynamic ports
        # All services run on a single port (unified FastAPI process)
        mcp_port = int(os.getenv("ADCP_SALES_PORT")) if os.getenv("ADCP_SALES_PORT") else find_free_port(20000, 25000)
        a2a_port = mcp_port  # A2A is on same port as MCP (unified FastAPI process)
        admin_port = mcp_port  # Admin is on same port as MCP (unified FastAPI process)
        postgres_port = int(os.getenv("POSTGRES_PORT")) if os.getenv("POSTGRES_PORT") else find_free_port(25000, 30000)

        print(f"Using ports: Server={mcp_port} (MCP+A2A+Admin), Postgres={postgres_port}")

        # Set port env vars in os.environ so that:
        # 1. docker-compose subprocess inherits them via os.environ.copy()
        # 2. Tests that read ports via os.getenv() (e.g., test_a2a_endpoints_working.py,
        #    test_landing_pages.py) pick up the correct dynamic ports
        os.environ["ADCP_SALES_PORT"] = str(mcp_port)
        os.environ["POSTGRES_PORT"] = str(postgres_port)

        env = os.environ.copy()
        # Set 5 seconds interval for delivery webhooks in E2E tests
        env["DELIVERY_WEBHOOK_INTERVAL"] = "5"
        # Ensure ADCP_TESTING is passed to Docker containers (for test mode validation)
        if "ADCP_TESTING" in os.environ:
            env["ADCP_TESTING"] = os.environ["ADCP_TESTING"]
        else:
            env["ADCP_TESTING"] = "true"  # Default to testing mode in E2E tests
        # Ensure SUPER_ADMIN_EMAILS is set (required by run_all_services.py)
        if not env.get("SUPER_ADMIN_EMAILS"):
            env["SUPER_ADMIN_EMAILS"] = "e2e-test@example.com"

        print("Building and starting Docker services with dynamic ports...")
        print("This may take 2-3 minutes for initial build...")

        # Build first with output visible, then start detached. This standalone
        # branch runs pytest ON THE HOST and reaches the stack over localhost, so
        # it overlays the ports file (the base compose publishes NO host ports —
        # that is reserved for the in-network runner). Same files for build + up.
        #
        # The compose stack now ALWAYS includes the pinned creative-agent (the
        # server resolves format specs against it and the live public host is
        # blackholed — salesagent-9qe2), and its image is built by the
        # single-source pin script, not by compose. Build it first so `up`
        # doesn't fail on a missing `adcp-creative-agent` image.
        print("Step 0/2: Building pinned creative-agent image (single-sourced)...")
        agent_build = subprocess.run(
            ["scripts/creative-agent-stack.sh", "build"],
            env=env,
            capture_output=False,
        )
        if agent_build.returncode != 0:
            print(f"❌ creative-agent image build failed with exit code {agent_build.returncode}")
            raise subprocess.CalledProcessError(agent_build.returncode, "creative-agent-stack.sh build")

        print("Step 1/2: Building Docker images...")
        # Same argv preference as readiness (`docker compose` plugin first).
        compose_base = _compose_argv(DEFAULT_E2E_COMPOSE_FILES)
        build_result = subprocess.run(
            [*compose_base, "build", "--progress=plain"],
            env=env,
            capture_output=False,  # Show build output
        )
        if build_result.returncode != 0:
            print(f"❌ Docker build failed with exit code {build_result.returncode}")
            raise subprocess.CalledProcessError(build_result.returncode, [*compose_base, "build"])

        print("Step 2/2: Starting services...")
        subprocess.run([*compose_base, "up", "-d"], check=True, env=env)

        # Same ordered readiness helper as verify-only (migrations + creative-agent start_period).
        wait_for_e2e_stack(
            ports={"mcp": mcp_port, "postgres": postgres_port},
            compose_files=DEFAULT_E2E_COMPOSE_FILES,
            host=e2e_host(),
            timeout_s=120,
        )

    # Initialize CI test data now that services are healthy
    print("📦 Initializing CI test data (products, principals, etc.)...")

    # Setup environment for init script - reuse existing env if available, else create minimal
    init_env = os.environ.copy()
    init_env["ADCP_SALES_PORT"] = str(mcp_port)
    init_env["POSTGRES_PORT"] = str(postgres_port)

    # Seed CI test data. On the host we shell into the server container via
    # compose exec (the host process can't reach the container DB directly).
    # In-network there is no compose CLI, but the runner already has
    # DATABASE_URL=postgres:5432 and the source, so it runs the seed script
    # itself — the script only needs a DB connection, not Docker.
    if _compose_available():
        init_cmd = [
            *_compose_argv(("docker-compose.e2e.yml",)),
            "exec",
            "-T",
            "adcp-server",
            "python",
            "scripts/setup/init_database_ci.py",
        ]
    else:
        init_cmd = [sys.executable, "scripts/setup/init_database_ci.py"]

    init_result = subprocess.run(
        init_cmd,
        env=init_env,
        capture_output=True,
        text=True,
    )
    if init_result.returncode != 0:
        print("❌ CI data initialization failed:")
        print(f"STDOUT: {init_result.stdout}")
        print(f"STDERR: {init_result.stderr}")
        pytest.fail("Failed to initialize CI test data")

    # Always print output to help with debugging
    if init_result.stdout:
        print("CI initialization output:")
        print(init_result.stdout)
    if init_result.stderr:
        print("CI initialization stderr:")
        print(init_result.stderr)
    print("✓ CI test data initialized successfully")

    # CRITICAL: Reset database connection pool to ensure MCP server sees fresh data
    # The MCP server started with an empty database, created connection pool with stale transactions.
    # After init_database_ci.py populates data, we need to flush those connections.
    print("🔄 Resetting MCP server database connection pool...")
    try:
        reset_response = requests.post(f"http://{e2e_host()}:{mcp_port}/_internal/reset-db-pool", timeout=5)
        if reset_response.status_code == 200:
            print("✓ Database connection pool reset successfully")
            print(f"  Response: {reset_response.json()}")
        else:
            print(f"⚠️  Warning: DB pool reset returned {reset_response.status_code}")
            print(f"  Response: {reset_response.text}")
    except Exception as e:
        print(f"⚠️  Warning: Failed to reset DB pool (non-fatal): {e}")
        print("  This may cause E2E tests to fail if database was empty at server startup")

    # Check MCP server's view of database via debug endpoint
    print("🔍 Checking MCP server's database view...")
    try:
        db_state_response = requests.get(f"http://{e2e_host()}:{mcp_port}/debug/db-state", timeout=5)
        if db_state_response.status_code == 200:
            db_state = db_state_response.json()
            print(f"   MCP server sees: {db_state['total_products']} total products")
            if db_state.get("principal"):
                print(f"   Principal: {db_state['principal']}")
            if db_state.get("tenant"):
                print(f"   Tenant: {db_state['tenant']}")
            print(f"   Tenant products: {db_state['tenant_products_count']} ({db_state.get('tenant_product_ids', [])})")
        else:
            print(f"   ⚠️  DB state endpoint returned {db_state_response.status_code}")
    except Exception as e:
        print(f"   ⚠️  Failed to check MCP server DB state: {e}")

    # VERIFICATION: Query database directly to confirm data is visible post-reset
    print("🔍 Verifying data visibility after connection pool reset...")
    try:
        import psycopg2

        # Resolve the same server-side DB as live_server (honors E2E_DATABASE_URL
        # in-network; was hard-split on ADCP_TEST_DB_HOST/PORT which ignored it and
        # hit localhost behind the compose network).
        _db = urlparse(e2e_db_url(postgres_port))
        conn = psycopg2.connect(
            host=_db.hostname or "localhost",
            port=_db.port or 5432,
            database=(_db.path.lstrip("/") or "adcp"),
            user=_db.username or "adcp_user",
            password=_db.password or "secure_password_change_me",
        )
        cursor = conn.cursor()

        # Count products
        cursor.execute("SELECT COUNT(*) FROM products")
        product_count = cursor.fetchone()[0]
        print(f"   Products in database: {product_count}")

        # Count principals
        cursor.execute("SELECT COUNT(*) FROM principals WHERE access_token = 'ci-test-token'")
        principal_count = cursor.fetchone()[0]
        print(f"   Principals with ci-test-token: {principal_count}")

        # Get principal's tenant_id
        cursor.execute("SELECT tenant_id FROM principals WHERE access_token = 'ci-test-token'")
        result = cursor.fetchone()
        if result:
            principal_tenant = result[0]
            print(f"   Principal's tenant_id: {principal_tenant}")

            # Count products for that tenant
            cursor.execute("SELECT COUNT(*) FROM products WHERE tenant_id = %s", (principal_tenant,))
            tenant_product_count = cursor.fetchone()[0]
            print(f"   Products for principal's tenant: {tenant_product_count}")

        cursor.close()
        conn.close()

        if product_count == 0:
            print("   ⚠️  WARNING: No products found in database after init!")
        elif tenant_product_count == 0:
            print("   ⚠️  WARNING: Products exist but not for principal's tenant!")
        else:
            print("   ✅ Database verification passed")

    except Exception as e:
        print(f"   ⚠️  Warning: Database verification failed: {e}")

    # Yield port information for use by other fixtures
    yield {"mcp_port": mcp_port, "a2a_port": a2a_port, "admin_port": admin_port, "postgres_port": postgres_port}

    # Cleanup Docker resources (unless --skip-docker was used, meaning services are external)
    if not use_existing_services:
        print("\n🧹 Cleaning up Docker resources...")
        try:
            # Stop and remove containers + volumes
            subprocess.run(
                [*_compose_argv(("docker-compose.e2e.yml",)), "down", "-v"],
                capture_output=True,
                check=False,
                timeout=30,
            )
            print("✓ Docker containers and volumes cleaned up")

            # Prune dangling volumes (created by tests but not tracked by docker-compose)
            result = subprocess.run(["docker", "volume", "prune", "-f"], capture_output=True, text=True, timeout=10)
            if result.stdout:
                print(f"✓ Pruned volumes: {result.stdout.strip()}")
        except subprocess.TimeoutExpired:
            print("⚠️  Warning: Docker cleanup timed out (non-fatal)")
        except Exception as e:
            print(f"⚠️  Warning: Docker cleanup failed (non-fatal): {e}")


@pytest.fixture
def live_server(docker_services_e2e):
    """Provide URLs for live services with dynamically allocated ports."""
    # Get dynamically allocated ports from docker_services_e2e fixture
    ports = docker_services_e2e

    host = e2e_host()
    # Resolve ONE effective DB URL (via the shared e2e_db_url helper) and PARSE
    # postgres_params from it, so the URL string (live_server["postgres"]) and the
    # param dict can never diverge on host/port/dbname — the divergence that made
    # direct-DB e2e helpers hit localhost:5435 in-network.
    pg_url = e2e_db_url(ports["postgres_port"])
    parsed = urlparse(pg_url)
    return {
        "mcp": f"http://{host}:{ports['mcp_port']}",
        "a2a": f"http://{host}:{ports['a2a_port']}",
        "admin": f"http://{host}:{ports['admin_port']}",
        "postgres": pg_url,
        "postgres_params": {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "user": parsed.username or "adcp_user",
            "password": parsed.password or "secure_password_change_me",
            "dbname": (parsed.path.lstrip("/") or "adcp"),
        },
    }


@pytest.fixture
def test_auth_token(live_server):
    """Create or get a test principal with auth token.

    This token must match the one created by src/core/database/database.py::init_db().
    """
    # Return the CI test token that is created by init_db() in database.py
    # This ensures consistency between database initialization and E2E tests
    return "ci-test-token"


@pytest.fixture
def auto_approval_adapter(live_server):
    """Pin the ci-test mock adapter to auto-approval before the test runs.

    The e2e suite shares ONE live database and pytest-randomly reorders tests
    per run, so a prior test that enables manual approval (the a2a submitted-
    webhook tests) leaks that state into any later test asserting the
    synchronous success shape — create/update then returns Submitted with no
    media_buy_id, flakily (salesagent-d1n0). Every test that requires the mock
    adapter's auto-approval path must request this fixture instead of trusting
    whatever state the previous test left behind.
    """
    from tests.e2e.utils import set_live_adapter_behavior

    set_live_adapter_behavior(live_server, manual_approval_required=False)


@pytest.fixture
async def e2e_client(live_server, test_auth_token):
    """Provide async client for E2E testing with testing hooks.

    Dry-run + per-test session id; tenant selected via x-adcp-tenant (the Host
    header is set by the HTTP client from the URL). Tests needing other header
    shapes call tests.e2e.utils.make_mcp_client directly (GH #1423).
    """
    from tests.e2e.utils import make_mcp_client

    client = make_mcp_client(
        live_server,
        token=test_auth_token,
        dry_run=True,
        session_id=str(uuid.uuid4()),
    )
    async with client:
        yield client


@pytest.fixture
async def clean_test_data(live_server, request):
    """Clean up test data after tests complete."""
    yield

    # Cleanup happens after test completes
    if not request.config.getoption("--keep-data", False):
        # Could add database cleanup here
        pass


@pytest.fixture
async def a2a_client(live_server, test_auth_token):
    """Provide A2A client for testing."""
    async with httpx.AsyncClient() as client:
        client.base_url = live_server["a2a"]
        client.headers.update(
            {
                "Authorization": f"Bearer {test_auth_token}",
                "X-Test-Session-ID": str(uuid.uuid4()),
                "X-Dry-Run": "true",
            }
        )
        yield client


@pytest.fixture
def performance_monitor():
    """Monitor performance during E2E tests."""
    try:
        import psutil
    except ImportError:
        # Skip if psutil not available
        class DummyMonitor:
            def checkpoint(self, name):
                pass

            def report(self):
                pass

        yield DummyMonitor()
        return

    class PerformanceMonitor:
        def __init__(self):
            self.start_time = time.time()
            self.start_cpu = psutil.cpu_percent()
            self.start_memory = psutil.virtual_memory().percent
            self.metrics = []

        def checkpoint(self, name):
            self.metrics.append(
                {
                    "name": name,
                    "time": time.time() - self.start_time,
                    "cpu": psutil.cpu_percent(),
                    "memory": psutil.virtual_memory().percent,
                }
            )

        def report(self):
            duration = time.time() - self.start_time
            print(f"\n⏱ Performance: {duration:.2f}s total")
            if self.metrics:
                for m in self.metrics:
                    print(f"  • {m['name']}: {m['time']:.2f}s")

    monitor = PerformanceMonitor()
    yield monitor
    monitor.report()


@pytest.fixture
async def adcp_validator(request):
    """Provide AdCP schema validator with offline mode support.

    Use --offline-schemas flag to use cached schemas only (no network requests).
    """
    from tests.e2e.adcp_schema_validator import AdCPSchemaValidator

    offline = request.config.getoption("--offline-schemas")
    async with AdCPSchemaValidator(offline_mode=offline) as validator:
        yield validator


# ============================================================================
# GAM E2E Test Fixtures (real GAM API)
# ============================================================================

GAM_TEST_NETWORK_CODE = "23341594478"
GAM_TEST_ADVERTISER_ID = "6007567433"
GAM_TEST_AD_UNIT_IDS = ["23340594484", "23340594268"]


def _get_gam_service_account_json():
    """Get GAM service account JSON from environment variables.

    Checks (in order):
    1. GAM_SERVICE_ACCOUNT_JSON env var (raw JSON string)
    2. GAM_SERVICE_ACCOUNT_KEY_FILE env var (path to JSON file)
    """
    import json

    # 1. Raw JSON from env var
    sa_json = os.environ.get("GAM_SERVICE_ACCOUNT_JSON")
    if sa_json:
        json.loads(sa_json)  # Validate it's valid JSON
        return sa_json

    # 2. File path from env var
    key_file = os.environ.get("GAM_SERVICE_ACCOUNT_KEY_FILE")
    if key_file and os.path.exists(key_file):
        with open(key_file) as f:
            return f.read()

    return None


@pytest.fixture(scope="session")
def gam_service_account_json():
    """Provide GAM service account JSON for real API tests.

    Skips tests if no credentials are available.
    """
    sa_json = _get_gam_service_account_json()
    if sa_json is None:
        pytest.skip("GAM credentials not available. Set GAM_SERVICE_ACCOUNT_JSON or GAM_SERVICE_ACCOUNT_KEY_FILE")
    return sa_json


@pytest.fixture(scope="session")
def gam_client_manager(gam_service_account_json):
    """Provide an initialized GAMClientManager connected to the test network."""
    from src.adapters.gam.client import GAMClientManager

    config = {"service_account_json": gam_service_account_json}
    manager = GAMClientManager(config, network_code=GAM_TEST_NETWORK_CODE)

    # Verify connection works
    client = manager.get_client()
    assert client is not None, "GAM client failed to initialize"

    return manager
