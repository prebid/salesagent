"""Structural guards for salesagent-z6nr.12 (#1291 B1) — the inbound RFC 9421
verifier's two ordering/coverage properties.

B1's correctness IS an ordering property, and ordering is the one thing a
behavioral test cannot fully protect: a future ``add_middleware`` line inserted
in the wrong place changes execution order silently. These two guards run on
every ``make quality``.

**R-M5(a) — the built middleware stack order.** ``app.user_middleware`` is
introspectable, and the refinement requires execution to be
CORS → UnifiedAuth → **verifier** → RestCompat → a2a_messageId → router. Both
halves matter and fail differently:

* verifier AFTER ``UnifiedAuthMiddleware`` — ``scope["state"]["auth_context"]``
  must already be populated, because the composition rule (R-H1) decides the
  ``required_for`` 401 on whether the bearer resolves to an accepted principal;
* verifier BEFORE ``RestCompatMiddleware`` and before the a2a messageId
  compatibility middleware — both REWRITE THE BODY, and a verifier downstream
  of a body rewriter hashes bytes the signer never signed (R-H2). The
  originally-planned placement got exactly this half wrong, and the proposed
  behavioral test ("a signed request observes auth_context populated") would
  not have detected it.

**R-M4 — the allowlist is tied to the route table.** Allowlist-over-denylist
kills the bootstrap-deadlock risk (an unverified JWKS/brand.json fetch can
never be blocked by the verifier) — keep it — but it introduces the mirror
failure: a future AdCP surface that nobody adds to the list is SILENTLY
UNVERIFIED, a security hole with no symptom, where a denylist's failure would
at least have been a noisy 401 on a trust-root fetch. This guard derives the
surface set from ``app.routes`` so adding a surface without classifying it
fails the build.

**The guard grades PRODUCTION's predicate, never a copy of it** (S2 fault B,
``salesagent-n78j0.2``). It used to import ``ADCP_SURFACE_PREFIXES`` and then
re-implement the segment-boundary rule against it — twice, byte-identically to
the verifier's own body. Sharing the DATA while copying the LOGIC is what made
this guard unable to fail: rewriting ``_is_adcp_surface`` to a bare
``str.startswith``, dropping the boundary its own comment calls load-bearing,
left all four tests below GREEN, because the guard was grading its own intact
copy. The rule now exists ONCE, in :mod:`src.core.signing.operations`
(``is_adcp_surface`` / ``matches_surface_prefix``, published through the
facade), and this file asks the verifier's own entry point.

Covers: salesagent-z6nr.12 (Refinement R-M4, R-M5(a)).
"""

from __future__ import annotations

import pytest
from starlette.middleware.cors import CORSMiddleware

from src.app import app
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.core.signing import ADCP_SURFACE_PREFIXES, RequestSignatureMiddleware, matches_surface_prefix
from src.core.signing.request_verifier_middleware import _is_adcp_surface
from src.routes.rest_compat_middleware import RestCompatMiddleware

#: Execution order, outermost first. ``app.add_middleware`` inserts at index 0,
#: so ``app.user_middleware`` reads outer → inner and the SOURCE order in
#: ``src/app.py`` is its inverse — which is exactly the trap this guard exists
#: to make loud (plan step 5 also rewrites the stale comment block that got the
#: order wrong).
_EXPECTED_ORDER = [
    CORSMiddleware,
    UnifiedAuthMiddleware,
    RequestSignatureMiddleware,
    RestCompatMiddleware,
]

#: The body rewriters the verifier must stay OUTSIDE of. ``RestCompatMiddleware``
#: rewrites via ``request._body`` (``src/routes/rest_compat_middleware.py:67``);
#: ``a2a_messageid_compatibility_middleware`` rebuilds the request from a
#: re-serialized body (``src/app.py``).
_A2A_BODY_REWRITER = "a2a_messageid_compatibility_middleware"


def _ordered_middleware_classes() -> list[type]:
    return [mw.cls for mw in app.user_middleware]


def _index_of(cls: type) -> int:
    ordered = _ordered_middleware_classes()
    assert cls in ordered, (
        f"{cls.__name__} is not registered on the app at all. Registered, outermost first: "
        f"{[c.__name__ for c in ordered]}"
    )
    return ordered.index(cls)


def _a2a_rewriter_index() -> int:
    """Index of the ``@app.middleware('http')`` a2a messageId body rewriter.

    Registered through the decorator, so its class is Starlette's generic
    ``BaseHTTPMiddleware`` and only the ``dispatch`` function identifies it.
    """
    for position, mw in enumerate(app.user_middleware):
        dispatch = mw.kwargs.get("dispatch") if getattr(mw, "kwargs", None) else None
        if dispatch is not None and getattr(dispatch, "__name__", "") == _A2A_BODY_REWRITER:
            return position
    raise AssertionError(
        f"{_A2A_BODY_REWRITER} is not registered on the app; the verifier's "
        "outside-every-body-rewriter property cannot be graded"
    )


def _top_segment(path: str) -> str:
    parts = path.split("/")
    return f"/{parts[1]}" if len(parts) > 1 else "/"


def _covered_by_allowlist(path: str) -> bool:
    """Would the verifier scope itself IN for *path*? Asked of PRODUCTION.

    This guard holds no copy of the boundary rule, and it does not ask a helper
    one level below the verifier either: it asks the exact predicate
    ``RequestSignatureMiddleware.__call__`` consults, so a rewrite at EITHER level
    — the published :func:`src.core.signing.is_adcp_surface` or this wrapper — is
    graded here. Re-implementing the rule (what this function used to do, verbatim
    to production's) is why a bare-``startswith`` rewrite of the segment boundary
    left every test in :class:`TestAllowlistTiedToRouteTable` green.

    The scope adapter is the only thing local: ``path`` is what
    ``path_from_asgi_scope`` reads.
    """
    return _is_adcp_surface({"type": "http", "path": path})


#: Top-level prefixes that are deliberately NOT AdCP protocol surfaces. Each is
#: exempt for a stated reason, and this is the list a reviewer reads when a new
#: entry appears. The trust-root documents are the load-bearing ones: A3's
#: ``/.well-known/{brand,adagents,jwks}.json`` MUST stay fetchable unsigned or
#: signature verification cannot bootstrap.
_NON_ADCP_PREFIXES: dict[str, str] = {
    "/": "landing page / admin WSGI catch-all mount",
    "/landing": "landing page",
    "/openapi.json": "FastAPI schema",
    "/docs": "FastAPI Swagger UI (+ its oauth2-redirect)",
    "/redoc": "FastAPI ReDoc",
    "/.well-known": "A3 trust-root documents + the A2A agent card — unsigned by construction",
    "/health": "liveness/readiness probes",
    "/_internal": "operational endpoint, not buyer-facing",
    "/debug": "debug endpoints, not buyer-facing",
    "/admin": "Flask admin UI (mounted at lifespan startup)",
}


class TestMiddlewareStackOrder:
    """R-M5(a) — the built stack order, the only thing that catches a reordering."""

    def test_adcp_middleware_execution_order(self):
        """CORS → UnifiedAuth → verifier → RestCompat, outermost first."""
        ordered = _ordered_middleware_classes()
        relevant = [cls for cls in ordered if cls in _EXPECTED_ORDER]

        assert relevant == _EXPECTED_ORDER, (
            "middleware execution order (outermost first) must be "
            f"{[c.__name__ for c in _EXPECTED_ORDER]}, got {[c.__name__ for c in relevant]}. "
            "Remember app.add_middleware inserts at index 0, so the registration order "
            "in src/app.py is the INVERSE of this list."
        )

    def test_verifier_runs_after_auth_context_is_populated(self):
        """The composition rule (R-H1) needs the resolved credential.

        The ``required_for`` 401 fires only when the bearer does NOT resolve to
        an accepted principal (security.mdx :1268-1269), so the verifier must
        run inside ``UnifiedAuthMiddleware``, which is what writes
        ``scope["state"]["auth_context"]``.
        """
        assert _index_of(UnifiedAuthMiddleware) < _index_of(RequestSignatureMiddleware), (
            "RequestSignatureMiddleware must run INSIDE UnifiedAuthMiddleware so "
            "auth_context is already populated when the composition rule is evaluated"
        )

    @pytest.mark.parametrize(
        "rewriter",
        [RestCompatMiddleware],
        ids=["rest-compat"],
    )
    def test_verifier_runs_outside_the_body_rewriters(self, rewriter):
        """R-H2 — a verifier downstream of a body rewriter hashes bytes the
        signer never signed, and the collision lands on ``/api/v1/media-buys``
        = ``create_media_buy``.
        """
        assert _index_of(RequestSignatureMiddleware) < _index_of(rewriter), (
            f"RequestSignatureMiddleware must run OUTSIDE {rewriter.__name__}, which "
            "rewrites the request body; otherwise content-digest is verified against "
            "bytes the counterparty never signed"
        )

    def test_verifier_runs_outside_the_a2a_messageid_rewriter(self):
        """The second body rewriter, registered via ``@app.middleware('http')``."""
        assert _index_of(RequestSignatureMiddleware) < _a2a_rewriter_index(), (
            f"RequestSignatureMiddleware must run OUTSIDE {_A2A_BODY_REWRITER}, which "
            "re-serializes the /a2a JSON-RPC body"
        )


class TestAllowlistTiedToRouteTable:
    """R-M4 — a new AdCP surface cannot ship silently unverified."""

    def test_every_route_is_either_an_allowlisted_surface_or_explicitly_exempt(self):
        """Derived from ``app.routes``: every registered path must be classified.

        A future AdCP surface (a new mount, a new APIRouter prefix, a new
        protocol route) lands here unclassified and fails ``make quality``,
        which is the only signal that would ever exist — an unverified surface
        has no runtime symptom.
        """
        unclassified = sorted(
            {
                path
                for route in app.routes
                if (path := getattr(route, "path", None))
                and not _covered_by_allowlist(path)
                and _top_segment(path) not in _NON_ADCP_PREFIXES
            }
        )

        assert unclassified == [], (
            f"routes not covered by ADCP_SURFACE_PREFIXES {tuple(ADCP_SURFACE_PREFIXES)} and not "
            f"listed in _NON_ADCP_PREFIXES: {unclassified}. If any of these serves AdCP protocol "
            "traffic, add its prefix to the middleware allowlist — an AdCP surface missing from "
            "the allowlist is silently unverified. Otherwise add it to _NON_ADCP_PREFIXES with a "
            "reason."
        )

    def test_every_allowlisted_prefix_matches_a_real_route(self):
        """No dead allowlist entry — a stale prefix hides the fact that the real
        surface moved and is now unverified.
        """
        paths = [path for route in app.routes if (path := getattr(route, "path", None))]
        dead = sorted(
            prefix
            for prefix in ADCP_SURFACE_PREFIXES
            if not any(matches_surface_prefix(path, prefix) for path in paths)
        )

        assert dead == [], (
            f"ADCP_SURFACE_PREFIXES entries matching no registered route: {dead}. "
            "A stale prefix means the real surface moved and is now unverified."
        )

    def test_the_three_adcp_surfaces_are_allowlisted(self):
        """The allowlist covers MCP, A2A and REST — plan step 4b."""
        for path in ("/mcp", "/a2a", "/api/v1/media-buys"):
            assert _covered_by_allowlist(path), (
                f"{path} is an AdCP protocol surface and must be covered by "
                f"ADCP_SURFACE_PREFIXES {tuple(ADCP_SURFACE_PREFIXES)}"
            )

    @pytest.mark.parametrize("suffix", ["x", "-internal", ".json"], ids=["segment", "hyphen", "dot"])
    def test_the_allowlist_stops_at_the_segment_boundary(self, suffix):
        """``/mcpx`` is NOT ``/mcp`` — the whole reason the rule is not ``startswith``.

        Derived from the allowlist rather than hand-listed, so a fourth surface gets
        its near-misses graded the moment it is added. This is the case that made the
        boundary load-bearing and the case a bare ``startswith`` gets wrong: it would
        pull ``/mcpx`` and ``/api/v1x`` — arbitrary non-AdCP paths that merely share a
        prefix — under the verifier, 401-ing traffic the layer never scoped, while
        every route-table-derived assertion in this class stayed green because a MORE
        permissive predicate can never leave a route unclassified.
        """
        for prefix in ADCP_SURFACE_PREFIXES:
            near_miss = f"{prefix}{suffix}"
            assert not _covered_by_allowlist(near_miss), (
                f"{near_miss} is not the AdCP surface {prefix} — it only shares its prefix. "
                "The allowlist must match on a SEGMENT boundary (equal, or followed by '/'); "
                "a bare str.startswith puts arbitrary non-AdCP paths behind the verifier."
            )
            assert _covered_by_allowlist(f"{prefix}/"), (
                f"{prefix}/ IS the AdCP surface {prefix} and must stay covered — the "
                "boundary rule must not be tightened into an exact match either"
            )

    def test_the_trust_root_documents_are_not_allowlisted(self):
        """A3's trust-root documents must stay OUTSIDE the verifier permanently.

        A signature-verifying middleware in front of ``/.well-known/jwks.json``
        is a bootstrap deadlock: the counterparty cannot fetch the keys it needs
        in order to sign.
        """
        for path in (
            "/.well-known/brand.json",
            "/.well-known/adagents.json",
            "/.well-known/jwks.json",
            "/.well-known/agent-card.json",
            "/health",
        ):
            assert not _covered_by_allowlist(path), (
                f"{path} must never be verified — putting a trust-root or health document "
                "behind the verifier creates a bootstrap deadlock"
            )


class TestCounterpartyAgentTypeIsATypeAtTheSeam:
    """A misconfigured ``counterparty_agent_type`` is refused while the settings load.

    ``SigningConfig.counterparty_agent_type`` is annotated as the SDK's ``BrandAgentType``
    Literal, so pydantic refuses an env override naming an unresolvable type at
    construction. The field WAS a plain ``str`` narrowed at each resolver call site, and
    before that ``cast(...)`` — a RUNTIME NO-OP. A typo passed validation, passed the
    cast, reached the resolver, matched no ``agents[]`` entry in the counterparty's
    brand.json, and 401'd EVERY signed counterparty with nothing naming the cause.

    One enforcement point now, so one grader. The permitted set needs no test of its
    own: the annotation IS the SDK's Literal, so it admits exactly that Literal's
    members by construction and any test restating them would re-derive the set it
    reads. MUTATION: widen the annotation back to ``str`` — the test below goes RED
    because nothing raises.
    """

    @pytest.mark.arch_guard
    def test_the_settings_boundary_refuses_a_typo(self) -> None:
        """A typo is refused where the env override lands: constructing the settings."""
        from pydantic import ValidationError

        from src.core.config import SigningConfig

        with pytest.raises(ValidationError) as excinfo:
            SigningConfig(counterparty_agent_type="buyng")

        message = str(excinfo.value)
        assert "buyng" in message, "the refusal must name the offending value"
        assert "buying" in message, "and must show what was expected, or it is not actionable"


class TestCounterpartyRegistryEntryIsATypeAtTheSeam:
    """A malformed ``counterparty_registry`` entry is refused while the settings load.

    ``CounterpartyRegistryEntry`` declares the four keys ``build_registry_resolution``
    reads, so an incomplete entry cannot reach the request path. The entry WAS
    ``dict[str, Any]``: those four subscripts raised ``KeyError`` inside a try whose
    only handler catches ``SignatureVerificationError``, so an operator's config typo
    escaped the handler and reached a buyer as a 500 on a signed request, rather than
    stopping the process that loaded the typo.

    ``SigningConfig`` is a ``BaseSettings`` carrying ``extra="forbid"``, which pydantic
    propagates into the ``TypedDict`` — hence the second test: a misspelled key names
    the misspelling itself, not merely the sibling it failed to spell.

    MUTATION: widen the annotation back to ``dict[str, dict[str, Any]]`` — both tests
    go RED because nothing raises.
    """

    @pytest.mark.arch_guard
    def test_an_incomplete_entry_is_refused_at_config_load(self) -> None:
        """Every absent required key is named, at construction."""
        from pydantic import ValidationError

        from src.core.config import SigningConfig

        with pytest.raises(ValidationError) as excinfo:
            SigningConfig(counterparty_registry={"kid1": {"agent_url": "https://a.example"}})

        absent = {error["loc"][-1] for error in excinfo.value.errors() if error["type"] == "missing"}
        assert absent == {"jwks_uri", "key_origin", "jwks"}, (
            "the refusal must name every missing key, or an operator fixes one and "
            f"rediscovers the next on the following boot; got {absent}"
        )

    @pytest.mark.arch_guard
    def test_a_misspelled_key_names_the_misspelling(self) -> None:
        """A typo'd key reports both the absent key and the unknown one that replaced it."""
        from pydantic import ValidationError

        from src.core.config import SigningConfig

        with pytest.raises(ValidationError) as excinfo:
            SigningConfig(
                counterparty_registry={
                    "kid1": {
                        "agent_url": "https://a.example",
                        "jwks_url": "https://a.example/jwks",
                        "key_origin": "https://a.example",
                        "jwks": {},
                    }
                }
            )

        reported = {(error["type"], error["loc"][-1]) for error in excinfo.value.errors()}
        assert ("missing", "jwks_uri") in reported, f"the absent key must be named; got {reported}"
        assert ("extra_forbidden", "jwks_url") in reported, (
            f"the misspelling must be named too, or the operator reads 'jwks_uri missing' "
            f"while looking straight at a line that appears to set it; got {reported}"
        )
