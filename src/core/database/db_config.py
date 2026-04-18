"""Database configuration - PostgreSQL only.

Production exclusively uses PostgreSQL. No SQLite support.
This aligns with our principle: "No fallbacks - if it's in our control, make it work."

DatabaseConnection retention (Agent F Audit 06 Decision 2, reaffirmed 2026-04-11):
    This module is RETAINED intentionally. The sync raw-psycopg2 path is the only
    fork-safe DB access pre-uvicorn. `scripts/deploy/run_all_services.py` runs as
    PID 1 and spawns uvicorn via `subprocess.Popen([sys.executable, ...])`; using
    SQLAlchemy's `get_db_session()` here would construct a pooled engine in the
    parent and leak open PG sockets across the fork into the child, corrupting
    them (the canonical SQLAlchemy fork-safety bug). Raw psycopg2 connect-query-
    close is fork-safe because the connection is fully torn down before Popen.

    Post pre-L0 PR scope cleanup (Agent F scope item D):
        `examples/upstream_quickstart.py` migrated to `get_db_session()`.
    Remaining callers of `get_db_connection()`:
        - `scripts/deploy/run_all_services.py:84` (check_database_health)
        - `scripts/deploy/run_all_services.py:135` (check_schema_issues)
    Total: 2 files, both inside the PID-1 orchestrator. NEW code MUST NOT call
    `get_db_connection()` — use `get_db_session()` instead. The structural guard
    `tests/unit/test_architecture_get_db_connection_callers_allowlist.py`
    enforces this allowlist.
"""

import os
from typing import Any
from urllib.parse import urlparse


class DatabaseConfig:
    """PostgreSQL database configuration."""

    @staticmethod
    def get_db_config() -> dict[str, Any]:
        """Get PostgreSQL configuration from environment."""

        # Support DATABASE_URL for easy deployment (Heroku, Railway, Fly.io, etc.)
        database_url = os.environ.get("DATABASE_URL")
        if database_url:
            return DatabaseConfig._parse_database_url(database_url)

        # Individual environment variables (fallback)
        return {
            "type": "postgresql",
            "host": os.environ.get("DB_HOST", "localhost"),
            "port": int(os.environ.get("DB_PORT", "5432")),
            "database": os.environ.get("DB_NAME", "adcp"),
            "user": os.environ.get("DB_USER", "adcp"),
            "password": os.environ.get("DB_PASSWORD", ""),
            "sslmode": os.environ.get("DB_SSLMODE", "prefer"),
        }

    @staticmethod
    def _parse_database_url(url: str) -> dict[str, Any]:
        """Parse DATABASE_URL into configuration dict.

        Supports both TCP connections and Cloud SQL socket connections:
        - TCP: postgresql://user:pass@host:port/dbname
        - Cloud SQL: postgresql://user:pass@/dbname?host=/cloudsql/project:region:instance
        """
        from urllib.parse import parse_qs

        parsed = urlparse(url)

        if parsed.scheme not in ["postgres", "postgresql"]:
            raise ValueError(
                f"Unsupported database scheme: {parsed.scheme}. Only PostgreSQL is supported. Use 'postgresql://' URLs."
            )

        # Parse query string for socket path (Cloud SQL format)
        query_params = parse_qs(parsed.query)
        socket_path = query_params.get("host", [None])[0]

        # Determine host - use socket path if no hostname (Cloud SQL style)
        host = parsed.hostname
        if not host and socket_path:
            # Cloud SQL socket connection
            host = socket_path
        elif not host:
            raise ValueError(
                "DATABASE_URL missing host. Use either:\n"
                "  - TCP: postgresql://user:pass@HOST:5432/dbname\n"
                "  - Cloud SQL socket: postgresql://user:pass@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE"
            )

        return {
            "type": "postgresql",
            "host": host,
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password or "",
            "sslmode": "require" if "sslmode=require" in url else "prefer",
        }

    @staticmethod
    def get_connection_string() -> str:
        """Get connection string for SQLAlchemy.

        Handles both TCP and Unix socket connections:
        - TCP: postgresql://user:pass@host:port/dbname?sslmode=...
        - Unix socket: postgresql://user:pass@/dbname?host=/path/to/socket
        """
        config = DatabaseConfig.get_db_config()

        password = config["password"]
        if password:
            auth = f"{config['user']}:{password}"
        else:
            auth = config["user"]

        host = config["host"]

        # Check if host is a Unix socket path (starts with /)
        if host.startswith("/"):
            # Unix socket: put path in query string, not in authority
            return f"postgresql://{auth}@/{config['database']}?host={host}"
        else:
            # TCP connection: standard format
            return f"postgresql://{auth}@{host}:{config['port']}/{config['database']}?sslmode={config['sslmode']}"


class DatabaseConnection:
    """PostgreSQL database connection wrapper."""

    def __init__(self):
        self.config = DatabaseConfig.get_db_config()
        self.connection: Any | None = None

    def connect(self):
        """Connect to PostgreSQL database.

        Hardened per Agent F pre-L0 hardening scope item C:
            - `connect_timeout`: caps the TCP handshake so a hanging DB cannot
              brick container startup (env override: `DATABASE_CONNECT_TIMEOUT`).
            - `options="-c statement_timeout=5000"`: caps individual statement
              execution at 5s so the health-check SELECTs in run_all_services.py
              cannot wedge the PID-1 orchestrator on a slow query.
        """
        import psycopg2
        import psycopg2.extras

        connect_timeout = int(os.environ.get("DATABASE_CONNECT_TIMEOUT", "10"))

        self.connection = psycopg2.connect(
            host=self.config["host"],
            port=self.config["port"],
            database=self.config["database"],
            user=self.config["user"],
            password=self.config["password"],
            sslmode=self.config["sslmode"],
            cursor_factory=psycopg2.extras.DictCursor,
            connect_timeout=connect_timeout,
            options="-c statement_timeout=5000",
        )

        return self.connection

    def execute(self, query: str, params: tuple | None = None):
        """Execute a query with parameter substitution."""
        if self.connection is None:
            raise RuntimeError("Connection not established. Call connect() first.")
        cursor = self.connection.cursor()

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        return cursor

    def cursor(self):
        """Get a database cursor."""
        if self.connection is None:
            raise RuntimeError("Connection not established. Call connect() first.")
        return self.connection.cursor()

    def commit(self):
        """Commit the current transaction."""
        if self.connection:
            self.connection.commit()

    def close(self):
        """Close database connection."""
        if self.connection:
            self.connection.close()

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager and close connection."""
        self.close()
        return False


def get_db_connection() -> DatabaseConnection:
    """Get a PostgreSQL database connection using current configuration."""
    conn = DatabaseConnection()
    conn.connect()
    return conn
