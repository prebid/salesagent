"""Guard: no BDD step calls the deprecated ``dispatch_raw_kwargs`` channel.

``dispatch_raw_kwargs`` was the legacy polymorphic dispatch primitive, split into two typed
successors: ``dispatch_request(ctx, *, req: BaseModel, identity=...)`` for well-formed
requests and ``dispatch_malformed_request(ctx, *, identity=..., **raw)`` for payloads
that deliberately cannot become a request model. It was kept ONLY as a transitional
escape hatch for call sites not yet migrated, then deleted from
``tests/bdd/steps/generic/_dispatch.py`` once the last caller was gone.

This guard now permanently pins the call-site count at ZERO (the function no longer
exists, so any new ``dispatch_raw_kwargs(...)`` call site would be a reintroduction of
the retired pattern, not a leftover migration).
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


def _find_dispatch_raw_kwargs_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of ``dispatch_raw_kwargs(...)`` call sites."""
    return [call.lineno for call in iter_call_expressions(tree, "dispatch_raw_kwargs")]


def test_no_dispatch_raw_kwargs_call_sites() -> None:
    """dispatch_raw_kwargs must have zero callers across tests/bdd/steps/.

    Every remaining call site is a not-yet-migrated leg of the
    dispatch_request/dispatch_malformed_request split. This is a
    ratchet — the count target is 0, and it may only shrink.
    """
    violations: dict[str, list[int]] = {}
    for tree, rel_path in iter_module_trees([_BDD_STEPS_ROOT]):
        if rel_path == _DEFINITION_FILE:
            continue
        lines = _find_dispatch_raw_kwargs_violations(tree)
        if lines:
            violations[rel_path] = lines

    total = sum(len(lines) for lines in violations.values())
    assert not violations, (
        f"dispatch_raw_kwargs(...) called at {total} site(s) — the deprecated flat-kwargs "
        "dispatch channel must be migrated to dispatch_request(ctx, req=...) or "
        "dispatch_malformed_request(ctx, **raw), then deleted from _dispatch.py once the "
        "last caller is gone:\n" + "\n".join(f"  {f}:{ln}" for f, lns in violations.items() for ln in lns)
    )


def test_detector_catches_dispatch_raw_kwargs_call() -> None:
    """Meta-test (positive): the detector must flag dispatch_raw_kwargs call sites."""
    assert_detector_catches_ast_snippets(
        _find_dispatch_raw_kwargs_violations,
        snippets={
            "flat_kwargs": ("def when_something(ctx):\n    dispatch_raw_kwargs(ctx, media_buy_ids=['mb-1'])\n"),
            "bare_call": ("def when_something(ctx):\n    dispatch_raw_kwargs(ctx)\n"),
            "identity_and_splat": (
                "def when_something(ctx):\n    dispatch_raw_kwargs(ctx, identity=foreign, **kwargs)\n"
            ),
        },
    )


def test_detector_ignores_sibling_dispatch_functions() -> None:
    """dispatch_request / dispatch_malformed_request are unaffected by this guard."""
    source = (
        "def when_something(ctx):\n"
        "    dispatch_request(ctx, req=req, identity=foreign)\n"
        "    dispatch_malformed_request(ctx, sampling_method='random')\n"
    )
    tree = ast.parse(source, filename="<sibling-functions>")
    assert not _find_dispatch_raw_kwargs_violations(tree)
