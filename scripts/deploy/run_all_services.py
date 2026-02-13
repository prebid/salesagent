#!/usr/bin/env python3
"""
Run all AdCP services in a single process for container deployment.
This is the main entrypoint for Docker containers.

Handles:
- Environment validation
- Database connectivity checks
- Database migrations and initialization
- Starting MCP server, Admin UI, A2A server, nginx, and cron
"""

import os
import signal
import subprocess
import sys
import threading
import time

# Store process references for cleanup
processes = []


def validate_required_env():
    """Validate required environment variables."""
    print("🔍 Validating required environment variables...")

    missing = []

    # Note: SUPER_ADMIN_EMAILS is optional - per-tenant OIDC with Setup Mode is the default auth flow.
    # New tenants start with auth_setup_mode=true, allowing test credentials to configure SSO.

    # Database URL is required
    if not os.environ.get("DATABASE_URL"):
        missing.append("DATABASE_URL")

    # Encryption key is required for storing OIDC client secrets
    if not os.environ.get("ENCRYPTION_KEY"):
        missing.append(
            "ENCRYPTION_KEY (generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        )

    # Multi-tenant mode requires SALES_AGENT_DOMAIN
    if os.environ.get("ADCP_MULTI_TENANT", "false").lower() == "true":
        if not os.environ.get("SALES_AGENT_DOMAIN"):
            missing.append("SALES_AGENT_DOMAIN (required for multi-tenant mode)")

    if missing:
        print("❌ Missing required environment variables:")
        for var in missing:
            print(f"   - {var}")
        print("")
        print("📖 See docs/deployment.md for configuration details.")
        sys.exit(1)

    print("✅ Required environment variables are set")


def check_database_health():
    """Check if database is accessible."""
    print("🔍 Checking database connectivity...")

    # Import here to avoid issues if database modules aren't available
    try:
        from src.core.database.db_config import DatabaseConfig, get_db_connection
    except ImportError as e:
        print(f"❌ Failed to import database modules: {e}")
        sys.exit(1)

    # Show parsed connection info (without password)
    db_url = os.environ.get("DATABASE_URL", "")
    print(f"DATABASE_URL set: {bool(db_url)}")

    if db_url:
        try:
            config = DatabaseConfig.get_db_config()
            print(f"Parsed host: {config.get('host', 'NOT SET')}")
            print(f"Parsed port: {config.get('port', 'NOT SET')}")
            print(f"Parsed database: {config.get('database', 'NOT SET')}")
        except Exception as e:
            print(f"⚠️ Could not parse database config: {e}")

    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT 1")
        cursor.fetchone()
        conn.close()
        print("✅ Database connection successful")
    except Exception as e:
        error_str = str(e)
        print(f"❌ Database connection failed: {e}")
        print("")

        # Provide specific guidance based on error
        if "No such file or directory" in error_str and "/var/run/postgresql" in error_str:
            print("💡 This error means the DATABASE_URL is missing a host.")
            print("")
            print("   For Cloud Run with Cloud SQL, use one of these formats:")
            print("")
            print("   Option 1 - Public IP (simpler but less secure):")
            print("     DATABASE_URL=postgresql://USER:PASS@IP_ADDRESS:5432/DATABASE")
            print("     Example: postgresql://postgres:YOUR_PASSWORD@YOUR_IP:5432/postgres")
            print("")
            print("   Option 2 - Cloud SQL Connector (recommended):")
            print("     1. Add Cloud SQL connection in Cloud Run service settings")
            print("     2. Use: DATABASE_URL=postgresql://USER:PASS@/DATABASE?host=/cloudsql/PROJECT:REGION:INSTANCE")
            print("")
        elif "could not connect to server" in error_str or "Connection refused" in error_str:
            print("💡 Database server is unreachable. Check:")
            print("   - Is the IP address correct?")
            print("   - Is Cloud SQL instance running?")
            print("   - Are authorized networks configured? (Cloud SQL > Connections > Authorized networks)")
            print("")
        elif "password authentication failed" in error_str:
            print("💡 Wrong password. Check:")
            print("   - Password in DATABASE_URL matches Cloud SQL user password")
            print("   - Special characters are URL-encoded (& -> %26, = -> %3D, etc.)")
            print("")
        elif "database" in error_str and "does not exist" in error_str:
            print("💡 Database does not exist. Either:")
            print('   - Use the default "postgres" database')
            print("   - Create your database: CREATE DATABASE yourdb;")
            print("")

        sys.exit(1)


def check_schema_issues():
    """Check for known schema issues (report only, don't fail)."""
    print("🔍 Checking for known schema issues...")

    try:
        from src.core.database.db_config import get_db_connection

        conn = get_db_connection()
        issues = []

        # Check for commonly missing columns
        checks = [
            ("media_buys", "context_id"),
            ("creative_formats", "updated_at"),
        ]

        for table, column in checks:
            cursor = conn.execute(
                f"""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = '{column}'
            """
            )
            if not cursor.fetchone():
                issues.append(f"Missing column: {table}.{column}")

        if issues:
            print("⚠️  Schema issues detected (non-critical):")
            for issue in issues:
                print(f"   - {issue}")
        else:
            print("✅ No known schema issues detected")

        conn.close()
    except Exception as e:
        print(f"⚠️  Could not check schema: {e}")


def init_database():
    """Initialize database schema and default data."""
    print("📦 Initializing database schema and default data...")
    print(
        "ℹ️  Note: init_db() is safe - it only creates tables (IF NOT EXISTS) and default tenant (if no tenants exist)"
    )

    try:
        from src.core.database.database import init_db

        init_db(exit_on_error=True)
        print("✅ Database initialization complete")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        sys.exit(1)


def cleanup(signum=None, frame=None):
    """Clean up all processes on exit."""
    print("\nShutting down all services...")
    for proc in processes:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    sys.exit(0)


# Register cleanup handlers
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def run_migrations():
    """Run database migrations before starting services."""
    print("📦 Running database migrations...")
    try:
        result = subprocess.run(
            [sys.executable, "scripts/ops/migrate.py"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print("✅ Migrations complete")
        else:
            print(f"❌ Migration failed: {result.stderr}")
            print(result.stdout)
            sys.exit(1)
    except subprocess.TimeoutExpired:
        print("❌ Migration timed out after 60 seconds")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Migration error: {e}")
        sys.exit(1)


def run_mcp_server():
    """Run the MCP server."""
    print("Starting MCP server on port 8080...")
    env = os.environ.copy()
    env["ADCP_SALES_PORT"] = "8080"
    proc = subprocess.Popen(
        [sys.executable, "scripts/run_server.py"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    processes.append(proc)

    # Monitor the process output
    for line in iter(proc.stdout.readline, b""):
        if line:
            print(f"[MCP] {line.decode().rstrip()}")
    print("MCP server stopped")


def run_admin_ui():
    """Run the Admin UI."""
    admin_port = os.environ.get("ADMIN_UI_PORT", "8001")
    print(f"Starting Admin UI on port {admin_port}...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "/app"
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.admin.server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    processes.append(proc)

    # Monitor the process output
    for line in iter(proc.stdout.readline, b""):
        if line:
            print(f"[Admin] {line.decode().rstrip()}")
    print("Admin UI stopped")


def run_a2a_server():
    """Run the A2A server for agent-to-agent interactions."""
    try:
        print("Starting A2A server on port 8091...")
        print("[A2A] Waiting 10 seconds for MCP server to be ready...")
        time.sleep(10)  # Wait for MCP server to be ready

        env = os.environ.copy()
        env["A2A_MOCK_MODE"] = "true"  # Use mock mode in production for now

        print("[A2A] Launching official a2a-sdk server...")
        # Use official a2a-sdk implementation with JSON-RPC 2.0 support
        proc = subprocess.Popen(
            [sys.executable, "src/a2a_server/adcp_a2a_server.py"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        processes.append(proc)

        print("[A2A] Process started, monitoring output...")
        # Monitor the process output
        for line in iter(proc.stdout.readline, b""):
            if line:
                print(f"[A2A] {line.decode().rstrip()}")
        print("A2A server stopped")
    except Exception as e:
        print(f"[A2A] ERROR: Failed to start A2A server: {e}")
        import traceback

        traceback.print_exc()


def run_nginx():
    """Run nginx as reverse proxy."""
    print("Starting nginx reverse proxy on port 8000...")

    # Create nginx directories if they don't exist
    os.makedirs("/var/log/nginx", exist_ok=True)
    os.makedirs("/var/run", exist_ok=True)

    # Select nginx config based on ADCP_MULTI_TENANT env var
    # Default: simple (single-tenant, path-based routing only)
    # ADCP_MULTI_TENANT=true: full config with subdomain routing for multi-tenant
    multi_tenant = os.environ.get("ADCP_MULTI_TENANT", "false").lower() == "true"
    if multi_tenant:
        config_path = "/etc/nginx/nginx-multi-tenant.conf"
        print("[Nginx] Using multi-tenant config (subdomain routing enabled)")

        # Multi-tenant config uses environment variable templates
        # Use envsubst to substitute ${SALES_AGENT_DOMAIN} with actual value
        sales_agent_domain = os.environ.get("SALES_AGENT_DOMAIN", "")
        if not sales_agent_domain:
            print("❌ SALES_AGENT_DOMAIN is required for multi-tenant mode")
            sys.exit(1)

        print(f"[Nginx] Substituting SALES_AGENT_DOMAIN={sales_agent_domain}")

        # Read template, substitute variables, write to active config
        with open(config_path) as f:
            config_content = f.read()

        # Simple string substitution for ${SALES_AGENT_DOMAIN}
        config_content = config_content.replace("${SALES_AGENT_DOMAIN}", sales_agent_domain)

        with open("/etc/nginx/nginx.conf", "w") as f:
            f.write(config_content)
    else:
        config_path = "/etc/nginx/nginx-single-tenant.conf"
        print("[Nginx] Using single-tenant config (path-based routing only)")

        # Copy selected config to active location
        import shutil

        shutil.copy(config_path, "/etc/nginx/nginx.conf")

    # Test nginx configuration first
    test_proc = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if test_proc.returncode != 0:
        print(f"❌ Nginx configuration test failed: {test_proc.stderr}")
        return
    else:
        print("✅ Nginx configuration test passed")

    # Start nginx
    proc = subprocess.Popen(
        ["nginx", "-g", "daemon off;"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    processes.append(proc)

    # Monitor the process output
    for line in iter(proc.stdout.readline, b""):
        if line:
            print(f"[Nginx] {line.decode().rstrip()}")
    print("Nginx stopped")


def run_cron():
    """Run supercronic for scheduled tasks."""
    crontab_path = "/app/crontab"
    if not os.path.exists(crontab_path):
        print("[Cron] No crontab found, skipping scheduled tasks")
        return

    print("Starting supercronic for scheduled tasks...")

    proc = subprocess.Popen(
        ["supercronic", crontab_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    processes.append(proc)

    # Monitor the process output
    for line in iter(proc.stdout.readline, b""):
        if line:
            print(f"[Cron] {line.decode().rstrip()}")
    print("Supercronic stopped")


def main():
    """Main entry point to run all services."""
    print("🚀 Starting AdCP Sales Agent...")
    print("=" * 60)
    print("AdCP Sales Agent - Starting All Services")
    print("=" * 60)

    # Validate environment variables
    validate_required_env()

    # Check database connectivity
    check_database_health()

    # Run migrations
    run_migrations()

    # Check for schema issues (non-blocking)
    check_schema_issues()

    # Initialize database
    init_database()

    # Start services in threads
    threads = []

    # MCP Server thread
    mcp_thread = threading.Thread(target=run_mcp_server, daemon=True)
    mcp_thread.start()
    threads.append(mcp_thread)

    # Admin UI thread
    admin_thread = threading.Thread(target=run_admin_ui, daemon=True)
    admin_thread.start()
    threads.append(admin_thread)

    # A2A Server thread for agent-to-agent communication
    a2a_thread = threading.Thread(target=run_a2a_server, daemon=True)
    a2a_thread.start()
    threads.append(a2a_thread)

    # Cron thread for scheduled tasks (syncing GAM tenants, etc.)
    skip_cron = os.environ.get("SKIP_CRON", "false").lower() == "true"
    if not skip_cron:
        cron_thread = threading.Thread(target=run_cron, daemon=True)
        cron_thread.start()
        threads.append(cron_thread)

    # Check if we should skip nginx (useful for docker-compose with separate services)
    skip_nginx = os.environ.get("SKIP_NGINX", "false").lower() == "true"

    if not skip_nginx:
        # Give services more time to start before nginx
        print("⏳ Waiting for backend services to be ready before starting nginx...")
        time.sleep(10)

        # Nginx reverse proxy thread
        nginx_thread = threading.Thread(target=run_nginx, daemon=True)
        nginx_thread.start()
        threads.append(nginx_thread)

        print("\n✅ All services started with unified routing:")
        print("  - MCP Server: http://localhost:8000/mcp")
        print("  - Admin UI: http://localhost:8000/admin")
        print("  - A2A Server: http://localhost:8000/a2a")
        print("\nPress Ctrl+C to stop all services")
    else:
        admin_port = os.environ.get("ADMIN_UI_PORT", "8001")
        print("\n✅ Services started (nginx skipped):")
        print("  - MCP Server: http://localhost:8080")
        print(f"  - Admin UI: http://localhost:{admin_port}")
        print("  - A2A Server: http://localhost:8091")
        print("\nℹ️  Nginx reverse proxy skipped (SKIP_NGINX=true)")
        print("Press Ctrl+C to stop all services")

    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nShutting down all services...")
        sys.exit(0)


if __name__ == "__main__":
    main()
