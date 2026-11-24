"""The base of a repository whose lookups name one kind of record, and the one refusal of a record it does not hold."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from src.core.operator_errors import RecordNotFound


class Repository:
    """A repository for one kind of record, refused by its ``NOT_FOUND`` class when the request names one it does not hold.

    ``NOT_FOUND`` is the :class:`~src.core.operator_errors.RecordNotFound` subclass for that
    kind. Defining a repository without it fails at import.
    """

    NOT_FOUND: ClassVar[type[RecordNotFound]]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not issubclass(getattr(cls, "NOT_FOUND", object), RecordNotFound):
            raise TypeError(f"{cls.__name__} must define NOT_FOUND as a RecordNotFound subclass")

    def require[**P, T](self, lookup: Callable[P, T | None], /, *args: P.args, **kwargs: P.kwargs) -> T:
        """The row *lookup* returns for *args*, or this repository's ``NOT_FOUND`` when it returns ``None``.

        *lookup* is one of this repository's methods. Its name and arguments are the refusal's
        own text, for the log; the operator is answered with the class's ``MESSAGE``.
        """
        row = lookup(*args, **kwargs)
        if row is None:
            raise self.NOT_FOUND(f"{lookup.__qualname__} found no row for {args!r} {kwargs!r}")
        return row
