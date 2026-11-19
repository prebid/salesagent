"""Operator-safe rendering of a caught exception.

Putting ``str(e)`` into a response shows the operator whatever the exception
carries: for a database error that is the raw driver dump (the failing statement
and a DETAIL line that can carry tenant data); for a GAM, OAuth or HTTP client
error it is remote text and internal state. The operator is told what failed and
given a short reference; the exception, with its traceback, goes to the server log
under the same reference.

No exception text passes through, for any type. ``ValueError`` is raised both by
handlers on purpose and by libraries (``int()``, ``json``, pydantic) with input
echoed back, so a type allowlist cannot tell the two apart. A failure with
operator-facing text on purpose is raised as its own type and answered with a
constant, as the domain routes do with ``DomainNotOwned``.

This module has no web-framework dependency, so the FastAPI routes and the
services use it without loading the admin package. The Flask ``json_error``
wrapper lives in ``src.admin.utils.operator_errors``.
"""

import logging
import secrets

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

DB_ERROR_OPERATOR_MESSAGE = "A database error occurred"
GENERIC_OPERATOR_MESSAGE = "An unexpected error occurred"
# Stored on a failed sync job, which a later status request returns. The failure
# itself is logged where it is caught, next to the tenant and sync it belongs to.
SYNC_FAILED_OPERATOR_MESSAGE = "The sync failed. The details have been logged."


def safe_error_message(e: BaseException, context: str | None = None) -> str:
    """Log ``e`` with its traceback and return operator-facing text that carries none of it.

    ``context`` names what failed ("Error saving GAM configuration"); it leads both
    the log line and the returned text. The returned text ends with a reference
    that also appears in the log line, so an operator can find the details.
    """
    reference = secrets.token_hex(4)
    if context is None:
        context = DB_ERROR_OPERATOR_MESSAGE if isinstance(e, SQLAlchemyError) else GENERIC_OPERATOR_MESSAGE
    logger.error("%s (reference %s)", context, reference, exc_info=e)
    return f"{context}. The details have been logged (reference {reference})."
