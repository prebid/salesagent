"""The pinned contents of the storyboard-conformance known-failures ledger.

``tests/storyboard/known_failures.txt`` is pinned in exactly one place, and two
suites grade against that pin: ``tests/unit/test_storyboard_ledger_state.py``
(the lock test — the ledger file must equal the pin) and
``tests/integration/test_storyboard_ledger_fitness_real_session.py`` (the
fitness function — its three cases are vacuous unless the ledger it drives a
real session with is the pinned one).

The pin used to live in the unit lock test and be imported out of it, which made
a module whose job is to BE a test double as a helper library — the disease
``tests/unit/test_architecture_no_cross_test_module_imports.py`` forbids:
renaming or splitting the lock test would break an unrelated suite, and the
breakage would surface as a collection error in a file nobody touched. Same fix
shape, and same home rationale, as ``tests/unit/_run_all_tests_helpers.py``,
except that these two consumers sit in DIFFERENT suites (unit + integration), so
a suite-local ``_*_helpers.py`` would just move the cross-suite reach rather
than remove it. ``tests/helpers/**`` is the cross-suite home, alongside
``tests/helpers/ledger.py`` — the shared parser both consumers already use.

RE-SEEDING is a standing rule, not a one-off: whenever a run seeds or retires
entries, update ``tests/storyboard/known_failures.txt`` AND ``EXPECTED_LEDGER``
below in the same change. A removed entry that creeps back is a graduation
regression; a genuine-gap entry deleted without landing the underlying fix is a
silent gap-hiding regression.
"""

from __future__ import annotations

from pathlib import Path

#: Repo root, computed once from this module's path.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The ledger file whose exact contents ``EXPECTED_LEDGER`` pins.
LEDGER_PATH = REPO_ROOT / "tests" / "storyboard" / "known_failures.txt"

# --- ledger pin ---
# Seeded from ONE measured in-network run, test-results/innet_160926_1917, which scored
# passed=30 failed=21 on both protocols with zero disparity. 8 mcp + 8 a2a, every failing
# check failing on both surfaces -- so an asymmetric pair appearing here is an mcp-only fix
# that left its a2a twin behind, which is the drift the per-protocol split exists to expose.
#
# The ledger was EMPTY before this, deliberately, to show the whole gap surface at once. That
# surface has been seen; the entries below are it. What the reversal costs is written at the
# top of tests/storyboard/known_failures.txt: conftest xfails these with strict=False, so a
# regression inside a ledgered check no longer reddens the job.
#
# GRADUATIONS. 30 of the original 46 entries were removed on 2026-09-20 against
# test-results/innet_200926_0647 (passed=99 failed=16 on both protocols), after seeding the
# storyboard tenant's account portfolio, its signing key and the product the corrected
# vectors name turned them into passes or into runner-reported skips. The fitness function
# names entries that grade nothing; that list is the only authority for removing one.
#
# GROWTH, once, on 2026-09-21 against test-results/innet_210926_1016: +16 entries, 8 checks
# on each protocol. The same seeding that graduated 30 entries let the corpus REACH eight
# checks that had never executed, so eight pre-existing production defects became measurable
# for the first time -- the gap did not widen, the measurement did. Each carries a GitHub
# issue in the ledger file, which is where the exception is argued; this pin only records
# that it happened, so a reader who meets 32 entries after a header saying "only shrinks"
# is not left guessing.
#
# Re-seed both files in the same change, always, and only from a measured run.
#
# MERGE NOTE: the 46 entries and the provenance above are #1721's (77b34c1ea) -- that
# machinery is theirs and it is measured. The HOME is ours: this pin lives in
# ``tests/helpers/`` rather than inside ``tests/unit/test_storyboard_ledger_state.py``
# because the storyboard fitness function
# (``tests/integration/test_storyboard_ledger_fitness_real_session.py``) grades against the
# same pin, and a module whose job is to BE a test must not double as a helper library --
# ``test_architecture_no_cross_test_module_imports.py`` fails on that shape. Neither side
# subsumes the other, so the merge keeps both: their data, our location.
#: SHRANK BY SIX AT THE 3.2.1 PIN. The three steps those rows graded --
#: ``creative_reception::list_formats``, ``inventory_list_no_match::create_buy_no_match``
#: and ``measurement_terms_rejected::create_media_buy_relaxed_terms``, two protocols each
#: -- are not produced by the 3.2.1 tree at all, so a known-failure row for them is an
#: exemption with no subject. Removed here and from the ledger file in the same change, as
#: this module's docstring requires. The remaining rows are still seeded from the
#: 3.1.1-era run the ledger header names and have NOT been re-measured against 3.2.1.
EXPECTED_LEDGER: frozenset[str] = frozenset()
