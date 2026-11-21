"""Every operator refusal the admin app and its services declare, by class name."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from functools import cache

from src.core.operator_errors import OperatorRefusal


def _descendants(cls: type[OperatorRefusal]) -> Iterator[type[OperatorRefusal]]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _descendants(sub)


@cache
def operator_refusal_classes() -> dict[str, type[OperatorRefusal]]:
    """Each concrete :class:`OperatorRefusal` subclass, found by importing every module of ``src.admin`` and ``src.services``.

    Imported rather than listed, because a route imports some services inside the function
    that calls them, so importing the app alone does not define every refusal.
    """
    import src.admin
    import src.services

    for package in (src.admin, src.services):
        for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
            importlib.import_module(module.name)
    # A meaning base (RecordNotFound, StateConflict, ...) sets a STATUS and no MESSAGE: it is never raised.
    classes = {cls for cls in _descendants(OperatorRefusal) if "MESSAGE" in vars(cls)}
    by_name = {cls.__name__: cls for cls in classes}
    assert len(by_name) == len(classes), f"two refusals share a class name: {sorted(c.__qualname__ for c in classes)}"
    return by_name
