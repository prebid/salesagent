"""Database module for Prebid Sales Agent Server.

This module contains all database-related functionality including:
- Database configuration and connection management
- Database schema definitions and migrations
- SQLAlchemy models and ORM mappings
- Database session handling and context management

Key components:
- db_config.py: Database configuration and connection setup
- database.py: Core database utilities and initialization
- database_schema.py: Schema definitions and table creation
- database_session.py: Session management and context handlers
- models.py: SQLAlchemy ORM models for all entities
- embedded_tenant_guard.py: model-layer write guard for platform-managed surfaces
"""

# NOTE: the embedded_tenant_guard registration was intentionally moved OUT of
# this package __init__ and into models.py (see the import at the bottom of
# models.py). Importing the guard here forced every ``src.core.database.*``
# import — even lightweight ones like ``database_session`` / ``db_config`` — to
# transitively load the ORM models and the ~1 GB ``adcp`` dependency, because
# the guard imports model classes to attach its listeners. Lightweight tools
# (e.g. the sync cron) only need a DB session, not the ORM/adcp. Registering
# the guard from models.py instead keeps it attached whenever the ORM is in use
# (you cannot mutate a guarded model without importing models.py) while letting
# the session/config layer be imported adcp-free.
