"""Guard: a BDD scenario no ``ENV_ROUTES`` row claims is REFUSED, never xfailed.

``tests/bdd/conftest.py``'s ``_harness_env`` resolves one env route per scenario. When
none matched it used to call ``pytest.xfail("No harness wired for ...")`` at FIXTURE
SETUP, before a single step ran -- the one routing mechanism in that file with no
baseline and no ledger, twenty lines from the dormancy converter that refuses a
scenario absent from ``dormant_scenarios.txt``.

What that bought was a ``scenarios()`` call that looked like binding a feature: add the
call, drop the row from ``UNBOUND_ALLOWLIST``, and every scenario in the file xfails
here at setup, so ``test_architecture_feature_file_bound.py`` goes green while the
feature grades nothing and appears in no ledger, no xfail census and no dormancy
baseline.

WHY A REFUSAL AND NOT A SHRINK-ONLY LEDGER. Because the population is empty. Measured
on run ``innet_091026_2116``, both BDD suites, each with ``collected == total`` and
exitcode 0: ZERO of 11569 scenario items reached that branch -- 8657 in
``bdd_inprocess``, 2912 in ``bdd_e2e``. Every bound-but-unwired scenario today is
claimed by a row that names itself. A baseline seeded with the empty set is exactly
this refusal, and a zero-entry file would only add something to grow.

WHICH IS ALSO WHY THIS FILE EXISTS. A mechanism with an empty population is
indistinguishable from one that stopped working -- it is green either way. So
``test_unrouted_scenario_is_refused`` drives the real fixture rather than
re-implementing its decision, and ``test_a_routed_scenario_still_gets_its_env`` is the
control: it proves this test can return the OTHER answer. Without that control, a
``resolve_env_route`` that returned None for everything would read as a pass here.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.bdd import conftest as bdd_conftest


class _FakeMarker:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeNode:
    """Just enough node for ``derive_marker_names``, which reads ``iter_markers()``."""

    def __init__(self, marker_names: set[str]) -> None:
        self._markers = [_FakeMarker(n) for n in marker_names]
        self.user_properties: list[tuple[str, Any]] = []

    def iter_markers(self) -> list[_FakeMarker]:
        return list(self._markers)


class _FakeRequest:
    def __init__(self, node: _FakeNode) -> None:
        self.node = node


def _drive_harness_env(marker_names: set[str]):
    """Run the REAL ``_harness_env`` body one step, with no DB.

    ``__wrapped__`` is the undecorated generator. An unrouted scenario is refused
    before ``_run_env_route`` is reached, so nothing here touches Postgres. ``ctx`` is
    a bare dict so ``ctx.get("e2e_config")`` is None and the e2e DB reset is skipped.
    """
    gen = bdd_conftest._harness_env.__wrapped__(_FakeRequest(_FakeNode(marker_names)), {})
    next(gen)
    return gen


def _outcome_of(marker_names: set[str]) -> BaseException | None:
    """Drive the fixture and hand back whatever it raised, or None.

    Catches ``BaseException`` ON PURPOSE. ``pytest.fail`` and ``pytest.xfail`` both
    raise ``OutcomeException``, which derives from ``BaseException``, not ``Exception``
    -- so ``pytest.raises(Exception)`` catches NEITHER. Worse, letting an ``XFailed``
    escape would mark this very test xfailed, i.e. the check would go QUIET at exactly
    the moment the hole reopened. Catching the base class and asserting on the type is
    what keeps a revert red here rather than silent.
    """
    try:
        _drive_harness_env(marker_names)
    except BaseException as exc:  # noqa: BLE001 - see docstring
        return exc
    return None


class TestUnroutedScenarioIsRefused:
    @pytest.mark.arch_guard
    def test_unrouted_scenario_is_refused(self) -> None:
        """A UC with no row FAILS. If this xfails instead, the hole is back."""
        # UC-020 is the worked case: build_creative, a creative-agent task this seller
        # does not serve, whose feature is unbound for that reason. If someone binds it
        # without an env route, this is the branch they must hit.
        outcome = _outcome_of({"T-UC-020-main-rest", "main-flow", "rest"})

        assert outcome is not None, (
            "An unrouted scenario must be refused, but the fixture returned an "
            "environment for UC-020, which no ENV_ROUTES row claims."
        )
        assert type(outcome).__name__ == "Failed", (
            "An unrouted scenario must FAIL, not xfail. Got "
            f"{type(outcome).__name__}: {outcome}. A pytest.xfail here is the "
            "unledgered setup-phase escape this guard exists to keep closed: it runs "
            "before any step, so the scenario grades nothing while reading green."
        )
        message = str(outcome)
        assert "UNROUTED SCENARIO" in message, f"refusal lost its name: {message}"
        assert "UC-020" in message, f"the refusal must name the UC so a reader knows what is unrouted: {message}"
        assert "T-UC-020-main-rest" in message, f"the refusal must carry the marker set that failed to route: {message}"

    @pytest.mark.arch_guard
    def test_a_routed_scenario_still_gets_its_env(self) -> None:
        """The control: a ROUTED marker set must NOT be refused.

        Without this, a ``resolve_env_route`` that returned None for every input --
        the exact way a hand-written marker set silently matches no row -- would still
        make the refusal test above pass, and this file would certify a broken
        instrument while reporting success.

        UC-005 is measured as routed, so driving it must get PAST the refusal. What it
        does next (ask for the Postgres this bare ``ctx`` has not got) is not the
        subject; the refusal needs no DB to raise, so anything other than the refusal
        is proof that a row claimed the scenario.
        """
        outcome = _outcome_of({"T-UC-005-partition-asset-types", "partition"})

        assert "UNROUTED SCENARIO" not in str(outcome), (
            "UC-005 has an ENV_ROUTES row and must not reach the refusal. Reaching it "
            "means routing is broken for everything, and the refusal test above is "
            f"passing vacuously. Got: {outcome}"
        )
