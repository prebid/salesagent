"""Single owner for the conformance-ledger check-id grammar and curated YAML loaders.

Before this module, the ledger check-id grammar (``test_storyboard_check[protocol::
track::storyboard::step]``) had two parsers: ``storyboard_roadmap._ledgered_failures``
owned the regex, and ``storyboard_check_index._ledger_steps`` reached into that
module's underscore-private ``_LEDGER_ID_RE`` as a sibling view — the exact pattern
this repo's structural guards ban for ``storyboard_spec`` primitives, just not yet
enforced here. The two curated YAML loaders (the issue map, the wireability map) had
the same shape: one lived behind ``storyboard_roadmap._load_issue_map`` and was
reached into privately by ``storyboard_check_index``; the other was local to
``storyboard_check_index`` alone. This module is the one owner for all of it.

``LedgerCheckId`` is a LOSSLESS identity record: ``parse(line).format()`` reconstructs
the bracket content exactly, with no normalization baked in. The ledger spells
hyphenated ``universal/`` storyboard stems with underscores (per the runner's own
emission), while ``storyboard_coverage_map``'s stems stay hyphenated — joining raw
silently matched nothing before that was understood. Normalizing inside ``parse()``
would make identity lossy (a hyphenated segment could never round-trip), so the
hyphen-to-underscore join spelling lives on the derived ``storyboard_key`` property
instead; callers that join against ``storyboard_coverage_map`` use ``storyboard_key``,
never ``storyboard_id``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.audit import storyboard_spec
from src.core.config import ToolingSettings

# Ledger ids are `protocol::track::storyboard::step` (the protocol segment arrived
# with A2A grading). Storyboard is the THIRD segment; a three-group pattern silently
# read the track as the storyboard and joined nothing -- caught by callers' zero-join
# guards rather than by rendering every row green.
_LEDGER_ID_RE = re.compile(r"test_storyboard_check\[([^:]+)::([^:]*)::([^:]+)::(.+)\]\s*$")


@dataclass(frozen=True)
class LedgerCheckId:
    """One ``(protocol, track, storyboard_id, step_id)`` conformance-ledger key.

    ``storyboard_id`` is the RAW segment as parsed — identity, must round-trip.
    ``storyboard_key`` is the derived, normalized join spelling.
    """

    protocol: str
    # ``str | None``, not ``str``: one call site constructs this with no track,
    # and the narrow annotation let that None render as the literal string
    # "None" in a ledger id.
    track: str | None
    storyboard_id: str
    step_id: str

    @classmethod
    def parse(cls, line: str) -> LedgerCheckId | None:
        """Parse one ledger line (or any line containing the bracket grammar).

        Uses ``.search()``, not ``.match()`` — real ledger lines carry a full
        nodeid prefix (``tests/storyboard/test_storyboard_conformance.py::``)
        before the bracket expression this grammar owns. Returns ``None`` on a
        non-matching line (comment, prose, or a grammar-drifted short form)
        rather than raising — callers that must fail loudly on a malformed
        entry do so themselves (see ``load()`` below, and
        ``test_storyboard_ledger_state.py``'s own assertion).
        """
        match = _LEDGER_ID_RE.search(line.strip())
        if not match:
            return None
        return cls(
            protocol=match.group(1),
            track=match.group(2),
            storyboard_id=match.group(3),
            step_id=match.group(4),
        )

    def format(self) -> str:
        """The bracket-content spelling: what ``parse()`` consumed, byte for byte.

        Not a full nodeid — the module path and function name
        (``test_storyboard_check[...]``) are pytest's concern (the producer at
        ``tests/storyboard/test_storyboard_conformance.py``'s
        ``pytest_generate_tests``), not the grammar's.
        """
        return f"{self.protocol}::{self.track}::{self.storyboard_id}::{self.step_id}"

    @property
    def storyboard_key(self) -> str:
        """The derived, normalized join spelling every consumer groups on.

        Hyphen -> underscore: the ledger carries the runner's underscore
        spelling (``webhook_emission``) while ``storyboard_coverage_map``
        stems are hyphenated for ``universal/`` (``webhook-emission``).
        """
        return self.storyboard_id.replace("-", "_")


def join_id(declared_id: str | None, stem: str) -> str:
    """The join key both index builders group on: the declared ``id:``, else the stem.

    The module docstring above says this spelling "lives on the derived
    ``storyboard_key`` property instead" and that callers joining against
    ``storyboard_coverage_map`` use it "never ``storyboard_id``". Two callers
    said otherwise, with byte-identical inline copies of
    ``storyboard_spec.storyboard_id(text) or row["stem"].replace("-", "_")`` --
    the exact form ``test_architecture_storyboard_ledger.py`` names as "the old
    bug". Divergence between the two current consumers would be loud today
    because both floors fire; the exposure is the next copy, written without one.

    Only the FALLBACK branch normalizes: a declared ``id:`` is already in the
    runner's underscore spelling, while a filename stem is hyphenated for
    ``universal/`` storyboards.
    """
    return declared_id or stem.replace("-", "_")


LEDGER = Path("tests") / "storyboard" / "known_failures.txt"


def ledger_path(repo: Path) -> Path:
    """The ledger file to load, under ``repo``; ``STORYBOARD_LEDGER_PATH`` redirects it.

    ONE resolver, because two things read the ledger and must read the same one: the
    xfail routing in ``tests/storyboard/conftest.py`` and the in-session stale-entry
    join in ``tests/storyboard/test_storyboard_conformance.py``. Resolved in two places,
    a redirect reaches one of them and the join then reports staleness against a file
    the routing never saw.

    The redirect exists because the committed ledger is empty — the storyboard grades
    every check it collects — so the only way to grade the machinery is to hand it a
    ledger with entries in it
    (``tests/integration/test_storyboard_ledger_fitness_real_session.py``).
    """
    override = ToolingSettings().storyboard_ledger_path  # STORYBOARD_LEDGER_PATH
    return override if override else repo / LEDGER


# The runner-level synthetic. `test_storyboard_conformance` emits this row when
# the runner grades zero checks, so it is a real ledger key with no `check:`
# line behind it and therefore no record in the check index. Named once, here,
# because both the emitter and the generator's join need it and retyping the
# strings in either place is how the pair drifts apart.
RUNNER_SYNTHETIC_STORYBOARD_ID = "agent_reachability"
RUNNER_SYNTHETIC_STEP_ID = "graded_checks_produced"
RUNNER_SYNTHETIC_KEY = (RUNNER_SYNTHETIC_STORYBOARD_ID, RUNNER_SYNTHETIC_STEP_ID)

#: The other synthetic check a run can produce: the protocol graded FEWER checks than
#: the floor this repo has recorded. ``agent_reachability`` above catches a run that
#: graded NOTHING; this catches the one that graded less than it used to, which no
#: pytest outcome can otherwise express -- a check that regresses from pass to SKIP
#: removes its own failing item, so the suite gets greener as the agent gets worse.
#
#: Deliberately NOT added to ``storyboard_check_index``'s ``known_step_keys``, unlike
#: ``RUNNER_SYNTHETIC_KEY`` above. That exemption is what lets a row naming a synthetic
#: live in the ledger without reading as an orphan -- and a floor breach that could be
#: ledgered is a floor that can be switched off in one line. Leaving the key out means
#: such a row fails the orphan join instead, so lowering the floor has to happen in
#: ``.storyboard-pass-floor``, where the number is visible and the diff says what it was.
PASS_FLOOR_STORYBOARD_ID = "pass_floor"
PASS_FLOOR_STEP_ID = "floor_met"

# The storyboard whose steps the runner generates from conformance fixtures
# rather than from `check:` lines, and where those fixtures live inside the
# pinned tree. See `vector_step_keys`.
VECTOR_STORYBOARD_ID = "signed_requests"
VECTOR_FIXTURE_DIR = Path("compliance") / "test-vectors" / "request-signing"


def vector_step_keys(adcp: Path) -> set[tuple[str, str]]:
    """Ledger keys the `signed_requests` runner generates from pinned fixtures.

    One id per fixture file under ``<adcp>/compliance/test-vectors/request-signing/``,
    spelled ``<subdir>-<stem>`` — ``positive-001-basic-post``,
    ``negative-011-malformed-header``. The pinned ``universal/signed-requests.yaml``
    declares this grading at lines 49-55: the runner replays each vector and
    compares the response against the vector's ``expected_outcome``.

    DERIVED, never a name list. The set is computed from the checksum-verified
    release bundle, so a vector added or removed upstream moves it automatically.
    """
    if not adcp.is_dir():
        return set()
    root = adcp / VECTOR_FIXTURE_DIR
    if not root.is_dir():
        return set()
    return {
        (VECTOR_STORYBOARD_ID, f"{sub.name}-{fixture.stem}")
        for sub in root.iterdir()
        if sub.is_dir()
        for fixture in sub.glob("*.json")
    }


def load(path: Path) -> list[LedgerCheckId]:
    """Parse every line of a ledger file into ``LedgerCheckId``.

    The line scan lives in :func:`storyboard_spec.parse_ledger_lines` — one scan
    for every ledger in the repo — and this supplies the bracket grammar.

    A line the grammar rejects RAISES. Dropping it instead would let a typo'd
    entry quietly stop grading its check with nothing to notice, so the drop
    branch is gone — and so is the paragraph that used to justify it, because
    leaving the justification in place is how the branch comes back.
    """
    if not path.is_file():
        return []
    return storyboard_spec.parse_ledger_lines(path, grammar=LedgerCheckId.parse)


ISSUE_MAP = Path("docs") / "test-obligations" / "storyboard-issue-map.yaml"


def load_issue_map(repo: Path) -> dict[str, dict[str, Any]]:
    """Curated storyboard -> GitHub-issue map. The one hand-maintained input here.

    Keyed by STORYBOARD, deliberately. A scenario-keyed cross-reference cannot
    express "this storyboard has no scenario but issue #N tracks the gap" --
    the majority case among on-path storyboards. Keying on the storyboard is
    what lets an uncovered gap carry a ticket, or be explicitly marked as
    carrying none.

    A `coverage: none` row with no issues is a real answer, not a hole: it
    means the pinned spec grades this, we do not test it, and nobody is
    tracking it.
    """
    import yaml

    path = repo / ISSUE_MAP
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded.get("storyboards") or {}


WIREABILITY = Path("docs") / "test-obligations" / "storyboard-wireability.yaml"


def load_wireability(repo: Path) -> dict[str, dict[str, Any]]:
    """Curated (storyboard, step) -> e2e-wireability verdict.

    The one judgement in the check index no program can derive: whether a
    step can be driven and observed from outside a running agent. Same
    status as the issue map -- curated, reviewable, joined rather than
    inferred.
    """
    import yaml

    path = repo / WIREABILITY
    if not path.is_file():
        return {}
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("steps") or {}


def load_wireability_untriaged(repo: Path) -> set[str]:
    """``(storyboard, step)`` keys explicitly deferred rather than assessed.

    A step can legitimately have no verdict yet -- someone has to look at it
    -- but that is a stated decision, not a silent absence. This is the
    curated escape hatch ``test_architecture_storyboard_wireability.py``
    checks against: a graded step missing from ``load_wireability()`` fails
    the guard UNLESS its key also appears here.
    """
    import yaml

    path = repo / WIREABILITY
    if not path.is_file():
        return set()
    return set((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("untriaged") or [])
