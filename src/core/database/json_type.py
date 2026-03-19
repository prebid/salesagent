"""Custom SQLAlchemy JSON type for PostgreSQL JSONB with validation.

This codebase uses PostgreSQL exclusively - no SQLite support.
This type uses native JSONB storage with additional validation.

When a Pydantic ``model`` is specified, values are coerced to typed models
on read and serialized transparently on write. All new columns should
specify a model — bare JSONType (no model) is legacy.
"""

import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)


class JSONType(TypeDecorator):
    """PostgreSQL JSONB type with optional Pydantic coercion.

    When ``model`` is provided, values are coerced to the Pydantic model
    on read (``process_result_value``) and serialized on write
    (``process_bind_param``). This is the correct way to use this type —
    all new columns should specify a model.

    When ``model`` is omitted, values pass through as raw dicts/lists.
    This is legacy behavior for columns not yet migrated to typed models.

    Usage::

        # Typed (preferred — new code):
        brand: Mapped[BrandReference | None] = mapped_column(
            JSONType(model=BrandReference), nullable=True
        )

        # Typed list:
        agents: Mapped[list[GovernanceAgent] | None] = mapped_column(
            JSONType(model=GovernanceAgent, is_list=True), nullable=True
        )

        # Legacy (untyped — existing code only):
        data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    """

    # PostgreSQL-specific JSONB type with none_as_null=True
    # This ensures Python None becomes SQL NULL, not JSON null
    impl = JSONB(none_as_null=True)
    cache_ok = True

    def __init__(
        self,
        *args: Any,
        model: type[BaseModel] | None = None,
        is_list: bool = False,
        **kwargs: Any,
    ) -> None:
        self._model = model
        self._is_list = is_list
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value: Any, dialect: Dialect) -> dict | list | None:
        """Serialize value for database storage.

        Accepts Pydantic models, dicts, and lists. Pydantic models are
        serialized via the engine's JSON serializer (pydantic_core.to_json).
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

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        """Deserialize value from database, coercing to Pydantic model if configured."""
        if value is None:
            return None

        # PostgreSQL JSONB is already deserialized by psycopg2 driver
        if not isinstance(value, dict | list):
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

        # No model configured — legacy passthrough
        if self._model is None:
            return value

        # Coerce to typed Pydantic model(s)
        if self._is_list:
            if not isinstance(value, list):
                raise TypeError(f"Expected list from JSONB for list column, got {type(value).__name__}")
            return [self._model.model_validate(item) for item in value]

        return self._model.model_validate(value)
