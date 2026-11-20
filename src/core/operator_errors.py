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
services use it without loading the admin package. The admin app's answer to each
of these types is ``src.admin.utils.operator_errors.handle_unexpected_error``.
"""

import json
import logging
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import ClassVar, NamedTuple

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from src.core.errors.issues import issue_from_pydantic_error, pointer_for_pydantic_error

logger = logging.getLogger(__name__)

DB_ERROR_OPERATOR_MESSAGE = "A database error occurred"
GENERIC_OPERATOR_MESSAGE = "An unexpected error occurred"


class OperatorRefusal(Exception):
    """An expected refusal whose operator-facing text is the class's ``MESSAGE``.

    Raised on purpose (a sync already running, a domain the tenant does not own)
    and answered with ``MESSAGE``, never with the exception's own text, which may
    carry ids for the log. Based on ``Exception`` rather than ``ValueError`` so a
    broad ``except ValueError`` cannot absorb one. Every subclass must set
    ``MESSAGE``; defining one without it fails at import.
    """

    MESSAGE: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not isinstance(cls.__dict__.get("MESSAGE"), str):
            raise TypeError(f"{cls.__name__} must define MESSAGE as a str")

    def response_fields(self) -> dict[str, object]:
        """Keys a JSON answer carries beside ``error``, for a refusal whose caller reads one."""
        return {}

    def details(self) -> tuple[str, ...]:
        """Lines a browser is shown beside ``MESSAGE``; each is text this seller wrote."""
        return ()


class RecordedFailure(Exception):
    """A failure already logged and already worded for the operator, raised in place of its cause.

    ``operator_message`` is fixed text ending with the reference its cause was logged
    under (:func:`safe_error_message`). It is answered as it is, with 500, and not logged
    again, so one failure keeps one log record and one reference.
    """

    def __init__(self, operator_message: str, log_text: str) -> None:
        super().__init__(log_text)
        self.operator_message = operator_message

    def response_fields(self) -> dict[str, object]:
        """Keys a JSON answer carries beside ``error``, for a caller that reads one."""
        return {}


class FormIssue(NamedTuple):
    """One refused form field and what is wrong with it.

    *text* is written by the validator that refused the field: a constant, or a template
    filled with the operator's own submitted values and ids this seller owns. Never another
    exception's text, which for a library error echoes input and internals.
    """

    field: str
    text: str

    @classmethod
    def invalid_json(cls, field: str, error: json.JSONDecodeError) -> "FormIssue":
        """*field* is not JSON: where the parser stopped, never the parser's own text."""
        return cls(field, f"{field} is not valid JSON at line {error.lineno}, column {error.colno}")

    @classmethod
    def from_validation_error(cls, field: str, error: ValidationError, document: object) -> tuple["FormIssue", ...]:
        """One issue per error pydantic found in *document*, the value submitted as *field*.

        The location is the error's path through *document*; the sentence is the fixed one
        ``issues[]`` derives from the JSON Schema keyword (:mod:`src.core.errors.issues`),
        never pydantic's ``msg`` or ``input``. An error no keyword can name gets
        ``UNNAMED_ERROR_SENTENCE``.
        """
        issues = []
        for entry in error.errors():
            path = pointer_for_pydantic_error(entry, document).field
            location = field + (path if path.startswith("[") or not path else f".{path}")
            named = issue_from_pydantic_error(entry, document)
            issues.append(cls(field, f"{location}: {named.message if named else UNNAMED_ERROR_SENTENCE}"))
        return tuple(issues)


#: The sentence for a pydantic error that maps to no JSON Schema keyword (``value_error``
#: and the like): the field is named, and the cause is not guessed at.
UNNAMED_ERROR_SENTENCE = "This field is not accepted."


class InvalidFormInput(OperatorRefusal):
    """A form whose submitted values cannot be saved, with one :class:`FormIssue` per refused field.

    Answered like any refusal: 400, ``MESSAGE``, and, for a JSON caller, ``issues``. A
    browser is also shown each issue's text, because ``MESSAGE`` alone does not say which
    value to change.
    """

    MESSAGE = "The form has values that cannot be saved"

    def __init__(self, *issues: FormIssue) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{issue.field}: {issue.text}" for issue in issues))

    def response_fields(self) -> dict[str, object]:
        return {"issues": [issue._asdict() for issue in self.issues]}

    def details(self) -> tuple[str, ...]:
        return tuple(issue.text for issue in self.issues)


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


@contextmanager
def best_effort(context: str) -> Iterator[None]:
    """Run a step whose failure must not fail its caller, and log the failure once under a reference.

    The one catch-all the admin app has: a side effect (a Slack notice, an audit row, a
    dashboard tile) or a fallback that keeps a page rendering. A caller that needs a value
    uses :func:`best_effort_call`. Anything a caller expects to fail in a known way is
    caught by its type instead, and anything that should fail the request is left to the
    admin app's error handler.
    """
    try:
        yield
    except Exception as e:  # noqa: BLE001 - the one sanctioned catch-all; logged with its traceback
        safe_error_message(e, context)


def best_effort_call[T](context: str, fn: Callable[[], T]) -> T | None:
    """*fn*'s value, or ``None`` when it raised: :func:`best_effort` for a step that returns one."""
    value = None
    with best_effort(context):
        value = fn()
    return value
