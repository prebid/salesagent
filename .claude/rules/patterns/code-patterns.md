# Code Patterns

Reference patterns for writing code in this project. Read this when implementing new features or modifying existing code.

## SQLAlchemy 2.0 (MANDATORY for new code)
```python
from sqlalchemy import select

# Use this
stmt = select(Model).filter_by(field=value)
instance = session.scalars(stmt).first()

# Not this (deprecated)
instance = session.query(Model).filter_by(field=value).first()
```

## Database JSON Fields
```python
from src.core.database.json_type import JSONType

class MyModel(Base):
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
```

## Import Patterns
```python
# Always use absolute imports
from src.core.schemas import Principal
from src.core.database.database_session import get_db_session
from src.adapters import get_adapter
```

## No Quiet Failures
```python
# WRONG - Silent failure
if not self.supports_feature:
    logger.warning("Skipping...")

# CORRECT - Explicit failure
if not self.supports_feature and feature_requested:
    raise FeatureNotSupportedException("Cannot fulfill contract")
```

## Code Style
- Use `uv` for dependencies
- Run `pre-commit run --all-files`
- Use type hints
- No hardcoded external system IDs (use config/database)
- No testing against production systems

## Type Checking
```bash
uv run mypy src/core/your_file.py --config-file=mypy.ini
```

When modifying code:
1. Fix mypy errors in files you change
2. Use SQLAlchemy 2.0 `Mapped[]` annotations for new models
3. Use `| None` instead of `Optional[]` (Python 3.10+)
