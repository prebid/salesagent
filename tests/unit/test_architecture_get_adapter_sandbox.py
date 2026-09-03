"""Structural guard: every get_adapter() call decides sandbox mode explicitly.

AdCP 3.1.1 ``dist/docs/3.1.1/media-buy/advanced-topics/sandbox.mdx`` §Seller
implementation requires that a request referencing a sandbox account:

    - MUST NOT make real ad platform API calls (no real orders, line items, etc.)
    - MUST NOT charge real money or create real billing records

Adapters are selected per-tenant, so ``get_adapter()`` is the single chokepoint where a
sandbox request can be diverted to the mock adapter. ``sandbox=`` is keyword-only and
REQUIRED — it has no default, so omitting it is a ``TypeError`` rather than a silent
dispatch to the tenant's real ad server. That is the enforcement this guard extends, not
the one it substitutes for: the signature covers every caller in any repo, while these
arms cover what a type checker cannot see — that a *present* keyword can still carry a
hard-wired constant, or a value read from an identity that was never enriched.

This is a semantic-SSOT guard: no linter or type checker can see that the wrong keyword
VALUE means "books a real campaign". Allowlists here may only shrink.
"""

from __future__ import annotations

import ast
from typing import NamedTuple

from tests.unit._architecture_helpers import REPO_ROOT, iter_call_expressions, safe_parse

SCAN_ROOTS = (REPO_ROOT / "src",)

# Call sites that legitimately cannot decide sandbox mode, with the reason.
# Keyed by ``path:lineno`` — the only arm where that is right, because a site with NO
# sandbox= keyword has no expression to name and no stable identity beyond its position.
# Empty by design — add an entry only with a written justification, never to silence.
KNOWN_EXEMPT: dict[str, str] = {}


# Sites whose sandbox= is a hard-wired literal, with the reason it cannot be derived.
# A literal is normally the defect this guard exists to catch — it satisfies the
# presence check while dispatching every request to one mode — so an entry here must
# say why the mode is genuinely static. Allowlists only shrink.
#
# Keyed by ``module::function``, matching IDENTITY_KEYED_SITES. Keying it by MODULE was
# the same escape hatch that arm was fixed for one commit earlier: a file-wide entry
# exempts every get_adapter call in the file, so adding a second hard-wired call to an
# already-listed module inherited the first one's exemption and left this arm green.
# The two allowlists now answer the same question — "which call site?" — the same way.
LITERAL_EXEMPT: dict[str, str] = {
    "src/core/tools/capabilities.py::_get_adcp_capabilities_impl": (
        "GetAdcpCapabilitiesRequest has no account field in the pinned SDK, so the mode is "
        "dead by protocol rather than by omission"
    ),
}


# Sites where identity.sandbox is the CORRECT source: the request itself carries an
# account reference, which enrich_identity_with_account resolves at the boundary before
# _impl runs. Everywhere else the identity is unenriched and the flag is structurally
# False, so reading it is the original defect wearing the right keyword.
# Keyed by ``module::function``, NOT by module. A module-wide entry exempts every
# get_adapter call in the file, and media_buy_create.py holds five across four
# functions — only two of which are request-scoped. Keying it by file therefore
# un-guarded the approval executor, the site the round that added this arm named as
# its own example: reverting it to identity.sandbox left the guard green.
#
# A function that receives the mode as a PARAMETER needs no entry here: the arm resolves
# names through assignments only, so a bare parameter never matches, and the value's
# correctness is graded where it is chosen — at the caller, which is listed. An entry for
# such a function exempts nothing and reads as coverage it does not provide; one was
# removed for exactly that reason (deleting it left this file at 6 passed).
IDENTITY_KEYED_SITES: dict[str, str] = {
    "src/core/tools/media_buy_create.py::_create_media_buy_impl": (
        "create_media_buy declares and forwards `account`; the boundary enriches the "
        "identity before _impl, so identity.sandbox is the resolved account's mode"
    ),
    "src/core/tools/products.py::_get_products_impl": (
        "get_products declares and forwards `account` on MCP/A2A/REST; the boundary "
        "enriches the identity before _impl, so identity.sandbox is the resolved "
        "account's mode"
    ),
}


class _AdapterCall(NamedTuple):
    """One ``get_adapter()`` call site and everything the three arms need about it."""

    path: str
    lineno: int
    site: str  # "path::function", or "path" at module scope — the allowlist key
    func: ast.FunctionDef | ast.AsyncFunctionDef | None
    value: ast.expr | None  # the sandbox= expression, or None when the keyword is absent


def _enclosing_function(tree: ast.Module, call: ast.Call):
    """The innermost function containing *call*, or None at module scope."""
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.lineno <= call.lineno and (node.end_lineno or node.lineno) >= call.lineno:
            if best is None or node.lineno > best.lineno:
                best = node
    return best


def _adapter_calls_in(tree: ast.Module, rel: str) -> list[_AdapterCall]:
    """Every ``get_adapter()``/``adapter_for_mode()`` call in one parsed module.

    ``adapter_for_mode`` is the thin ``get_adapter`` wrapper that factors out the
    dry_run-from-testing_ctx boilerplate (``src/core/helpers/adapter_helpers.py``); its
    ``sandbox=`` keyword is the exact same sandbox-routing decision, so a call site
    written through the wrapper must be scanned identically to a direct ``get_adapter``
    call, not silently drop off this guard's radar.

    Split out from the filesystem walk so the self-tests can drive the REAL extraction
    over a synthetic module. A self-test that re-implements the extraction against a
    string grades its own copy: neutering an arm's predicate left two such tests green,
    which from the outside is indistinguishable from the arm working.
    """
    found: list[_AdapterCall] = []
    for name in ("get_adapter", "adapter_for_mode"):
        for call in iter_call_expressions(tree, name):
            value = next((kw.value for kw in call.keywords if kw.arg == "sandbox"), None)
            func = _enclosing_function(tree, call)
            site = f"{rel}::{func.name}" if func is not None else rel
            found.append(_AdapterCall(rel, call.lineno, site, func, value))
    return found


def _get_adapter_calls() -> list[_AdapterCall]:
    """Every ``get_adapter()`` call under SCAN_ROOTS, with its enclosing function.

    One collector for all three arms. Each previously walked the scan roots itself, so
    "which sites does this guard see?" had two answers that could drift apart silently.
    """
    found: list[_AdapterCall] = []
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            tree = safe_parse(path)
            if tree is None:
                continue
            found.extend(_adapter_calls_in(tree, str(path.relative_to(REPO_ROOT))))
    return found


def _is_identity_attr(node: ast.expr) -> bool:
    """``identity.sandbox``, ``self.identity.sandbox``, ``ctx.identity.sandbox`` ..."""
    if not (isinstance(node, ast.Attribute) and node.attr == "sandbox"):
        return False
    base = node.value
    if isinstance(base, ast.Name):
        return base.id == "identity"
    return isinstance(base, ast.Attribute) and base.attr == "identity"


def _for_loop_candidates(name: str, func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    """Values a for-loop target named *name* can bind, resolved from a literal iterable.

    Handles ``for x in <iterable>:`` (bare ``Name`` target) and ``for a, b, ... in
    <iterable of tuples>:`` (``Tuple``/``List`` target, one level deep — matches every
    current production shape, e.g. ``media_buy_list.py``'s
    ``for partition, partition_is_sandbox in ((live_buys, False), (sandbox_buys,
    True)):``). Only a literal ``Tuple``/``List``/``Set`` for ``node.iter`` yields
    candidates — a non-literal iterable (a function call, a variable) has no values
    visible on the AST, so it contributes nothing, same as any other expression this
    resolver can't see through.
    """
    candidates: list[ast.expr] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.For):
            continue
        target = node.target
        index: int | None = None
        if isinstance(target, ast.Name):
            if target.id != name:
                continue
        elif isinstance(target, ast.Tuple | ast.List):
            for i, elt in enumerate(target.elts):
                if isinstance(elt, ast.Name) and elt.id == name:
                    index = i
                    break
            else:
                continue
        else:
            continue

        iterable = node.iter
        if not isinstance(iterable, ast.Tuple | ast.List | ast.Set):
            continue
        for element in iterable.elts:
            if index is None:
                candidates.append(element)
            elif isinstance(element, ast.Tuple | ast.List) and index < len(element.elts):
                candidates.append(element.elts[index])
    return candidates


def _one_hop_candidates(value: ast.expr, func: ast.FunctionDef | ast.AsyncFunctionDef | None) -> list[ast.expr]:
    """What ``value`` can hold: itself, what a local of that name is assigned in *func*,
    or what a for-loop target of that name is bound to across a literal iterable.

    Shared by the identity and literal arms. It exists because production writes the
    via-local form (``s = <expr>`` then ``sandbox=s``) at 4 of 12 sites, so an arm that
    inspects the call's expression directly sees a bare ``Name`` and concludes nothing.
    (7 of the 12 sites pass a bare ``Name``; 2 of those 7 are forwarded parameters,
    which resolve to themselves by design — see below. A third — media_buy_list.py's
    ``for partition, partition_is_sandbox in (...)`` loop target — is resolved by
    :func:`_for_loop_candidates`, to ``[Constant(False), Constant(True)]``: two DIFFERENT
    literals, one per partition, each correct for its own data. That leaves 4 sites that
    are genuinely local assignments.) The identity arm was extended to follow one hop;
    its sibling was not, which left ``s = False; sandbox=s`` — the cheapest hard-wire,
    written in production's own idiom — passing the literal arm at those 4 sites. One
    resolver now serves both, so a future arm cannot inherit half the fix.

    A bare PARAMETER deliberately resolves to itself: the mode is chosen by the caller,
    which each arm grades on its own. Following parameters would flag a forwarding
    function whose argument is already correct at its only source.
    """
    if not (isinstance(value, ast.Name) and func is not None):
        return [value]
    assigned: list[ast.expr] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            if any(isinstance(tgt, ast.Name) and tgt.id == value.id for tgt in node.targets):
                assigned.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == value.id and node.value is not None:
                assigned.append(node.value)
    assigned.extend(_for_loop_candidates(value.id, func))
    return assigned or [value]


def _hard_wires_sandbox(call: _AdapterCall) -> bool:
    """True when every expression this site's ``sandbox=`` can hold is the SAME constant.

    Not just "all constants": a for-loop like ``for partition, is_sandbox in ((live,
    False), (sandbox, True)):`` resolves to TWO different constants, one per
    partition — each is correct for its own data, so that is a legitimate derivation,
    not a hard-wire. Collapsing to a single constant (every partition passing the same
    literal) is the actual hard-wire signature: the value would then not depend on
    which partition — or which account — is being processed.

    ``all``, not ``any``, for the constant-ness check: a local initialised to a literal
    and then reassigned from the account is not hard-wired, and flagging it would push
    contributors toward the allowlist to silence a false positive — the one pressure a
    shrink-only allowlist cannot absorb.
    """
    if call.value is None:
        return False
    candidates = _one_hop_candidates(call.value, call.func)
    if not all(isinstance(candidate, ast.Constant) for candidate in candidates):
        return False
    values = {candidate.value for candidate in candidates}
    return len(values) == 1


def _hard_wired_offenders(calls: list[_AdapterCall]) -> list[str]:
    """Sites whose ``sandbox=`` is hard-wired and not allowlisted.

    The arm applies this to the repo; its self-test applies it to a synthetic module.
    Extracted so the two cannot diverge: while the arm inlined this comprehension, its
    self-test asserted on ``ast`` directly, and neutering the comprehension to ``if
    False`` left every test in this file green — the arm and its proof were independent.
    """
    return [
        f"{call.path}:{call.lineno}" for call in calls if _hard_wires_sandbox(call) and call.site not in LITERAL_EXEMPT
    ]


def _identity_sourced_offenders(calls: list[_AdapterCall]) -> list[str]:
    """Sites reading ``identity.sandbox`` where the request carries no account reference.

    Same extraction, same reason, as :func:`_hard_wired_offenders`.
    """
    return [
        f"{call.path}:{call.lineno} (in {call.func.name if call.func else '<module>'})"
        for call in calls
        if call.value is not None
        and call.site not in IDENTITY_KEYED_SITES
        and _resolves_to_identity_sandbox(call.value, call.func)
    ]


def _missing_sandbox_offenders(calls: list[_AdapterCall]) -> list[str]:
    """Sites with no ``sandbox=`` keyword at all, and not allowlisted.

    The arm applies this to the repo; its self-test applies it to a synthetic module.
    Extracted so the two cannot diverge — same reasoning as :func:`_hard_wired_offenders`
    and :func:`_identity_sourced_offenders`: this arm previously inlined its
    comprehension, so neutering it to ``if False`` left every test in this file green.
    """
    return [
        f"{call.path}:{call.lineno}"
        for call in calls
        if call.value is None and f"{call.path}:{call.lineno}" not in KNOWN_EXEMPT
    ]


def test_every_get_adapter_call_decides_sandbox_explicitly() -> None:
    """A call site that omits sandbox= silently dispatches sandbox buys to a real adapter."""
    calls = _get_adapter_calls()
    assert calls, "found no get_adapter() calls — the scan roots are wrong"

    missing = _missing_sandbox_offenders(calls)

    assert not missing, (
        "get_adapter() called without an explicit sandbox= decision at:\n  "
        + "\n  ".join(missing)
        + "\n\nPass sandbox=identity.sandbox where an account-enriched ResolvedIdentity is "
        "in scope. On buy-keyed and deferred paths (update, performance, creative push, "
        "the approval executor, admin routes) derive it from the buy's account: with a UoW "
        "in scope use BuyKeyedSandboxMixin.sandbox_mode(buy) — or sandbox_mode_by_id(id) when "
        "only the id is held — and without one call "
        "account_helpers.sandbox_mode_for_buy(accounts, buy). Named for the MIXIN, not a concrete "
        "UoW: deferred creative push holds an AdminCreativeUoW, so MediaBuyUoW.sandbox_mode_by_id "
        "is an AttributeError there, and the admin detail route holds no UoW at all. "
        "See AdCP 3.1.1 sandbox.mdx §Seller implementation."
    )


def test_guard_would_catch_a_regression() -> None:
    """Self-test with BOTH signs, through the real offender lister.

    This previously re-implemented arm 1's predicate against a synthetic string —
    ``any(kw.arg == "sandbox" ...)`` — so it graded ``ast``, not the guard. It later
    moved to reading ``_adapter_calls_in`` directly, which exercises the collector but
    not the ``missing = [...]`` comprehension the arm itself ran — neutering that
    comprehension to ``if False`` left this test green. It now runs
    ``_missing_sandbox_offenders``, the same function the arm calls, so neutering the
    arm reddens its own proof.
    """
    missing = _adapter_calls_in(ast.parse("a = get_adapter(principal, dry_run=False, tenant=t)\n"), "synthetic.py")
    present = _adapter_calls_in(ast.parse("a = get_adapter(principal, sandbox=identity.sandbox)\n"), "synthetic.py")

    assert len(missing) == 1 and len(present) == 1, "the collector no longer finds a get_adapter call at all"
    assert _missing_sandbox_offenders(missing), (
        "the missing arm no longer flags an absent sandbox= — it would pass vacuously"
    )
    assert not _missing_sandbox_offenders(present), (
        "the missing arm flags a present sandbox= as absent — it would fire on every site"
    )


def test_collector_also_extracts_adapter_for_mode_calls() -> None:
    """Self-test, both signs, for the ``adapter_for_mode`` half of the extraction.

    ``_adapter_calls_in`` scans both ``get_adapter(`` and ``adapter_for_mode(`` (see its
    docstring), but every synthetic-module string in this file up to this point only
    exercises the ``get_adapter`` name. A regression that narrowed the loop back to
    ``for name in ("get_adapter",)`` would leave every other test in this file green,
    because none of them ever spells ``adapter_for_mode`` — this is the only test that
    would catch it going dark.
    """
    present = _adapter_calls_in(
        ast.parse("a = adapter_for_mode(principal, sandbox=identity.sandbox, tenant=t)\n"), "synthetic.py"
    )
    missing = _adapter_calls_in(ast.parse("a = adapter_for_mode(principal, tenant=t)\n"), "synthetic.py")

    assert len(present) == 1 and len(missing) == 1, "the collector no longer finds an adapter_for_mode call at all"
    assert present[0].value is not None, "adapter_for_mode's sandbox= keyword was not extracted"
    assert missing[0].value is None, "the missing arm should still see an absent sandbox= as None"
    assert _missing_sandbox_offenders(missing), (
        "the missing arm no longer flags an adapter_for_mode call with no sandbox= — it would pass vacuously"
    )
    assert not _missing_sandbox_offenders(present), (
        "the missing arm flags a present adapter_for_mode sandbox= as absent — it would fire on every site"
    )


def _resolves_to_identity_sandbox(value: ast.expr, func: ast.FunctionDef | ast.AsyncFunctionDef | None) -> bool:
    """True when ``value`` reaches ``identity.sandbox`` — directly or through a local.

    The previous arm only matched ``sandbox=identity.sandbox`` written inline, so it was
    inert: every scanned site passes a bare Name (``_mb_sandbox``, ``is_sandbox``,
    ``partition_is_sandbox``, ``sandbox``). Deleting its whole body left the guard green,
    and reverting a module to the original defect in its own idiom did not redden it.
    Resolving one assignment hop is what makes the arm able to fail at all.

    ``any``, not ``all``: a local assigned from the identity on ANY branch reads the
    unenriched flag on that branch, which is the defect regardless of what the other
    branches do.
    """
    return any(_is_identity_attr(candidate) for candidate in _one_hop_candidates(value, func))


def test_only_account_carrying_paths_source_sandbox_from_identity() -> None:
    """A correct keyword from the wrong source still dispatches sandbox buys to live.

    Scans every module with a get_adapter call rather than a hardcoded four. The old
    list named update/performance/list/delivery while the failure message advertised
    "update, performance, creative push, the approval executor, admin routes" — so
    injecting the defect at the approval executor or the admin route left the guard
    green at exactly the sites the message told the reader were covered.
    """
    offenders = _identity_sourced_offenders(_get_adapter_calls())

    assert not offenders, (
        "sandbox= resolves to identity.sandbox on a path that does not carry an account "
        "reference:\n  " + "\n  ".join(offenders) + "\n\nidentity.sandbox is only populated where the boundary ran "
        "enrich_identity_with_account. Derive the mode from the buy's account instead "
        "(BuyKeyedSandboxMixin.sandbox_mode / sandbox_mode_by_id / "
        "partition_by_sandbox_mode), or add the site to IDENTITY_KEYED_SITES with the "
        "reason its requests carry an account."
    )


def test_identity_arm_catches_both_the_inline_and_the_via_local_form() -> None:
    """Self-test, both signs and both idioms — the arm was previously inert.

    Production writes the via-local form at every site, so an arm that only matched the
    inline form could never fire. A negative case is included because an arm that flags
    everything is as useless as one that flags nothing.
    """
    inline = "def f(identity):\n    return get_adapter(p, sandbox=identity.sandbox)\n"
    via_local = "def f(identity):\n    s = identity.sandbox\n    return get_adapter(p, sandbox=s)\n"
    via_self = "def f(self):\n    s = self.identity.sandbox\n    return get_adapter(p, sandbox=s)\n"
    derived = "def f(uow, mb):\n    s = uow.sandbox_mode(mb)\n    return get_adapter(p, sandbox=s)\n"

    def _flags(source: str) -> bool:
        calls = _adapter_calls_in(ast.parse(source), "synthetic.py")
        assert len(calls) == 1, f"expected one get_adapter call in:\n{source}"
        # Through the arm's own offender lister, so neutering the arm reddens this.
        return bool(_identity_sourced_offenders(calls))

    assert _flags(inline), "the arm no longer catches the inline identity.sandbox form"
    assert _flags(via_local), "the arm does not follow a local assignment — this is how it was inert"
    assert _flags(via_self), "the arm misses self.identity.sandbox"
    assert not _flags(derived), "the arm flags a correct buy-keyed derivation"


def test_no_call_site_hard_wires_the_sandbox_mode() -> None:
    """A literal satisfies the presence check while deciding nothing.

    Replacing any site's expression with ``sandbox=False`` — the cheapest way to
    satisfy arm 1 — left that arm green at 12 of 12 sites, and the unit suite
    byte-identical for several of them. Presence and value are different claims, so
    they get different arms: this one rejects a constant unless the site is listed in
    LITERAL_EXEMPT with a written reason.
    """
    offenders = _hard_wired_offenders(_get_adapter_calls())

    assert not offenders, (
        "get_adapter() called with a hard-wired sandbox= literal at:\n  "
        + "\n  ".join(offenders)
        + "\n\nA constant dispatches every request to one mode regardless of the account. "
        "Derive it (identity.sandbox where the request carries an account reference, "
        "uow.sandbox_mode*/partition_by_sandbox_mode on buy-keyed paths), or add the site "
        "to LITERAL_EXEMPT with the reason the mode is genuinely static."
    )


def test_literal_arm_catches_both_the_inline_and_the_via_local_form() -> None:
    """Self-test through the real predicate, on both idioms and both signs.

    This previously asserted ``isinstance(_value(tree), ast.Constant)`` — its own copy
    of the predicate — so it stayed green when the arm's comprehension condition was
    neutered, and it never exercised the via-local form at all. That form is the one
    production writes at 4 of 12 sites, and it is where the arm was blind: ``s = False``
    followed by ``sandbox=s`` is a hard-wired live dispatch the guard passed.
    """

    def _flags(source: str) -> bool:
        calls = _adapter_calls_in(ast.parse(source), "synthetic.py")
        assert len(calls) == 1, f"expected one get_adapter call in:\n{source}"
        # Through the arm's own offender lister, not just the predicate: neutering the
        # arm must redden its proof.
        return bool(_hard_wired_offenders(calls))

    assert _flags("def f():\n    return get_adapter(p, sandbox=False)\n"), (
        "the literal arm no longer catches an inline constant"
    )
    assert _flags("def f():\n    s = False\n    return get_adapter(p, sandbox=s)\n"), (
        "the literal arm does not follow a local assignment — this is how it was blind at 4 of 12 sites"
    )
    assert not _flags("def f(identity):\n    return get_adapter(p, sandbox=identity.sandbox)\n"), (
        "the literal arm flags a legitimate identity-keyed derivation"
    )
    assert not _flags("def f(uow, mb):\n    s = uow.sandbox_mode(mb)\n    return get_adapter(p, sandbox=s)\n"), (
        "the literal arm flags a legitimate buy-keyed derivation"
    )
    assert not _flags(
        "def f(cond, identity):\n    s = False\n    if cond:\n        s = identity.sandbox\n    return get_adapter(p, sandbox=s)\n"
    ), (
        "a local reassigned from the account is not hard-wired; flagging it would push contributors "
        "toward the allowlist to silence a false positive"
    )


def test_literal_arm_follows_a_for_loop_target() -> None:
    """The for-loop binding form — media_buy_list.py's actual shape — through the real
    predicate, both signs.

    Production writes ``for partition, partition_is_sandbox in ((live_buys, False),
    (sandbox_buys, True)): ... sandbox=partition_is_sandbox``. Before
    :func:`_for_loop_candidates` existed, this loop target was invisible to both arms —
    a for-loop target is neither an ``ast.Assign`` nor an ``ast.AnnAssign`` — so a
    regression collapsing both partitions to the same literal (routing every buy through
    one adapter mode regardless of which partition it came from) would have passed both
    arms silently. This asserts through the real offender lister, not a re-implementation
    of the predicate, so neutering the resolver reddens this test, not just the guard.
    """

    def _flags(source: str) -> bool:
        calls = _adapter_calls_in(ast.parse(source), "synthetic.py")
        assert len(calls) == 1, f"expected one get_adapter call in:\n{source}"
        return bool(_hard_wired_offenders(calls))

    # The real shape: two DIFFERENT literals, one per partition — legitimate.
    assert not _flags(
        "def f(live_buys, sandbox_buys):\n"
        "    for partition, is_sbx in ((live_buys, False), (sandbox_buys, True)):\n"
        "        return get_adapter(p, sandbox=is_sbx)\n"
    ), "two different per-partition literals is a legitimate derivation, not a hard-wire"

    # The regression this arm exists to catch: both partitions collapsed to the SAME
    # literal — the value no longer depends on which partition is being processed.
    assert _flags(
        "def f(live_buys, sandbox_buys):\n"
        "    for partition, is_sbx in ((live_buys, False), (sandbox_buys, False)):\n"
        "        return get_adapter(p, sandbox=is_sbx)\n"
    ), "both partitions passing the same literal is exactly the hard-wire this arm exists to catch"

    # A bare (non-tuple) for-loop target over a literal iterable, for completeness.
    assert _flags("def f():\n    for is_sbx in (False, False):\n        return get_adapter(p, sandbox=is_sbx)\n"), (
        "a bare for-loop target collapsed to one literal must also be caught"
    )
    assert not _flags("def f():\n    for is_sbx in (False, True):\n        return get_adapter(p, sandbox=is_sbx)\n"), (
        "a bare for-loop target genuinely varying across iterations is not a hard-wire"
    )


def test_exemptions_are_function_scoped_not_module_scoped() -> None:
    """A second hard-wired call in an already-exempt MODULE must still be flagged.

    Both allowlists were keyed by module at some point, and both times the entry that
    covered one legitimate site silently covered every future one in the same file. This
    asserts the property rather than the spelling: every key names a function, and adding
    a call in an exempt module under a DIFFERENT function is not exempt.
    """
    for key in (*LITERAL_EXEMPT, *IDENTITY_KEYED_SITES):
        assert "::" in key, f"{key!r} exempts a whole module — key it as 'path::function' instead"

    exempt_modules = {key.split("::")[0] for key in LITERAL_EXEMPT}
    assert exempt_modules, "no literal exemptions to check — this self-test has gone vacuous"
    for module in exempt_modules:
        intruder = f"{module}::_some_other_function"
        assert intruder not in LITERAL_EXEMPT, (
            f"{intruder} is exempt without its own entry — the arm is module-scoped again"
        )


def test_every_exemption_names_a_real_call_site() -> None:
    """An allowlist entry that exempts nothing reads as coverage it does not provide.

    The removed ``_execute_adapter_media_buy_creation`` entry matched no site the arm
    would ever flag — it forwards a parameter, which the arm does not resolve — so the
    file read as if that path were reviewed and exempted when it was simply invisible.
    Stale entries are how an allowlist grows without anyone deciding to grow it.

    ``literal_sites`` is computed through ``_hard_wires_sandbox`` — the same one-hop
    resolver the arm itself uses — not a raw ``isinstance(call.value, ast.Constant)``.
    Both LITERAL_EXEMPT entries happen to be inline today, so the two checks agree; a
    future exemption written in production's own via-local idiom (``s = False;
    sandbox=s``) would resolve to a bare ``Name`` under the raw check and be reported
    stale even though the arm correctly matches and exempts it.
    """
    calls = _get_adapter_calls()
    literal_sites = {call.site for call in calls if _hard_wires_sandbox(call)}
    identity_sites = {
        call.site for call in calls if call.value is not None and _resolves_to_identity_sandbox(call.value, call.func)
    }

    assert not (set(LITERAL_EXEMPT) - literal_sites), (
        "LITERAL_EXEMPT entries that no longer match a hard-wired call site: "
        f"{sorted(set(LITERAL_EXEMPT) - literal_sites)} — delete them; the site they "
        "described has changed or gone"
    )
    assert not (set(IDENTITY_KEYED_SITES) - identity_sites), (
        "IDENTITY_KEYED_SITES entries that no longer match a call reading identity.sandbox: "
        f"{sorted(set(IDENTITY_KEYED_SITES) - identity_sites)} — delete them; an entry that "
        "exempts nothing is not coverage"
    )
