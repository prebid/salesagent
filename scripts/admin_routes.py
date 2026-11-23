"""The routes the Flask admin application serves, read from the URL map of ``create_app()``.

The route-conflict hook (``.pre-commit-hooks/check_route_conflicts.py``) and the route
reference (``scripts/generate_admin_route_reference.py``) both read the routes here, so the
conflict check and the page describe the same application. A caller puts the repository
root on ``sys.path`` before importing this module.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, NamedTuple


class AdminRoute(NamedTuple):
    path: str
    methods: tuple[str, ...]
    endpoint: str
    view: Callable[..., Any]


def iter_admin_routes() -> Iterator[AdminRoute]:
    """Every URL rule of the admin application, with its methods sorted.

    ``HEAD`` and ``OPTIONS`` are left out: Flask adds them to every rule on its own.
    """
    from src.admin.app import create_app

    app = create_app()
    for rule in app.url_map.iter_rules():
        methods = tuple(sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}))
        yield AdminRoute(str(rule), methods, rule.endpoint, app.view_functions[rule.endpoint])
