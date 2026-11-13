"""Shared UoW mock builder.

Eliminates the 7-line boilerplate repeated 49 times in test_delivery_behavioral.py::

    mock_repo = MagicMock()
    mock_repo.get_by_principal.return_value = [buy]
    mock_repo.get_packages.return_value = []
    mock_uow = MagicMock()
    mock_uow.media_buys = mock_repo
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from unittest.mock import MagicMock

from tests.factories.media_buy import package_rows_for


def wire_effect_boundary(repo: MagicMock, *, preview: bool = False) -> None:
    """Make a mocked repo behave like SessionEffectsMixin (#1970).

    ``outbound(call)`` INVOKES ``call`` on the live path and returns its result;
    a bare MagicMock would swallow it, so the outbound request under test never
    happens and the assertion passes against a Mock. Same failure shape as the
    idempotency-cache default below: the mock silently answers the opposite of
    production. ``after_commit`` is deliberately left as a plain recorder —
    with no real transaction there is no drain, so a deferred effect not running
    is the CORRECT behavior for a unit test, and ``.assert_called_once_with()``
    is how a test grades that it was registered.
    """
    repo.is_preview = preview
    repo.outbound.side_effect = lambda call, preview_result=None: preview_result if preview else call()


def make_mock_uow(
    repos: dict[str, MagicMock] | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Create (uow_cls_mock, uow_instance) with context manager protocol.

    The uow_instance has:
        - ``media_buys``: a mock repo with ``.get_by_principal`` and ``.get_packages``
          defaulting to empty lists (when *repos* is ``None``)
        - Any repos passed via *repos* dict, keyed by attribute name
        - Context manager protocol (``__enter__`` / ``__exit__``)

    Usage::

        uow_cls, uow = make_mock_uow()
        uow.media_buys.get_by_principal.return_value = [buy1, buy2]
        uow.media_buys.get_packages.return_value = []
        # uow_cls is what gets patched as MediaBuyUoW

        # Custom repos:
        creative_repo = MagicMock()
        uow_cls, uow = make_mock_uow(repos={"creatives": creative_repo})

    Returns:
        Tuple of (uow_cls_mock, uow_instance).
        Wire uow_cls_mock into the patcher; manipulate uow_instance in tests.
    """
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)

    if repos is not None:
        for name, repo in repos.items():
            setattr(mock_uow, name, repo)
            wire_effect_boundary(repo)
    else:
        # Default: MediaBuyUoW-style with media_buys repo
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = []
        mock_repo.get_packages.return_value = []
        wire_effect_boundary(mock_repo)
        mock_uow.media_buys = mock_repo

    # Idempotency cache defaults to a MISS — idempotency_key is required on
    # create requests, so the probe runs on every create; a bare MagicMock would
    # read as a cache hit (truthy) with a mismatching hash → spurious conflict.
    if repos is None or "idempotency_attempts" not in repos:
        attempts_repo = MagicMock()
        attempts_repo.find_by_key.return_value = None
        # The admission policy unpacks (count, oldest) from the two scope
        # queries — empty scope by default so the ceiling never trips in units.
        attempts_repo.count_inserts_since.return_value = (0, None)
        attempts_repo.count_active.return_value = (0, None)
        mock_uow.idempotency_attempts = attempts_repo

    mock_uow_cls = MagicMock(return_value=mock_uow)

    return mock_uow_cls, mock_uow


def serve_package_rows(repo: MagicMock, rows_by_buy: Mapping[str, list[Any]]) -> None:
    """Answer the mocked repo's ``get_packages_for_ids`` with *rows_by_buy*, for the buys asked for.

    A buy's rows are its packages. Left unset, the mock hands back a ``MagicMock``, which
    the delivery service reads as a buy with no packages only by accident. *rows_by_buy* is
    read at call time, so rows registered after this call are served too.
    """
    repo.get_packages_for_ids.side_effect = lambda ids: {i: rows_by_buy[i] for i in ids if i in rows_by_buy}


def delivery_mock_uow(target_buys: Iterable[tuple[str, Any]]) -> MagicMock:
    """A mocked ``MediaBuyUoW`` for the delivery service over ``(media_buy_id, buy)`` pairs.

    Its repository serves, for each buy, the package rows the buy's stored request names
    (``package_rows_for``), each carrying the terms create stores with it. A buy whose
    request names no package is served no rows.
    """
    _, mock_uow = make_mock_uow()
    serve_package_rows(
        mock_uow.media_buys,
        {mb_id: package_rows_for(mb_id, (buy.raw_request or {}).get("packages", [])) for mb_id, buy in target_buys},
    )
    return mock_uow
