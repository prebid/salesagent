"""The two ratchets too slow for the unit suite, graded by the full gate.

``mypy --check-untyped-defs`` over ``src/`` and ``pylint R0801`` over
``src/``+``tests/``+``scripts/`` are minutes each, against a unit suite that
runs in ~5m30s — putting them there would multiply the suite every executor
runs on every iteration. They run here instead, in the ``quality`` tox env,
which ``run_all_tests.sh`` executes alongside the other suites.

That does NOT make them free, and this docstring used to say it did. ``quality``
has been the longest suite in every full run since 2026-09-09 and the run's
suite span equals it, so whatever this env costs, the gate costs. Each counter
is measured once per process (``_MEASURED`` in ``tests/utils/ratchet_counters``)
because the two assertions below need the same number and measuring it twice
ran six pylint processes for three answers.

The registry and the assertions are shared with the fast half — see
``tests/utils/ratchet_counters.py``. This module only chooses WHERE they run.
"""

from __future__ import annotations

import pytest

from tests.utils.ratchet_counters import SLOW_RATCHETS, RatchetCounter, assert_not_over_baseline, assert_not_slack


@pytest.mark.slow
@pytest.mark.parametrize("ratchet", SLOW_RATCHETS, ids=lambda r: r.hook_id)
def test_slow_ratchet_is_at_or_below_its_committed_baseline(ratchet: RatchetCounter) -> None:
    assert_not_over_baseline(ratchet)


@pytest.mark.slow
@pytest.mark.parametrize("ratchet", SLOW_RATCHETS, ids=lambda r: r.hook_id)
def test_slow_ratchet_baseline_is_not_slack(ratchet: RatchetCounter) -> None:
    assert_not_slack(ratchet)
