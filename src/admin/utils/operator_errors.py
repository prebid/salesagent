"""Operator-safe rendering of caught exceptions for flash messages and JSON bodies.

Admin handlers wrap their work in blanket ``except Exception`` blocks and put
``str(e)`` straight into the response. For database errors that string is the
raw driver dump — ``(psycopg2.errors.…)``, the full failing statement, and a
DETAIL line that can carry tenant data (parameter values / key values). The
operator may be told a write failed; they must never be shown driver internals.

Non-database exceptions keep their message: handlers raise ``ValueError`` and
friends with operator-facing text on purpose, and flattening those would
destroy legitimate diagnostics.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

DB_ERROR_OPERATOR_MESSAGE = "A database error occurred. The details have been logged."


def safe_error_message(e: Exception) -> str:
    """The operator-facing text for a caught exception.

    Database errors (any :class:`SQLAlchemyError`, which wraps the DBAPI error
    and embeds its statement/DETAIL text) collapse to a generic message, with
    the full exception logged server-side; everything else renders as before.
    """
    if isinstance(e, SQLAlchemyError):
        logger.error("Database error in admin handler: %s", e, exc_info=True)
        return DB_ERROR_OPERATOR_MESSAGE
    return str(e)
