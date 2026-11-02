"""Guard: a ratchet counter measures the invariant it is named for, not a text proxy.

Each of these numbers is a COMMITTED, SHRINK-ONLY baseline. So a counter that measures a
proxy does not merely mis-report — the wrong quantity is the one being paid down, and it
can be paid down by an edit that changes no behaviour at all. That is why these are guards
rather than a note (salesagent-b341x.19, disease form (a) under salesagent-v03pe).

MEASURED BEFORE ANY CHANGE, because the ticket's four claims were right about the METHOD
and had to be checked against this tree for present effect:

    counter                     claim                          measured here
    check_admin_raw_session     regex counts comments          TRUE: 190 vs 189 call
                                                               sites — operations.py:403
                                                               is a COMMENT
    check_admin_raw_session     `local_session.add(` unseen    latent: zero such
                                                               receivers today; every
                                                               non-session `.add(` is a set
    check_code_duplication      substring tally for a block    latent: 29/29 and 61/61
                                count                          agree exactly today
    check_type_ignore_count     one spelling of suppression    latent: zero file-level
                                                               `# mypy: ignore-errors`
    check_fixme_citation_count  counts prose occurrences       moot: all four counts are
                                                               ZERO, so the population the
                                                               proxy would mis-measure is
                                                               empty

Three were repaired at the moment their proxy and their invariant AGREED, which is what
makes the change provably baseline-neutral instead of a silent re-scoping. The fourth
(fixme citations) has nothing to repair while its population is empty; if it ever refills,
the proxy hazard returns and this file is where the guard belongs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import load_hook_module, repo_root


@pytest.fixture(scope="module")
def admin_hook():
    return load_hook_module("check_admin_raw_session_count")


@pytest.fixture(scope="module")
def duplication_hook():
    return load_hook_module("check_code_duplication")


# ── check_admin_raw_session_count: call sites, not text ──────────────────────


def test_a_mention_in_a_comment_is_not_raw_session_debt(admin_hook, tmp_path: Path) -> None:
    """THE PRESENT DEFECT. src/admin/blueprints/operations.py:403 says, in a comment,
    "and get_db_session()'s exit closes the shared thread-scoped ..." — and the regex
    counted it, so the baseline carried one unit of debt payable by editing prose."""
    admin = tmp_path / "src" / "admin"
    admin.mkdir(parents=True)
    (admin / "blueprint.py").write_text(
        "def live():\n"
        "    with get_db_session() as s:\n"
        "        pass\n"
        "\n"
        "# a comment mentioning get_db_session() and how it closes\n"
        'DOC = """a docstring mentioning get_db_session() too"""\n',
        encoding="utf-8",
    )
    counts = admin_hook.count_raw_session_usage(tmp_path)
    assert counts["admin_get_db_session"] == 1, "only the call site is debt; prose is not"


def test_a_renamed_session_receiver_is_still_counted(admin_hook, tmp_path: Path) -> None:
    """The mirror hazard: the old regex required the receiver to be spelled `session` or
    `db_session`, so renaming it paid the ratchet down without changing behaviour."""
    admin = tmp_path / "src" / "admin"
    admin.mkdir(parents=True)
    (admin / "blueprint.py").write_text("def live(session):\n    session.add(row)\n", encoding="utf-8")
    assert admin_hook.count_raw_session_usage(tmp_path)["admin_session_add"] == 1

    # The classification is a reviewable pin, not a pattern nobody re-reads.
    assert "db_session" in admin_hook.SESSION_RECEIVERS


def test_a_set_add_is_not_raw_session_debt(admin_hook, tmp_path: Path) -> None:
    """Nine of the eleven `.add(` receivers under src/admin are sets. Counting them would
    inflate the debt with calls that have nothing to do with the UoW migration."""
    admin = tmp_path / "src" / "admin"
    admin.mkdir(parents=True)
    (admin / "blueprint.py").write_text("def live():\n    seen_tenant_ids.add(x)\n", encoding="utf-8")
    assert admin_hook.count_raw_session_usage(tmp_path)["admin_session_add"] == 0


def test_an_unclassified_receiver_refuses_to_be_guessed(admin_hook, tmp_path: Path) -> None:
    """The UNDECIDABLE discipline: a receiver in neither pin is a decision for a human,
    not something for the counter to resolve by whichever pattern happens to match."""
    admin = tmp_path / "src" / "admin"
    admin.mkdir(parents=True)
    (admin / "blueprint.py").write_text("def live():\n    mystery_thing.add(x)\n", encoding="utf-8")
    with pytest.raises(admin_hook.UnclassifiedReceiver, match="mystery_thing"):
        admin_hook.count_raw_session_usage(tmp_path)


def test_the_receiver_pins_are_disjoint(admin_hook) -> None:
    """A receiver in both pins would be counted or not depending on branch order."""
    assert not (admin_hook.SESSION_RECEIVERS & admin_hook.NON_SESSION_RECEIVERS)


# ── check_code_duplication: messages, not occurrences of the string ──────────


def test_an_echoed_r0801_literal_is_not_extra_duplication(duplication_hook) -> None:
    """pylint prints the duplicated SOURCE under each message. This tree has 15 literal
    "R0801" occurrences in source, all in comments explaining the DRY ratchet, so a
    duplicated block containing one would be tallied as extra duplication."""
    stdout = (
        "************* Module src.a\n"
        "src/a.py:1:0: R0801: Similar lines in 2 files\n"
        "==src.a:[1:9]\n"
        "==src.b:[1:9]\n"
        "    # the DRY ratchet (pylint R0801) rejects this shape\n"
        "    x = 1\n"
    )
    assert duplication_hook._count_r0801_messages(stdout) == 1
    assert stdout.count("R0801") == 2, "the substring tally this replaces would say 2"


def test_every_real_message_is_counted(duplication_hook) -> None:
    stdout = "src/a.py:1:0: R0801: Similar lines in 2 files\nsrc/b.py:4:0: R0801: Similar lines in 3 files\n"
    assert duplication_hook._count_r0801_messages(stdout) == 2


def test_no_messages_is_zero(duplication_hook) -> None:
    assert duplication_hook._count_r0801_messages("") == 0


# ── check_type_ignore_count: the relabel hazard ──────────────────────────────


#: ``# type: ignore`` is ONE spelling of type suppression, and the ratchet is named for
#: the debt rather than the spelling. A file-level ``# mypy: ignore-errors`` suppresses
#: strictly more while counting zero, so adopting one anywhere in ``src/`` would let the
#: baseline be paid down without removing any suppression at all. Measured today: none.
#:
#: ``cast()`` is deliberately NOT pinned here. There are 85 in ``src/`` and a cast is a
#: legitimate narrowing tool, not per se suppression; pinning it would assert a judgment
#: about 85 call sites that this guard has no standing to make.
def test_no_file_level_mypy_suppression_in_src() -> None:
    pattern = re.compile(r"#\s*mypy:\s*ignore-errors")
    offenders = [
        f"{path.relative_to(repo_root())}:{i}"
        for path in (repo_root() / "src").rglob("*.py")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        "A file-level `# mypy: ignore-errors` suppresses more than `# type: ignore` while "
        "counting ZERO toward .type-ignore-baseline, so the ratchet could be paid down "
        f"without removing any suppression: {offenders}"
    )


def test_the_type_ignore_counter_still_measures_what_its_baseline_names(tmp_path: Path) -> None:
    """A sanity pin on the counter itself: it counts the comment spelling, per file."""
    hook = load_hook_module("check_type_ignore_count")
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("x = 1  # type: ignore[arg-type]\ny = 2  # type:ignore\n", encoding="utf-8")
    assert hook.count_type_ignores(src) == 2


# ── check_fixme_citation_count: nothing to repair while the population is empty ──


def test_the_fixme_citation_population_is_still_empty() -> None:
    """The proxy hazard here (counting a beads id that appears in prose) is unreachable
    while every count is zero. If this ever fails, the population has refilled and the
    counter needs the same AST treatment the admin one got."""
    hook = load_hook_module("check_fixme_citation_count")
    counts = hook.count_beads_citations(repo_root())
    assert set(counts.values()) == {0}, (
        "FIXME/TODO beads citations are back. The counter measures TEXT, including "
        f"occurrences inside docstrings and prose, so this number is now a proxy: {counts}"
    )


def test_ast_is_what_makes_the_admin_counter_immune(admin_hook) -> None:
    """Named so the reason survives: a parse cannot see a comment, so the comment-counting
    defect is structurally impossible rather than patched."""
    source = ast.parse("# get_db_session()\nx = 1\n")
    assert not [n for n in ast.walk(source) if isinstance(n, ast.Call)]
