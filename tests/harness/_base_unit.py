"""Backward-compatibility shim for unit test environments.

The base class has been merged into ``_base.py`` as ``BaseTestEnv``.
Unit envs use ``BaseTestEnv`` with ``use_real_db = False`` (the default).

This module re-exports ``ImplTestEnv`` so existing imports continue to work::

    from tests.harness._base_unit import ImplTestEnv  # still works
"""

from tests.harness._base import BaseTestEnv as ImplTestEnv

__all__ = ["ImplTestEnv"]
