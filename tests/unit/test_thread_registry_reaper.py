"""Parametrized regression suite for the wired-in ThreadRegistry reapers.

PR #1264 added the dead-thread reaper pattern to several module-level
services, then salesagent-x2h.3 consolidated the dict + lock + reaper +
accessors into :class:`src.core.thread_registry.ThreadRegistry`. The reaper
drops registry entries whose threads are no longer ``is_alive()`` — defensive
cleanup that catches threads which exited WITHOUT hitting their worker
``finally`` block (``KeyboardInterrupt``, ``SystemExit``, signal-killed
workers). Without it, dead-thread refs pin DB sessions, GAM clients, and
result payloads — slow growth over weeks of uptime (production memory-leak
triage #5).

This module pins that behavior as it is *wired into* the single-dict
module-level service reapers — i.e. through the public service accessors
that real callers use, not through the ThreadRegistry class directly
(``test_thread_registry.py`` covers the class in isolation):

============================  ===================  =====================  ==========
service                       list accessor        membership accessor    match kind
============================  ===================  =====================  ==========
background_sync_service       get_active_syncs     is_sync_running        exact
order_approval_service        get_active_approvals is_approval_running    exact
background_approval_service   get_active_          is_approval_task_      substring
                              approval_tasks       running
============================  ===================  =====================  ==========

The dual-dict sites (``delivery_simulator``, ``gam/reporting``) carry a
parallel ``_stop_signals`` dict and a reap callback; their dual-dict
invariant is pinned in their own dedicated files
(``test_delivery_simulator_reaper.py``, ``test_gam_reporting_reaper.py``)
and is intentionally out of scope here.

Helpers come from the single shared module ``_thread_registry_helpers`` —
no helper or scenario body is duplicated anywhere (CLAUDE.md DRY invariant,
enforced by ``check_code_duplication.py``). ``test_background_sync_reaper.py``
is superseded by this file (sync is one of the parametrized services).

Beads: salesagent-x2h.8 (sibling reaper coverage),
salesagent-x2h.9 (shared helpers + parametrization).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from src.core.thread_registry import ThreadRegistry
from src.services import background_approval_service, background_sync_service, order_approval_service

from ._thread_registry_helpers import dead_thread, live_thread, release


@dataclass(frozen=True)
class ReaperCase:
    """One ThreadRegistry-backed service reaper, addressed via its public accessors.

    ``key_for`` maps a logical id to the registry key the service stores. For
    exact-match services it is the identity; for the substring-match
    background_approval service the production key is
    ``approval_{order_id}_{workflow_step_id}`` while the membership accessor
    is queried with just ``order_id``.
    """

    id: str
    registry: ThreadRegistry
    list_active: Callable[[], list[str]]
    is_running: Callable[[str], bool]
    key_for: Callable[[str], str]
    query_for: Callable[[str], str]
    substring: bool


CASES: list[ReaperCase] = [
    ReaperCase(
        id="background_sync_service",
        registry=background_sync_service._active_syncs,
        list_active=background_sync_service.get_active_syncs,
        is_running=background_sync_service.is_sync_running,
        key_for=lambda x: x,
        query_for=lambda x: x,
        substring=False,
    ),
    ReaperCase(
        id="order_approval_service",
        registry=order_approval_service._active_approvals,
        list_active=order_approval_service.get_active_approvals,
        is_running=order_approval_service.is_approval_running,
        key_for=lambda x: x,
        query_for=lambda x: x,
        substring=False,
    ),
    ReaperCase(
        id="background_approval_service",
        registry=background_approval_service._active_approval_tasks,
        list_active=background_approval_service.get_active_approval_tasks,
        is_running=background_approval_service.is_approval_task_running,
        # Production thread_id is approval_{order_id}_{workflow_step_id};
        # the accessor matches on the order_id substring.
        key_for=lambda order_id: f"approval_{order_id}_step1",
        query_for=lambda order_id: order_id,
        substring=True,
    ),
]

_CASE_PARAMS = [pytest.param(c, id=c.id) for c in CASES]
_SUBSTRING_PARAMS = [pytest.param(c, id=c.id) for c in CASES if c.substring]


@pytest.fixture
def case(request: pytest.FixtureRequest) -> ReaperCase:
    """Yield the parametrized ReaperCase with its module-global registry cleaned.

    These registries are module-global singletons; a real worker's ``finally``
    block calls ``.remove()``. We drain before AND after each test so a stuck
    entry from one case can never leak into another (or into unrelated suites).
    """
    c: ReaperCase = request.param
    _drain(c.registry)
    try:
        yield c
    finally:
        _drain(c.registry)


def _drain(registry: ThreadRegistry) -> None:
    """Remove every key from a registry (reap-independent hard reset)."""
    for key in list(registry.list_active()):
        registry.remove(key)


# ---------------------------------------------------------------------------
# Scenario 1: dead threads are pruned from the registry (via list accessor)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _CASE_PARAMS, indirect=True)
def test_reaper_drops_dead_threads(case: ReaperCase) -> None:
    """A dead-thread entry is pruned on the next read through the list accessor."""
    key = case.key_for("dead_one")
    case.registry.add(key, dead_thread())

    assert case.list_active() == []
    assert case.registry.contains(key) is False


# ---------------------------------------------------------------------------
# Scenario 2: live threads survive a reap
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _CASE_PARAMS, indirect=True)
def test_reaper_keeps_live_threads(case: ReaperCase) -> None:
    """A live-thread entry survives a reap and is reported by the accessors."""
    key = case.key_for("live_one")
    live, ev = live_thread()
    try:
        case.registry.add(key, live)
        assert case.list_active() == [key]
        assert case.is_running(case.query_for("live_one")) is True
    finally:
        release(live, ev)


# ---------------------------------------------------------------------------
# Scenario 3: the list accessor reaps on read (mixed live + zombie)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _CASE_PARAMS, indirect=True)
def test_list_accessor_reaps_on_read(case: ReaperCase) -> None:
    """The list accessor returns only the live entry and drops the zombie."""
    alive_key = case.key_for("alive")
    zombie_key = case.key_for("zombie")
    live, ev = live_thread()
    try:
        case.registry.add(alive_key, live)
        case.registry.add(zombie_key, dead_thread())

        assert case.list_active() == [alive_key]
        assert case.registry.contains(zombie_key) is False
    finally:
        release(live, ev)


# ---------------------------------------------------------------------------
# Scenario 4: the membership accessor reaps on read and returns False for a
#             dead-thread entry (and the entry is pruned)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _CASE_PARAMS, indirect=True)
def test_membership_accessor_reaps_dead_entry(case: ReaperCase) -> None:
    """``is_*_running`` returns False for a dead-thread entry AND drops it."""
    key = case.key_for("zombie_member")
    case.registry.add(key, dead_thread())

    assert case.is_running(case.query_for("zombie_member")) is False
    assert case.registry.contains(key) is False


# ---------------------------------------------------------------------------
# Scenario 5: reaper handles an empty registry (no off-by-one)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _CASE_PARAMS, indirect=True)
def test_reaper_handles_empty_registry(case: ReaperCase) -> None:
    """Both accessors are safe no-ops on an empty registry."""
    assert case.list_active() == []
    assert case.is_running(case.query_for("never_added")) is False


# ---------------------------------------------------------------------------
# Scenario 6: mixed live/dead state — only the dead are pruned
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _CASE_PARAMS, indirect=True)
def test_reaper_handles_mixed_state(case: ReaperCase) -> None:
    """A registry with multiple live and dead entries reaps only the dead ones."""
    a_live = case.key_for("a_live")
    c_live = case.key_for("c_live")
    live1, ev1 = live_thread()
    live2, ev2 = live_thread()
    try:
        case.registry.add(a_live, live1)
        case.registry.add(case.key_for("b_dead"), dead_thread())
        case.registry.add(c_live, live2)
        case.registry.add(case.key_for("d_dead"), dead_thread())

        assert set(case.list_active()) == {a_live, c_live}
        assert case.is_running(case.query_for("a_live")) is True
        assert case.is_running(case.query_for("c_live")) is True
        assert case.is_running(case.query_for("b_dead")) is False
        assert case.is_running(case.query_for("d_dead")) is False
    finally:
        release(live1, ev1)
        release(live2, ev2)


# ---------------------------------------------------------------------------
# Scenario 7 (substring services only): the substring-match membership
# accessor still works after reaping. background_approval_service stores
# ``approval_{order_id}_{workflow_step_id}`` and queries by the order_id
# substring (``any(order_id in tid for tid in reg)`` semantics).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _SUBSTRING_PARAMS, indirect=True)
def test_substring_accessor_matches_live_and_reaps_dead(case: ReaperCase) -> None:
    """Substring membership: matches a live composite key, False after a dead one is reaped."""
    assert case.substring is True  # guards the param filter — only substring cases reach here

    live_key = case.key_for("order42")  # approval_order42_step1
    live, ev = live_thread()
    try:
        case.registry.add(live_key, live)

        # Substring of a live composite key matches.
        assert case.is_running("order42") is True
        # A different order id does not match.
        assert case.is_running("order99") is False

        # Add a dead composite key for a third order; substring query reaps it.
        dead_key = case.key_for("order77")  # approval_order77_step1
        case.registry.add(dead_key, dead_thread())
        assert case.is_running("order77") is False
        assert case.registry.contains(dead_key) is False

        # The live entry is still matched and still present.
        assert case.is_running("order42") is True
        assert case.registry.contains(live_key) is True
    finally:
        release(live, ev)
