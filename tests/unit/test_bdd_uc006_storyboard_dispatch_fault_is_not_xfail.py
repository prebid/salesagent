"""Mutation grader: an injected 500 must FAIL the UC-006 storyboard scenarios, not xfail-pass.

The structural lock in ``test_architecture_bdd_no_inline_xfail.py`` proves the
inline-xfail SHAPE is gone. This module proves the BEHAVIOR that shape produced
is gone too: inject an operation-level fault into the exact ctx state a 500
leaves behind, drive every Then step of
``tests/bdd/steps/domain/uc006_storyboard_creative_sync.py`` through it, and
require each one to fail LOUDLY.

Why this is a real mutation and not a re-statement of the AST guard: the guard
looks at source shape; this looks at outcome. A step could stop calling
``pytest.xfail`` and still swallow the fault (e.g. by returning early when
``ctx["response"]`` is absent), which the guard cannot see and this catches.

Why it lives here rather than as a one-shot manual pytest run: a manual
mutation is executed once by a human and never again. This runs on every
``make quality``.

Scope, per the pre-implementation solution review:
this is NECESSARY BUT NOT SUFFICIENT. It cannot detect RELOCATION of the hatch
to a conftest tag route, an ``e2e_rest_known_failures.txt`` line, or another
step module — only the AST lock pins those. Keep both.

Ledger safety: neither ``@T-UC-006-storyboard-multi-format-sync`` nor
``@T-UC-006-storyboard-format-id-roundtrip-on-sync`` carries a ledger tag in
``_UC006_SPECGAP_XFAIL_TAGS`` (tests/bdd/conftest.py) or in
``tests/bdd/e2e_rest_known_failures.txt`` — verified, and asserted below — so a
ledger entry cannot absorb the injected failure and make this vacuous.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest
from _pytest.outcomes import Skipped, XFailed

from src.core.exceptions import AdCPInternalError
from tests.bdd.steps.domain import uc006_storyboard_creative_sync as steps
from tests.harness.wire_fixtures import wire_error_result
from tests.helpers.envelope_assertions import envelope_for

#: The scenario tags whose Then steps this module mutates. Both are deliberately
#: UN-ledgered: a ledgered tag would xfail the scenario before the injected fault
#: could be observed, and the mutation would prove nothing.
MUTATED_SCENARIO_TAGS = (
    "T-UC-006-storyboard-multi-format-sync",
    "T-UC-006-storyboard-format-id-roundtrip-on-sync",
)

#: A wire-faithful 500. ``sync_creatives`` returning this is exactly the class of
#: failure ``_response_or_xfail`` converted into a green "SPEC-PRODUCTION GAP".
#:
#: Constructed against the post-ADR-010 error API: ``AdCPSalesAgentError.__init__``
#: is keyword-only and takes no ``message``/``status_code``, because ``error_code``,
#: ``message``, ``recovery`` and ``status_code`` are read-only properties resolved
#: from ``CODE_TABLE`` per read. So the 500 is not asserted by the fixture — it is
#: DERIVED: ``AdCPInternalError`` IS ``AppErrorCode.INTERNAL_ERROR`` by class
#: identity, and the table classifies that code as status 500, recovery=transient.
#: The injected fault is the exception the seller would have caught, so it rides the
#: non-wire ``internal_detail`` slot as itself, never as a sentence.
_INJECTED_500 = AdCPInternalError(
    internal_detail=RuntimeError("injected fault: sync_creatives blew up inside the seller"),
)

#: The envelope a real boundary would have put on the wire for that exception —
#: produced by the production ``AdcpErrorResponse.of`` + ``to_wire`` pair rather than
#: hand-authored, so the mutated steps read a genuine two-layer
#: ``{"adcp_error": ..., "errors": [...]}`` body through ``locate_envelope_error``.
#: A hand-rolled shape would resolve to ``None`` there, and a step could then fail
#: for the wrong reason (missing error region) instead of for the injected 500 this
#: module is grading.
_INJECTED_500_ENVELOPE: dict[str, Any] = envelope_for(_INJECTED_500)


class _NoSessionEnv:
    """Stand-in for ctx['env'] — every attribute touch is a loud failure.

    A step that reaches the env after an unhandled 500 is reading state that a
    failed dispatch never produced; blowing up here is the correct outcome, not
    a false negative.
    """

    DEFAULT_AGENT_URL = "https://creative.example.com"

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"step reached env.{name} after an injected 500 — the dispatch never succeeded")


def _errored_ctx() -> dict[str, Any]:
    """The ctx a failed dispatch actually leaves behind.

    Mirrors ``_populate_ctx_from_result`` (tests/bdd/steps/generic/_dispatch.py)
    on the ``result.is_error`` branch: ``result`` and ``error`` are set, the
    detached envelope copies are NOT (steps read them off
    ``ctx['result'].error_envelope()``), and ``response`` is NEVER set. The
    Given-supplied keys are present because the Givens ran before the When.
    """
    result = wire_error_result(
        # The injected fault is a REST 500 whose body came back over HTTP — bytes
        # crossed the wire, which the fixture derives from the envelope being present.
        # No synthesized envelope: only the IMPL dispatcher, which has no wire, may
        # populate that field (tests/unit/test_harness_mcp_never_synthesizes.py).
        _INJECTED_500_ENVELOPE,
        error=_INJECTED_500,
    )
    return {
        "env": _NoSessionEnv(),
        "transport": "rest",
        "result": result,
        "error": _INJECTED_500,
        # Given-supplied state from the two mutated scenarios.
        "creatives": [{"creative_id": f"creative-bulk-{n}"} for n in ("display", "video", "native")],
        "captured_format_id": {"agent_url": "https://creative.example.com", "id": "display_300x250"},
    }


def _then_steps() -> list[tuple[str, Any]]:
    """Every Then step defined by the UC-006 storyboard step module."""
    found = [
        (name, obj)
        for name, obj in vars(steps).items()
        if name.startswith("then_") and inspect.isfunction(obj) and obj.__module__ == steps.__name__
    ]
    assert found, "no then_* steps found in uc006_storyboard_creative_sync — discovery is broken"
    return sorted(found)


def _placeholder_kwargs(step: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """Bind ctx plus a placeholder for each parser-captured string argument."""
    kwargs: dict[str, Any] = {}
    for name, param in inspect.signature(step).parameters.items():
        kwargs[name] = ctx if name == "ctx" else f"<{name}>"
        assert param.kind is not inspect.Parameter.VAR_KEYWORD, f"unexpected **kwargs on step {step.__name__}"
    return kwargs


def _classify(step: Any, ctx: dict[str, Any]) -> str | None:
    """Return a swallow description, or None when the step failed loudly (correct)."""
    try:
        step(**_placeholder_kwargs(step, ctx))
    except (XFailed, Skipped) as exc:
        return f"converted the 500 into {type(exc).__name__}: {exc}"
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 — any hard failure is the correct outcome
        return None
    return "returned green despite an injected 500"


class TestUc006StoryboardDispatchFaultIsNotXfail:
    """An operation-level fault is a FAILURE, never a registered "known gap"."""

    @pytest.mark.arch_guard
    def test_every_then_step_fails_loudly_on_an_injected_500(self):
        swallowed = {}
        for name, step in _then_steps():
            verdict = _classify(step, _errored_ctx())
            if verdict is not None:
                swallowed[name] = verdict

        assert not swallowed, (
            f"{len(swallowed)} of {len(_then_steps())} UC-006 storyboard Then step(s) swallowed an "
            "injected 500 instead of failing:\n"
            + "\n".join(f"  {name}: {verdict}" for name, verdict in sorted(swallowed.items()))
            + "\n\nA dispatch fault is not evidence that production has not implemented the graded "
            "behavior — it is evidence of SOMETHING, and the step cannot tell which. Assert the "
            "obligation unconditionally; register a genuine gap as a ledgered scenario tag."
        )

    @pytest.mark.arch_guard
    def test_mutated_scenarios_are_not_ledgered(self):
        """Guard the guard: a ledger entry would absorb the injected failure.

        If either mutated tag is later ledgered, this test fails and forces the
        mutation target to move — rather than letting the ledger silently make
        the mutation above vacuous.
        """
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        conftest_src = (repo_root / "tests" / "bdd" / "conftest.py").read_text()
        e2e_ledger = (repo_root / "tests" / "bdd" / "e2e_rest_known_failures.txt").read_text()

        # Match a ledger KEY, never a bare substring, for two reasons the split hit
        # in one change:
        #   1. `tag in source` reports "...-multi-format-sync" as ledgered the
        #      moment a DIFFERENT scenario "...-multi-format-sync-status" is
        #      ledgered beside it — exactly what D2's split creates.
        #   2. It also matches PROSE. The split's ledger entry carries the
        #      comment "Split out of @T-UC-006-storyboard-multi-format-sync",
        #      which is documentation, not a registration.
        # In the conftest the ledger is a dict, so a real entry is a quoted key
        # followed by a colon. In the nodeid ledger it is a whole line.
        def _ledgered(tag: str) -> bool:
            key_pattern = r'"' + re.escape(tag) + r'"\s*:'
            line_pattern = r"(?m)^\s*" + re.escape(tag) + r"(?![A-Za-z0-9_-])"
            return bool(re.search(key_pattern, conftest_src) or re.search(line_pattern, e2e_ledger))

        ledgered = [tag for tag in MUTATED_SCENARIO_TAGS if _ledgered(tag)]
        assert ledgered == [], (
            f"Mutation target(s) {ledgered} became ledgered — the ledger's own xfail would absorb the "
            "injected 500 and this module would pass vacuously. Point MUTATED_SCENARIO_TAGS at an "
            "un-ledgered storyboard scenario in the same change."
        )
