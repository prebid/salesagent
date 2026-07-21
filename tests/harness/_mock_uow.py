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

from unittest.mock import MagicMock


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
    else:
        # Default: MediaBuyUoW-style with media_buys repo
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = []
        mock_repo.get_packages.return_value = []
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
