"""Guard: every ``dispatch_request(...)`` call site passes ``req=``.

salesagent-hwji retired the old polymorphic ``dispatch_request`` that accepted
either a validated request model OR raw flat kwargs indistinguishably (the "one
accessor returning two types" disease -- see ``tests/bdd/steps/generic/_dispatch.py``
for the full writeup). ``dispatch_request`` now declares ``req`` as a keyword-only
*required* parameter, so a flat-kwargs call raises ``TypeError`` at runtime -- but
that only fires when the scenario actually executes. This guard catches the
regression statically: a BDD step file that copies the old
``dispatch_request(ctx, some_field=value)`` shape fails the moment it is written,
not the moment its scenario happens to run.

The negative-path twin, ``dispatch_malformed_request(ctx, **raw)``, still
accepts flat kwargs by design -- this guard is scoped to the
``dispatch_request`` name only. (The old deprecated legacy form,
``dispatch_raw_kwargs``, that also accepted flat kwargs was retired once its
last call site migrated.)
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    iter_call_expressions,
    iter_module_trees,
)

_BDD_STEPS_ROOT = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# The function's own definition legitimately has no call site to check.
_DEFINITION_FILE = "tests/bdd/steps/generic/_dispatch.py"


def _find_missing_req_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of ``dispatch_request(...)`` calls with no ``req=`` kwarg."""
    violations: list[int] = []
    for call in iter_call_expressions(tree, "dispatch_request"):
        has_req_kwarg = any(kw.arg == "req" for kw in call.keywords)
        if not has_req_kwarg:
            violations.append(call.lineno)
    return violations


def test_dispatch_request_call_sites_all_pass_req() -> None:
    """Every real ``dispatch_request(...)`` call site in tests/bdd/steps/ passes req=."""
    violations: dict[str, list[int]] = {}
    for tree, rel_path in iter_module_trees([_BDD_STEPS_ROOT]):
        if rel_path == _DEFINITION_FILE:
            continue
        lines = _find_missing_req_violations(tree)
        if lines:
            violations[rel_path] = lines

    assert not violations, (
        "dispatch_request(...) called without req= (the flat-kwargs shape salesagent-hwji "
        "retired) at:\n" + "\n".join(f"  {f}:{ln}" for f, lns in violations.items() for ln in lns) + "\n"
        "Use dispatch_request(ctx, req=SomeRequest(...)) for well-formed requests, or "
        "dispatch_malformed_request(ctx, **raw) for values that cannot become a request model."
    )


def test_detector_catches_flat_kwargs_dispatch_request() -> None:
    """Meta-test (positive): the detector must flag the retired flat-kwargs shape."""
    assert_detector_catches_ast_snippets(
        _find_missing_req_violations,
        snippets={
            "flat_kwargs": (
                "def when_something(ctx):\n"
                "    dispatch_request(ctx, media_buy_ids=['mb-1'], status_filter=['active'])\n"
            ),
            "bare_call": ("def when_something(ctx):\n    dispatch_request(ctx)\n"),
            "identity_only_no_req": ("def when_something(ctx):\n    dispatch_request(ctx, identity=foreign)\n"),
        },
    )


def test_detector_allows_req_kwarg_dispatch_request() -> None:
    """Meta-test (negative): a well-formed req= call must NOT be flagged."""
    good_snippets = {
        "req_only": ("def when_something(ctx):\n    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest())\n"),
        "req_and_identity": ("def when_something(ctx):\n    dispatch_request(ctx, req=req, identity=foreign)\n"),
        "req_keyword_reordered": ("def when_something(ctx):\n    dispatch_request(ctx, identity=foreign, req=req)\n"),
    }
    for label, source in good_snippets.items():
        tree = ast.parse(source, filename=f"<known-good:{label}>")
        violations = _find_missing_req_violations(tree)
        assert not violations, f"False positive on known-good snippet {label!r}: {violations}"


def test_detector_ignores_sibling_dispatch_functions() -> None:
    """dispatch_malformed_request / dispatch_typed_or_malformed accept flat kwargs by design."""
    source = (
        "def when_something(ctx):\n"
        "    dispatch_malformed_request(ctx, sampling_method='random')\n"
        "    dispatch_typed_or_malformed(ctx, SomeModel, brand={'domain': 'x.com'})\n"
    )
    tree = ast.parse(source, filename="<sibling-functions>")
    assert not _find_missing_req_violations(tree)
