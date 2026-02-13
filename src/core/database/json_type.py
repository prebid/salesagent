"""Custom SQLAlchemy JSON type for PostgreSQL JSONB with validation.

This codebase uses PostgreSQL exclusively - no SQLite support.
This type uses native JSONB storage with additional validation.
"""

import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)


class JSONType(TypeDecorator):
    """PostgreSQL JSONB type with validation.

    This type uses PostgreSQL's native JSONB storage (binary JSON format)
    with additional validation to ensure data integrity.

    Architecture Decision:
        Per CLAUDE.md, this codebase is PostgreSQL-only. We do NOT support SQLite.
        Therefore, we use native JSONB for optimal performance and features.

    Usage:
        class MyModel(Base):
            data = Column(JSONType)  # Stores as PostgreSQL JSONB

    Features:
        - Native PostgreSQL JSONB storage (binary format, faster than TEXT)
        - Validates data before storage (dict/list only)
        - Handles None values gracefully (stores as SQL NULL)
        - Supports all JSONB operators (@>, ?, ->, etc.)
        - GIN indexes work natively without CAST
        - Cache-safe for SQLAlchemy query caching

    PostgreSQL JSONB Benefits:
        - Faster queries (binary format vs TEXT parsing)
        - Smaller storage size (compressed binary)
        - Native indexing support (GIN, GiST)
        - Query operators built-in (@>, ?, #>, etc.)
        - Can index nested fields
        - Automatic validation on insert

    Error Handling:
        - Non-JSON types are logged and converted to empty dict
        - Validation happens before database write
        - PostgreSQL enforces JSONB validity at database level
    """

    # PostgreSQL-specific JSONB type with none_as_null=True
    # This ensures Python None becomes SQL NULL, not JSON null
    impl = JSONB(none_as_null=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> dict | list | None:
        """Process value being sent to database.

        Args:
            value: Python object to store (dict, list, or None)
            dialect: Database dialect (must be PostgreSQL)

        Returns:
            Python dict/list for PostgreSQL JSONB storage, or None for SQL NULL
        """
        if value is None:
            return None

        # Accept dict, list, and Pydantic BaseModel instances.
        # The engine's _pydantic_json_serializer (pydantic_core.to_json) handles
        # BaseModel serialization correctly — no need to model_dump() here.
        if not isinstance(value, dict | list | BaseModel):
            logger.warning(
                f"JSONType received non-JSON type: {type(value).__name__}. "
                f"Converting to empty dict to prevent data corruption."
            )
            value = {}

        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> dict | list | None:
        """Process value returned from database.

        Args:
            value: Raw value from database (dict, list, or None from PostgreSQL JSONB)
            dialect: Database dialect (must be PostgreSQL)

        Returns:
            Python object (dict/list) or None

        Note:
            PostgreSQL JSONB columns are automatically deserialized by psycopg2 driver.
            This method just validates and passes through the already-deserialized value.
        """
        if value is None:
            return None

        # PostgreSQL JSONB is already deserialized by psycopg2 driver
        if isinstance(value, dict | list):
            return value

        # Unexpected type - should never happen with PostgreSQL JSONB
        logger.error(
            f"Unexpected type in JSONB column: {type(value).__name__}. "
            f"Expected dict or list from PostgreSQL JSONB. "
            f"Value: {repr(value)[:100]}"
        )
        raise TypeError(
            f"Unexpected type in JSONB column: {type(value).__name__}. "
            "PostgreSQL JSONB should always return dict or list. "
            "This may indicate a database schema issue."
        )
