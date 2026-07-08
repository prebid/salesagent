"""Guard: test infra must never read the error ``suggestion`` out of ``details``.

Regression for salesagent-9val: harness/step code satisfied suggestion
assertions from ``details['suggestion']`` (a hand-buried copy in the free-form
dict) instead of the error.json TOP-LEVEL position, masking every emitter that
buried or omitted the protocol field. This guard AST-scans the test-infra
accepting side (``tests/harness/``, ``tests/bdd/steps/``) and fails on any
read of ``suggestion`` through a ``details``-derived expression:

* ``<details-ish>.get("suggestion")``
* ``<details-ish>["suggestion"]``
* ``"suggestion" in <details-ish>``

Writing fixture envelopes with a buried suggestion (e.g. the negative cases in
``tests/harness/test_transport_conformance.py``) is fine — only READS are the
disease, because only reads let a non-conformant envelope satisfy an assertion.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    REPO_ROOT / "tests" / "harness",
    REPO_ROOT / "tests" / "bdd" / "steps",
)


def _mentions_details(node: ast.AST) -> bool:
    """True if the expression tree contains a name/attribute called ``details``."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "details":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "details":
            return True
        if isinstance(sub, ast.Constant) and sub.value == "details":
            return True
    return False


def _is_suggestion_const(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "suggestion"


def find_details_suggestion_reads(tree: ast.AST) -> list[str]:
    """Return unparsed source for every ``suggestion``-via-``details`` read."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        # <details-ish>.get("suggestion")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and _is_suggestion_const(node.args[0])
            and _mentions_details(node.func.value)
        ):
            offenders.append(ast.unparse(node))
        # <details-ish>["suggestion"]
        elif isinstance(node, ast.Subscript) and _is_suggestion_const(node.slice) and _mentions_details(node.value):
            offenders.append(ast.unparse(node))
        # "suggestion" in <details-ish>
        elif isinstance(node, ast.Compare) and _is_suggestion_const(node.left):
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                if isinstance(op, ast.In) and _mentions_details(comparator):
                    offenders.append(ast.unparse(node))
    return offenders


def test_no_details_suggestion_reads_in_test_infra():
    """No harness/step code reads ``suggestion`` through ``details``."""
    violations: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for offender in find_details_suggestion_reads(tree):
                violations.append(f"{path.relative_to(REPO_ROOT)}: {offender}")
    assert not violations, (
        "error.json places `suggestion` at the TOP LEVEL of the error object; "
        "reading it out of the free-form `details` dict masks non-conformant "
        "emitters (salesagent-9val). Read `error.suggestion` / the envelope's "
        "top-level key instead. Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(snippet: str) -> list[str]:
    return find_details_suggestion_reads(ast.parse(snippet))


class TestGuardDetector:
    def test_positive_get_on_details_attribute(self):
        assert _detect('s = (error.details or {}).get("suggestion")')

    def test_positive_membership_in_details(self):
        assert _detect('assert "suggestion" in error.details')

    def test_positive_subscript_on_details_name(self):
        assert _detect('s = details["suggestion"]')

    def test_positive_chained_envelope_lookup_would_be_missed_case(self):
        # The original transport.py form: the details dict is itself fetched by
        # key, so a naive "base is a name called details" detector would miss it.
        assert _detect('s = (errors[0].get("details") or {}).get("suggestion")')

    def test_negative_top_level_attribute_read(self):
        assert not _detect("s = error.suggestion")

    def test_negative_top_level_envelope_key(self):
        assert not _detect('s = errors[0].get("suggestion")')

    def test_negative_other_details_key(self):
        assert not _detect('s = details.get("minimum_budget")')
