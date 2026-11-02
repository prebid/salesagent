"""No BDD step file reaches a transport without gating and recording what it sent.

THE SHAPE THIS PREVENTS is the one that produced salesagent-99w2t: a canonical
helper lands, the ticket that introduced it wires the motivating site, and the
other call sites keep dispatching around it. ``assert_declared_malformations`` had
one call site and three step-level entries; the payload capture would have
inherited exactly that, and a gate with an unmeasured path is not a gate — it is a
claim about the path someone happened to look at.

The convergence removed one entry (``when_request._call_via`` folded into
``dispatch_request``). ``dispatch_via_client`` cannot be removed —
``AccountListDispatchMixin.is_list_request`` discriminates on
``isinstance(kwargs["req"], ListAccountsRequest)``, so a raw list payload sent
through ``env.call_via`` is misrouted to ``sync_accounts`` — so it is gated in
place. Two read-back sites reach a transport directly and must not route through
either ctx-writing entry, because ``ctx["result"]`` has exactly one producer and a
read-back must not clobber the result the scenario is grading; they call
``gate_and_record`` themselves.

That is the complete set, and it may only shrink. A new direct site fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

BDD_ROOT = Path(__file__).resolve().parents[1] / "bdd"

#: ``call_via`` is matched on the METHOD NAME ALONE, whatever the receiver is called.
#: The harness defines it in one place and nothing else in this repo spells it, so a
#: future site written as ``seller_env.call_via(...)`` cannot slip past by naming its
#: variable something this guard never heard of.
_TRANSPORT_METHOD = "call_via"

#: ``.call`` is different: ``mock.call``, ordinary helpers and callables spell it too,
#: so it is matched only on receivers that really are the transport-generic client. A
#: guard that fired on every ``.call`` would need an allowlist of unrelated rows and
#: would stop meaning anything.
_CLIENT_METHOD = "call"
_CLIENT_RECEIVERS = {"client", "_client", "test_client"}

#: The one module that owns the dispatch entries. Its ``gate_and_record`` call is
#: the obligation every other site borrows.
_ENTRY_MODULE = "steps/generic/_dispatch.py"

#: Sites that reach a transport directly, by function. Each must call
#: ``gate_and_record`` itself, which is what the assertion below checks — being on
#: this list buys the right to dispatch directly, never the right to skip the gate.
#: ALLOWLISTS ONLY SHRINK. A new entry here is a design decision, not a fix.
_DIRECT_DISPATCH_SITES = {
    ("steps/domain/uc006_storyboard_creative_sync.py", "then_format_id_roundtrips_verbatim"),
    ("steps/domain/uc011_accounts.py", "_persisted_subscribers"),
}


def _enclosing_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def _reaches_transport(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr == _TRANSPORT_METHOD:
        return True
    if node.func.attr != _CLIENT_METHOD:
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Name):
        return receiver.id in _CLIENT_RECEIVERS
    if isinstance(receiver, ast.Attribute):
        return receiver.attr in _CLIENT_RECEIVERS
    return False


def _calls_gate(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "gate_and_record"
        for node in ast.walk(function)
    )


def _dispatch_sites() -> list[tuple[str, str]]:
    """Every ``(module, function)`` under tests/bdd that reaches a transport."""
    sites: list[tuple[str, str]] = []
    for path in sorted(BDD_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(BDD_ROOT).as_posix()
        for function in _enclosing_functions(tree):
            if any(_reaches_transport(node) for node in ast.walk(function)):
                sites.append((rel, function.name))
    return sites


def test_every_transport_reaching_site_is_the_entry_module_or_allowlisted() -> None:
    unexpected = [site for site in _dispatch_sites() if site[0] != _ENTRY_MODULE and site not in _DIRECT_DISPATCH_SITES]
    assert unexpected == [], (
        f"{len(unexpected)} BDD site(s) reach a transport outside the dispatch entries and outside the "
        "named read-back allowlist:\n  "
        + "\n  ".join(f"{module}::{function}" for module, function in unexpected)
        + "\n\nDispatch through tests/bdd/steps/generic/_dispatch.py (dispatch_request or "
        "dispatch_via_client), which gate the payload and record it. If the site genuinely cannot — a "
        "read-back must not clobber ctx['result'] — call gate_and_record(payload) yourself and add the "
        "function to _DIRECT_DISPATCH_SITES with the reason. A dispatch nothing records reads as "
        "'this nodeid dispatched nothing', which is a lie the comparator cannot detect."
    )


def test_every_allowlisted_direct_site_still_gates_and_records() -> None:
    """Being on the allowlist buys direct dispatch, never a pass on the obligation."""
    ungated: list[str] = []
    for module, function_name in sorted(_DIRECT_DISPATCH_SITES):
        tree = ast.parse((BDD_ROOT / module).read_text(encoding="utf-8"))
        matches = [f for f in _enclosing_functions(tree) if f.name == function_name]
        assert matches, f"stale allowlist row: {module}::{function_name} no longer exists — remove it"
        if not all(_calls_gate(f) for f in matches):
            ungated.append(f"{module}::{function_name}")
    assert ungated == [], "allowlisted direct-dispatch site(s) no longer call gate_and_record: " + ", ".join(ungated)


def test_the_entry_module_gates_every_entry_it_owns() -> None:
    """Both surviving entries, by name — a third would have to declare itself here."""
    tree = ast.parse((BDD_ROOT / _ENTRY_MODULE).read_text(encoding="utf-8"))
    entries = {f.name for f in _enclosing_functions(tree) if any(_reaches_transport(n) for n in ast.walk(f))}
    assert entries == {"dispatch_request", "dispatch_via_client"}, (
        f"the dispatch entry module reaches a transport from {sorted(entries)}; the design is exactly two "
        "entries, both gated. A third entry is how the gate came to cover one path of three."
    )
    for name in sorted(entries):
        function = next(f for f in _enclosing_functions(tree) if f.name == name)
        assert _calls_gate(function), f"{name} reaches a transport without calling gate_and_record"
