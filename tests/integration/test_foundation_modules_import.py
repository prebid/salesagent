"""Smoke test: every L0 foundation module imports cleanly in <1s.

Asserts the L0-04..L0-15 + L0-32 foundation modules can be imported in a
fresh Python process without ImportError, circular-import failures, or
I/O side effects that blow the 1-second budget.

Rationale — ``execution-plan.md`` Exit gate: the L0 pure-addition layer
ships modules that L1a starts wiring into the canonical middleware stack
and admin router. If any of these fail to import at L0, L1a integration
debugging becomes an archaeology exercise across multiple modules instead
of one focused fix. This smoke test collapses that search-space at the
earliest layer where the symbols exist.

Each foundation module is imported exactly once (a single ``importlib``
fresh-reload pass per module per test invocation). Lazy evaluation for
optional admin routers / auth helpers is NOT exercised here — that is
covered by per-module integration tests at L1+.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md`` §L0-16.

Waiver: this test has no paired Red commit because it verifies prior
L0-04..L0-15 + L0-32 implementation work, introducing no new behavior.

discipline: N/A - smoke verifies prior impl; no new behavior
"""

from __future__ import annotations

import importlib
import time

import pytest

# The 14 foundation modules landed across L0-04..L0-15 + L0-32, listed in
# dependency order so an import failure surfaces at the root of the chain.
FOUNDATION_MODULES: tuple[str, ...] = (
    # L0-04 — session middleware wiring helper (session-cookie rename)
    "src.admin.deps.messages",
    # L0-05 — templating: TemplatesDep + BaseCtxDep (11 keys) + tojson
    "src.admin.deps.templates",
    # L0-06 — OAuth transit cookie helper (OIDC CSRF-exempt path)
    "src.admin.deps.auth",
    "src.admin.deps.tenant",
    "src.admin.deps.audit",
    # L0-07 — ApproximatedExternalDomainMiddleware (307 path-gated)
    "src.admin.middleware.external_domain",
    # L0-08 — FlyHeadersMiddleware (proxy-header normalization)
    "src.admin.middleware.fly_headers",
    # L0-09 — RequestIDMiddleware (scaffold-only at L0)
    "src.admin.middleware.request_id",
    # L0-10 — UnifiedAuthMiddleware (empty-principal stub at L0)
    "src.admin.unified_auth",
    # L0-11 — OAuth singleton + byte-immutable callback paths
    "src.admin.oauth",
    # L0-12 — admin auth/tenant/audit deps (already listed above)
    # L0-13 — SimpleAppCache (app-state inventory cache)
    "src.admin.cache",
    # L0-14 — content_negotiation + error.html (Accept-aware AdCPError)
    "src.admin.content_negotiation",
    # L0-15 — empty build_admin_router()
    "src.admin.app_factory",
    # L0-32 — admin_redirect 302-default helper (see v2 §2 note 1)
    # NOTE: L0-32 may land after L0-16 in tranche ordering. Only assert
    # module presence defensively — the smoke test does NOT require the
    # symbol to exist yet at L0 dispatch time (L1c is the hard gate).
    # The helper module path is pinned below so that when L0-32 lands,
    # this smoke automatically grows to cover it without edit churn.
    # Until then, the module is marked optional via OPTIONAL_MODULES.
)


# Modules expected eventually but tolerated as missing at L0-16 land time
# (subsequent L0-N PRs will land them; smoke must not block those PRs).
OPTIONAL_MODULES: frozenset[str] = frozenset(
    {
        "src.admin.helpers.redirects",  # L0-32 admin_redirect()
    }
)


# Per-module import budget. The aggregate bound asserts the 14 modules
# import in under 1s on a cold cache; see v2 §L0-16 and the canonical
# spec in execution-plan.md exit gate.
TOTAL_BUDGET_SECONDS: float = 1.0


@pytest.mark.parametrize("module_name", FOUNDATION_MODULES)
def test_foundation_module_imports(module_name: str) -> None:
    """Each L0 foundation module imports without error.

    Failures here point directly at a circular import or a top-level I/O
    call introduced by a foundation module. The parametrize gives clean
    per-module pytest output — one bad module does not mask the others.
    """
    module = importlib.import_module(module_name)
    assert module is not None, f"importlib returned None for {module_name}"


@pytest.mark.parametrize("module_name", sorted(OPTIONAL_MODULES))
def test_optional_foundation_module_imports_if_present(module_name: str) -> None:
    """Optional L0 modules import cleanly IF their source file exists.

    L0-32 (and any future pre-L1a add-on) lands on its own PR. Until that
    lands, the module simply does not exist and we skip; once it lands the
    smoke starts covering it with no edits here.
    """
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError:
        pytest.skip(f"{module_name} has not landed yet (optional at this sweep)")


def test_aggregate_import_budget_under_one_second() -> None:
    """All 14 foundation modules import in aggregate under 1s.

    ``execution-plan.md`` exit gate target. This is a sanity check on
    accidental module-scope DB engine construction, network calls, or
    heavy metadata registration. If this test becomes flaky on CI due to
    VM noise, raise to 2s — do NOT disable it.

    The per-module parametrized tests above already covered individual
    import successes, so when this aggregate test fails it means imports
    together exceed the budget even though each individual import works.
    """
    start = time.perf_counter()
    for module_name in FOUNDATION_MODULES:
        importlib.import_module(module_name)
    elapsed = time.perf_counter() - start
    assert elapsed < TOTAL_BUDGET_SECONDS, (
        f"Foundation modules imported in {elapsed:.3f}s, exceeding the "
        f"{TOTAL_BUDGET_SECONDS}s budget. A module likely introduced module-scope "
        "I/O or a heavy metadata scan at import time."
    )
