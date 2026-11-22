"""One assertion for "this derived population is not empty", and why it needs one.

A guard that derives its population — from a file scan, a schema bundle, a regex
over a feature file, a subclass walk, a TOML table — has a failure mode a
hardcoded list does not: **the list goes stale loudly, but a derivation goes
stale silently by matching nothing.** "Matched nothing" and "found nothing
wrong" are both green.

WHEN THAT POPULATION FEEDS A ``@pytest.mark.parametrize`` IT IS WORSE THAN
VACUOUS: the cases do not fail, they CEASE TO EXIST, and pytest reports the
collapsed parametrize as a single SKIP. Measured (salesagent-8qela.176) by
emptying the derived asset vocabulary behind
``tests/unit/test_format_factory_refuses_unknown_asset.py``:

    control                           derived 21/21   47 passed, rc 0
    every discriminator stripped      derived  0/21   2 failed, 24 passed, 1 SKIPPED

42 of 47 items vanished. The exit code, the total and the FAILURE COUNT all
reported improvement — a lane reading pass/fail totals sees a better result.
Only two instruments see this at all: a per-nodeid delta (items removed) and
reconciling passed+failed+skipped+xfailed against collected (the skip count
rising). It is the same shape as a pass traded for a skip, with the trade
happening at COLLECTION rather than at execution.

So the remedy cannot be an ``assert population`` inside the parametrized test —
that assertion disappears along with the cases it was meant to protect. It has
to be ITS OWN test item, which is what this helper is called from.

``minimum`` is for the populations whose size is itself known: passing the count
you measured turns "not empty" into "did not silently shrink", and a derivation
that drops from 49 rows to 1 is as broken as one that drops to 0 while still
being non-empty.
"""

from __future__ import annotations

from collections.abc import Sized


def assert_derived_population(population: Sized, *, name: str, derived_from: str, minimum: int = 1) -> None:
    """Fail if *population* has fewer than *minimum* members.

    Args:
        population: the collection a ``parametrize`` (or a sweep) consumes.
        name: how the module spells it, so the failure names the binding.
        derived_from: what it is derived FROM — the thing to go and look at,
            because an empty derivation is nearly always a change THERE rather
            than a change in the guard.
        minimum: the floor. Defaults to 1 ("not empty"); pass the measured count
            to also catch a partial collapse.
    """
    actual = len(population)
    assert actual >= minimum, (
        f"{name} derived {actual} member(s), expected at least {minimum}. "
        f"It is derived from {derived_from}, so look there first: this is normally a change "
        f"at the source that the derivation can no longer see, not a change in the guard.\n"
        "This test exists as its own item on purpose. The checks that consume this population "
        "are parametrized over it, so an empty derivation does not fail them — it DELETES them, "
        "and pytest reports the collapsed parametrize as a single skip while the failure count "
        "goes down (salesagent-8qela.176). Do not relax this to make a run green; restore the "
        "population or delete the guard that no longer has one."
    )
