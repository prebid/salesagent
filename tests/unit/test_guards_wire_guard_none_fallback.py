"""Guard: no wire-assertion guard may treat an UNSET transport like Transport.IMPL.

GH #1744: ``_wire_body``'s loud guard used ``transport not in (None, Transport.IMPL)``,
so an unset transport (``None`` — any non-parametrized caller, e.g. a transport-tagged
BDD scenario's empty ctx) silently fell through to the ``model_dump()`` fallback,
turning "assert on the wire" into a serializer round-trip. The same clone existed in
test_uc018_list_creatives.py's private ``_wire_creatives``. Both are fixed: only an
EXPLICIT ``Transport.IMPL`` may take the serializer path; unset raises, naming the fix
(contract pinned by tests/harness/test_outcome_helpers_wire_contract.py).

This guard bans the membership-test FORM that bundles ``None`` with ``Transport.IMPL``
anywhere under tests/, so a new hand-rolled wire guard cannot reintroduce the hole.
"""

import re
from pathlib import Path

from tests.unit._architecture_helpers import REPO_ROOT

# The disease form: a membership test whose container bundles None with
# Transport.IMPL — either member order, tuple/set/list literal. Matching the
# container literal (not the `in`/`not in` keyword) also catches a variant that
# binds the container first (`_ALLOWED = (None, Transport.IMPL)`), which a
# keyword-anchored regex would miss.
_DISEASE = re.compile(r"[(\[{]\s*None\s*,\s*Transport\.IMPL\s*[)\]}]|[(\[{]\s*Transport\.IMPL\s*,\s*None\s*[)\]}]")

_TESTS_DIR = REPO_ROOT / "tests"
_THIS_FILE = Path(__file__).resolve()


def _scan_tree():
    offenders = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.resolve() == _THIS_FILE:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _DISEASE.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    return offenders


def test_no_wire_guard_bundles_none_with_impl():
    offenders = _scan_tree()
    assert not offenders, (
        "A wire/transport guard bundles None with Transport.IMPL in one membership "
        "container, which silently grants unset-transport callers the model_dump "
        "fallback (GH #1744). Handle None with an explicit raise and compare IMPL "
        "with `is`/`is not`:\n" + "\n".join(offenders)
    )


class TestGuardMetaCases:
    """The guard's own detection contract (positive / negative / would-be-missed)."""

    def test_flags_the_original_disease_form(self):
        assert _DISEASE.search("if wire is None and transport not in (None, Transport.IMPL):")

    def test_flags_reversed_member_order(self):
        assert _DISEASE.search("if transport in (Transport.IMPL, None):")

    def test_flags_set_and_list_containers(self):
        assert _DISEASE.search("allowed = {None, Transport.IMPL}")
        assert _DISEASE.search("allowed = [None, Transport.IMPL]")

    def test_flags_prebound_container(self):
        """The would-be-missed case for a keyword-anchored regex: container bound to a name."""
        assert _DISEASE.search("_NO_WIRE = (None, Transport.IMPL)")

    def test_does_not_flag_explicit_impl_identity_check(self):
        assert not _DISEASE.search("if wire is None and transport is not Transport.IMPL:")

    def test_does_not_flag_lone_none_or_lone_impl_membership(self):
        assert not _DISEASE.search("if transport in (Transport.IMPL,):")
        assert not _DISEASE.search("if value in (None, ''):")
