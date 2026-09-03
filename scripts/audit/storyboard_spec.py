"""Shared L0 parsing layer for the pinned AdCP storyboard compliance tree.

Every ``scripts/audit/storyboard_*.py`` script and the ``make quality`` guard
(``tests/unit/test_architecture_storyboard_binding.py``) need the same
handful of structured facts out of the pinned compliance tree and our own
``.feature`` files: the pinned spec version, our declared protocols/
specialisms, a storyboard's ``required_tools``/``requires_capability``/
phases, a phase's graded check types, a tagged scenario's ``@source``
footer. Before this module each consumer re-derived these independently —
14+ primitives, 2-4 incompatible implementations each, six of which had
silently diverged into live bugs. This module is the single implementation;
every consumer imports it.

Deliberately import-safe: nothing here resolves a live ``~/projects/adcp``
clone at import time. ``dist_root()`` takes the clone path as an argument and
is only ever called inside a consumer's own ``build()``/``main()`` — the
``make quality`` guard runs in CI, where no clone exists, and reads the
vendored ``tests/fixtures/adcp_storyboards_pinned/index.json`` instead of
calling into this module's tree-walking functions at all.

Regex over raw text, not ``yaml.safe_load``, remains deliberate: one pinned
file (``universal/runner-output-contract.yaml``) is not valid plain YAML
(it embeds prose/code blocks), and it is already excluded by the ``track:``
predicate in :func:`storyboards` before any consumer would try to parse it
structurally.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import adcp
import yaml


class StoryboardAuditError(Exception):
    """A library-level failure in the storyboard audit pipeline.

    Every ``scripts/audit/storyboard_*.py`` consumer imports these functions
    as a library (from tests, from sibling scripts, from ``make quality``
    guards) as well as running them as a CLI. ``raise SystemExit`` from inside
    a library function kills the whole importing process — a pytest run, a
    sibling script's in-process call — instead of giving the caller something
    catchable. This is the one typed error every ``build()``/``audit()``/
    parsing function in this pipeline raises for a data problem it detects;
    only :func:`run_cli`, the CLI boundary, is allowed to turn it into a
    process exit.
    """


# ── Pinned version + declared capabilities ─────────────────────────────────

_SPEC_VERSION_RE = re.compile(r"targets \*\*AdCP spec version ([0-9][^*]*)\*\*")


def pinned_version(repo: Path) -> str:
    """The pinned AdCP spec version, from the INSTALLED SDK.

    ``adcp.get_adcp_spec_version()`` is the authority: it is the version the
    wheel this repo actually runs against reports for itself, so it cannot
    disagree with the code under audit.

    Prose was the sole source before, regex-parsed out of
    ``docs/adcp-spec-version.md`` and fed to 8+ call sites including the SDK-pin
    guard — so an edit to one English sentence silently repointed the entire
    audit chain at a compliance tree the installed wheel is not on. The doc is
    still CHECKED here (it is documentation of the pin, and drifting docs are
    their own defect) but it no longer decides.
    """
    version = adcp.get_adcp_spec_version()
    documented = _SPEC_VERSION_RE.search((repo / "docs" / "adcp-spec-version.md").read_text(encoding="utf-8"))
    if documented and documented.group(1).strip() != version:
        raise StoryboardAuditError(
            f"docs/adcp-spec-version.md documents AdCP {documented.group(1).strip()}, but the installed "
            f"adcp SDK reports {version}. The SDK is the pin — update the doc."
        )
    return version


ADCP_HOME_ENV_VAR = "ADCP_HOME"
ADCP_REPO = "adcontextprotocol/adcp"
# Where `gh release download <tag> --repo adcontextprotocol/adcp` + `tar -xzf`
# lands, relative to the repo root. The storyboard-conformance CI job already
# does exactly this (.github/workflows/ci.yml, "Download pinned … bundle").
BUNDLE_PARENT = Path("tests") / "storyboard" / "runner"


def adcp_home(repo: Path | None = None, version: str | None = None) -> Path:
    """Root of the pinned AdCP tree, preferring the PUBLISHED release bundle.

    Resolution order, first hit wins:

    1. ``$ADCP_HOME`` — explicit override.
    2. The GitHub release bundle extracted in-repo at
       ``tests/storyboard/runner/adcp-<version>/``. This is the authority: it
       is the published, sha256-verified artifact of ``adcontextprotocol/adcp``,
       identical for CI and for every contributor, and it is what the
       storyboard-conformance job already downloads.
    3. ``~/projects/adcp`` — a personal working clone. Last, and only a
       convenience: it is one maintainer's checkout at whatever revision that
       happens to sit on, which is not a thing CI or a contributor can reproduce.

    Six structural guards hardcoded (3) and gated on it, so 23 guards were dead
    in every CI run — they pass whenever they can actually resolve a tree.
    """
    override = os.environ.get(ADCP_HOME_ENV_VAR)
    if override:
        return Path(override)
    if repo is not None:
        resolved = version or pinned_version(repo)
        bundle = repo / BUNDLE_PARENT / f"adcp-{resolved}"
        if bundle.is_dir():
            return bundle
    return Path.home() / "projects" / "adcp"


def dist_root(adcp: Path, version: str) -> Path:
    """The pinned compliance tree root for ``version`` under an adcp root.

    Callers own the existence check (``is_dir()``) — this function only
    differently and both are legitimate inputs:

    * clone   — ``<adcp>/dist/compliance/<version>/`` (every version side by side)
    * bundle  — ``<adcp>/compliance/`` (the tarball is already ONE version)

    Callers own the existence check (``is_dir()``), so this stays import-safe
    with no tree present at all.
    """
    bundle = adcp / "compliance"
    if bundle.is_dir():
        return bundle
    return adcp / "dist" / "compliance" / version


_SPECIALISM_RE = re.compile(r"AdcpSpecialism\.(\w+)")
_PROTOCOL_RE = re.compile(r"SupportedProtocol\.(\w+)")


def declared_capabilities(repo: Path) -> dict[str, set[str]]:
    """Our declared specialisms + protocols, read from src/core/tools/capabilities.py.

    Normalized hyphenated (``sales-non-guaranteed``), matching the majority
    convention (2 of the 3 pre-migration readers) — the path segments this
    is compared against are hyphenated in the pinned tree.
    """
    text = (repo / "src" / "core" / "tools" / "capabilities.py").read_text(encoding="utf-8")
    return {
        "specialisms": {s.replace("_", "-") for s in _SPECIALISM_RE.findall(text)},
        "protocols": {p.replace("_", "-") for p in _PROTOCOL_RE.findall(text)},
    }


# ── Storyboard universe ─────────────────────────────────────────────────────

_SKIP_PREFIXES = ("domains/", "test-kits/", "test-vectors/")
_TRACK_RE = re.compile(r"^track:\s*\S", re.M)


@dataclass
class Storyboard:
    rel: str
    stem: str
    text: str


def storyboard_key(rel: str) -> str:
    """Stable identity for a storyboard file.

    ``Path.stem`` collapses every ``*/index.yaml`` onto the literal
    ``"index"``, making one citation of ``protocols/creative/index.yaml``
    look like a claim on every specialism's index. Key index files by their
    parent directory instead.
    """
    path = Path(rel)
    return path.parent.name if path.stem == "index" else path.stem


def storyboards(dist: Path) -> Iterator[Storyboard]:
    """Every real, gradable storyboard file under the pinned compliance tree.

    Skips: ``domains/`` (mirrors ``protocols/`` byte-for-byte — counted
    once), ``test-kits/``/``test-vectors/`` (not storyboards),
    ``storyboard-schema`` (the schema file, not a storyboard), and any
    ``universal/`` file lacking a top-level ``track:`` (``fictional-
    entities.yaml`` is a shared data catalog; ``runner-output-contract.yaml``
    describes runner OUTPUT shape and is not valid plain YAML). Every real
    storyboard declares ``track:``; confirmed against a real-runner
    baseline — neither exclusion ever appears in ``storyboards_executed`` or
    ``storyboards_missing_tools``.
    """
    for yaml_file in sorted(dist.rglob("*.yaml")):
        rel = str(yaml_file.relative_to(dist))
        if rel.startswith(_SKIP_PREFIXES):
            continue
        stem = storyboard_key(rel)
        if stem == "storyboard-schema":
            continue
        text = yaml_file.read_text(encoding="utf-8")
        if rel.startswith("universal/") and not _TRACK_RE.search(text):
            continue
        yield Storyboard(rel=rel, stem=stem, text=text)


def storyboard_tier(rel_path: str) -> str:
    """Classify a storyboard path into the tier that decides how it is gated.

    Prefix match on the first path segment — NOT a substring scan (a prior
    implementation matched a tier token appearing anywhere in the path, not
    just the leading segment).
    """
    for tier in ("universal", "specialisms", "protocols", "domains", "test-kits", "test-vectors"):
        if rel_path.startswith(f"{tier}/"):
            return tier
    return "unknown"


# ── Gate fields: required_tools / requires_capability / requires_scenarios ─

_TOOLS_BLOCK_RE = re.compile(r"^required_tools:\n((?:\s+-\s+\S+\n)+)", re.M)
# ANCHORED to column 0: a storyboard-level gate only. The pinned schema
# (universal/storyboard-schema.yaml:259-279) also allows requires_capability on a
# PHASE, where it "skips only that phase" — 8 such declarations exist across 3
# files. The previous pattern was unanchored, so once the matcher understood
# more than `equals` it would have attributed a phase gate to its whole
# storyboard: universal/deterministic-testing.yaml has no storyboard-level gate
# and six phase-level `contains:` ones.
#
# All THREE matchers the schema defines, not just `equals`. Measured at the pin,
# counting STORYBOARD-LEVEL declarations only: 54 declaring files — 26 `equals`,
# 16 `contains`, 12 `present`. The equals-only pattern returned None for 28 of
# them, and every one of those was published as fully graded.
#
# (26/17/12 = 55 is the UNANCHORED count. It includes
# universal/deterministic-testing.yaml, whose only gates are phase-level — the
# very file the anchor exists to exclude. Counting the thing you are about to
# exclude is how the wrong number gets written down.)
# Locate the storyboard-level block; PARSE its body rather than pattern-matching
# the keys. Anchored to column 0 on purpose — the pinned schema
# (universal/storyboard-schema.yaml:259-279) also allows requires_capability on a
# PHASE, where it "skips only that phase". 8 such declarations exist across 3
# files, and universal/deterministic-testing.yaml has ONLY phase-level ones, so
# an unanchored pattern would attribute a phase gate to its whole storyboard.
#
# Matching the keys positionally is what the first version did, and it was
# fragile in four ways that legal YAML permits and the tree merely does not use
# today: the matcher declared before `path:`, a comment between the keys, the
# inline flow form `{path: a, equals: b}`, and quoted values (which came back
# WITH their quotes). At the next pin any of those returns None, and a None gate
# is exactly the silent misgrade this change exists to remove — so the block is
# handed to the YAML parser instead.
#
# Whole-file `yaml.safe_load` stays out: one pinned file
# (universal/runner-output-contract.yaml) is not valid plain YAML. It is already
# excluded by the `track:` predicate in storyboards() before any consumer sees
# it, but parsing one small block is narrower than parsing the file regardless.
_CAPABILITY_BLOCK_RE = re.compile(
    r"^requires_capability:[ \t]*(?P<inline>\{.*\})?[ \t]*\n(?P<body>(?:(?:[ \t]+.*)?\n)*)",
    re.M,
)
_CAPABILITY_MATCHERS = ("equals", "contains", "present")
_REQUIRES_SCENARIOS_RE = re.compile(r"^requires_scenarios:\n((?:\s+-\s+\S+\n)+)", re.M)


def required_tools(text: str) -> set[str]:
    """The ``required_tools:`` any-of list from a storyboard's raw text, or empty."""
    block = _TOOLS_BLOCK_RE.search(text)
    if not block:
        return set()
    return {line.strip().lstrip("- ") for line in block.group(1).splitlines()}


def requires_capability(text: str) -> tuple[str, str, str] | None:
    """The storyboard-level ``requires_capability`` gate as (path, matcher, value).

    Carries the MATCHER, because the three the schema defines mean different
    things and a caller that assumes ``equals`` publishes a false predicate for
    the other two. Callers render it with :func:`capability_predicate`.

    Returns None when the storyboard declares no storyboard-level gate. A
    phase-level gate is deliberately not returned: the schema scopes it to its
    phase, so it is not a property of the storyboard.
    """
    match = _CAPABILITY_BLOCK_RE.search(text)
    if not match:
        return None
    fragment = match.group("inline") or textwrap.dedent(match.group("body"))
    try:
        gate = yaml.safe_load(fragment)
    except yaml.YAMLError as exc:  # a malformed gate must not read as "no gate"
        raise StoryboardAuditError(f"requires_capability block is not parseable YAML: {exc}") from exc
    if not isinstance(gate, dict) or "path" not in gate:
        raise StoryboardAuditError(f"requires_capability declares no `path`: {gate!r}")
    declared = [m for m in _CAPABILITY_MATCHERS if m in gate]
    if len(declared) != 1:
        # The schema says exactly one of equals/contains/present SHOULD be
        # declared, and that runners MAY fail-load otherwise. Failing loudly
        # beats returning None, which would silently grade the storyboard as
        # ungated — the defect this whole change removes.
        raise StoryboardAuditError(
            f"requires_capability must declare exactly one of {list(_CAPABILITY_MATCHERS)}, got {declared or 'none'}"
        )
    matcher = declared[0]
    return (
        str(gate["path"]),
        matcher,
        str(gate[matcher]).lower() if isinstance(gate[matcher], bool) else str(gate[matcher]),
    )


def capability_predicate(gate: tuple[str, str, str]) -> str:
    """Render a gate as the predicate the schema defines, e.g. ``a.b contains x``.

    One renderer, so no call site re-spells it — the reason string in
    ``storyboard_coverage_map`` hardcoded ``==`` and would have published
    ``path == value`` for 29 storyboards that declare ``contains`` or ``present``.
    """
    path, matcher, value = gate
    symbol = {"equals": "==", "contains": "contains", "present": "present:"}[matcher]
    return f"{path} {symbol} {value}"


def requiring_indexes(dist: Path) -> dict[str, list[str]]:
    """Map each scenario id to the index.yaml files whose requires_scenarios pulls it in.

    A scenario's directory does NOT determine its gate — ``requires_scenarios``
    is composition ("scenario IDs that must pass alongside this storyboard"),
    the reachability edge, never a whitelist of what applies.
    """
    required_by: dict[str, list[str]] = {}
    for index in sorted(dist.rglob("index.yaml")):
        rel_index = str(index.relative_to(dist))
        if rel_index.startswith("domains/"):
            continue
        block = _REQUIRES_SCENARIOS_RE.search(index.read_text(encoding="utf-8"))
        if not block:
            continue
        for line in block.group(1).splitlines():
            scenario_id = line.strip().lstrip("- ").split("/")[-1]
            required_by.setdefault(scenario_id, []).append(rel_index)
    return required_by


def index_reachable(rel_index: str, declared: dict[str, set[str]]) -> bool:
    """Can we reach this index at all, given what we declare?"""
    parts = rel_index.split("/")
    tier, name = parts[0], parts[1]
    if tier == "specialisms":
        return name in declared["specialisms"]
    if tier == "protocols":
        return name in declared["protocols"]
    return True


# ── Phase / check parsing ───────────────────────────────────────────────────

_PHASE_ID_RE = re.compile(r"^\s*-\s*id:\s*(?P<id>[a-z][a-z0-9_]{3,})\s*$", re.M)
# A real "check:" line's value is a bare identifier alone on the line — either
# list-item-first ("- check: X") or a sibling key within an id-first item
# ("  check: X"). Prose that happens to end in the word "check:" has nothing
# (or a multi-word sentence) after it, not a lone token, so it never matches:
# requiring `\s*$` immediately after the captured token excludes both
# "...parity check:" (nothing captured) and "check: error_code assertions
# ensure..." (more text follows the first token before end of line).
_CHECK_LINE_RE = re.compile(r"^\s*(?:-\s*)?check:\s*(\S+)\s*$", re.M)


def phases(text: str) -> set[str]:
    """Every phase/step ``- id:`` declared in a storyboard's raw text."""
    return {m.group("id") for m in _PHASE_ID_RE.finditer(text)}


def phase_index(dist: Path) -> dict[str, list[str]]:
    """Map every phase/step id at the pinned version to the files declaring it."""
    index: dict[str, list[str]] = {}
    for sb in storyboards(dist):
        for phase_id in phases(sb.text):
            index.setdefault(phase_id, []).append(sb.rel)
    return index


def _phase_window(text: str, phase_id: str) -> tuple[int, str] | None:
    """The text a phase owns, as ``(absolute offset, window)`` — ``None`` if absent.

    Anchors on the phase's ``- id:`` line, then windows to the next SIBLING
    id at the same indent (not the next id at any depth — phases sit two
    spaces in and their steps six; stopping at the first six-space ``- id:``
    would truncate at the phase's first step and never reach a later
    ``validations:`` block, misreporting a graded phase as prose).

    A phase's window therefore ENCLOSES its own steps' windows. That overlap
    is correct for "is this phase graded at all?" and fatal for "how many
    checks does this storyboard grade?" — which is why the offset is returned
    alongside the text: :func:`check_inventory` keys on it to count each
    check line exactly once.
    """
    anchor = re.search(rf"^(?P<indent>\s*)-\s*id:\s*{re.escape(phase_id)}\s*$", text, re.M)
    if anchor is None:
        return None
    indent = len(anchor.group("indent"))
    sibling = re.compile(rf"^\s{{0,{indent}}}-\s*id:\s*\S+\s*$", re.M)
    rest = text[anchor.end() :]
    following = sibling.search(rest)
    return anchor.end(), (rest[: following.start()] if following else rest)


def checks_for_phase(text: str, phase_id: str) -> list[str]:
    """Check types graded under one phase — ``[]`` if absent or narrative-only.

    Windows via :func:`_phase_window`, so a phase whose grading lives in a
    later step still reads as graded. Matches ``- check: X`` and the id-first
    sibling form ``check: X`` alike — the leading-dash-only form used
    pre-migration missed every id-first ordering (18 real checks in
    ``protocols/media-buy/scenarios/refine_finalize_exclusivity.yaml`` alone).

    Deliberately NOT summable across :func:`phases` — see
    :func:`check_inventory`.
    """
    window = _phase_window(text, phase_id)
    return _CHECK_LINE_RE.findall(window[1]) if window else []


_STEP_TOOL_RE = re.compile(r"^\s*(?:task|requires_tool):\s*(\S+)\s*$", re.M)


def step_tools(text: str, step_id: str) -> set[str]:
    """Tools a single step names via ``task:`` or ``requires_tool:``.

    A storyboard's top-level ``required_tools:`` is not the whole story: several
    pinned storyboards reach for ``comply_test_controller`` in one seeding step
    while the rest of the file is ordinary client traffic
    (``universal/pagination-integrity-list-accounts.yaml`` seeds three accounts
    that way, then grades ``list_accounts`` normally). Judging such a file only
    by its top-level block marks every check in it ungradable, when in truth
    only the seeding steps are.
    """
    window = _phase_window(text, step_id)
    return set(_STEP_TOOL_RE.findall(window[1])) if window else set()


def checks_by_owner(text: str) -> list[tuple[str, str, str | None]]:
    """Every graded check as ``(owner_id, check_type, parent_id)``, in file order.

    A check line sits inside several nested windows at once — its step's, and
    that step's phase's. The OWNER is the innermost id whose window contains
    it, and the parent is the next one out. Attributing to the innermost is
    what makes a check addressable: the conformance ledger keys on
    ``(protocol, track, storyboard_id, step_id)``, so the step is the unit a
    scenario or a ticket can actually be mapped onto.

    Same offset-keyed traversal as :func:`check_inventory`, which counts these
    same lines — kept as one traversal so the two cannot disagree about what a
    check is.
    """
    windows: list[tuple[int, int, str]] = []
    for phase_id in phases(text):
        window = _phase_window(text, phase_id)
        if window is None:
            continue
        offset, body = window
        windows.append((offset, offset + len(body), phase_id))

    owners: dict[int, tuple[str, str, str | None]] = {}
    for offset, end, _owner_id in windows:
        # Scanned per window, but ownership is decided by the ENCLOSING spans
        # below, not by the window a match was found in — a check line appears
        # in every window that contains it.
        body = text[offset:end]
        for match in _CHECK_LINE_RE.finditer(body):
            position = offset + match.start()
            enclosing = [w for w in windows if w[0] <= position < w[1]]
            # Innermost = smallest span; its parent = the next smallest.
            enclosing.sort(key=lambda w: w[1] - w[0])
            innermost = enclosing[0][2]
            parent = enclosing[1][2] if len(enclosing) > 1 else None
            owners[position] = (innermost, match.group(1), parent)

    return [owners[position] for position in sorted(owners)]


# A step whose `task:` is one of these is GRADED by the runner even though it
# declares no `check:` line of its own — the assertion lives in structured
# fields (`expect_idempotency_key`, `webhook_payload_schema_ref`,
# `expect_min_deliveries`, …) instead. The pinned storyboard schema documents
# the family and its `triggered_by` link (universal/storyboard-schema.yaml:501,
# 2005-2019). At 3.1.1 that is `expect_webhook`, `expect_webhook_*`,
# `assert_*`, and `fetch_brand_jwks`.
_ASSERTION_TASK_PREFIXES = ("expect_", "assert_")
_ASSERTION_TASK_NAMES = frozenset({"fetch_brand_jwks"})

_STEP_ID_RE = re.compile(r"^      - id: (\S+)\s*$", re.M)
_STEP_TASK_RE = re.compile(r"^\s+task: ([A-Za-z0-9_]+)\s*$", re.M)
_STEP_TRIGGERED_BY_RE = re.compile(r"^\s+triggered_by: (\S+)\s*$", re.M)


def _is_assertion_task(task: str) -> bool:
    return task.startswith(_ASSERTION_TASK_PREFIXES) or task in _ASSERTION_TASK_NAMES


def graded_steps_by_task(text: str) -> list[tuple[str, str, str | None]]:
    """Graded steps that declare NO ``check:`` line — ``(owner_id, task, phase_id)``.

    The companion to :func:`checks_by_owner`, not a replacement: that function
    owns the literal ``check:`` traversal and :func:`check_inventory` is pinned
    to it line-for-line by a guard. This one covers the OTHER way the pinned
    tree grades a step.

    Why it exists: the conformance ledger keys on
    ``(protocol, track, storyboard_id, step_id)`` and takes ``step_id`` verbatim
    from the real ``@adcp/sdk`` runner. The runner grades an ``expect_webhook``
    step and attributes any failure to the step named in its ``triggered_by``
    — while the index, seeing no ``check:`` line, produced no row at all. So
    ``measured`` joined nothing for those steps: 7 ledger entries on
    ``universal/webhook-emission.yaml`` resolved to no record, and a check
    reading "no ledger entry" was not evidence that it passed.

    OWNER is ``triggered_by`` when present — matching the runner — and the step
    itself otherwise (``fetch_brand_jwks``/``assert_jwks_purpose`` carry no
    trigger). A step that declares its own ``check:`` lines is SKIPPED here and
    left to :func:`checks_by_owner`, so the two never double-count the same
    step: at 3.1.1, 8 assertion-task steps carry ``check:`` lines and 19 do not.
    """
    windows: list[tuple[int, int, str]] = []
    for phase_id in phases(text):
        window = _phase_window(text, phase_id)
        if window is not None:
            offset, body = window
            windows.append((offset, offset + len(body), phase_id))

    steps = list(_STEP_ID_RE.finditer(text))
    graded: list[tuple[str, str, str | None]] = []
    for index, match in enumerate(steps):
        end = steps[index + 1].start() if index + 1 < len(steps) else len(text)
        block = text[match.end() : end]
        task_match = _STEP_TASK_RE.search(block)
        if task_match is None or not _is_assertion_task(task_match.group(1)):
            continue
        if _CHECK_LINE_RE.search(block):
            continue
        triggered_by = _STEP_TRIGGERED_BY_RE.search(block)
        owner = triggered_by.group(1) if triggered_by else match.group(1)
        enclosing = [w for w in windows if w[0] <= match.start() < w[1]]
        enclosing.sort(key=lambda w: w[1] - w[0])
        graded.append((owner, task_match.group(1), enclosing[0][2] if enclosing else None))
    return graded


def check_inventory(text: str) -> dict[str, int]:
    """Every check type this storyboard grades, counted once — ``{type: count}``.

    The per-storyboard check inventory published by the storyboard roadmap
    (``scripts/audit/storyboard_roadmap.py``). Summing
    :func:`checks_for_phase` over :func:`phases` — the obvious spelling, and
    the one that shipped — double-counts every nested check: ``phases()``
    returns ids at ANY depth, and a phase's window already encloses its
    steps'. At the 3.1.1 pin that inflated 119 of 121 storyboards
    (``universal/signed-requests.yaml`` published 2 field_present + 2
    field_value against 1 of each in the file).

    :func:`checks_by_owner` already does the absolute-OFFSET-keyed traversal
    that dedupes overlapping windows (a check line counted once regardless of
    how many enclosing phase/step windows contain it) -- this is a Counter
    over its check types, not a second traversal. Two implementations of the
    same offset-keyed walk previously existed here and in
    :func:`checks_by_owner`; keeping one is what stops them from silently
    disagreeing about what a check is.
    """
    return dict(sorted(Counter(check_type for _, check_type, _ in checks_by_owner(text)).items()))


_STORYBOARD_ID_RE = re.compile(r"^id:\s*(\S+)", re.M)


def storyboard_id(text: str) -> str | None:
    """A storyboard's DECLARED top-level ``id:``, or ``None``.

    Differs from the filename for 69 of 121 storyboards at 3.1.1
    (``universal/security.yaml`` declares ``security_baseline``; every
    media-buy scenario is namespaced ``media_buy_seller/<name>``), and the
    runner keys its results on the declared id — joining on the filename stem
    matches only where the two happen to coincide. Callers own the fallback:
    what to do with an id-less storyboard is consumer policy, not a fact
    about the tree.
    """
    match = _STORYBOARD_ID_RE.search(text)
    return match.group(1) if match else None


def phase_is_graded(text: str, phase_id: str) -> str | None:
    """Is ``phase_id`` present, and graded (``validations:``) or narrative (``expected:``)?

    Returns ``"graded"``, ``"prose"``, ``"absent"``, or ``None`` when
    ``phase_id`` is falsy (no phase was cited).
    """
    if not phase_id:
        return None
    if re.search(rf"^\s*-\s*id:\s*{re.escape(phase_id)}\s*$", text, re.M) is None:
        return "absent"
    return "graded" if checks_for_phase(text, phase_id) else "prose"


# ── @source footers + tagged scenarios ──────────────────────────────────────

# ── THE SHARED LIVENESS CONTRACT (#1858) ───
#
# This module is the ONE owner of the constants and lookups that tests/bdd and
# scripts/audit both need. It is stdlib-only and imports no pytest and no
# conftest, so either side may import it at module scope. The direction matters:
# ~20 test modules already import scripts.audit.* at module scope, while
# scripts/audit has exactly one import of the test tree and it is deliberately
# deferred inside a function body. An owner under tests/ would invert that.
#
# (Defining a CLI entry point here does not execute one — importing this module
# runs no argparse.)

#: The storyboard provenance tag, in ONE representation: WITHOUT the leading
#: ``@``. The two former copies disagreed — scenario_liveness.py held
#: "storyboard-v3.1" and this module held "@storyboard-v3.1" — so any consumer
#: comparing them had to know which convention it was holding. Callers that need
#: the Gherkin spelling use :func:`tag_literal`.
STORYBOARD_TAG = "storyboard-v3.1"


def tag_literal(tag: str = STORYBOARD_TAG) -> str:
    """The Gherkin spelling of *tag* (with the leading ``@``)."""
    return tag if tag.startswith("@") else f"@{tag}"


#: Environment variable naming where the BDD liveness artifact is written, and
#: the default filename when it is unset. Read by the pytest plugin that WRITES
#: the artifact and by the audit join that READS it — hence shared.
ARTIFACT_ENV_VAR = "BDD_LIVENESS_ARTIFACT"
DEFAULT_ARTIFACT_PATH = "bdd_scenario_liveness.json"

#: Identity tag -> use-case number. Byte-identical copies previously sat in
#: tests/bdd/conftest.py and scripts/audit/scenario_liveness_join.py, each with
#: a comment stating it mirrored the other.
UC_TAG_RE = re.compile(r"^T-UC-(\d{3})(?:-|$)")

TAG = tag_literal()


ADMIN_TAG_PREFIX = "T-ADMIN-"


def is_brand_shorthand_media_buy(marker_names: frozenset[str]) -> bool:
    """True when a brand_shorthand scenario targets create_media_buy (the UC-002 harness)."""
    return "brand_shorthand" in marker_names and "create_media_buy" in marker_names


def detect_uc(marker_names: frozenset[str]) -> str | None:
    """The use-case bucket a scenario belongs to, from its marker names alone.

    The UC number derives from the identity tag itself, so a scenario carrying
    ``T-UC-<n>`` needs no per-UC branch. Only genuinely non-derivable cases are
    named: ADMIN and COMPAT carry no ``T-UC-<n>`` tag, UC-GET-PRODUCTS /
    brand_shorthand-create_media_buy route on tags that are not UC-numbered,
    and A2A-TASK-OWNERSHIP uses ``T-A2A-TASK-OWNERSHIP-`` (#1959).

    Absorbed from tests/bdd/conftest.py, which is why it lives here: the audit
    join RE-IMPLEMENTED this lookup, and the two copies disagreeing is the
    mechanism behind the dormant-claim false positive the join exists to remove.
    """
    if any(t.startswith(ADMIN_TAG_PREFIX) for t in marker_names):
        return "ADMIN"
    if "inventory_profile" in marker_names or (
        "brand_shorthand" in marker_names and not is_brand_shorthand_media_buy(marker_names)
    ):
        return "UC-GET-PRODUCTS"
    if is_brand_shorthand_media_buy(marker_names):
        return "UC-002"
    if any(t.startswith("T-COMPAT") for t in marker_names):
        return "COMPAT"
    # Local A2A protocol-method scenarios (tasks/get, tasks/cancel — #1702/#1780/#1959).
    if any(t.startswith("T-A2A-TASK-OWNERSHIP-") for t in marker_names):
        return "A2A-TASK-OWNERSHIP"
    for tag in sorted(marker_names):
        match = UC_TAG_RE.match(tag)
        if match:
            return f"UC-{match.group(1)}"
    return None


def uc011_harness(marker_names: frozenset[str]) -> str:
    """Which UC-011 account harness a scenario's markers call for.

    A ROUTING PREDICATE, so it lives with the resolver rather than beside the
    conftest that used to own it: the audit join has to reach the same verdict,
    and a predicate one call-frame outside the shared resolver is invisible to it.

    ``sync`` wins when both @sync and @list are present — it is the superset and
    already has a cross-cutting list path.
    """
    has_list, has_sync = "list" in marker_names, "sync" in marker_names
    if has_sync and has_list:
        return "sync"
    if has_list:
        return "list"
    if has_sync:
        return "sync"
    if "context-echo" in marker_names or "sandbox" in marker_names:
        return "sync"
    return "unknown"


def uc004_harness(marker_names: frozenset[str]) -> str:
    """Which UC-004 delivery harness a scenario's markers call for.

    TOTAL: every marker set maps to "create", "circuit-breaker" or the "poll"
    fallback, which is why UC-004 needs no not-wired row — and why a change that
    made it partial would silently start reporting UC-004 scenarios unwired.
    """
    if {"T-UC-004-webhook-creds-short", "T-UC-004-webhook-creds-valid"} & marker_names:
        return "create"
    if "webhook-reliability" in marker_names or "webhook" in marker_names:
        return "circuit-breaker"
    return "poll"


def resolve_env_route(marker_names: Any, env_routes: Any) -> Any:
    """The ONE routing decision: which harness env a scenario resolves to, or None.

    *env_routes* is an ORDERED sequence of route rows, INJECTED by the caller so
    this stdlib-only module keeps no import-time coupling to the pytest conftest
    that owns the registry (the same injection precedent as
    ``scenario_liveness_join.build_index``). A row is opaque here except for two
    attributes:

    * ``when``  — optional predicate over the marker-name set. Rows carrying one
      are tried first, in declaration order.
    * ``uc``    — optional use-case bucket, matched against :func:`detect_uc`.

    Returning ``None`` means NOT WIRED. That is a real answer, not an absence:
    each branch UC keeps a catch-all that xfails its unwired scenarios, so a
    resolver that reported everything wired would convert today's false-DORMANT
    into a false-LIVE — at the ~800-scenario scale the conftest documents.

    Both sides call THIS function. Previously the conftest matched a UC bucket
    and then fell through a hardcoded ``elif`` chain of marker predicates, while
    the audit join knew only about the buckets — so every scenario routed by a
    predicate was invisible to the join and reported dormant.
    """
    markers = frozenset(marker_names)
    for route in env_routes:
        predicate = getattr(route, "when", None)
        if predicate is not None and predicate(markers):
            return route
    uc = detect_uc(markers)
    if uc is None:
        return None
    for route in env_routes:
        if getattr(route, "when", None) is None and getattr(route, "uc", None) == uc:
            return route
    return None


def parse_ledger_lines(path: Path, *, grammar: Callable[[str], Any]) -> list[Any]:
    """Parse a line-based known-failures ledger, applying *grammar* to each entry.

    ONE line scan for every ledger in the repo. Blank lines and ``#`` comments
    are dropped; every surviving line is handed to the caller's *grammar*.

    The two ledgers are genuinely different — tests/bdd/e2e_rest_known_failures.txt
    holds plain pytest nodeids, tests/storyboard/known_failures.txt holds the
    bracket grammar ``protocol::track::storyboard::step`` — so this is one
    function with two callers, NOT one function with two implementations. The
    thing they must share is the SCAN and the failure policy, not the grammar.

    LOUD, never silent: a line the grammar rejects (by returning ``None`` or
    raising) is a ``ValueError`` naming the file and line number. The previous
    bracket parser dropped unparsable lines silently AND its docstring ratified
    the drop as intended, so a typo'd ledger entry simply stopped grading its
    scenario with nothing to notice. A ledger that cannot be parsed is a broken
    ledger.
    """
    parsed: list[Any] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            entry = grammar(stripped)
        except Exception as exc:  # noqa: BLE001 - re-raised with position below
            raise ValueError(f"{path}:{lineno}: unparsable ledger line {stripped!r}: {exc}") from exc
        if entry is None:
            raise ValueError(
                f"{path}:{lineno}: unparsable ledger line {stripped!r} — "
                "fix the entry or remove it; a ledger line that cannot be parsed grades nothing"
            )
        parsed.append(entry)
    return parsed


_TAGLINE_RE = re.compile(r"^\s*@[\w.\-]+(?:\s+@[\w.\-]+)*\s*$")
_SCENARIO_RE = re.compile(r"^\s*Scenario(?: Outline)?:\s*(?P<title>.+?)\s*$")
_IDENT_TAG_RE = re.compile(r"@(T-[A-Za-z0-9_\-]+)")
_SOURCE_LINE_RE = re.compile(r"^\s*#?\s*@source[ \t]+(?P<rest>.+)$", re.M)
_KV_TOKEN_RE = re.compile(r"^(\w+)=(\S+)$")
_CITED_PATH_PREFIX_RE = re.compile(r"^(?:dist/compliance/[^/]+/|static/compliance/source/)")
_SELF_DECLARED_NAME_RE = re.compile(r"^\s*#\s*([a-z][a-z0-9_]{3,}):\s", re.M)

# The closed @source footer grammar. Any key outside this set (a `phases=`
# pluralization typo) or a bare `key=value` token that fails to parse (trailing
# prose like `(recovery via enumMetadata)`) is a WRITER error, not a fact to
# silently drop -- see SourceFooterError.
_SOURCE_FOOTER_KEYS = frozenset({"repo", "ref", "commit", "phase", "step", "path"})
_REQUIRED_SOURCE_FOOTER_KEYS = frozenset({"repo", "ref", "path"})


@dataclass
class TaggedScenario:
    """One scenario tagged with ``TAG``, and its trailing comment block."""

    feature: str
    line: int
    identifier: str
    tags: list[str]
    title: str
    block: str
    self_declared_names: set[str] = field(default_factory=set)


def tagged_scenarios(features_dir: Path, tag: str = TAG) -> list[TaggedScenario]:
    """Every scenario tagged ``tag``, with its title and trailing comment block.

    The block runs from the tag line to the next scenario's tag line (or 80
    lines, whichever comes first) — NOT a fixed 60-line window with no
    terminator, which bleeds the following scenario's ``@source`` citations
    into this one's block (verified: 16 of 21 tagged scenarios in
    tests/bdd/features/ were affected pre-fix).
    """
    found: list[TaggedScenario] = []
    for feature in sorted(features_dir.glob("*.feature")):
        lines = feature.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if tag not in line or not _TAGLINE_RE.match(line):
                continue
            tags = line.split()
            title, title_line = "", idx + 1
            for probe in range(idx + 1, min(idx + 4, len(lines))):
                if match := _SCENARIO_RE.match(lines[probe]):
                    title, title_line = match.group("title"), probe + 1
                    break
            ident = next((m.group(1) for t in tags if (m := _IDENT_TAG_RE.match(t))), "(unnamed)")
            block_lines: list[str] = []
            for probe in range(idx, min(idx + 80, len(lines))):
                if probe > idx and _TAGLINE_RE.match(lines[probe]):
                    break
                block_lines.append(lines[probe])
            block = "\n".join(block_lines)
            found.append(
                TaggedScenario(
                    feature=feature.name,
                    line=title_line,
                    identifier=ident,
                    tags=tags,
                    title=title,
                    block=block,
                    self_declared_names=set(_SELF_DECLARED_NAME_RE.findall(block)),
                )
            )
    return found


class SourceFooterError(ValueError):
    """An ``@source`` line does not match the closed footer grammar.

    Raised, never silently swallowed: an unknown key (``phases=``, a
    pluralization typo) or a non-``key=value`` token (trailing prose like
    ``(recovery via enumMetadata)``) used to parse clean and vanish -- the
    unknown key was dropped, the trailing prose was never even attempted.
    Both are writer-side mistakes and must fail loudly at ``make quality``,
    not ship as a footer that looks valid and isn't.
    """


@dataclass(frozen=True)
class SourceFooter:
    """One parsed ``@source`` footer.

    ``repo``, ``ref``, ``path`` are always present -- :func:`parse_source_footer`
    raises :class:`SourceFooterError` rather than return a partial footer.
    ``commit``, ``phase``, ``step`` are optional and ``None`` when the footer
    omits them. ``step`` names the addressable unit a scenario or ticket maps
    onto (the conformance ledger's ``(protocol, track, storyboard_id, step_id)``
    key) -- callers that resolve bindings must check it, not just carry it.
    """

    repo: str
    ref: str
    path: str
    commit: str | None = None
    phase: str | None = None
    step: str | None = None


def parse_source_footer(block: str) -> SourceFooter | None:
    """Parse an ``@source repo=... ref=... [commit=...] [phase=...] [step=...] path=...`` footer.

    Returns ``None`` only when no ``@source`` line is present at all -- a
    tagged scenario simply carrying no footer, which is its own (separately
    reported) violation. When an ``@source`` line IS present, its grammar is
    enforced strictly and unforgivingly: every token must be ``key=value``
    with ``key`` drawn from the closed set ``{repo, ref, commit, phase, step,
    path}``, no key repeats, and ``repo``/``ref``/``path`` are all present --
    anything else raises :class:`SourceFooterError`. ``path``'s ``#L..``
    line-fragment suffix is stripped.

    Order-agnostic over the ``key=value`` tokens (a prior implementation
    required an exact ``repo ref [commit] [phase] path`` order and silently
    failed to match any footer that didn't follow it).
    """
    match = _SOURCE_LINE_RE.search(block)
    if match is None:
        return None
    line = match.group(0)
    values: dict[str, str] = {}
    for token in match.group("rest").split():
        kv = _KV_TOKEN_RE.match(token)
        if kv is None:
            raise SourceFooterError(f"non key=value token {token!r} in @source footer: {line!r}")
        key, value = kv.group(1), kv.group(2)
        if key not in _SOURCE_FOOTER_KEYS:
            raise SourceFooterError(f"unknown key {key!r} in @source footer: {line!r}")
        if key in values:
            raise SourceFooterError(f"repeated key {key!r} in @source footer: {line!r}")
        values[key] = value
    missing = _REQUIRED_SOURCE_FOOTER_KEYS - values.keys()
    if missing:
        raise SourceFooterError(f"missing required key(s) {sorted(missing)} in @source footer: {line!r}")
    return SourceFooter(
        repo=values["repo"],
        ref=values["ref"],
        path=values["path"].split("#", 1)[0],
        commit=values.get("commit"),
        phase=values.get("phase"),
        step=values.get("step"),
    )


def normalize_cited_path(raw_path: str) -> str:
    """Strip the dist/compliance/<version>/ or static/compliance/source/ prefix."""
    return _CITED_PATH_PREFIX_RE.sub("", raw_path)


# ── Shared CLI entrypoint ────────────────────────────────────────────────────
# Every scripts/audit/storyboard_*.py sibling has the identical
# --repo/--adcp/--markdown argparse + "print JSON or rendered markdown" shape
# — a duplicate `main()` in each file is the same disease this module exists
# to close, one layer up (caught by the duplication guard when a 3rd copy of
# this exact function landed alongside coverage_map.py and binding_sweep.py).


def run_cli(
    description: str,
    build_fn: Callable[..., dict[str, Any]],
    render_fn: Callable[[dict[str, Any]], str],
    jsonl_fn: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    *,
    configure_args: Callable[[argparse.ArgumentParser], None] | None = None,
    build_args: Callable[[argparse.Namespace], tuple[Any, ...]] | None = None,
) -> int:
    """Standard CLI: parse args, ``build_fn(*build_args(args))``, print, catch.

    Default shape (``configure_args``/``build_args`` both ``None``) is
    ``--repo/--adcp/--markdown[/--jsonl]``, ``build_fn(repo, adcp)`` — every
    ``storyboard_*.py`` sibling but ``storyboard_reconciliation`` uses this
    default. A consumer whose ``build_fn`` takes different arguments (e.g.
    ``storyboard_reconciliation.build(proposals, expected)``) supplies both
    hooks instead of hand-rolling its own argparse + print + error-handling
    boilerplate — the duplicate ``main()`` this module exists to close.

    ``--jsonl`` emits one JSON object per line. A consumer that offers it is
    declaring that the JSONL is its SOURCE OF TRUTH and the markdown a
    rendering of it — so the two can never drift, and a new view is a new
    renderer rather than a new artifact.

    A :class:`StoryboardAuditError` raised by ``build_fn`` (a data problem
    the pipeline itself detected — a broken join, a malformed pinned file, an
    unresolvable citation) is caught here, printed to stderr, and turned into
    exit code 1. Turning a caught error into a process exit belongs to CLI
    entry points only — every library function raises the typed error and lets
    its caller decide, so importing these functions from a test or a sibling
    script never risks killing the importing process. This is one of two such
    entry points; a caller with its own argument parser supplies one
    and repeats the same catch.
    """
    parser = argparse.ArgumentParser(description=description)
    if configure_args is None:
        parser.add_argument("--repo", type=Path, default=Path.cwd())
        # Resolved AFTER parsing, from --repo, so it goes through adcp_home():
        # $ADCP_HOME, then the in-repo release bundle, then a personal clone.
        # Defaulting to the clone here made the published regeneration command
        # fail on any machine that has the bundle and no clone — which is every
        # CI runner and every contributor.
        parser.add_argument("--adcp", type=Path, default=None)
    else:
        configure_args(parser)
    parser.add_argument("--markdown", action="store_true")
    if jsonl_fn is not None:
        parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        # Inside the try: adcp_home() consults pinned_version(), which reads the
        # installed SDK and raises the typed error on pin drift. Resolving out
        # here would traceback instead of the documented "error: ..." exit 1.
        if getattr(args, "adcp", None) is None and hasattr(args, "repo"):
            args.adcp = adcp_home(args.repo)
        call_args = build_args(args) if build_args is not None else (args.repo.resolve(), args.adcp.resolve())
        result = build_fn(*call_args)
    except StoryboardAuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # --jsonl and --markdown are INDEPENDENT outputs, not alternatives. The
    # published regeneration command passes both (see the check index's own
    # rendered header), and returning after the JSONL emitted only half the pair —
    # which is how the committed markdown drifted from its own source of truth
    # back when it WAS committed.
    wrote = False
    if jsonl_fn is not None and getattr(args, "jsonl", False):
        for record in jsonl_fn(result):
            print(json.dumps(record, sort_keys=True))
        wrote = True
    if args.markdown:
        # Exactly ONE trailing newline. render_fn already ends its last line, so
        # a bare print() added a second — which end-of-file-fixer then strips,
        # leaving the committed artifact permanently unreproducible from its own
        # generator and every regeneration a spurious one-byte diff.
        sys.stdout.write(render_fn(result).rstrip("\n") + "\n")
        wrote = True
    if not wrote:
        print(json.dumps(result, indent=2))
    return 0


__all__ = [
    "TAG",
    "SourceFooter",
    "SourceFooterError",
    "Storyboard",
    "StoryboardAuditError",
    "TaggedScenario",
    "check_inventory",
    "checks_by_owner",
    "checks_for_phase",
    "declared_capabilities",
    "dist_root",
    "index_reachable",
    "normalize_cited_path",
    "parse_source_footer",
    "phase_index",
    "phase_is_graded",
    "phases",
    "pinned_version",
    "required_tools",
    "requires_capability",
    "requiring_indexes",
    "run_cli",
    "step_tools",
    "storyboard_id",
    "storyboard_key",
    "storyboard_tier",
    "storyboards",
    "tagged_scenarios",
]
