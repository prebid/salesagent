"""Guard: the BDD deselection exemption must read a TOKEN, not free English.

``tests/bdd/conftest.py``'s ``pytest_collection_modifyitems`` deselects the
redundant mcp/rest copies of a strict-xfail scenario, and exempts scenarios whose
xfail reason declares ``scope=per-transport`` — an obligation each transport
enforces separately has to xpass on its own, so deselecting two of three would
grade a cross-transport MUST on one transport and call it covered.

Today the exemption is ``"scope=per-transport" in str(reason)``: an unguarded
substring test against a free-text field. Two measured consequences, both of them
silent:

  * Retyping the token to ``scope=per_transport`` drops eight rows from collection
    (the mcp and rest copies of UC-003's four CONFLICT Examples rows) and NOTHING
    fails: deselected items simply cease to exist. Measured on the UC-003 module:
    132 passed, 70 deselected, 1208 xfailed, 0 failed.
  * The substring is not even reading the token. The #1607 reason ALSO explains
    itself in prose — "scope=per-transport because each transport enforces (or
    fails to enforce) independently" — so retyping the actual token while leaving
    the sentence alone drops ZERO rows. The predicate was matching an English
    sentence and would have gone on matching it with the declaration deleted.

So the reason field needs a parse: a ``cause=``-prefixed reason is a typed record,
and a value outside the vocabulary is an ERROR rather than a silent miss.

Deliberately NARROW, and the claim is DETECTED, not "unrepresentable":

  * Only a reason that STARTS with ``cause=`` is parsed. 62 of the 65 xfail sites
    under ``tests/bdd/`` are free text and three of those already contain ``k=``
    pairs of their own (``code='NOT_FOUND'``, ``action='failed'``,
    ``principal_id=null``), so keys cannot be validated globally — the typed form
    has to be recognised before it can be checked. The 62 pass through untouched;
    this guard does not sweep them.
  * A typo in the ``cause=`` recogniser itself still degrades silently, into a
    free-text reason. That residue is stated rather than papered over.

GH: PR #1941 round-5 review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._collection_manifest import load, manifest_dir

_BDD_DIR = Path(__file__).resolve().parents[1] / "bdd"
_CONFTEST = _BDD_DIR / "conftest.py"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UC003_MODULE = _REPO_ROOT / "tests" / "bdd" / "test_uc003_update_media_buy.py"


def _parser():
    """The parse under test, imported lazily.

    Deliberately NOT a module-level import: until the parse exists this guard is
    RED, and a module-level ImportError interrupts collection of the entire unit
    suite rather than failing the tests that actually grade the missing behavior.
    """
    from tests.bdd.conftest import XfailReasonError, parse_xfail_reason

    return parse_xfail_reason, XfailReasonError


# The live #1607 reason, restated here rather than imported from the marker site: a
# grader that reads its input out of the thing it grades moves with it and cannot
# fail. This is the reason as it stands, tokens AND the prose sentence that made
# the substring match vacuous.
_LIVE_PER_TRANSPORT_REASON = (
    "cause=production-gap scope=per-transport ref=#1607 — update_media_buy "
    "accepts a stale or ahead revision and returns success; the spec MUST "
    "reject it with CONFLICT. scope=per-transport because each transport "
    "enforces (or fails to enforce) independently, so each must xpass on its "
    "own when #1607 lands."
)

# The same reason with ONLY the token retyped. The prose sentence is left intact,
# which is exactly the shape the substring predicate could not see.
_RETYPED_TOKEN_REASON = _LIVE_PER_TRANSPORT_REASON.replace("scope=per-transport ref=", "scope=per_transport ref=", 1)

# A free-text reason that HAPPENS to contain a k= pair. One of the three real ones
# (tests/bdd/conftest.py, UC-006 PACKAGE_NOT_FOUND) — the reason the parser must
# gate on the cause= prefix instead of validating keys globally.
_FREE_TEXT_REASON_WITH_A_K_TOKEN = (
    "SPEC-PRODUCTION GAP: outcome 'PACKAGE_NOT_FOUND' not in Then dispatch — "
    "production returns AdCPNotFoundError(code='NOT_FOUND'), spec expects "
    "'PACKAGE_NOT_FOUND'. See _assignments.py:62-69"
)


def _string_parts(node: ast.AST) -> list[str]:
    """Collect every string literal in an expression subtree.

    Handles a plain ``Constant``, implicit adjacent concatenation, explicit
    ``a + b`` concatenation, and f-strings — so a reason split across several
    lines cannot slip the check.
    """
    return [c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)]


def _is_xfail_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "xfail"
    return isinstance(func, ast.Name) and func.id == "xfail"


def _xfail_reasons(path: Path) -> list[tuple[int, str]]:
    """(lineno, reason) for every ``pytest.mark.xfail(reason=...)`` in ``path``."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and _is_xfail_call(node)):
            continue
        for kw in node.keywords:
            if kw.arg == "reason":
                hits.append((node.lineno, "".join(_string_parts(kw.value))))
    return hits


class TestTheTypedReasonParse:
    """A ``cause=``-prefixed reason is a record, and a bad value is an error."""

    def test_the_live_per_transport_reason_parses_to_its_three_tokens(self):
        """Non-vacuity for every rejection below: the real reason still parses.

        All three tokens are read, not just the one the exemption consults — a
        parser that returned a bare boolean would leave ``ref=`` and ``cause=``
        exactly as unchecked as they are today.
        """
        parse_xfail_reason, _ = _parser()

        parsed = parse_xfail_reason(_LIVE_PER_TRANSPORT_REASON)

        assert parsed is not None, "the live #1607 reason is not recognised as a typed reason at all"
        assert parsed.cause == "production-gap"
        assert parsed.scope == "per-transport"
        assert parsed.ref == "#1607"

    def test_a_retyped_scope_token_is_rejected_rather_than_silently_missed(self):
        """``scope=per_transport`` fails loudly.

        This is the mutation that today drops eight rows and fails nothing. It
        must become an error at collection time, which is the only moment at
        which anyone is still looking.
        """
        parse_xfail_reason, XfailReasonError = _parser()

        with pytest.raises(XfailReasonError) as excinfo:
            parse_xfail_reason(_RETYPED_TOKEN_REASON)

        assert "per_transport" in str(excinfo.value), (
            f"the rejection does not name the offending value, so an author cannot see the typo: {excinfo.value}"
        )

    def test_prose_before_the_declaration_completes_is_never_read_as_the_declaration(self):
        """A token appearing in prose EARLIER than the declaration must not win.

        This is the discriminating case for the anchoring, and the reason the sibling
        test above cannot grade it: that reason declares scope FIRST, so a whole-string
        scan taking the first match happens to agree. Reverse the order and the two
        implementations diverge —

          unanchored finditer : takes ``scope=per-transport`` out of the SENTENCE
          anchored declaration: the leading run holds only ``cause=``, so the reason is
                                incomplete and is REFUSED

        A verify pass reverted the anchoring and every test in this file still passed;
        the fix for the diff review's headline finding had no grader. This is it.
        """
        parse_xfail_reason, XfailReasonError = _parser()
        reason = (
            "cause=production-gap — unlike scope=per-transport rows this one is global. "
            "scope=transport-independent ref=#1607"
        )

        with pytest.raises(XfailReasonError) as excinfo:
            parse_xfail_reason(reason)

        assert "scope" in str(excinfo.value), (
            "a reason whose declaration is incomplete must be refused by name, not completed from its own prose"
        )

    def test_prose_repeating_the_token_does_not_decide_the_scope(self):
        """The declaration decides; the explanation does not.

        The measured hole: retyping the ``scope=`` token while leaving the prose
        sentence "scope=per-transport because each transport enforces ..." intact
        changed NOTHING under the substring predicate. Here the same reason —
        declared transport-independent, explained with the other phrase — must
        read as transport-independent.
        """
        reason = (
            "cause=harness-limitation scope=transport-independent ref=#1607 — revision 0 is "
            "rejected during request construction. This is NOT scope=per-transport: the "
            "request never reaches any seller, so no transport can grade it separately."
        )

        parse_xfail_reason, _ = _parser()

        parsed = parse_xfail_reason(reason)

        assert parsed is not None
        assert parsed.scope == "transport-independent", (
            "a sentence mentioning the other scope changed the parsed scope; the parse is still "
            "reading free text rather than the declaration"
        )

    def test_a_free_text_reason_passes_through_untyped(self):
        """The other 62 are not swept, and a stray ``k=`` pair does not make one typed.

        A 62-marker bulk rewrite is out of scope by owner ruling. An unparsed
        reason is not an error — it is untyped, and stays that way.
        """
        parse_xfail_reason, _ = _parser()

        assert parse_xfail_reason(_FREE_TEXT_REASON_WITH_A_K_TOKEN) is None, (
            "a free-text reason containing an unrelated k= pair was treated as a typed reason; "
            "the parser must gate on the cause= prefix, not on the presence of '='"
        )


class TestEveryTypedReasonInTheTreeParses:
    """The parse is applied to the tree, not just to hand-written examples."""

    def test_every_cause_prefixed_reason_under_tests_bdd_parses(self):
        """A retyped token in ANY typed reason reddens here.

        Without this, the parser could exist and be correct while a marker site
        carried a value it rejects — the failure would only surface as rows
        quietly leaving collection, which is the original defect.
        """
        parse_xfail_reason, XfailReasonError = _parser()
        violations: list[str] = []
        typed_seen = 0
        for path in sorted(_BDD_DIR.rglob("*.py")):
            for lineno, reason in _xfail_reasons(path):
                if not reason.lstrip().startswith("cause="):
                    continue
                typed_seen += 1
                try:
                    parse_xfail_reason(reason)
                except XfailReasonError as exc:
                    rel = path.relative_to(_BDD_DIR.parents[1])
                    violations.append(f"{rel}:{lineno}: {exc}")

        assert typed_seen >= 3, (
            f"only {typed_seen} typed xfail reasons found under tests/bdd/; the tree had 3 when this "
            f"guard was written, so the scan is no longer finding them and passes vacuously"
        )
        assert not violations, "typed xfail reasons that do not parse:\n" + "\n".join(violations)


class TestTheExemptionConsultsTheParse:
    """A parser nothing calls grades nothing."""

    def test_the_collection_hook_does_not_match_a_token_as_a_substring(self):
        """No ``"<key>=..." in ...`` comparison survives in the collection hook.

        The parser only closes the hole if the deselection predicate stops asking
        the free-text question. This scans for the shape of the old predicate —
        an ``in`` comparison whose left side is a string literal beginning with
        one of the token keys — rather than for one hard-coded string, so
        re-introducing it under a different value is caught too.
        """
        keys = ("cause=", "scope=", "ref=")
        offenders: list[str] = []
        for node in ast.walk(ast.parse(_CONFTEST.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, ast.In) for op in node.ops):
                continue
            left = node.left
            if isinstance(left, ast.Constant) and isinstance(left.value, str):
                if left.value.lstrip().startswith(keys):
                    offenders.append(f"tests/bdd/conftest.py:{node.lineno}: {left.value!r} in ...")

        assert not offenders, (
            "the deselection exemption still asks a substring question of a free-text field:\n"
            + "\n".join(offenders)
            + "\nRoute it through parse_xfail_reason() so a retyped value is an error, not a silent miss."
        )

    def test_the_collection_hook_actually_calls_the_parser(self):
        """``pytest_collection_modifyitems`` contains a call to ``parse_xfail_reason``.

        The negative test above grades only that the OLD shape is gone, and a diff review
        demonstrated the evasion: delete the parser call and route the predicate some
        third way, and the substring scan stays green because there is no substring left
        to find. Absence of the wrong shape is not presence of the right one.

        So this asserts the positive directly, and scopes it to the hook rather than the
        module — a call anywhere in a 3000-line conftest would satisfy a file-wide scan
        while the deselection predicate consulted nothing.
        """
        hook = next(
            (
                node
                for node in ast.walk(ast.parse(_CONFTEST.read_text(encoding="utf-8")))
                if isinstance(node, ast.FunctionDef) and node.name == "pytest_collection_modifyitems"
            ),
            None,
        )
        assert hook is not None, "pytest_collection_modifyitems is gone from tests/bdd/conftest.py"

        calls = {
            node.func.id for node in ast.walk(hook) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "parse_xfail_reason" in calls, (
            "the deselection hook no longer calls parse_xfail_reason(), so whatever it now "
            "consults is ungraded — a parser nothing calls grades nothing, and the substring "
            "scan above cannot see a predicate that was replaced rather than reintroduced."
        )

    def test_the_hook_aborts_collection_on_a_malformed_reason(self):
        """``pytest_collection_modifyitems`` raises rather than absorbing a parse error.

        The acceptance criterion is "the ``scope=`` token typo FAILS COLLECTION", and the
        parse alone does not deliver that: a hook that catches ``XfailReasonError`` and
        carries on leaves the row deselected with nothing reported — the original defect
        wearing a parser. A verify pass showed this directly: disabling the raise left
        every other test in this file green, so nothing here was grading it.

        Scanned rather than executed because driving the hook needs a full collection
        session; what is asserted is that the collected errors reach a ``raise``.
        """
        hook = next(
            (
                node
                for node in ast.walk(ast.parse(_CONFTEST.read_text(encoding="utf-8")))
                if isinstance(node, ast.FunctionDef) and node.name == "pytest_collection_modifyitems"
            ),
            None,
        )
        assert hook is not None, "pytest_collection_modifyitems is gone from tests/bdd/conftest.py"

        # The raise must be GUARDED BY the collected errors, not merely present. An
        # earlier version of this assertion looked only for a Raise node mentioning
        # `reason_errors`, and `if False:` in front of it left that node intact — the
        # guard passed against a mutation that disabled the very abort it was written
        # for. Presence is not reachability; the condition is what makes it fire.
        guarded_raises = [
            node
            for node in ast.walk(hook)
            if isinstance(node, ast.If)
            and any(isinstance(sub, ast.Name) and sub.id == "reason_errors" for sub in ast.walk(node.test))
            and any(isinstance(sub, ast.Raise) for sub in ast.walk(node))
        ]

        assert guarded_raises, (
            "the collection hook does not raise ON the collected malformed-reason errors. "
            "Either it never raises, or the raise is no longer conditioned on them — and a "
            "parse that reports nothing is the silent deselection this guard exists to "
            "remove: the row still drops, and now it drops past a parser that noticed."
        )


@pytest.mark.arch_guard
class TestTheRoutingOutcome:
    """What actually reaches the runner, rather than the shape of the code that decides it."""

    def test_a_per_transport_reason_keeps_its_mcp_and_rest_rows(self):
        """The exemption still SELECTS the rows a per-transport reason protects.

        Every other test here asks an AST-shape question — is the old literal gone, does
        the parser get called, is the raise guarded. None of them observes the routing
        OUTCOME, and a verify pass showed the consequence: inverting the predicate (route
        per-transport rows to deselection and everything else to selection) reds nothing,
        because no test in the lane runs collection and asserts which rows survive.

        So this reads which rows survived a REAL collection. It needs an UNFILTERED
        record: `[testenv:bdd_e2e]` runs `pytest tests/bdd/ -k e2e_rest`, where every
        mcp/rest row is deselected by the `-k` expression rather than by the exemption
        this grades, so `selected` there answers a different question. `filter=""`
        demands a record with no `-k`/`-m` narrowing — a shard on the sharded path,
        `[testenv:bdd]` on the plain one — and `target=None` because a shard's target is
        its file list, which moves whenever a bdd file is added.
        """
        rows = load(manifest_dir(), target=None, filter="")
        uc003 = f"{_UC003_MODULE.relative_to(_REPO_ROOT).as_posix()}::"
        module_rows = [row for row in rows if row["nodeid"].startswith(uc003)]
        assert module_rows, (
            f"no collected rows for {uc003} in any unfiltered collection record. Without them this test grades nothing."
        )

        def _is_mcp_or_rest(nodeid: str) -> bool:
            return any(token in nodeid for token in ("[mcp-", "[rest-", "[mcp]", "[rest]"))

        _CONFLICT_TOKENS = ("stale_revision", "ahead_revision", "revision below current", "revision above current")

        # The CONFLICT rows carry cause=production-gap scope=per-transport ref=#1607, so
        # the exemption must keep their mcp and rest copies. If the predicate inverts,
        # these are the first things to vanish.
        # Narrowed to the rows the exemption actually protects, and COUNTED. A first
        # version matched any mcp/rest revision-concurrency row and passed under an
        # inverted predicate, because 8 of those rows carry no strict xfail at all and
        # survive either way — the assertion was reading the wrong population, which is
        # this child's own defect in its own grader.
        protected = [
            row["nodeid"]
            for row in module_rows
            if row["selected"]
            and _is_mcp_or_rest(row["nodeid"])
            and any(token in row["nodeid"] for token in _CONFLICT_TOKENS)
        ]

        assert len(protected) == 8, (
            f"expected the 8 mcp/rest CONFLICT rows to survive collection, got "
            f"{len(protected)}. These carry `cause=production-gap scope=per-transport "
            f"ref=#1607`, so the exemption must keep them; losing them means the "
            f"predicate is inverted or gone. No AST-shape assertion in this file can see "
            f"that — only reading a real collection can.\n" + "\n".join(protected)
        )

        # Positive control. The count above is a survival assertion, so it cannot tell
        # "the exemption kept these 8" from "nothing was deselected at all" — which is
        # what BDD_ALL_TRANSPORTS=1 (tests/bdd/conftest.py) produces by disabling the
        # exemption outright, with `filter` still empty. Requiring that SOME mcp/rest row
        # was deselected reds under both an inverted predicate and an all-transports run.
        deselected_mcp_or_rest = [
            row["nodeid"] for row in module_rows if not row["selected"] and _is_mcp_or_rest(row["nodeid"])
        ]
        assert deselected_mcp_or_rest, (
            "no mcp/rest row in this module was deselected, so the 8 above survived a "
            "collection that deselected nothing. Either the exemption is disabled "
            "(BDD_ALL_TRANSPORTS=1) or the deselection is gone; in both cases the count "
            "above passes while grading nothing."
        )
