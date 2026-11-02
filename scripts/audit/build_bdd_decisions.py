#!/usr/bin/env python3
"""Render the BDD findings as DECISIONS, grouped by defect class.

The companion page (``build_bdd_findings_report.py``) renders every agent report
whole. That is the evidence, and it is unreviewable as a work queue: it answers
"what did the agents find" when the question is "what do I have to decide".

This page inverts it. One row per decision, grouped by the defect class it
belongs to, each with the measured count, the verbatim evidence, and the specific
question whose answer unblocks the work. Nothing here is a summary of an agent
report — the counts are measured directly from the tree at render time, so the
page cannot drift from the code the way a hand-written finding list does.

    python3 scripts/audit/build_bdd_decisions.py <out.html>
"""

from __future__ import annotations

import ast
import collections
import functools
import glob
import html
import pathlib
import re
import sys

STEPS = "tests/bdd/steps/**/*.py"
FEATURES = "tests/bdd/features/*.feature"


def step_functions():
    """Every decorated step definition, with its AST node and location."""
    for path in sorted(glob.glob(STEPS, recursive=True)):
        text = pathlib.Path(path).read_text()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            kinds = {
                (d.func.id if isinstance(d.func, ast.Name) else getattr(d.func, "attr", ""))
                for d in node.decorator_list
                if isinstance(d, ast.Call)
            }
            kinds &= {"given", "when", "then"}
            if kinds:
                yield path, node, kinds, lines


def called_names(node: ast.AST) -> set[str]:
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def snippet(lines: list[str], lineno: int, span: int = 7) -> str:
    start = max(0, lineno - 1)
    return "\n".join(lines[start : start + span])


# ── Assertion reachability: a THREE-STATE verdict, resolved by following the call ──
#
# The question a Then-step classifier must answer is "does this step grade anything",
# and the only sound way to answer it is to FOLLOW THE CALL. This script used to ask
# instead whether a callee's NAME began with assert_/require_/expect_/verify_/check_
# — a proxy, and one wrong in the direction that inflates the finding. Measured on the
# no-creative-literal half of the tree: 49 sites read as "grades nothing", 47 of them
# reach an assertion through a helper whose name matches no prefix (``_expect_flag``,
# ``wire_dict``, ``wire_field``, ``_assert_billing_party_array``), and the remaining 2
# assert as well. The 337/174/65 headline set the scope of a whole epic on that basis
# (salesagent-v03pe.4).
#
# So the verdict is resolved, and it has three states, never two:
#
#   GRADES        an assertion is reachable — the step raises or asserts itself, or a
#                 callee resolved inside the scanned tree does, transitively.
#   GRADES_NOTHING no assertion is reachable AND every callee on every path was
#                 resolved. The absence is PROVEN, not assumed.
#   UNDECIDABLE   no assertion was found, but at least one callee could not be resolved
#                 (a name defined outside the scanned roots, a method on an object this
#                 scanner does not type, or a path past the depth limit). Counted and
#                 printed separately, NEVER folded into either side — an instrument that
#                 quietly calls what it cannot evaluate "fine" is the same defect one
#                 axis over (salesagent-b341x.18 established the convention).
#
# RESOLUTION RULES, stated so they can be argued with:
#
#   * A callee is looked up by BARE NAME across every function and method defined under
#     ROOTS. Attribute calls (``result.assert_wire_error(...)``) resolve by method name,
#     because this scanner does not infer receiver types; that is a deliberate widening,
#     and its cost is recorded — an unresolvable name is UNDECIDABLE, not "grades nothing".
#   * One name may map to several definitions. The step GRADES if ANY of them asserts.
#     Conservative in the direction that avoids inflating the finding, which is the
#     direction the old proxy got wrong.
#   * Cycles are tracked and do not recurse. Exceeding MAX_DEPTH yields UNDECIDABLE
#     rather than a negative verdict.
#   * Builtins that cannot assert are not treated as unresolved; see _INERT.

ROOTS = ("tests/bdd/steps/**/*.py", "tests/harness/**/*.py", "tests/helpers/**/*.py", "tests/factories/**/*.py")
MAX_DEPTH = 6

#: Callees that provably cannot assert, so an unresolved lookup of one is not a hole.
#: Builtins and stdlib constructors only — anything project-defined must be resolved.
_INERT = frozenset(
    """len list dict set tuple str int float bool sorted any all zip range enumerate
    getattr setattr hasattr isinstance issubclass type repr format join split strip lower upper
    append extend add update get keys values items pop setdefault copy print min max sum abs round
    startswith endswith replace splitlines encode decode dumps loads deepcopy fullmatch search match
    group compile escape now utcnow isoformat fromisoformat timedelta Decimal UUID uuid4 hex""".split()
)

GRADES, GRADES_NOTHING, UNDECIDABLE = "GRADES", "GRADES_NOTHING", "UNDECIDABLE"


@functools.lru_cache(maxsize=1)
def _definitions() -> dict[str, list[ast.AST]]:
    """Every function/method body in the scanned roots, keyed by bare name."""
    defs: dict[str, list[ast.AST]] = collections.defaultdict(list)
    for pattern in ROOTS:
        for path in glob.glob(pattern, recursive=True):
            try:
                tree = ast.parse(pathlib.Path(path).read_text())
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs[node.name].append(node)
    return defs


def _callee_names(node: ast.AST) -> list[str]:
    """Bare names of everything this body calls, attribute calls included."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name:
                out.append(name)
    return out


def assertion_verdict(node: ast.AST, _seen: frozenset = frozenset(), _depth: int = 0) -> str:
    """GRADES / GRADES_NOTHING / UNDECIDABLE for one function body."""
    if any(isinstance(s, (ast.Assert, ast.Raise)) for s in ast.walk(node)):
        return GRADES
    if _depth >= MAX_DEPTH:
        return UNDECIDABLE
    defs = _definitions()
    unresolved = False
    for name in _callee_names(node):
        if name in _INERT or name in _seen:
            continue
        bodies = defs.get(name)
        if not bodies:
            unresolved = True
            continue
        for body in bodies:
            verdict = assertion_verdict(body, _seen | {name}, _depth + 1)
            if verdict == GRADES:
                return GRADES
            if verdict == UNDECIDABLE:
                unresolved = True
    return UNDECIDABLE if unresolved else GRADES_NOTHING


def _is_comparison(test: ast.AST) -> bool:
    """Does this assert test COMPARE something, rather than check truthiness?

    Walks the WHOLE test expression rather than matching its top node. A top-node match
    was tried first and under-detected badly: ``assert any(kw in text for kw in KEYWORDS)``
    is an ``ast.Call`` at its top node, so it read as truthiness even though the comparison
    it performs is the entire point. Under-detecting here inflates the finding, which is the
    direction this rewrite exists to stop.
    """
    for sub in ast.walk(test):
        if isinstance(sub, ast.Compare):
            return True
        if isinstance(sub, ast.Call):
            fn = sub.func.id if isinstance(sub.func, ast.Name) else getattr(sub.func, "attr", "")
            if fn in ("isinstance", "issubclass"):
                return True
    return False


def _callee_asserts(node: ast.AST) -> bool:
    """Does anything this body CALLS assert, ignoring the body's own asserts?

    Distinct from :func:`assertion_verdict`, which short-circuits on the caller's own
    ``assert``. The presence-only class needs the other question — "is this step the
    whole oracle, or does a helper grade for it" — and answering it with the first
    function silently empties the class to zero, which is how this was caught.
    """
    defs = _definitions()
    for name in _callee_names(node):
        if name in _INERT:
            continue
        for body in defs.get(name, ()):
            if assertion_verdict(body, frozenset({name}), 1) == GRADES:
                return True
    return False


def _branch_asserts(branch: list) -> bool:
    """Does any statement on this branch assert, raise, or call something that does?"""
    for stmt in branch:
        if any(isinstance(s, (ast.Assert, ast.Raise)) for s in ast.walk(stmt)):
            return True
        if assertion_verdict(stmt) == GRADES:
            return True
    return False


DISPATCH = ("call_via", "dispatch_request", "call_raw", "_call")


def collect():
    """Every finding, keyed by defect class. Measured, not recalled."""
    found = collections.defaultdict(list)
    for path, node, kinds, lines in step_functions():
        name = pathlib.Path(path).name
        loc = f"{name}:{node.lineno}"
        calls = called_names(node)
        dumped = ast.dump(node)
        has_assert = any(isinstance(s, ast.Assert) for s in ast.walk(node))
        has_raise = any(isinstance(s, ast.Raise) for s in ast.walk(node))
        if "then" in kinds and not (has_assert or has_raise):
            # Resolved, not guessed. The three states are kept apart on purpose: a step
            # whose helper this scanner cannot follow is UNDECIDABLE, and saying so IS the
            # finding — folding it into "grades nothing" is what produced the 65.
            verdict = assertion_verdict(node)
            if verdict == GRADES_NOTHING:
                found["then-grades-nothing"].append((loc, snippet(lines, node.lineno)))
            elif verdict == UNDECIDABLE:
                found["then-undecidable"].append((loc, snippet(lines, node.lineno)))

        if "then" in kinds and any(d in calls for d in DISPATCH):
            found["then-dispatches"].append((loc, snippet(lines, node.lineno)))

        if "given" in kinds and any(d in calls for d in DISPATCH):
            found["given-dispatches"].append((loc, snippet(lines, node.lineno)))

        if "then" in kinds:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "ctx"
                    for t in sub.targets
                ):
                    found["then-writes-ctx"].append((loc, snippet(lines, node.lineno)))
                    break

        if {"patch", "MagicMock", "Mock", "monkeypatch"} & calls:
            found["step-patches-mock"].append((loc, snippet(lines, node.lineno)))

        if "mock" in dumped and "env" in dumped and re.search(r"env\.mock", ast.unparse(node)):
            found["step-reaches-env-mock"].append((loc, snippet(lines, node.lineno)))

        raw = sum(
            1
            for s in ast.walk(node)
            if isinstance(s, ast.Subscript) and isinstance(s.value, ast.Name) and s.value.id == "ctx"
        )
        if raw >= 8:
            found["raw-ctx-heavy"].append((f"{loc} — {raw} subscripts", snippet(lines, node.lineno)))

        # PRESENCE-ONLY GRADING, which is not "contains a bare assert".
        #
        # The old rule flagged any ``assert x``, and on the half of the tree audited by
        # hand it was almost entirely ANTI-VACUITY GUARDS: ``assert accounts, "expected a
        # non-empty array"`` standing in FRONT of ``assert returned == expected``. That
        # guard is good practice — it stops an empty collection satisfying an all() — and
        # counting it as a defect is what made this class 174 sites.
        #
        # A step grades by presence only when NOTHING in it compares: no comparison in any
        # of its own asserts, no raise, and no CALLEE that asserts on its behalf. THEN
        # steps only — a Given or When asserting ``account_id`` is a setup guard, and
        # grading is not its job.
        asserts = [s for s in ast.walk(node) if isinstance(s, ast.Assert)]
        bare = [s for s in asserts if isinstance(s.test, (ast.Name, ast.Attribute))]
        raises = any(isinstance(s, ast.Raise) for s in ast.walk(node))
        if (
            "then" in kinds
            and bare
            and not raises
            and not any(_is_comparison(s.test) for s in asserts)
            and not _callee_asserts(node)
        ):
            found["presence-only"].append((loc, snippet(lines, node.lineno)))

        # ── The classes that actually found the defects in the audited half ──
        # Added because the classes above found 5 real defects across 205 flagged sites
        # there, and these two found most of those 5 (salesagent-v03pe.4).
        if "then" in kinds:
            params = [a.arg for a in node.args.args if a.arg not in ("ctx", "request", "env")]
            if params:
                graded = set()
                for st in ast.walk(node):
                    if isinstance(st, ast.Assert):
                        graded |= {n.id for n in ast.walk(st.test) if isinstance(n, ast.Name)}
                    elif isinstance(st, ast.Call):
                        for arg in list(st.args) + [k.value for k in st.keywords]:
                            graded |= {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
                    elif isinstance(st, (ast.Assign, ast.Return)) and getattr(st, "value", None) is not None:
                        graded |= {n.id for n in ast.walk(st.value) if isinstance(n, ast.Name)}
                    elif isinstance(st, (ast.If, ast.While)):
                        graded |= {n.id for n in ast.walk(st.test) if isinstance(n, ast.Name)}
                    elif isinstance(st, (ast.For, ast.comprehension)):
                        graded |= {n.id for n in ast.walk(st.iter) if isinstance(n, ast.Name)}
                ungraded = [p for p in params if p not in graded]
                if ungraded:
                    found["param-never-graded"].append((f"{loc} — {', '.join(ungraded)}", snippet(lines, node.lineno)))

            for sub in ast.walk(node):
                if not isinstance(sub, ast.If):
                    continue
                hit = False
                for branch in (sub.body, sub.orelse):
                    if branch and isinstance(branch[-1], ast.Return) and not _branch_asserts(branch):
                        found["vacuous-return-branch"].append(
                            (f"{loc} — returns at line {branch[-1].lineno}", snippet(lines, node.lineno))
                        )
                        hit = True
                        break
                if hit:
                    break
    return found


# ── Where a step module is REGISTERED, which decides whether a shadow competes ──
#
# "The same sentence is defined in two modules" is not by itself a hazard, and treating
# it as one made this class 11 sites of which ZERO were real. Whether two definitions
# compete depends on where each module is REGISTERED:
#
#   GLOBAL   listed in tests/bdd/conftest.py's ``pytest_plugins`` — in scope for every
#            feature in the suite.
#   LOCAL    star-imported by one test module, which scopes it to that module's
#            scenarios. uc019 does this deliberately and says why in its own docstring:
#            it redefines generic step texts and a global registration "would override
#            the generic versions for every other UC".
#   NOWHERE  neither. then_media_buy.py is in this state on purpose — its steps are
#            re-exported one at a time by registered modules — so its definitions cannot
#            compete with anything.
#
# Measured on this tree: of 11 shadowed sentences, 6 involve then_media_buy.py (registered
# NOWHERE) and 5 involve uc019 (LOCAL, deliberate scoped override). None is a same-scope
# collision. Reporting all 11 as "which one runs depends on plugin registration order"
# was answering a question the detector had not asked.

_CONFTEST = "tests/bdd/conftest.py"


@functools.lru_cache(maxsize=1)
def _registration_scopes() -> dict[str, frozenset[str]]:
    """Module basename -> the scopes it is registered in ("GLOBAL", or a test module)."""
    scopes: dict[str, set[str]] = collections.defaultdict(set)
    try:
        tree = ast.parse(pathlib.Path(_CONFTEST).read_text())
    except OSError:
        return {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytest_plugins" for t in node.targets):
            for element in getattr(node.value, "elts", []):
                if isinstance(element, ast.Constant):
                    scopes[element.value.split(".")[-1] + ".py"].add("GLOBAL")
    for path in glob.glob("tests/bdd/test_*.py"):
        text = pathlib.Path(path).read_text()
        for match in re.finditer(r"from tests\.bdd\.steps[.\w]*\.(\w+) import \*", text):
            scopes[match.group(1) + ".py"].add(pathlib.Path(path).name)
    return {name: frozenset(where) for name, where in scopes.items()}


def classify_shadow(modules: list[str]) -> str:
    """COMPETING / SCOPED_OVERRIDE / NOT_REGISTERED for one shadowed sentence."""
    scopes = _registration_scopes()
    where = [scopes.get(module, frozenset()) for module in modules]
    if any(not w for w in where):
        return "NOT_REGISTERED"
    for i, a in enumerate(where):
        for b in where[i + 1 :]:
            if a & b:
                return "COMPETING"
    return "SCOPED_OVERRIDE"


def shadowed():
    """Sentences bound in more than one module, with the bodies that compete."""
    reg = collections.defaultdict(list)
    for path, node, _kinds, _ in step_functions():
        body = [x for x in node.body if not (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant))]
        key = ast.dump(ast.Module(body=body, type_ignores=[]), annotate_fields=False)
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            fn = d.func.id if isinstance(d.func, ast.Name) else getattr(d.func, "attr", "")
            if fn not in ("given", "when", "then"):
                continue
            a = d.args[0] if d.args else None
            s = (
                a.value
                if isinstance(a, ast.Constant)
                else (
                    a.args[0].value
                    if isinstance(a, ast.Call) and a.args and isinstance(a.args[0], ast.Constant)
                    else None
                )
            )
            if s:
                reg[(fn, s)].append((pathlib.Path(path).name, node.name, key))
    return {k: v for k, v in reg.items() if len({m for m, _, _ in v}) > 1}


DECISIONS = [
    (
        "then-grades-nothing",
        "A Then that grades nothing",
        "No assert, no raise, and — following every call transitively through tests/{bdd/steps,harness,helpers,factories} — no helper that asserts either. The absence is PROVEN: every callee on every path resolved. A step whose helper could NOT be resolved is not here; it is under 'Cannot be decided by this scanner'.",
        "Delete the step and the sentence, or write the assertion it implies? Each needs the scenario read to know which.",
    ),
    (
        "shadowed",
        "One sentence, two competing bodies",
        "The same sentence is defined in two modules that are BOTH registered in the SAME scope, with different normalized bodies. Which one runs depends on registration order, and pytest-bdd does not warn. Same class as the duplicate-scenario-name trap.",
        "For each: is one body correct and the other dead, or do they mean different things and need two sentences? Three of seven reviewed had the first opinion objected to.",
    ),
    (
        "shadowed-scoped-override",
        "One sentence, deliberately overridden in one module's scope",
        "The same sentence is defined in a GLOBAL module and in one star-imported by a single test module. That is a deliberate scoped override, not a collision: uc019 does it so its redefinitions of generic step texts apply to UC-019 scenarios only, and says so in its own docstring. Listed so the arrangement stays visible, not because it is wrong.",
        "Is the override still wanted, and does its module docstring still say why? Nothing to fix while both are true.",
    ),
    (
        "shadowed-not-registered",
        "One sentence, but one definition can never run",
        "The same sentence is defined in two modules, at least one of which is registered NOWHERE — not in conftest's pytest_plugins and not star-imported by any test module. Its definition cannot compete, so registration order decides nothing. then_media_buy.py is in this state deliberately; registered modules re-export its steps one at a time.",
        "Nothing to decide unless the unregistered module is meant to be live. Reported so the state is visible rather than inferred.",
    ),
    (
        "then-dispatches",
        "A Then that performs the action it grades",
        "The assertion step calls the dispatch seam. A Then that acts is a When wearing the wrong keyword, and it makes the scenario's own Given/When/Then structure a lie about what happened in which order.",
        "Move the dispatch to a When, or is the second call deliberate (a re-read, an idempotency probe)?",
    ),
    (
        "given-dispatches",
        "A Given that performs the action",
        "Setup that dispatches through the tool seam. The scenario then has two actions and the When is not the one under test.",
        "Is the dispatch setup (seed via the API because no factory exists) or is it the action? If setup, it wants a factory; if the action, it wants to be the When.",
    ),
    (
        "then-writes-ctx",
        "A Then that mutates state",
        "An assertion step writing back into ctx. Later steps then depend on assertion order, so removing or reordering an assertion changes behaviour rather than just coverage.",
        "What later step consumes the write? If none, delete the write. If one, the value belongs in the When's result, not in an assertion.",
    ),
    (
        "step-patches-mock",
        "A step that patches a mock",
        "Patching inside a step definition. The scenario then grades the patch rather than production, and the patch is invisible in the feature file — a reader cannot tell the system was replaced.",
        "Can the state be produced for real (a factory, a seeded row)? If genuinely not, should the scenario say so out loud rather than hide it in a step?",
    ),
    (
        "step-reaches-env-mock",
        "A step reaching into env.mock[...]",
        "Direct access to the mock registry, bypassing the realization seam. These are the sites that cannot work on e2e, and they are not declared unsupported anywhere — so they are invisible until the transport changes.",
        "Route through the seam, or declare unsupported? Silently working on one transport is the outcome to avoid.",
    ),
    (
        "raw-ctx-heavy",
        "Raw ctx access, 8+ subscripts in one step",
        "A step reading and writing many ctx keys directly instead of going through the guarded helpers. Every raw read is a place a key can be absent, misspelled, or written by a different sentence than the one the author had in mind.",
        "Which of these keys are a real contract between steps, and which are incidental? The contract ones want a named accessor.",
    ),
    (
        "presence-only",
        "The step grades presence and nothing else",
        "Every assertion in the step is `assert x` with no comparison anywhere, it raises nothing, and no callee asserts on its behalf — so a wrong value of the right shape sails through. This is NOT every bare assert: `assert accounts, 'expected a non-empty array'` in front of `assert returned == expected` is an anti-vacuity guard and good practice, and counting those is what made the predecessor class 174 sites.",
        "What is the actual expected value? A truthy check that is the step's ONLY check is nearly always a missed equality assertion.",
    ),
    (
        "then-undecidable",
        "Cannot be decided by this scanner",
        "The step has no assertion of its own and calls something this scanner could not resolve — a name defined outside tests/{bdd/steps,harness,helpers,factories}, a method on an object it does not type, or a path past the depth limit. It may grade perfectly well. It is reported because an instrument that quietly files what it cannot evaluate under 'fine' — or under 'broken' — is answering a question it cannot answer.",
        "Read the call. If the helper asserts, nothing is wrong; if the resolution gap is systematic, widen ROOTS rather than guessing.",
    ),
    (
        "param-never-graded",
        "A value the scenario names that the step never grades",
        "The sentence names a value — 'for media buy X', 'with status Y' — and the step takes it as a parameter, then never puts it in a comparison, a call, or a filter. Any value passes, so the scenario's own Examples column grades nothing.",
        "Assert it, or take it out of the sentence. This class found the uc019 advisory defect, where 'exactly one advisory for media buy X' checked the field and never X.",
    ),
    (
        "vacuous-return-branch",
        "A path that returns having asserted nothing",
        "One branch of the step returns without asserting, so whatever leads down that branch passes unconditionally. Legitimate for a genuinely tri-state oracle whose SENTENCE says so ('a PRESENT section should include ...'), which is why this is a question rather than a verdict.",
        "Does the scenario's sentence admit the skipped case? This class found the UC-004 exclusion pair, where a request that 500ed satisfied 'the response should not include delivery data'.",
    ),
]

CSS = """
:root{--bg:#fff;--fg:#191919;--mut:#6a6a6a;--line:#e3e3e3;--code:#f7f7f5;--red:#b3261e;--amb:#8a6100;--ok:#0a6b3d}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#151515;--fg:#e8e8e8;--mut:#9a9a9a;--line:#2d2d2d;--code:#1d1d1d;--red:#ff8a80;--amb:#ffcc66;--ok:#7fd1a5}}
:root[data-theme=dark]{--bg:#151515;--fg:#e8e8e8;--mut:#9a9a9a;--line:#2d2d2d;--code:#1d1d1d;--red:#ff8a80;--amb:#ffcc66;--ok:#7fd1a5}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:34px 22px 100px}
h1{font-size:25px;margin:0 0 6px}
.lede{color:var(--mut);margin:0 0 26px;max-width:70ch}
.sum{width:100%;border-collapse:collapse;margin:0 0 34px;font-size:14px}
.sum th,.sum td{border:1px solid var(--line);padding:7px 10px;text-align:left}
.sum th{background:var(--code)}
.sum td.n{text-align:right;font:13px ui-monospace,Menlo,monospace;width:5.5em}
.sum a{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
.d{border:1px solid var(--line);border-radius:7px;margin:22px 0;overflow:hidden}
.d>h2{margin:0;padding:13px 17px;font-size:17px;background:var(--code);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.cnt{font:13px ui-monospace,Menlo,monospace;color:var(--mut);white-space:nowrap}
.d .in{padding:14px 17px}
.why{margin:0 0 10px}
.ask{border-left:3px solid var(--amb);background:var(--code);padding:9px 13px;margin:12px 0 4px;border-radius:0 4px 4px 0}
.ask strong{color:var(--amb)}
details.ev{margin-top:12px}
details.ev>summary{cursor:pointer;color:var(--mut);font-size:13.5px}
pre{background:var(--code);border:1px solid var(--line);border-radius:4px;padding:10px 12px;overflow-x:auto;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:6px 0 14px}
.loc{font:12.5px ui-monospace,Menlo,monospace;color:var(--mut);display:block;margin-top:10px}
code{background:var(--code);padding:1px 5px;border-radius:3px;font:13px ui-monospace,Menlo,monospace}
.more{color:var(--mut);font-size:13px;margin:4px 0 0}
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = pathlib.Path(sys.argv[1])
    found = collect()
    sh = shadowed()
    key_for = {
        "COMPETING": "shadowed",
        "SCOPED_OVERRIDE": "shadowed-scoped-override",
        "NOT_REGISTERED": "shadowed-not-registered",
    }
    for (kw, sentence), v in sorted(sh.items()):
        modules = sorted({m for m, _, _ in v})
        verdict = classify_shadow(modules)
        scopes = _registration_scopes()
        found[key_for[verdict]].append(
            (
                f"@{kw} {sentence}",
                "\n".join(f"{m}::{fn}" for m, fn, _ in v)
                + f"\n\n# {len({b for _, _, b in v})} distinct bodies"
                + "".join(f"\n# {m}: registered {sorted(scopes.get(m, [])) or 'NOWHERE'}" for m in modules),
            )
        )

    rows = "".join(
        f'<tr><td><a href="#{k}">{html.escape(t)}</a></td><td class="n">{len(found.get(k, []))}</td></tr>'
        for k, t, _, _ in DECISIONS
    )

    blocks = []
    for key, title, why, ask in DECISIONS:
        items = found.get(key, [])
        ev = "".join(
            f'<span class="loc">{html.escape(loc)}</span><pre>{html.escape(code)}</pre>' for loc, code in items[:6]
        )
        more = f'<p class="more">+{len(items) - 6} more of this class in the tree.</p>' if len(items) > 6 else ""
        blocks.append(
            f'<section class="d" id="{key}"><h2><span>{html.escape(title)}</span>'
            f'<span class="cnt">{len(items)} site{"" if len(items) == 1 else "s"}</span></h2><div class="in">'
            f'<p class="why">{html.escape(why)}</p>'
            f'<div class="ask"><strong>Decide:</strong> {html.escape(ask)}</div>'
            f'<details class="ev"><summary>Evidence — first {min(6, len(items))} of {len(items)}, verbatim</summary>{ev}{more}</details>'
            f"</div></section>"
        )

    total = sum(len(found.get(k, [])) for k, _, _, _ in DECISIONS)
    out.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>BDD decisions</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<h1>BDD harness — what needs deciding</h1>"
        f"<p class='lede'>{total} sites across {len(DECISIONS)} defect classes, measured from the tree at render time "
        f"rather than copied from a report, so this page cannot drift from the code. Each class states why it is a "
        f"defect and the one question whose answer unblocks the work. The agent reports behind these live in "
        f"<code>bdd-cluster-findings.html</code>.</p>"
        f"<table class='sum'><thead><tr><th>Defect class</th><th class='n'>Sites</th></tr></thead><tbody>{rows}</tbody></table>"
        f"{''.join(blocks)}</div></body></html>",
        encoding="utf-8",
    )
    print(f"wrote {out}  ({total} sites, {out.stat().st_size // 1024} KB)")
    for k, t, _, _ in DECISIONS:
        print(f"  {len(found.get(k, [])):5}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
