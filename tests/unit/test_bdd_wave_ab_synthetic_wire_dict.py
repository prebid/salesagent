"""Synthetic wire-dict proof for 5 sandbox/array oracle functions.

A dormant oracle (no live, non-xfailed BDD scenario reaches it) that reads
wire_dict(ctx)/wire_field(ctx, ...) instead of the typed payload still needs proof it is
correct: a module/BDD run is not proof for a dormant item, since it is green whether the
implementation is right or wrong. This module is that proof, for all 5 targets (one,
``then_response_contains_array``, is additionally live and BDD-verifiable; the other 4 are
dormant):

  1. ``then_response_contains_array``   (generic/then_success.py)        — live
  2. ``then_response_has_sandbox``      (domain/uc003_update_media_buy.py) — dormant
  3. ``_get_packages``                  (domain/uc026_package_media_buy.py) — dormant
  4. ``then_no_real_orders_created``    (generic/then_success.py)        — dormant
  5. ``then_no_real_api_calls``         (generic/then_success.py, part 3 only) — dormant

Each function reads ``wire_dict(ctx)``/``wire_field(ctx, ...)`` (``tests/bdd/steps/_outcome_helpers.py``),
never the harness-reconstructed TYPED payload (``ctx["response"]``). Every test below constructs
a hand-built ``ctx["wire_response"]`` dict shaped like the real wire body plus a real-wire
``ctx["transport"]`` (``Transport.REST``, satisfying ``wire_dict``/``wire_field``'s loud
"wire_response missing" guard) and asserts the function's behavior against it.

Two test shapes (see each docstring for which applies):

- **"wire-only" tests**: omit ``ctx["response"]`` entirely. The function must succeed reading
  only ``wire_response`` — proving it never requires the typed payload.
- **"coercion/staleness divergence" tests**: populate BOTH ``ctx["response"]`` (a typed value
  that looks fine) AND ``ctx["wire_response"]`` (a wire value that is wrong/stale/malformed in a
  way the function must catch or must prefer). A function that read the typed value instead
  would be fooled here — this is the exact coercion blind spot wire-reading oracles close (a
  bool serialized as the string ``"true"`` reconstructs to real ``True`` on the typed side and
  passes vacuously).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from tests.bdd.steps.domain.uc003_update_media_buy import then_response_has_sandbox
from tests.bdd.steps.domain.uc026_package_media_buy import _get_packages
from tests.bdd.steps.generic.then_success import (
    then_no_real_api_calls,
    then_no_real_orders_created,
    then_response_contains_array,
)
from tests.harness.transport import Transport


class _StubResponseWithSandbox(BaseModel):
    """Minimal real Pydantic model — satisfies then_response_has_sandbox's isinstance(resp,
    BaseModel) guard while carrying a controllable typed ``sandbox`` value."""

    sandbox: bool


class _StubPackagesResponse:
    """Plain (non-Pydantic) object carrying a typed ``packages`` list — mirrors
    CreateMediaBuyResult/UpdateMediaBuySuccess's attribute shape without needing the real
    schema types."""

    def __init__(self, packages: list[dict]) -> None:
        self.packages = packages


class _StubOpaqueResponse:
    """A response object with no sandbox attribute at all — proves the operation "ran" for
    then_no_real_api_calls' unmigrated part 1/2 checks without pre-supplying a sandbox value
    the migrated part 3 could accidentally read."""


class _FakeEnv:
    """Minimal harness-env double for then_no_real_api_calls' unmigrated parts 1/2:
    non-empty EXTERNAL_PATCHES plus a mock that was called."""

    EXTERNAL_PATCHES = {"adapter": "src.adapters.mock.MockAdapter"}

    def __init__(self) -> None:
        self.mock = {"adapter": SimpleNamespace(called=True)}


# ═══════════════════════════════════════════════════════════════════════
# 1. then_response_contains_array (generic/then_success.py) — Wave A
# ═══════════════════════════════════════════════════════════════════════


def test_response_contains_array_reads_wire_only() -> None:
    """Migrated: succeeds reading wire_dict(ctx).get(field) with no ctx['response'] at all.

    Today: raises because the function requires ctx['response'] (getattr(resp, field, None)).
    """
    ctx = {"wire_response": {"widgets": ["a", "b"]}, "transport": Transport.REST}
    then_response_contains_array(ctx, field="widgets")


def test_response_contains_array_catches_non_list_on_wire_despite_typed_list() -> None:
    """Coercion divergence: typed resp.widgets is a valid list, but the real wire carries a
    non-list value for the same field. The migrated function must fail on the WIRE value.

    Today: reads the typed resp.widgets (a real list) and passes — vacuously ignoring that the
    wire itself is malformed. Wrapping this in pytest.raises makes the CURRENT run fail (no
    exception is raised today), which is the honest TDD-red signal for this case.
    """
    ctx = {
        "response": SimpleNamespace(widgets=["a"]),
        "wire_response": {"widgets": "not-actually-a-list"},
        "transport": Transport.REST,
    }
    with pytest.raises(AssertionError):
        then_response_contains_array(ctx, field="widgets")


# ═══════════════════════════════════════════════════════════════════════
# 2. then_response_has_sandbox (domain/uc003_update_media_buy.py) — Wave B, dormant
# ═══════════════════════════════════════════════════════════════════════


def test_response_has_sandbox_reads_wire_only() -> None:
    """Migrated: succeeds reading wire_dict(ctx) with 'sandbox' present as a bool, no
    ctx['response'] at all (the isinstance(resp, BaseModel) guard is dropped per the plan).

    Today: raises because the function requires ctx['response'] to be present and a BaseModel.
    """
    ctx = {"wire_response": {"sandbox": True}, "transport": Transport.REST}
    then_response_has_sandbox(ctx)


def test_response_has_sandbox_catches_non_bool_on_wire_despite_typed_bool() -> None:
    """Coercion divergence: typed resp.sandbox is a real bool True, but the wire carries the
    string "true" for the same field — the migrated presence+type check must fail on it.

    Today: getattr(resp, "sandbox", None) reads the typed True and passes; the malformed wire is
    never inspected. pytest.raises fails today (no exception raised) — the honest red signal.
    """
    ctx = {
        "response": _StubResponseWithSandbox(sandbox=True),
        "wire_response": {"sandbox": "true"},
        "transport": Transport.REST,
    }
    with pytest.raises(AssertionError):
        then_response_has_sandbox(ctx)


def test_response_has_sandbox_catches_missing_key_on_wire_despite_typed_value() -> None:
    """Presence divergence: typed resp.sandbox is True, but the wire OMITS the key entirely —
    the migrated function must fail (step text claims the envelope includes it unconditionally).

    Today: getattr(resp, "sandbox", None) reads the typed True and passes, oblivious to the
    wire's omission. pytest.raises fails today (no exception raised) — the honest red signal.
    """
    ctx = {
        "response": _StubResponseWithSandbox(sandbox=True),
        "wire_response": {},
        "transport": Transport.REST,
    }
    with pytest.raises(AssertionError):
        then_response_has_sandbox(ctx)


# ═══════════════════════════════════════════════════════════════════════
# 3. _get_packages (domain/uc026_package_media_buy.py) — Wave B, dormant
# ═══════════════════════════════════════════════════════════════════════


def test_get_packages_reads_wire_only_via_packages_key() -> None:
    """Migrated: wire_dict(ctx).get("packages") with no ctx['response'] at all.

    Today: raises because the function requires ctx['response'] to be present.
    """
    ctx = {"wire_response": {"packages": [{"product_id": "p1"}]}, "transport": Transport.REST}
    assert _get_packages(ctx) == [{"product_id": "p1"}]


def test_get_packages_reads_wire_only_via_affected_packages_fallback() -> None:
    """Migrated: falls back to wire_dict(ctx).get("affected_packages") when "packages" is
    absent from the wire (UpdateMediaBuySuccess shape), with no ctx['response'] at all.

    Today: raises because the function requires ctx['response'] to be present.
    """
    ctx = {
        "wire_response": {"affected_packages": [{"package_id": "pkg1"}]},
        "transport": Transport.REST,
    }
    assert _get_packages(ctx) == [{"package_id": "pkg1"}]


def test_get_packages_returns_empty_list_when_neither_key_present_on_wire() -> None:
    """Migrated: `wire_dict(ctx).get("packages") or wire_dict(ctx).get("affected_packages") or
    []` — neither key on the wire yields [], not a raise.

    Today: raises because the function requires ctx['response'] to be present (and would also
    raise "No packages in response" even if it were, since the current code hard-asserts
    packages is not None instead of defaulting to []).
    """
    ctx = {"wire_response": {}, "transport": Transport.REST}
    assert _get_packages(ctx) == []


def test_get_packages_prefers_wire_over_stale_typed_packages() -> None:
    """Staleness divergence: the typed resp.packages carries a DIFFERENT (stale/reconstructed)
    package list than what the real wire carries. The migrated function must return the WIRE
    list, not the typed one.

    Today: `inner = getattr(resp, "response", resp)` falls through to resp itself (no nested
    .response attribute on the stub), then `getattr(inner, "packages", None)` returns the STALE
    typed list — a direct value mismatch against what should have been read from the wire.
    """
    ctx = {
        "response": _StubPackagesResponse(packages=[{"product_id": "stale-typed"}]),
        "wire_response": {"packages": [{"product_id": "wire-fresh"}]},
        "transport": Transport.REST,
    }
    assert _get_packages(ctx) == [{"product_id": "wire-fresh"}]


# ═══════════════════════════════════════════════════════════════════════
# 4. then_no_real_orders_created (generic/then_success.py) — Wave B, dormant
# ═══════════════════════════════════════════════════════════════════════


def test_no_real_orders_created_reads_wire_only() -> None:
    """Migrated: wire_dict(ctx).get("sandbox") is True, with no ctx['response'] at all.

    Today: raises because the function requires ctx['response'] to be present.
    """
    ctx = {"wire_response": {"sandbox": True}, "transport": Transport.REST}
    then_no_real_orders_created(ctx)


def test_no_real_orders_created_catches_non_true_on_wire_despite_typed_true() -> None:
    """Coercion divergence: typed resp.sandbox is real bool True, but the wire carries the
    string "true" for the same field — the migrated "is True" check must fail on it.

    Today: getattr(resp, "sandbox", None) reads the typed True and passes. pytest.raises fails
    today (no exception raised) — the honest red signal.
    """
    ctx = {
        "response": SimpleNamespace(sandbox=True),
        "wire_response": {"sandbox": "true"},
        "transport": Transport.REST,
    }
    with pytest.raises(AssertionError):
        then_no_real_orders_created(ctx)


# ═══════════════════════════════════════════════════════════════════════
# 5. then_no_real_api_calls, part 3 only (generic/then_success.py) — Wave B, dormant
# ═══════════════════════════════════════════════════════════════════════
# Parts 1/2 (operation ran; harness external mocks were exercised) are unchanged and stay on
# ctx["response"]/ctx["env"] — only part 3's sandbox-flag read migrates to the wire.


def test_no_real_api_calls_part3_reads_wire_only() -> None:
    """Migrated part 3: wire_dict(ctx).get("sandbox") is True — with a response object that
    proves "the operation ran" for part 1 but carries NO sandbox attribute at all, so a pass
    here can only be explained by the migrated function reading the wire, not the typed payload.

    Today: getattr(resp, "sandbox", None) on the opaque stub returns None, so `None is True`
    fails — an uncaught exception, the honest TDD-red signal for the currently-unmigrated code.
    """
    ctx = {
        "response": _StubOpaqueResponse(),
        "wire_response": {"sandbox": True},
        "transport": Transport.REST,
        "env": _FakeEnv(),
    }
    then_no_real_api_calls(ctx)


def test_no_real_api_calls_part3_catches_non_true_on_wire_despite_typed_true() -> None:
    """Coercion divergence: typed resp.sandbox is real bool True, but the wire carries the
    string "true" for the same field — the migrated "is True" check must fail on it.

    Today: getattr(resp, "sandbox", None) reads the typed True, parts 1/2 both pass (env has
    active mocks), so the whole function passes. pytest.raises fails today (no exception
    raised) — the honest red signal.
    """
    ctx = {
        "response": SimpleNamespace(sandbox=True),
        "wire_response": {"sandbox": "true"},
        "transport": Transport.REST,
        "env": _FakeEnv(),
    }
    with pytest.raises(AssertionError):
        then_no_real_api_calls(ctx)
