"""Guard: no exponential retry backoff computed outside the egress seam.

``src/core/security/outbound_http.py`` decides the retry schedule for every
outbound request: BR-RULE-029's 1s/2s/4s, each plus a ``uniform(0, 1)`` draw.
A call site that computes its own geometric delay is deciding that policy a
second time, and the two drift — which is exactly what happened before
GH #1802: one site slept 1/2/4, another skipped the 1s step entirely
and slept 2/4, and the seam itself slept 0.1/0.2/0.4 with no jitter at all. The
BDD step that was supposed to catch it only compared successive delays as a
ratio, so every one of those schedules passed.

This is the class-level counterpart to the egress import bans in
``ruff-egress.toml`` (TID251, run over ``src/`` by ``make quality-ci``).
Those bans stop a call site owning the *transport*; this one stops it owning
the *schedule*. They are separate diseases: a site can route through the seam
and still wrap it in its own retry loop (``src/core/utils/mcp_client.py`` does).

What counts as a violation: a ``sleep()`` whose duration is computed
geometrically. Three forms, because real code uses all three and a detector that
only read the inline one would report almost nothing:

- inline — ``time.sleep(2**attempt)``, ``time.sleep(min(5 * 2**attempt, 30))``
- via a local — ``backoff_time = 2**attempt`` then ``time.sleep(backoff_time)``
- via a helper in the same module — ``time.sleep(_backoff_seconds(attempt))``,
  which is the seam's own form (one hop only; a guard is not a call graph)

What does NOT count, deliberately:

- A constant or config-driven sleep: ``time.sleep(poll_interval)``,
  ``asyncio.sleep(SLEEP_INTERVAL_SECONDS)``, ``time.sleep(0.5)``. Polling a
  remote that is *working*, pacing an SSE stream and ticking a scheduler are not
  retry schedules, and there are two dozen of them in ``src/``.
- Exponentiation that never reaches a sleep. The power has to flow into the
  duration, by one of the three routes above.

``NON_HTTP_BACKOFF`` is a taxonomy, not a debt list: every remaining entry is
a geometric sleep that is genuinely not outbound HTTP (a business-state poll,
a non-HTTP protocol, a database connection retry), so the seam is not where
it belongs. It only shrinks — a site migrating onto the seam removes its
entry — and it does not grow by exempting a real violation.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_call_expressions,
    parse_module,
    repo_root,
    scan_src,
)

# The one module allowed to compute a retry schedule.
SEAM_FILE = "src/core/security/outbound_http.py"
EXEMPT_FILES = frozenset({SEAM_FILE})

# Sleep callables, however they are spelled: time.sleep, asyncio.sleep, a bare
# imported sleep, or an awaited one. iter_call_expressions matches both the bare
# name and the attribute form.
SLEEP_NAME = "sleep"

# Method names that, called as an ATTRIBUTE inside a sleep() argument, count as
# schedule-derived — same principle as _functions_returning_a_power, but for a
# call the detector's same-module ast.Name resolution cannot see.
# ``wait_seconds`` is Attempts.wait_seconds (src/core/security/egress/
# attempts.py, GH #1802): the seam itself now sleeps
# time.sleep(attempts.wait_seconds()), an ast.Attribute-shaped call, and
# WITHOUT this the seam's own exemption would exempt an already-clean file --
# see TestNoCallSiteBackoff.test_seam_module_would_otherwise_be_flagged, which
# exists precisely to catch that. Narrow and hardcoded on purpose: this is not
# a call graph, it names the one method whose result IS the schedule.
_SCHEDULE_METHOD_NAMES = frozenset({"wait_seconds"})

# Sites: (module_path, geometric_sleep_count). Not pre-existing debt with a
# removal ticket per entry — each is a taxonomy entry, kept visible because the
# detector's proxy ("geometric sleep anywhere in src/") cannot statically tell
# "not outbound HTTP" from the real thing; the comment on each entry below is
# what makes that call. The set only shrinks (a site migrating onto the seam
# removes its entry); it does not grow by exempting a real violation.
NON_HTTP_BACKOFF = {
    # All three entries below are geometric, but not outbound HTTP — so the
    # seam is not where they belong, and they stay listed with the reason in
    # writing rather than exempted by a path rule the next reader has to
    # reverse-engineer. (Every entry that WAS outbound HTTP has already
    # migrated onto the seam and been removed: GH #1802-.11,
    # GH #1802 migrated the counterparty-URL call sites;
    # GH #1802 deleted oauth_retry.py; GH #1802 moved
    # mcp_client onto the seam's sleep_backoff.)
    #
    # GAM forecasting readiness (NO_FORECAST_YET): a business-state poll.
    ("src/adapters/gam/managers/orders.py", 1),
    # GAM SOAP retries via the googleads client, which the seam does not carry.
    ("src/adapters/gam/utils/error_handler.py", 1),
    # PostgreSQL connection retry — not HTTP at all.
    ("src/core/database/database_session.py", 1),
}

FIX_HINT = (
    "Outbound retry backoff is decided once, in src/core/security/outbound_http.py "
    "(BR-RULE-029: 1s/2s/4s + jitter). Route the call through send/asend and let the "
    "seam schedule the retries instead of computing a delay here."
)


def _contains_power(node: ast.AST) -> bool:
    """True when the expression computes an exponentiation anywhere inside it.

    Walking the whole subtree — rather than matching ``BinOp(op=Pow)`` at the
    top — is what catches the forms that hide the power one layer down:
    ``base * 2**n``, ``min(5 * 2**attempt, 30)``, ``2**n + random.uniform(0, 1)``.
    """
    return any(isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Pow) for sub in ast.walk(node))


def _names_bound_to_a_power(tree: ast.AST) -> set[str]:
    """Names assigned an expression that contains an exponentiation.

    Almost no real backoff is written inline. Every one in this repo computes
    the delay first and sleeps the variable::

        backoff_time = 2**attempt
        time.sleep(backoff_time)

    A detector that only read the sleep argument would report none of them, so
    it would pass a codebase full of the disease. Binding is collected per
    module and order-insensitively: an assignment that reaches the sleep by any
    path still counts, and over-reporting here is the safe direction.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _contains_power(node.value):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign) and node.value is not None and _contains_power(node.value):
            targets = [node.target]
        else:
            continue
        for target in targets:
            bound.update(sub.id for sub in ast.walk(target) if isinstance(sub, ast.Name))
    return bound


def _functions_returning_a_power(tree: ast.AST) -> set[str]:
    """Names of functions in this module that compute an exponentiation, directly or via each other.

    ``time.sleep(_backoff_seconds(attempt))`` — the seam's original form — hides
    the schedule behind one helper; without resolving that, the seam itself would
    read as clean and the guard's own exemption would prove nothing.

    One hop is not enough, and the seam proved it: adding Retry-After support made
    the chain ``sleep -> _wait_seconds -> _backoff_seconds``, two hops, and a
    one-hop detector went quietly blind to the module it exists to watch. So this
    closes over same-module calls to a fixpoint. Still bounded — names in THIS
    module only, no imports followed, no aliasing — because a guard that needed a
    real call graph would be a program analyser, not a test.
    """
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}
    powered = {name for name, node in functions.items() if _contains_power(node)}

    while True:
        grown = {
            name
            for name, node in functions.items()
            if name not in powered
            and any(isinstance(call.func, ast.Name) and call.func.id in powered for call in iter_call_expressions(node))
        }
        if not grown:
            return powered
        powered |= grown


def find_call_site_backoff_violations(tree: ast.Module) -> list[int]:
    """Line numbers of sleeps whose duration is computed geometrically."""
    power_names = _names_bound_to_a_power(tree)
    power_functions = _functions_returning_a_power(tree)

    violations: list[int] = []
    for node in iter_call_expressions(tree, name=SLEEP_NAME):
        duration = node.args[0] if node.args else None
        if duration is None:
            continue
        referenced = {sub.id for sub in ast.walk(duration) if isinstance(sub, ast.Name)}
        called = {
            sub.func.id for sub in ast.walk(duration) if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
        } | {
            sub.func.attr
            for sub in ast.walk(duration)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
        }
        if (
            _contains_power(duration)
            or (referenced & power_names)
            or (called & (power_functions | _SCHEDULE_METHOD_NAMES))
        ):
            violations.append(node.lineno)
    return violations


def _scan_src(exempt: frozenset[str] = EXEMPT_FILES) -> dict[str, list[int]]:
    """Every module in src/ with a geometric sleep, and which lines carry it.

    ``scan_src`` raises on an exemption that suppresses nothing, so ``exempt``
    is a live sanctioned set by construction. Callers wanting a count take
    ``len()``; the line list is strictly more than the count it replaced.
    """
    return scan_src(find_call_site_backoff_violations, exempt=exempt)


class TestNoCallSiteBackoff:
    """No module outside the seam computes its own exponential retry delay."""

    @pytest.mark.arch_guard
    def test_no_new_call_site_backoff(self):
        """The set of geometric sleeps in src/ matches the allowlist exactly.

        Fails on a new violation AND on a stale entry, so a migrated site must
        be removed from the list rather than left to rot.
        """
        assert_violations_match_allowlist(
            {(path, len(lines)) for path, lines in _scan_src().items()},
            NON_HTTP_BACKOFF,
            fix_hint=FIX_HINT,
        )

    @pytest.mark.arch_guard
    def test_allowlist_only_shrinks(self):
        """The allowlist is a ratchet: its size is pinned exactly, not just capped.

        A ``<=`` ceiling above the real count leaves slack a new call-site
        backoff could fill without tripping THIS assertion by name — only
        ``test_no_new_call_site_backoff``'s membership check would catch it.
        Update this number DOWNWARD when a site migrates. Raising it means a
        new call site grew its own schedule, which is the thing the guard
        exists to prevent.
        """
        assert len(NON_HTTP_BACKOFF) == 3, (
            f"NON_HTTP_BACKOFF is {len(NON_HTTP_BACKOFF)} entries, expected exactly 3 — a call-site backoff was "
            f"added or removed without updating this pin. {FIX_HINT}"
        )


class TestGuardDetector:
    """The guard's own correctness, on synthetic sources.

    A guard that cannot fail is not a guard. None of these touch real source
    files — they feed the detector known-bad and known-good snippets directly.
    """

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        """Every geometric form is reported, including the ones that bury the power."""
        assert_detector_catches_ast_snippets(
            find_call_site_backoff_violations,
            snippets={
                "bare power": "import time\ntime.sleep(2**attempt)\n",
                "spaced power": "import time\ntime.sleep(2 ** attempt)\n",
                "base times power": "import time\ntime.sleep(base_delay * (2**attempt))\n",
                "power plus jitter": ("import random\nimport time\ntime.sleep(2**attempt + random.uniform(0, 1))\n"),
                "capped power": "import time\ntime.sleep(min(5 * (2**attempt), 30))\n",
                "async power": "import asyncio\n\n\nasync def f(n):\n    await asyncio.sleep(2**n)\n",
                "async base times power": (
                    "import asyncio\n\n\nasync def f(d, n):\n    await asyncio.sleep(d * (2**n))\n"
                ),
                "bare imported sleep": "from time import sleep\nsleep(2**attempt)\n",
                "multiplier power": "import time\ntime.sleep(base * multiplier**attempt)\n",
                "attempts-machine method": "import time\ntime.sleep(attempts.wait_seconds())\n",
                "async attempts-machine method": (
                    "import asyncio\n\n\nasync def f(attempts):\n    await asyncio.sleep(attempts.wait_seconds())\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("constant poll", "import time\ntime.sleep(0.5)\n"),
            ("named interval", "import time\ntime.sleep(poll_interval)\n"),
            (
                "module constant tick",
                "import asyncio\n\n\nasync def f():\n    await asyncio.sleep(SLEEP_INTERVAL_SECONDS)\n",
            ),
            ("division, not exponentiation", "import time\ntime.sleep(delay_ms / 1000)\n"),
            ("linear multiple", "import time\ntime.sleep(attempt * 2)\n"),
            ("header-driven wait", "import asyncio\n\n\nasync def f(r):\n    await asyncio.sleep(retry_after)\n"),
            ("power outside a sleep", "x = 2**attempt\nprint(x)\n"),
            ("seam usage", "from src.core.security.outbound_http import send\n\nr = send('https://x/', json={})\n"),
            (
                "unrelated attribute method",
                "import time\ntime.sleep(r.retry_after_seconds())\n",
            ),
        ],
    )
    def test_detector_ignores_non_backoff(self, label, source):
        """A sleep that is not a geometric retry delay is not a violation."""
        assert find_call_site_backoff_violations(ast.parse(source)) == [], f"false positive on {label}"

    @pytest.mark.arch_guard
    def test_would_be_missed_by_a_text_scan(self):
        """The AST catches forms a ``2\\*\\*`` text grep would not.

        ``base * multiplier**attempt`` (the shape the deleted oauth_retry
        module used) and
        ``time.sleep(pow(2, attempt))`` contain no literal ``2**``. A regex
        anchored on the common spelling would pass both; the guard reads shape.
        """
        assert find_call_site_backoff_violations(ast.parse("import time\ntime.sleep(base * multiplier**attempt)\n"))
        assert find_call_site_backoff_violations(ast.parse("import time\ntime.sleep(2 * 3 ** (attempt - 1))\n")), (
            "spacing variant missed"
        )

    @pytest.mark.arch_guard
    def test_a_power_two_helpers_deep_is_still_found(self):
        """The chain the seam actually grew: sleep -> wrapper -> geometric helper.

        A one-hop detector passes this, which is how it would have gone blind to
        the seam when GH #1802 added Retry-After support.
        """
        source = (
            "import time\n\n\n"
            "def _backoff(n):\n    return 2**n\n\n\n"
            "def _wait(n, ra):\n    return max(_backoff(n), ra or 0)\n\n\n"
            "def go(n):\n    time.sleep(_wait(n, None))\n"
        )
        assert find_call_site_backoff_violations(ast.parse(source)), "two-hop power missed"

    @pytest.mark.arch_guard
    def test_seam_module_would_otherwise_be_flagged(self):
        """The seam is exempt by path, and is genuinely exempt — not merely clean.

        An exemption that excludes an already-clean file proves nothing. The seam
        really does compute a geometric delay; the scan skips it by path.
        """
        seam = repo_root() / SEAM_FILE
        assert find_call_site_backoff_violations(parse_module(seam)), (
            "seam no longer computes a geometric backoff — is the path stale?"
        )
        assert SEAM_FILE not in _scan_src()

    @pytest.mark.arch_guard
    def test_seam_is_the_only_exempt_path(self):
        """Exempting the seam changes exactly one file, and no other."""
        without_exemption = _scan_src(exempt=frozenset())
        difference = set(without_exemption) - set(_scan_src())
        assert difference == {SEAM_FILE}, f"unexpected exempt path(s): {sorted(difference - {SEAM_FILE})}"
