"""Guard: agent-URL canonicalization must delegate authority handling to the signing layer.

#1291. The agent-URL normalizer hand-rolled its own URL normalization and never
checked whether the authority was malformed per RFC 9421's canonicalization
rules -- a URL the signing verifier (``src.core.signing.canonical``) would
reject could still be silently accepted, normalized, and used for
agent-registration comparison. The fix made the normalizer call the signing
layer instead of re-deriving authority logic. This structural guard pins that
delegation so a future edit cannot silently drop it back to hand-rolled
(re-derived) authority parsing.

**The function this guard pins moved.** #1291 landed on
``src.core.validation.normalize_agent_url``; #1721 deleted that module along
with the function, because its extra ``/mcp`` / ``/a2a`` /
``/.well-known/adcp/sales`` suffix-stripping decided an AUTHORIZATION outcome
the pin never asked for (see the comment at the registration check in
``src.core.tools.media_buy_create``). Its one surviving job -- canonicalize an
``agent_url`` for identity comparison -- is now
``src.core.schemas._base.canonical_agent_url``, which reaches the vendored
canonicalizer through ``src.core.signing.canonical.producer_target_uri``. The
obligation is unchanged and only its address moved: the delegation is now TOTAL
(the whole algorithm is the seam's, not just a pre-check), and ``_delegated``
folds the vendored rejections onto ``TargetUriMalformedError``, which is how a
malformed authority still refuses rather than canonicalizes.

``tests/unit/test_architecture_vendored_code.py`` does NOT already cover this.
That guard pins that ``src/core/signing/canonical.py`` is the only importer of
``src.vendor`` -- it fires when a second caller reaches PAST the seam, and stays
silent when a caller stops using the seam altogether and hand-rolls ``urlparse``
instead. This guard is the other half.

A whole-tree AST guard for "hand-rolled netloc/urlparse authority parsing" is
intentionally NOT used: the codebase-wide disease scan found 13 other
urlparse/netloc/hostname call sites serving genuinely different purposes
(admin-form validation, SSRF/open-redirect guards, DB DSN parsing, GAM/Slack
integration URLs) -- a tree-wide checker would need to permanently allowlist
all 13 just to exist, which is disproportionate for a bug whose disposition
scan confirmed exactly one live instance. The targeted pin below is precise
instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Derived locally rather than imported from ``tests.unit._architecture_helpers``: this
# guard needs one Path constant and nothing else from that 1700-line module, and
# importing it would make a pure-AST check depend on the whole helper surface loading.
REPO_ROOT = Path(__file__).resolve().parents[2]

CANONICAL_MODULE = REPO_ROOT / "src" / "core" / "schemas" / "_base.py"
FUNCTION_NAME = "canonical_agent_url"
SIGNING_SEAM = "src.core.signing.canonical"

#: Entry points on the signing seam that refuse a malformed authority rather than
#: canonicalizing it. ``producer_target_uri`` is the one in use -- the PRODUCER side of
#: url-canonicalization.mdx step 2, which is what an agent_url comparison key is. The
#: comparer-side names are accepted too, so that moving between the two sides of step 2
#: stays a decision the seam owns rather than a build break here.
REQUIRED_CALLEES = frozenset(
    {
        "producer_target_uri",
        "canonical_target_uri",
        "canonical_authority",
        "reject_malformed_target",
    }
)

FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def function_delegates_to_signing_gate(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff *func*'s body calls one of ``REQUIRED_CALLEES`` anywhere.

    Shared predicate so the real-source test and the positive/negative
    meta-tests exercise the exact same logic.
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
            if name in REQUIRED_CALLEES:
                return True
    return False


def _module_tree() -> ast.Module:
    return ast.parse(CANONICAL_MODULE.read_text(), filename=str(CANONICAL_MODULE))


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, FuncDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {CANONICAL_MODULE} — did it move?")


def _imports_callee_from_the_seam(tree: ast.Module) -> bool:
    """True iff the module imports one of ``REQUIRED_CALLEES`` from the signing seam.

    Without this the call-name check alone is satisfiable by a module-local function
    of the same name, which is precisely the hand-rolled re-derivation #1291 removed.
    The import is function-local today (the signing layer pulls in the ORM), so the
    whole tree is walked rather than just the module body.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == SIGNING_SEAM:
            if any(alias.name in REQUIRED_CALLEES for alias in node.names):
                return True
    return False


def test_canonical_agent_url_delegates_to_signing_gate():
    """canonical_agent_url must call the signing layer's canonicalization seam."""
    func = _find_function(_module_tree(), FUNCTION_NAME)
    assert function_delegates_to_signing_gate(func), (
        f"{FUNCTION_NAME} no longer calls any of {sorted(REQUIRED_CALLEES)} -- it would "
        "silently accept a URL the RFC 9421 signing verifier rejects as malformed (#1291). "
        f"Re-add the `from {SIGNING_SEAM} import producer_target_uri` delegation before any "
        "hand-rolled normalization."
    )


def test_canonical_agent_url_takes_the_callee_from_the_signing_seam():
    """The delegation must reach the real seam, not a same-named local re-derivation."""
    assert _imports_callee_from_the_seam(_module_tree()), (
        f"{CANONICAL_MODULE.name} calls a canonicalization entry point but imports none of "
        f"{sorted(REQUIRED_CALLEES)} from `{SIGNING_SEAM}`. A module-local function of that "
        "name is the hand-rolled authority logic #1291 removed, wearing the seam's name."
    )


# -- Meta-tests: prove the predicate catches the disease and accepts the cure --

_GUARDED_SAMPLE = """
def canonical_agent_url(url):
    if not url:
        return url
    canonical = producer_target_uri(url)
    return canonical
"""

_UNGUARDED_SAMPLE = """
def canonical_agent_url(url):
    if not url:
        return url
    normalized = url.rstrip("/")
    return normalized
"""

_ATTRIBUTE_CALL_SAMPLE = """
def canonical_agent_url(url):
    if not url:
        return url
    canonical = canonical_module.producer_target_uri(url)
    return canonical
"""

_LOCAL_REDEFINITION_SAMPLE = """
def producer_target_uri(url):
    return url.rstrip("/")


def canonical_agent_url(url):
    return producer_target_uri(url)
"""


def _parse_single_func(src: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))


def test_meta_guard_accepts_delegating_function():
    """Positive: a function that calls the seam passes."""
    assert function_delegates_to_signing_gate(_parse_single_func(_GUARDED_SAMPLE))


def test_meta_guard_accepts_attribute_call_form():
    """Positive: calling it as `module.producer_target_uri(...)` also counts."""
    assert function_delegates_to_signing_gate(_parse_single_func(_ATTRIBUTE_CALL_SAMPLE))


def test_meta_guard_rejects_hand_rolled_function():
    """Negative: a function that re-derives normalization itself is caught."""
    assert not function_delegates_to_signing_gate(_parse_single_func(_UNGUARDED_SAMPLE))


def test_meta_guard_import_check_rejects_a_local_redefinition():
    """Negative: the call name alone is not enough -- the import must be the seam's."""
    assert not _imports_callee_from_the_seam(ast.parse(_LOCAL_REDEFINITION_SAMPLE))
