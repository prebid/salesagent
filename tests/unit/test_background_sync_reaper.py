"""SUPERSEDED — see ``test_thread_registry_reaper.py``.

PR #1264 added six dedicated scenarios here for the dead-thread reaper in
``background_sync_service``. salesagent-x2h.3 then consolidated the reaper
into :class:`src.core.thread_registry.ThreadRegistry`, and
salesagent-x2h.8 / salesagent-x2h.9 replaced these six near-identical
scenarios with a single DRY parametrized suite that runs the same six
scenarios across *every* single-dict ThreadRegistry-backed service reaper
(``background_sync_service`` is one of them).

Keeping copies of the sync scenarios here would duplicate the parametrized
bodies verbatim — a CLAUDE.md DRY-invariant violation (the
``check_code_duplication.py`` ratchet would flag it). The shared
thread-lifecycle helpers already live in ``_thread_registry_helpers.py``
(single source of truth — this file never redefined them).

This module intentionally contains no tests. The ``background_sync_service``
reaper is fully exercised by:

    tests/unit/test_thread_registry_reaper.py
        ::test_*[background_sync_service]

and the ThreadRegistry class itself by ``tests/unit/test_thread_registry.py``.
"""

from __future__ import annotations
