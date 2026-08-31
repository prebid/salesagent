"""Guard: every harness env's (REST_METHOD, REST_ENDPOINT) resolves to a live APIRoute.

An ``IntegrationEnv`` that declares ``REST_ENDPOINT`` is dispatched on two REST legs —
the in-process ``RestDispatcher`` (``_run_rest_request``) and the in-network
``RestE2EDispatcher``. Both read ``REST_METHOD`` (default ``post``). If the declared
``(verb, path)`` does not resolve to a FastAPI ``APIRoute``, the request falls through
to the catch-all Flask mount at ``/`` and returns a Werkzeug HTML 404 — invisible to
``make quality`` (unit + offline) and to the in-process leg when only ONE leg was
overridden.

That is exactly the ``CapabilitiesEnv`` regression (#1329): it overrode only
the in-process ``_run_rest_request`` to GET, so the in-network leg POSTed to the
GET-only ``/api/v1/capabilities`` and 404'd on the live route — a red in-network CI job
that no unit/in-process check could see. This guard performs that check structurally:
it fails the moment a harness env points at a ``(verb, path)`` with no matching
``APIRoute``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.routing import APIRoute

from src.routes.api_v1 import router as api_v1_router
from tests.harness._base import BaseTestEnv

_HARNESS_DIR = Path(__file__).resolve().parents[1] / "harness"

# The allowlist is intentionally EMPTY and shrink-only: no harness env may declare a
# REST_ENDPOINT that resolves to no live route. (The lone historical offender —
# MediaBuyListEnv's /api/v1/media-buys/query for the REST-less get_media_buys — had its
# dead REST_ENDPOINT removed rather than allowlisted; #1329.) A new entry is never added:
# fix the env's REST_METHOD/REST_ENDPOINT or add the route.
_KNOWN_UNRESOLVED: frozenset[tuple[str, str]] = frozenset()


def _import_all_harness_modules() -> None:
    """Import every harness module so all env subclasses are registered."""
    for py_file in sorted(_HARNESS_DIR.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
        importlib.import_module(f"tests.harness.{py_file.stem}")


def _all_env_subclasses() -> set[type]:
    """Every (transitive) BaseTestEnv subclass currently imported."""
    seen: set[type] = set()
    stack = list(BaseTestEnv.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return seen


def _live_routes() -> set[tuple[str, str]]:
    """(method-lower, path) for every APIRoute reachable via the api_v1 router."""
    routes: set[tuple[str, str]] = set()
    for route in api_v1_router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                routes.add((method.lower(), route.path))
    return routes


# A collapsed scan set (import → no-op, or the subclass walk returning nothing) would make
# every check below pass vacuously, so test_scan_set_is_populated floors this. 12 envs declare
# a static REST_ENDPOINT today; 10 leaves margin for env churn while still reddening a collapse.
_MIN_DECLARED_DISPATCHES = 10


def _declared_rest_dispatches() -> set[tuple[str, str, str]]:
    """(env_name, method-lower, endpoint) for every env declaring a STATIC string REST_ENDPOINT.

    Coverage limitation (honest): an env whose ``REST_ENDPOINT`` is a ``@property`` — evaluated
    per-request, e.g. ``MediaBuyDualEnv`` whose update path yields a concrete
    ``/api/v1/media-buys/<id>`` — reads back as a non-str on the CLASS, so the ``continue`` below
    skips it ENTIRELY: it is checked at no verb and no path. (Set-membership against
    ``_live_routes`` cannot match a templated ``('put', '/api/v1/media-buys/{media_buy_id}')``
    against a concrete id anyway.) Resolving dynamic envs against ``route.path_format``, or having
    such envs declare their ``(verb, path)`` pairs on a class attribute this guard can read, is
    the follow-up that would extend coverage to them (#1329 R9-E2).
    """
    _import_all_harness_modules()
    dispatches: set[tuple[str, str, str]] = set()
    for cls in _all_env_subclasses():
        endpoint = getattr(cls, "REST_ENDPOINT", "")
        if not isinstance(endpoint, str) or not endpoint:
            continue
        method_attr = getattr(cls, "REST_METHOD", "post")
        method = method_attr if isinstance(method_attr, str) else "post"
        dispatches.add((cls.__name__, method.lower(), endpoint))
    return dispatches


def _unresolved(dispatches: set[tuple[str, str, str]], live: set[tuple[str, str]]) -> set[tuple[str, str, str]]:
    """The dispatches whose (verb, path) resolves to no live route and is not allowlisted.

    Extracted so the detection math is testable directly on a synthetic dispatch set
    (test_guard_flags_synthetic_verb_path_mismatch), independent of the app route table
    (#1329 R9-E1).
    """
    return {
        (name, method, endpoint)
        for name, method, endpoint in dispatches
        if (method, endpoint) not in live and (method, endpoint) not in _KNOWN_UNRESOLVED
    }


def test_every_harness_rest_endpoint_resolves_to_live_route():
    """No harness env may declare a REST dispatch that resolves to no live APIRoute."""
    unresolved = _unresolved(_declared_rest_dispatches(), _live_routes())
    assert unresolved == set(), (
        f"Harness env(s) declare a REST dispatch that resolves to no live APIRoute: {sorted(unresolved)}. "
        f"A (verb, path) with no matching route falls through to the Flask mount and 404s on the in-network "
        f"leg (#1329). Fix the env's REST_METHOD/REST_ENDPOINT or add the route. Do NOT add to the "
        f"allowlist."
    )


def test_scan_set_is_populated():
    """Floor: a collapsed scan set (no-op import / empty subclass walk) must redden, not pass vacuously.

    Both `_import_all_harness_modules` → no-op and `_declared_rest_dispatches` → set() would make
    the main check pass with nothing to check; this asserts the scan set is actually populated
    (#1329 R9-E1).
    """
    count = len(_declared_rest_dispatches())
    assert count >= _MIN_DECLARED_DISPATCHES, (
        f"Only {count} harness REST dispatches discovered (floor {_MIN_DECLARED_DISPATCHES}) — the scan set "
        f"collapsed (import no-op or empty subclass walk), which would make the resolution guard vacuous."
    )


def test_guard_flags_synthetic_verb_path_mismatch():
    """Positive+negative control: the detector flags a bad (verb, path) and passes a live one.

    Grades the DETECTION logic itself (not the route table): a synthetic POST-to-the-GET-only
    capabilities route must be reported unresolved, while the real GET must pass (#1329 R9-E1).
    """
    live = _live_routes()
    bad = {("SyntheticEnv", "post", "/api/v1/capabilities")}
    good = {("SyntheticEnv", "get", "/api/v1/capabilities")}
    assert _unresolved(bad, live) == bad, "detector failed to flag a POST to the GET-only capabilities route"
    assert _unresolved(good, live) == set(), "detector wrongly flagged a valid GET dispatch"


def test_known_unresolved_not_stale():
    """Allowlist only shrinks — an entry that now resolves (or is no longer declared) must be removed."""
    live = _live_routes()
    declared = {(method, endpoint) for _name, method, endpoint in _declared_rest_dispatches()}
    stale = {pair for pair in _KNOWN_UNRESOLVED if pair in live or pair not in declared}
    assert stale == set(), (
        f"Stale _KNOWN_UNRESOLVED entr(ies) — now resolvable or no longer declared, remove them: {sorted(stale)}."
    )


def test_guard_detects_verb_path_mismatch():
    """Meta: the exact blocker-A shape (POST to the GET-only capabilities route) does NOT resolve."""
    live = _live_routes()
    assert ("get", "/api/v1/capabilities") in live, "capabilities GET route missing — app route table changed"
    assert ("post", "/api/v1/capabilities") not in live, "capabilities must be GET-only; a POST match hides the gap"
