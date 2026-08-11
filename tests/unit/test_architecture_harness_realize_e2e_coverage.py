"""Structural guard: Given-step helper methods in tests/harness/ must be
``@realize_e2e``-wrapped (directly or by delegation) or explicitly allowlisted
(salesagent-689e sweep-verify).

Disease pattern this guards against: a harness env "Given-step" helper method
(``set_*``/``make_*``/``break_*``/``configure_*``) implemented as a plain
in-process ``unittest.mock`` setter with NO ``@realize_e2e`` dual-branch
dispatch — the established pattern in ``tests/harness/`` (``_realize.py``,
exemplified by ``DeliveryPollMixin`` and ``CreativeFormatsEnv``). Such a
method silently no-ops over e2e_rest instead of realizing the intent or
declaring it unrealizable, so a scenario that thinks it configured a fault
actually asserts against unconfigured server state.

Four escape hatches, each requiring a pinned, reasoned entry (ratchet like
``EXPECTED_LEDGER`` / ``EXPECTED_UNSUPPORTED_DECLARATIONS`` — adding OR
removing an entry requires updating this file in the same change and saying
why):

  * ``ALLOWLIST_DELEGATES_TO_REALIZE_E2E`` -- the public method builds data
    and hands off to a private ``@realize_e2e``-decorated sibling (the
    "build-then-realize" variant of the pattern, e.g.
    ``DeliveryPollMixin.set_adapter_response`` -> ``_realize_adapter_response``).
  * ``ALLOWLIST_ALWAYS_DB_WRITE`` -- the method writes through the real DB
    session unconditionally (``self._session``), so the same code path is
    already correct in both in-process and e2e mode; no branch needed.
  * ``ALLOWLIST_NOT_WIRE_DISPATCHED`` -- the env's call path never crosses
    the wire (TRANSPORT-BYPASS, documented at tests/bdd/conftest.py
    ``_production_db_pointed_at``) so an in-process mock is correct in every
    parametrized "transport" row.
  * ``ALLOWLIST_NOT_BDD_REACHABLE`` -- no BDD Given step calls this method at
    all (verified by grep against tests/bdd/steps/), so it's never dispatched
    over e2e and the pattern doesn't apply. Latent risk if a future Given
    step starts calling it — re-run the scan then.
  * ``ALLOWLIST_UNIT_ONLY_ENV`` -- the owning class is ``BaseTestEnv``-rooted
    (not ``IntegrationEnv``), so it never runs over e2e by construction.

Methods decorated with ``@realize_e2e(e2e_unsupported(...))`` are the honest
declaration itself and are excluded from this guard entirely (not an escape
hatch — see ``_is_realize_e2e_decorated``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HARNESS_DIR = _REPO_ROOT / "tests" / "harness"

#: Given-step-helper method name shape. Deliberately excludes leading-
#: underscore names (``_realize_targeting_capabilities``, ``_persist_simulation_config``,
#: ``_validate_registry_formats``) -- those are the private ``e2e_impl`` realization
#: helpers passed to ``@realize_e2e(...)``, not the public Given-step surface BDD
#: steps call. A double-underscore or capitalized dodge (``set__channels``,
#: ``SetChannels``) is still caught: the prefix match only requires the verb +
#: underscore, not a specific following-character shape.
_GIVEN_STEP_METHOD_RE = re.compile(r"^(set|make|break|configure|force|simulate|inject|disable|enable|fail)_")

ALLOWLIST_DELEGATES_TO_REALIZE_E2E: frozenset[tuple[str, str]] = frozenset(
    {
        # Builds an AdapterGetMediaBuyDeliveryResponse then calls the
        # @realize_e2e-decorated _realize_adapter_response (_mixins.py:201).
        ("tests/harness/_mixins.py", "set_adapter_response"),
    }
)

ALLOWLIST_ALWAYS_DB_WRITE: frozenset[tuple[str, str]] = frozenset(
    {
        # configure_tenant_field (_base.py) writes through self._session
        # unconditionally -- the same code path is already correct in both
        # in-process and e2e mode (session is rebound to the live server DB
        # over e2e). account_sync.py's setters delegate to it.
        ("tests/harness/_base.py", "configure_tenant_field"),
        ("tests/harness/account_sync.py", "set_billing_policy"),
        ("tests/harness/account_sync.py", "set_approval_mode"),
        # CircuitBreakerEnv.make_webhook_config/set_db_webhooks persist
        # PushNotificationConfig rows via self._session unconditionally --
        # same shape.
        ("tests/harness/delivery_circuit_breaker.py", "make_webhook_config"),
        ("tests/harness/delivery_circuit_breaker.py", "set_db_webhooks"),
    }
)

ALLOWLIST_NOT_WIRE_DISPATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        # WebhookMixin / CircuitBreakerMixin: WebhookEnv.call_deliver /
        # CircuitBreakerEnv.call_send+call_deliver invoke production functions
        # (deliver_webhook_with_retry, WebhookDeliveryService methods) DIRECTLY
        # in-process, never through call_via/dispatch_request -- confirmed via
        # tests/bdd/conftest.py's "_production_db_pointed_at" docstring, which
        # documents this as the accommodated "TRANSPORT-BYPASS Given calling an
        # _impl" case. The mock takes effect identically in every parametrized
        # "transport" row because the call itself never leaves the process, so
        # @realize_e2e would be an inert no-op wrapper (salesagent-689e scan).
        ("tests/harness/_mixins.py", "set_http_status"),
        ("tests/harness/_mixins.py", "set_http_sequence"),
        ("tests/harness/_mixins.py", "set_http_error"),
        ("tests/harness/_mixins.py", "set_url_invalid"),
        # set_url_valid is set_url_invalid's mirror (same in-process SSRF mock
        # knob, opposite value; added by #1697) -- same never-leaves-the-process
        # classification as the rest of this bucket.
        ("tests/harness/_mixins.py", "set_url_valid"),
        ("tests/harness/_mixins.py", "set_http_response"),
    }
)

ALLOWLIST_NOT_BDD_REACHABLE: frozenset[tuple[str, str]] = frozenset(
    {
        # ProductMixin setters: zero call sites in tests/bdd/steps/ (verified
        # by grep) -- only used by tests/harness/test_harness_product.py and
        # tests/integration/*, which never run over e2e. Latent risk only if
        # a future BDD Given step starts calling one of these.
        ("tests/harness/_mixins.py", "set_policy_approved"),
        ("tests/harness/_mixins.py", "set_policy_blocked"),
        ("tests/harness/_mixins.py", "set_dynamic_variants"),
        ("tests/harness/_mixins.py", "set_property_list"),
        ("tests/harness/_mixins.py", "set_ranking_disabled"),
    }
)

ALLOWLIST_UNIT_ONLY_ENV: frozenset[tuple[str, str]] = frozenset(
    {
        # BaseTestEnv-rooted (not IntegrationEnv) -- never runs over e2e by
        # construction (tests/CLAUDE.md env-hierarchy table: "Unit" mode).
        ("tests/harness/delivery_circuit_breaker_unit.py", "make_webhook_config"),
        ("tests/harness/delivery_circuit_breaker_unit.py", "set_db_webhooks"),
        ("tests/harness/delivery_poll_unit.py", "set_pricing_options"),
        ("tests/harness/media_buy_update.py", "set_currency_limit"),
        ("tests/harness/media_buy_update.py", "set_media_buy"),
    }
)

#: Genuine disease instances NOT yet fixed -- tracked by a filed follow-up
#: ticket, not silently exempted. Removing an entry requires the ticket to be
#: closed AND the method to gain @realize_e2e in the same change.
ALLOWLIST_DEFERRED: frozenset[tuple[str, str, str]] = frozenset(
    {
        # UC-006 domain, out of salesagent-689e's UC-010 capabilities scope.
        # setup_generative_build is NOT in this allowlist: it doesn't match
        # _GIVEN_STEP_METHOD_RE (name is "setup_", not "set_"-prefixed), so
        # this guard doesn't detect it at all -- both methods are tracked in FIXME(#1887).
        ("tests/harness/creative_sync.py", "set_run_async_result", "FIXME(#1887)"),
    }
)


def _is_realize_e2e_decorated(node: ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "realize_e2e":
            return True
    return False


def _find_unwrapped_given_step_methods(tree: ast.Module) -> list[tuple[str, int]]:
    """Return (method_name, lineno) for every plain Given-step-shaped method."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _GIVEN_STEP_METHOD_RE.match(item.name):
                continue
            if _is_realize_e2e_decorated(item):
                continue
            found.append((item.name, item.lineno))
    return found


def _harness_unwrapped_sites() -> list[tuple[str, str, int]]:
    sites: list[tuple[str, str, int]] = []
    for path in sorted(_HARNESS_DIR.glob("*.py")):
        if path.name.startswith("test_") or path.name == "_realize.py":
            continue
        relpath = f"tests/harness/{path.name}"
        for name, lineno in _find_unwrapped_given_step_methods(ast.parse(path.read_text())):
            sites.append((relpath, name, lineno))
    return sites


def _all_pinned_pairs() -> set[tuple[str, str]]:
    return (
        set(ALLOWLIST_DELEGATES_TO_REALIZE_E2E)
        | set(ALLOWLIST_ALWAYS_DB_WRITE)
        | set(ALLOWLIST_NOT_WIRE_DISPATCHED)
        | set(ALLOWLIST_NOT_BDD_REACHABLE)
        | set(ALLOWLIST_UNIT_ONLY_ENV)
        | {(relpath, name) for relpath, name, _ticket in ALLOWLIST_DEFERRED}
    )


def test_unwrapped_given_step_methods_are_pinned() -> None:
    """Every plain (non-realize_e2e) Given-step helper is allowlisted or deferred.

    A NEW harness Given-step helper method that is neither @realize_e2e-wrapped
    nor listed in one of the allowlist buckets fails here -- the disease this
    guard exists to prevent (salesagent-689e).
    """
    pinned = _all_pinned_pairs()
    actual = {(relpath, name) for relpath, name, _lineno in _harness_unwrapped_sites()}

    assert_violations_match_allowlist(
        actual,
        pinned,
        fix_hint=(
            "Wrap the method in @realize_e2e(...) (see tests/harness/creative_formats.py "
            "for the reference pattern), or add a reasoned entry to the appropriate "
            "ALLOWLIST_* bucket in this file (see module docstring)."
        ),
    )


def test_positive_unwrapped_method_is_detected() -> None:
    """Meta-test: a plain Given-step method with no @realize_e2e is caught."""
    src = """
class FooEnv:
    def set_something(self, x):
        self.mock["x"] = x
"""
    found = _find_unwrapped_given_step_methods(ast.parse(src))
    assert found == [("set_something", 3)]


def test_negative_realize_e2e_wrapped_method_is_not_flagged() -> None:
    """Meta-test: an @realize_e2e-decorated method is correctly excluded."""
    src = """
class FooEnv:
    @realize_e2e(_realize_something)
    def set_something(self, x):
        self.mock["x"] = x
"""
    found = _find_unwrapped_given_step_methods(ast.parse(src))
    assert found == []


def test_regex_slip_leading_underscore_helper_is_not_a_given_step() -> None:
    """Meta-test: private e2e_impl realization helpers are intentionally excluded.

    ``_realize_something`` (leading underscore) is the e2e_impl argument passed
    to @realize_e2e on a sibling public method, not a Given-step surface BDD
    steps call directly -- it must NOT be flagged even though it starts with a
    verb the pattern otherwise matches (it doesn't, because of the leading
    underscore, which is the point of this test: prove that's deliberate, not
    an oversight that also happens to miss a real disease instance).
    """
    src = """
class FooEnv:
    def _realize_targeting_capabilities(self, **dims):
        set_adapter_test_behavior(self, self._tenant_id, targeting_capabilities=dims)

    @realize_e2e(_realize_targeting_capabilities)
    def set_targeting_capabilities(self, **dims):
        self._adapter_mock.get_targeting_capabilities.return_value = dims
"""
    found = _find_unwrapped_given_step_methods(ast.parse(src))
    assert found == []


def test_regex_slip_double_underscore_and_capitalized_dodges_are_still_caught() -> None:
    """Meta-test: naming dodges around the verb prefix don't escape the guard.

    A method named with a double underscore after the verb (``set__channels``)
    or a capitalized verb (``Set_channels``) must still be caught if it has no
    @realize_e2e -- the prefix regex must not be so strict that trivial
    variants slip through.
    """
    src = """
class FooEnv:
    def set__channels(self, channels):
        self.mock["x"] = channels

    def make_thing_unavailable(self, x):
        self.mock["y"] = x
"""
    found = {name for name, _lineno in _find_unwrapped_given_step_methods(ast.parse(src))}
    assert found == {"set__channels", "make_thing_unavailable"}
