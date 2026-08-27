"""Guard: only ONE handler in well_known.py ever resolves signing MATERIAL.

#1291 A5 follow-up, salesagent-z6nr.27. The architect review flagged that the
"narrowed seam" invariant (only the revocation-list handler decrypts a
private key; the other three secret-free bootstrap documents never do)
rested on convention, not enforcement — the shared ``DocumentBuilder`` type
hands every handler the whole ``TrustRootUoW``, so any handler COULD call
``resolve_signing_material``/``_resolve_signing_provider`` without a type
error. This guard makes the invariant executable: an AST scan of
``src/routes/well_known.py`` for calls to either name, asserting they occur
in exactly one named function.

Widening from one call site to two (or moving the call into a shared helper
this module's handlers all invoke) is exactly the regression this guard
exists to catch: it would mean a currently-unauthenticated, secret-free
bootstrap route (brand.json/adagents.json/jwks.json) started resolving
private key material on every GET — a DoS-adjacent amplification surface on
routes whose module docstring justifies their lack of auth by carrying no
secret.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import iter_call_expressions

REPO_ROOT = Path(__file__).resolve().parents[2]
WELL_KNOWN_MODULE = REPO_ROOT / "src" / "routes" / "well_known.py"

_MATERIAL_RESOLVERS = ("resolve_signing_material", "_resolve_signing_provider")


def _functions_calling_material_resolvers(tree: ast.Module) -> set[str]:
    """Names of top-level functions whose body calls a material resolver."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(next(iter_call_expressions(node, name=resolver), None) is not None for resolver in _MATERIAL_RESOLVERS):
            found.add(node.name)
    return found


def test_no_handler_resolves_signing_material():
    """STRENGTHENED from "exactly one" to "none" (#1757).

    The invariant is unchanged and its bar went UP. Material resolution moved out of this
    module entirely, into ``src.core.signing.publishable_revocation_list`` — the route now
    calls ONE layer operation instead of assembling resolve -> read -> build -> sign
    itself. So no handler here touches private key material, not even the one that
    legitimately needed to.

    This is not the guard going quiet because its subject vanished: the companion test
    below pins that the revocation handler still reaches the material-resolving operation,
    so "nobody resolves material" cannot be satisfied by the document silently ceasing to
    be signed.
    """
    tree = ast.parse(WELL_KNOWN_MODULE.read_text(encoding="utf-8"), filename=str(WELL_KNOWN_MODULE))
    resolvers = _functions_calling_material_resolvers(tree)
    assert resolvers == set(), (
        "no well_known.py handler may resolve signing material directly "
        "(resolve_signing_material/_resolve_signing_provider) -- the three secret-free "
        "bootstrap documents must never touch private key material, and the revocation "
        f"list reaches it through publishable_revocation_list. Found: {sorted(resolvers)}"
    )


def test_exactly_one_handler_publishes_the_revocation_list():
    """The signed document still has exactly one producer, and it is the right handler.

    Paired with the test above so that "no handler resolves material" states something.
    Without this, deleting the signing step entirely would satisfy that assertion — the
    three bootstrap documents would stay secret-free and the revocation list would quietly
    stop being signed.
    """
    tree = ast.parse(WELL_KNOWN_MODULE.read_text(encoding="utf-8"), filename=str(WELL_KNOWN_MODULE))
    publishers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and next(iter_call_expressions(node, name="publishable_revocation_list"), None) is not None
    }
    assert publishers == {"_governance_revocations_handler"}, (
        "exactly one handler may publish the combined revocation list, and it must be "
        f"_governance_revocations_handler. Found: {sorted(publishers)}"
    )


# ---------------------------------------------------------------------------
# Meta-tests: the detector can actually go red, and does not cry wolf on
# unrelated calls.
# ---------------------------------------------------------------------------

_TWO_HANDLERS_SOURCE = """
def handler_a(uow, tenant, now):
    material = resolve_signing_material(uow.signing_keys, tenant_id=tenant.tenant_id, now=now)
    return material

def handler_b(uow, tenant, now):
    return _resolve_signing_provider(uow.signing_keys, tenant_id=tenant.tenant_id, now=now)
"""

_ONE_HANDLER_SOURCE = """
def handler_a(uow, tenant, now):
    return build_jwks(uow.signing_keys.publishable_at(now=now, grace_seconds=1))

def handler_b(uow, tenant, now):
    material = resolve_signing_material(uow.signing_keys, tenant_id=tenant.tenant_id, now=now)
    return material
"""


def test_detector_flags_more_than_one_resolving_handler():
    tree = ast.parse(_TWO_HANDLERS_SOURCE)
    assert _functions_calling_material_resolvers(tree) == {"handler_a", "handler_b"}


def test_detector_does_not_flag_unrelated_calls():
    tree = ast.parse(_ONE_HANDLER_SOURCE)
    assert _functions_calling_material_resolvers(tree) == {"handler_b"}
