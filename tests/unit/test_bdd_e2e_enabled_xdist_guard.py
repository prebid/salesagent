"""Regression test for the BDD_E2E_ENABLED + xdist collection guard.

Guards PR #1420 review finding #5: when BDD_E2E_ENABLED=true is run under
pytest-xdist (-n auto / >0), the e2e_rest transport is silently dropped at
collection (the worker's pytest_generate_tests never appends it) and the bdd
suite goes green having never exercised the 5th transport. The ctx fixture's
hard-error can't catch this — collection never happens. pytest_configure must
turn the silent drop into a hard error.

Drives the REAL pytest_configure via a minimal stub config (it reads only
config.option.numprocesses and calls config.addinivalue_line).
"""

from types import SimpleNamespace

import pytest

from tests.bdd.conftest import pytest_configure


def _config(numprocesses):
    return SimpleNamespace(
        option=SimpleNamespace(numprocesses=numprocesses),
        addinivalue_line=lambda *a, **k: None,
    )


# xdist resolves "auto"/"logical" to a concrete int (or None) before
# pytest_configure runs, so the real shapes reaching the guard are int>=0 and
# None. 1 (a single distributed worker) and 4 cover the positive-int failure mode.
@pytest.mark.parametrize("numprocesses", [1, 4])
def test_e2e_enabled_under_xdist_raises(monkeypatch, numprocesses):
    # Shared-server case: no per-worker isolation, so the guard MUST raise. Isolate
    # E2E_PER_WORKER explicitly — the in-network box exports it globally (Phase B),
    # which would otherwise leak into this unit test and suppress the guard.
    monkeypatch.delenv("E2E_PER_WORKER", raising=False)
    monkeypatch.setenv("BDD_E2E_ENABLED", "true")
    with pytest.raises(pytest.UsageError, match="BDD_XDIST_N=0"):
        pytest_configure(_config(numprocesses))


@pytest.mark.parametrize("numprocesses", [1, 4])
def test_e2e_enabled_under_xdist_with_per_worker_is_allowed(monkeypatch, numprocesses):
    # Phase B escape: with E2E_PER_WORKER=1 each xdist worker targets its OWN
    # server + DB, so e2e_rest CAN run in parallel — the guard must NOT raise.
    monkeypatch.setenv("BDD_E2E_ENABLED", "true")
    monkeypatch.setenv("E2E_PER_WORKER", "1")
    pytest_configure(_config(numprocesses))  # must not raise


@pytest.mark.parametrize("numprocesses", [0, None])
def test_e2e_enabled_serial_is_allowed(monkeypatch, numprocesses):
    monkeypatch.delenv("E2E_PER_WORKER", raising=False)
    monkeypatch.setenv("BDD_E2E_ENABLED", "true")
    pytest_configure(_config(numprocesses))  # must not raise


@pytest.mark.parametrize("numprocesses", [1, 4])
def test_xdist_without_e2e_enabled_is_allowed(monkeypatch, numprocesses):
    monkeypatch.delenv("BDD_E2E_ENABLED", raising=False)
    pytest_configure(_config(numprocesses))  # must not raise
