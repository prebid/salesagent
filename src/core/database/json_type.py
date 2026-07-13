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


def _dump_for_storage(model: BaseModel) -> dict:
    """Dump a Pydantic model for JSONB storage: JSON-mode, optional-absent (not null)."""
    return model.model_dump(mode="json", exclude_none=True)


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

        Accepts Pydantic models, dicts, and lists. Pydantic models are dumped
        here with ``exclude_none=True`` rather than left to the engine's JSON
        serializer: AdCP optional fields are ABSENT, not null, and the migrated
        database enforces that (e.g. the ``validate_format_ids`` plpgsql CHECK
        rejects ``"width": null``). Letting ``pydantic_core.to_json`` serialize
        the model would emit null-valued optional keys — a write that passes on
        ``create_all``-built test databases (no trigger) but fails on any
        Alembic-migrated database.

        When ``model`` is configured, raw dicts are validated through it BEFORE
        storage. The typed column validates on every read, so an invalid value
        that reaches storage makes the row unreadable — the write boundary must
        reject what the read path cannot load (#1172 review: a non-URL
        agent_url persisted from a form; the plpgsql CHECK validates shape,
        not URL syntax). Raises ``pydantic.ValidationError`` (surfaced by
        SQLAlchemy as ``StatementError``) on invalid input.
        """
        if value is None:
            return None

        if isinstance(value, BaseModel):
            return _dump_for_storage(value)

        if isinstance(value, list):
            return [self._dump_item(item) for item in value]

        if not isinstance(value, dict):
            logger.warning(
                f"JSONType received non-JSON type: {type(value).__name__}. "
                f"Converting to empty dict to prevent data corruption."
            )
            value = {}

        if self._model is not None and not self._is_list:
            return _dump_for_storage(self._model.model_validate(value))

        return value

    def _dump_item(self, item: Any) -> Any:
        """Serialize one list element, validating raw dicts through the configured model."""
        if isinstance(item, BaseModel):
            return _dump_for_storage(item)
        if self._model is not None and isinstance(item, dict):
            return _dump_for_storage(self._model.model_validate(item))
        return item

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
