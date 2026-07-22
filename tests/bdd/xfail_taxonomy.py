"""Single source of truth for the BDD xfail-reason vocabulary.

Two very different consumers need the same strings:

* ``tests/bdd/conftest.py`` **writes** them — the ``pytest_runtest_makereport``
  hook composes ``report.wasxfail`` reasons, and ``_harness_env`` raises
  ``pytest.xfail(...)`` for scenarios no harness binds.
* ``scripts/check_dormant_scenarios.py`` **reads** them back out of pytest's
  ``-rxX`` output to tell a *dormant* scenario (nothing wires it — never runs
  anywhere) from a *documented* spec-production gap (deliberately xfailed).

Before this module the reader hand-copied the writer's literals, so rewording a
reason in conftest silently moved scenarios into the "documented gap (fine)"
bucket — the exact false-green #1603 exists to kill. Both sides now import from
here, and ``tests/unit/test_check_dormant_scenarios.py`` pins the classifier
against these builders, so a reworded reason breaks a test instead of quietly
under-reporting.

This module is deliberately a **leaf**: no pytest import, no conftest import, so
``scripts/`` can import it without dragging the whole BDD plugin tree in.
"""

from __future__ import annotations

# ── Reason prefixes, exactly as emitted ──────────────────────────────────────

#: ``pytest_bdd`` could not find a step definition — the scenario's Gherkin is
#: written but nothing binds it. Dormant.
STEP_DEFINITION_NOT_FOUND = "Step definition not found"

#: A harness/dispatcher raised ``NotImplementedError`` (e.g. the E2E_MCP /
#: E2E_A2A placeholder dispatchers). Dormant.
NOT_IMPLEMENTED = "Not implemented"

#: A mock-setup intent the live e2e stack has no surface for, declared at the
#: env method. NOT dormant: the in-process transports of the same scenario run.
E2E_UNSUPPORTED_SETUP = "impl-only setup declared in env"

#: ``_harness_env``'s catch-all: no harness branch exists for this use case.
#: The single most-cited dormant reason in the tree.
NO_HARNESS_WIRED = "No harness wired for"

#: Fragment shared by the per-UC "harness not yet wired ..." xfail sites, whose
#: full phrasing is bespoke per use case. Dormant.
NOT_YET_WIRED = "not yet wired"


# ── Builders: the exact strings conftest emits ───────────────────────────────


def step_definition_not_found(exc: object) -> str:
    """Reason for a ``StepDefinitionNotFoundError`` converted to xfail."""
    return f"{STEP_DEFINITION_NOT_FOUND}: {exc}"


def not_implemented(exc: object) -> str:
    """Reason for a ``NotImplementedError`` converted to xfail."""
    return f"{NOT_IMPLEMENTED}: {exc}"


def e2e_unsupported_setup(exc: object) -> str:
    """Reason for an ``E2EUnsupportedSetup`` converted to xfail (not dormant)."""
    return f"{E2E_UNSUPPORTED_SETUP}: {exc}"


def no_harness_wired(uc: object) -> str:
    """Reason for the ``_harness_env`` catch-all branch."""
    return f"{NO_HARNESS_WIRED} {uc}"


# ── Classification vocabulary ────────────────────────────────────────────────

#: Lowercased substrings that mean "dormant because nothing wires it", as
#: opposed to a documented spec-production gap. Matched case-insensitively
#: against the whole xfail reason.
#:
#: The two hook prefixes carry their trailing colon on purpose: dozens of
#: *documented* reasons in conftest read "... not implemented in production
#: (spec-production gap)", and a bare "not implemented" marker would swallow
#: every one of them into the dormant bucket. Only the hook's own
#: ``"Not implemented: <exc>"`` prefix has the colon.
DORMANT_REASON_MARKERS: tuple[str, ...] = (
    NO_HARNESS_WIRED.lower(),
    NOT_YET_WIRED.lower(),
    f"{STEP_DEFINITION_NOT_FOUND.lower()}:",
    f"{NOT_IMPLEMENTED.lower()}:",
)


def is_dormant_reason(reason: str) -> bool:
    """True when an xfail reason means "nothing wires this scenario"."""
    lowered = reason.lower()
    return any(marker in lowered for marker in DORMANT_REASON_MARKERS)


def scenario_name(nodeid: str) -> str:
    """Collapse a pytest nodeid to its scenario (function) name.

    Drops the module path and any parametrization, so the transports of one
    scenario count once. Outline ids nest brackets
    (``test_x[a2a-op-["operator"]-absent]``), hence a plain split on the FIRST
    ``[`` rather than a regex.
    """
    return nodeid.split("::")[-1].split("[", 1)[0]
