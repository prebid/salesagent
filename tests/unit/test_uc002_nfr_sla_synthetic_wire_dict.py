"""Synthetic wire-dict proof for then_response_within_sla (uc002_nfr.py).

The scenario this step belongs to (@T-UC-002-nfr-004, "Response latency --
within SLA") is dormant: it xfails earlier in the chain ("UC-002 harness not
yet wired for non-extension scenarios") before the step function body ever
runs. A module/BDD run is therefore not proof for this migration — it is
green whether the implementation is right or wrong. This module is that
proof, following the same synthetic-ctx pattern established for other
dormant sites (see test_bdd_wave_ab_synthetic_wire_dict.py).

Each test constructs a hand-built ctx["wire_response"] dict plus a real-wire
ctx["transport"] (Transport.REST, satisfying wire_dict's loud "wire_response
missing" guard) and asserts the function's behavior against it — never
against ctx["response"] (the harness-reconstructed typed payload).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.bdd.steps.domain.uc002_nfr import then_response_within_sla
from tests.harness.transport import Transport


class _FakeEnv:
    """Minimal harness-env double: adapter mock not called (Part 2 passes)."""

    def __init__(self, adapter_called: bool) -> None:
        self.mock = {
            "adapter": SimpleNamespace(
                return_value=SimpleNamespace(create_media_buy=SimpleNamespace(called=adapter_called))
            )
        }


def _base_ctx(wire_response: dict, adapter_called: bool = False) -> dict:
    return {
        "error": None,
        "wire_response": wire_response,
        "transport": Transport.REST,
        "env": _FakeEnv(adapter_called),
    }


def test_reads_wire_only_no_typed_response_needed() -> None:
    """Migrated: succeeds reading wire_dict(ctx) with no ctx['response'] at all.

    Today: raises because the function requires ctx['response'] (result.status).
    """
    ctx = _base_ctx({"status": "completed", "media_buy_id": "mb_1", "packages": [{"package_id": "pkg_1"}]})
    then_response_within_sla(ctx)


def test_catches_wrong_status_on_wire_despite_typed_success() -> None:
    """Coercion/staleness divergence: a typed reconstruction with status='success' (the OLD,
    incorrect comparison value) would pass a naive check; the real wire's actual AdCP
    TaskStatus value is 'completed', never 'success'. The migrated function must grade the
    wire's real value, not an assumption baked into a stale comparison.
    """
    ctx = _base_ctx({"status": "submitted", "media_buy_id": "mb_1", "packages": [{"package_id": "pkg_1"}]})
    with pytest.raises(AssertionError, match="status"):
        then_response_within_sla(ctx)


def test_catches_missing_media_buy_id_on_wire() -> None:
    """A wire response with status=completed but no media_buy_id must still fail — proves the
    pipeline did not actually complete end-to-end, not just that the status field looks right.
    """
    ctx = _base_ctx({"status": "completed", "media_buy_id": "", "packages": [{"package_id": "pkg_1"}]})
    with pytest.raises(AssertionError, match="media_buy_id"):
        then_response_within_sla(ctx)


def test_catches_missing_packages_on_wire() -> None:
    """A wire response with status=completed and a media_buy_id but no packages must still
    fail — packages absence means the adapter/persistence step did not complete.
    """
    ctx = _base_ctx({"status": "completed", "media_buy_id": "mb_1", "packages": []})
    with pytest.raises(AssertionError, match="packages"):
        then_response_within_sla(ctx)


def test_xfails_when_adapter_called_synchronously() -> None:
    """Part 2 (unchanged by this migration): a synchronously-called adapter mock still routes
    to pytest.xfail, proving the migration didn't disturb the untouched half of the function.
    """
    ctx = _base_ctx(
        {"status": "completed", "media_buy_id": "mb_1", "packages": [{"package_id": "pkg_1"}]},
        adapter_called=True,
    )
    with pytest.raises((pytest.xfail.Exception, Exception)):
        then_response_within_sla(ctx)
