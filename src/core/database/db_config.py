"""Database configuration - PostgreSQL only.

Production exclusively uses PostgreSQL. No SQLite support.
This aligns with our principle: "No fallbacks - if it's in our control, make it work."
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
        """Parse DATABASE_URL into configuration dict."""
        parsed = urlparse(url)

        if parsed.scheme not in ["postgres", "postgresql"]:
            raise ValueError(
                f"Unsupported database scheme: {parsed.scheme}. "
                f"Only PostgreSQL is supported. Use 'postgresql://' URLs."
            )

        return {
            "type": "postgresql",
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password or "",
            "sslmode": "require" if "sslmode=require" in url else "prefer",
        }

    @staticmethod
    def get_connection_string() -> str:
        """Get connection string for SQLAlchemy."""
        config = DatabaseConfig.get_db_config()

        password = config["password"]
        if password:
            auth = f"{config['user']}:{password}"
        else:
            auth = config["user"]

        return (
            f"postgresql://{auth}@{config['host']}:{config['port']}"
            f"/{config['database']}?sslmode={config['sslmode']}"
        )


class DatabaseConnection:
    """PostgreSQL database connection wrapper."""

    def __init__(self):
        self.config = DatabaseConfig.get_db_config()
        self.connection = None

    def connect(self):
        """Connect to PostgreSQL database."""
        import psycopg2
        import psycopg2.extras

        self.connection = psycopg2.connect(
            host=self.config["host"],
            port=self.config["port"],
            database=self.config["database"],
            user=self.config["user"],
            password=self.config["password"],
            sslmode=self.config["sslmode"],
            cursor_factory=psycopg2.extras.DictCursor,
        )

        return self.connection

    def execute(self, query: str, params: tuple | None = None):
        """Execute a query with parameter substitution."""
        cursor = self.connection.cursor()

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        return cursor

    def cursor(self):
        """Get a database cursor."""
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
