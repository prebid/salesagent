"""
End-to-end test specific fixtures.

These fixtures are for complete system tests that exercise the full AdCP protocol.
Implements testing hooks from https://github.com/adcontextprotocol/adcp/pull/34
"""

import os
import socket
import subprocess
import time
import uuid

import httpx
import pytest
import requests

# Import contract validation - this automatically validates tool calls at test collection time
from tests.e2e.conftest_contract_validation import pytest_collection_modifyitems  # noqa: F401


def find_free_port(start_port: int = 10000, end_port: int = 60000) -> int:
    """Find an available port in the given range."""
    for port in range(start_port, end_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
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
        mcp_port = int(os.getenv("ADCP_SALES_PORT", "8092"))
        a2a_port = int(os.getenv("A2A_PORT", "8094"))
        admin_port = int(os.getenv("ADMIN_UI_PORT", "8093"))
        postgres_port = int(os.getenv("POSTGRES_PORT", "5435"))

        print(f"✓ Using ports: MCP={mcp_port}, A2A={a2a_port}, Admin={admin_port}, Postgres={postgres_port}")

        # Quick health check
        try:
            response = requests.get(f"http://localhost:{a2a_port}/.well-known/agent.json", timeout=2)
            if response.status_code == 200:
                print("✓ A2A server is healthy")
        except Exception as e:
            print(f"⚠️  Warning: Could not verify A2A server: {e}")
            print("   Services may still be starting up")

        yield {"mcp_port": mcp_port, "a2a_port": a2a_port, "admin_port": admin_port, "postgres_port": postgres_port}
        return

    # Check if Docker is available
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("Docker not available")

    # Always clean up existing services and volumes to ensure fresh state
    print("Cleaning up any existing Docker services and volumes...")
    subprocess.run(["docker-compose", "down", "-v"], capture_output=True, check=False)

    # Explicitly remove volumes in case docker-compose down -v didn't work
    print("Explicitly removing Docker volumes...")
    subprocess.run(["docker", "volume", "prune", "-f"], capture_output=True, check=False)

    # Use environment variable ports if set, otherwise allocate dynamic ports
    mcp_port = int(os.getenv("ADCP_SALES_PORT")) if os.getenv("ADCP_SALES_PORT") else find_free_port(10000, 20000)
    a2a_port = int(os.getenv("A2A_PORT")) if os.getenv("A2A_PORT") else find_free_port(20000, 30000)
    admin_port = int(os.getenv("ADMIN_UI_PORT")) if os.getenv("ADMIN_UI_PORT") else find_free_port(30000, 40000)
    postgres_port = int(os.getenv("POSTGRES_PORT")) if os.getenv("POSTGRES_PORT") else find_free_port(40000, 50000)

    print(f"Using ports: MCP={mcp_port}, A2A={a2a_port}, Admin={admin_port}, Postgres={postgres_port}")

    # Set environment variables for docker-compose
    env = os.environ.copy()
    env["ADCP_SALES_PORT"] = str(mcp_port)
    env["A2A_PORT"] = str(a2a_port)
    env["ADMIN_UI_PORT"] = str(admin_port)
    env["POSTGRES_PORT"] = str(postgres_port)
    # Ensure ADCP_TESTING is passed to Docker containers (for test mode validation)
    if "ADCP_TESTING" in os.environ:
        env["ADCP_TESTING"] = os.environ["ADCP_TESTING"]
    else:
        env["ADCP_TESTING"] = "true"  # Default to testing mode in E2E tests

    print("Building and starting Docker services with dynamic ports...")
    print("This may take 2-3 minutes for initial build...")

    # Build first with output visible, then start detached
    print("Step 1/2: Building Docker images...")
    build_result = subprocess.run(
        ["docker-compose", "build", "--progress=plain"],
        env=env,
        capture_output=False,  # Show build output
    )
    if build_result.returncode != 0:
        print(f"❌ Docker build failed with exit code {build_result.returncode}")
        raise subprocess.CalledProcessError(build_result.returncode, "docker-compose build")

    print("Step 2/2: Starting services...")
    subprocess.run(["docker-compose", "up", "-d"], check=True, env=env)

    # Wait for services to be healthy
    max_wait = 120  # Increased from 60 to 120 seconds for CI
    start_time = time.time()

    mcp_ready = False
    a2a_ready = False

    print(f"Waiting for services (max {max_wait}s)...")
    print(f"  MCP: http://localhost:{mcp_port}/health")
    print(f"  A2A: http://localhost:{a2a_port}/")

    while time.time() - start_time < max_wait:
        elapsed = int(time.time() - start_time)

        # Show progress every 5 seconds
        if elapsed > 0 and elapsed % 5 == 0 and not (mcp_ready and a2a_ready):
            print(f"  ⏱️  Still waiting... ({elapsed}s / {max_wait}s)")
            # Show container status for debugging
            try:
                ps_result = subprocess.run(
                    ["docker-compose", "ps", "--format", "table"], capture_output=True, text=True, timeout=2
                )
                if ps_result.returncode == 0 and ps_result.stdout:
                    print(f"  Container status:\n{ps_result.stdout}")
            except:
                pass

        # Check MCP server health
        if not mcp_ready:
            try:
                response = requests.get(f"http://localhost:{mcp_port}/health", timeout=2)
                if response.status_code == 200:
                    print(f"✓ MCP server is ready (after {elapsed}s)")
                    mcp_ready = True
            except requests.RequestException as e:
                if elapsed % 10 == 0:  # Log every 10 seconds
                    print(f"  MCP not ready yet ({elapsed}s): {type(e).__name__}")

        # Check A2A server health
        if not a2a_ready:
            try:
                response = requests.get(f"http://localhost:{a2a_port}/", timeout=2)
                if response.status_code in [200, 404, 405]:  # Any response means it's up
                    print(f"✓ A2A server is ready (after {elapsed}s)")
                    a2a_ready = True
            except requests.RequestException as e:
                if elapsed % 10 == 0:  # Log every 10 seconds
                    print(f"  A2A not ready yet ({elapsed}s): {type(e).__name__}")

        # Both services ready
        if mcp_ready and a2a_ready:
            break

        time.sleep(2)
    else:
        # Timeout - try to get container logs for debugging
        print("\n❌ Health check timeout. Attempting to get container logs...")

        # Get logs from all services
        for service in ["adcp-server", "postgres", "admin-ui"]:
            try:
                print(f"\n📋 {service} logs (last 100 lines):")
                result = subprocess.run(
                    ["docker-compose", "logs", "--tail=100", service], capture_output=True, text=True, timeout=5
                )
                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(f"STDERR: {result.stderr}")
            except Exception as e:
                print(f"Could not get {service} logs: {e}")

        # Show container status
        try:
            print("\n📊 Container status:")
            ps_result = subprocess.run(["docker-compose", "ps"], capture_output=True, text=True, timeout=2)
            print(ps_result.stdout)
        except Exception as e:
            print(f"Could not get container status: {e}")

        if not mcp_ready:
            pytest.fail(f"MCP server did not become healthy in time (waited {max_wait}s, port {mcp_port})")
        if not a2a_ready:
            pytest.fail(f"A2A server did not become healthy in time (waited {max_wait}s, port {a2a_port})")

    # Initialize CI test data now that services are healthy
    print("📦 Initializing CI test data (products, principals, etc.)...")
    init_result = subprocess.run(
        ["docker-compose", "exec", "-T", "adcp-server", "python", "scripts/setup/init_database_ci.py"],
        env=env,
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
        reset_response = requests.post(f"http://localhost:{mcp_port}/admin/reset-db-pool", timeout=5)
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
        db_state_response = requests.get(f"http://localhost:{mcp_port}/debug/db-state", timeout=5)
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

        conn = psycopg2.connect(
            host="localhost",
            port=postgres_port,
            database="adcp",
            user="adcp_user",
            password="secure_password_change_me",
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
            subprocess.run(["docker-compose", "down", "-v"], capture_output=True, check=False, timeout=30)
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

    return {
        "mcp": f"http://localhost:{ports['mcp_port']}",
        "a2a": f"http://localhost:{ports['a2a_port']}",
        "admin": f"http://localhost:{ports['admin_port']}",
        "postgres": f"postgresql://adcp_user:secure_password_change_me@localhost:{ports['postgres_port']}/adcp",
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
async def e2e_client(live_server, test_auth_token):
    """Provide async client for E2E testing with testing hooks."""
    from fastmcp.client import Client
    from fastmcp.client.transports import StreamableHttpTransport

    # Create MCP client with test session ID
    # Note: Host header is automatically set by HTTP client based on URL,
    # so we use x-adcp-tenant header for explicit tenant selection in E2E tests
    test_session_id = str(uuid.uuid4())
    headers = {
        "x-adcp-auth": test_auth_token,
        "x-adcp-tenant": "ci-test",  # Explicit tenant selection for E2E tests
        "X-Test-Session-ID": test_session_id,
        "X-Dry-Run": "true",  # Always use dry-run for tests
    }

    transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)
    client = Client(transport=transport)

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
