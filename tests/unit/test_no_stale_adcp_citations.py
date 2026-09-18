"""Guard: no stale AdCP citations — pre-GA markers, a dangling prose root, a wrong pin.

Three diseases, one scanner. Each is "a citation that looks authoritative and
is not", and each regressed by hand before a guard existed.

Stale pre-GA markers (#1417)
----------------------------
AdCP 3.1.0 and 3.1.1 are published as GA compliance dirs. Comments that still
cite the pre-GA ``3.1.0-rc.12`` storyboard as "latest published compliance", or
assert "no GA 3.1.0 dir exists yet", are now factually wrong and mislead the
next reader about which spec artifact grades the pinned behavior.

This regressed once (#1417 re-review): a citation-refresh commit enumerated 8
sites but touched 7, leaving stale ``rc.12`` / "no GA 3.1.0" citations in
tests/bdd/features/BR-UC-002-media-buy-status-dual-emit.feature. "Done" was a
subjective "refreshed the citations" instead of a grep that must return empty.

This test makes that grep permanent: any ``rc.12`` or "no GA 3.1.0" marker in
tests/, docs/, or src/ fails the build so a stale citation cannot reappear.

The dangling prose root (#1757)
-------------------------------
Spec prose is read from the repository-root ``docs/`` tree at the pinned tag.
The built ``dist/docs/`` tree stops at ``3.1.0``, so a citation naming
``dist/docs/3.1.1/`` resolves at NO tag — it is not stale, it is unresolvable,
and ``git cat-file -e`` on it exits non-zero. Five such citations sat in
tests/bdd/features/BR-UC-010-discover-seller-capabilities.feature and were
corrected by hand.

That file is GENERATED, which is what makes a guard load-bearing rather than
tidy: a regeneration run reinstates whatever the generator emits, and the
generator is not in this repository (searched: docs/test-obligations/, its
bdd-traceability.yaml, scripts/, src/, tests/, and the surface skill and
formula — nothing writes these ``@source`` headers). Until it is found and the
correction mirrored into it, this guard is what turns a silent regression into
a red test.

Why the marker is narrow, and must stay narrow
----------------------------------------------
Only the ``3.1.1`` root dangles. ``dist/docs/3.1.0``, and the ``-rc`` and
``-beta`` roots, all RESOLVE at ``v3.1.1`` and are legitimate citations — a
marker on ``dist/docs`` broadly would redden working ones, and a marker on
``dist/`` would redden 323 lines, since ``dist/schemas/3.1.1`` and
``dist/compliance/3.1.1`` both keep version-numbered roots.

The marker also has to tell a CITATION from a WARNING. Two files inside the
scan roots name the dangling root precisely in order to refuse it —
docs/adcp-spec-version.md and src/core/signing/verifier.py (the spec-grounding
header of the in-boundary verifier) — and reddening those would delete the
warning to satisfy the guard. The discriminator is what FOLLOWS the root: a
citation continues into a path (``dist/docs/3.1.1/building/...``), while a
warning stops at the trailing slash and closes its quoting (``there is no
``dist/docs/3.1.1/`` at that tag``). Requiring a path character after the
slash separates them with no allowlist at all, which matters because an
allowlist may only shrink and this one would need entries for prose that is
doing the right thing.

The wrong pin claim (#1567)
---------------------------
Third marker, same disease one level up: a file that states IN THE PRESENT
TENSE which AdCP spec version / ``adcp`` SDK release this project targets,
where that statement does not match the actual pin. The pin has exactly two
sources — ``docs/adcp-spec-version.md`` (spec version, read through
``scripts.audit.storyboard_spec.pinned_version``) and the ``adcp==`` entry in
``pyproject.toml`` (SDK). Every other file merely repeats them, and repetition
rots: after the #1567 bump to ``adcp==6.6.0`` / spec 3.1.1, CLAUDE.md and
README.md still told every reader the project targets 3.1.0-beta.3 via
``adcp==5.7.0`` — i.e. the document that defines the spec-grounding gate named
the wrong authority.

The marker is deliberately the PIN CLAIM, not the digits. A bare version
matcher would fail on dated review records (docs/releases/2.0.0.md's "Spec
target — adcp==5.7.0" release note), on past-tense history (adr-001's "The
original setup used adcp==3.2.0"), and on era references ("the 3.1.0-beta.3
era"), all of which are legitimately historical and must stay green. Only a
sentence that asserts what this project targets *now* is graded, and it is
graded against the resolved pin — never against a literal in this file.
"""

import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from scripts.audit.storyboard_spec import pinned_version
from tests.unit._architecture_helpers import REPO_ROOT, iter_git_tracked_files

# The two disease markers from #1417 (mirrors the task's acceptance grep
# `rc\.12|no GA 3\.1\.0`). Kept as separate strings so this guard file itself
# does not textually contain the joined pattern it scans for.
_STALE_MARKERS = re.compile(r"rc\.12|no GA 3\.1\.0")

# The dangling prose root (#1757). The trailing character class is the whole
# point: it requires the root to continue into a PATH, so a line that names the
# root to warn against it (next char is a quote or a space) does not match.
_DANGLING_PROSE_ROOT = re.compile(r"dist/docs/3\.1\.1/[A-Za-z0-9_-]")

_SCAN_ROOTS = ("tests", "docs", "src")
# Text extensions worth scanning; skips binaries/fixtures where a coincidental
# byte match is meaningless.
_TEXT_SUFFIXES = {".py", ".feature", ".md", ".yaml", ".yml", ".txt", ".rst"}

_THIS_FILE = Path(__file__).resolve()


def _scanned_files():
    for path in iter_git_tracked_files(REPO_ROOT):
        if path.resolve() == _THIS_FILE:
            continue  # this guard names the markers on purpose
        if path.suffix not in _TEXT_SUFFIXES:
            continue
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in _SCAN_ROOTS:
            yield path, rel


def _offending_lines(marker: re.Pattern[str]) -> list[str]:
    """Every scanned line matching *marker*, as ``path:lineno: text``.

    One implementation for both markers. A second copy that walked the files
    again is how the two diseases end up scanned by rules that quietly differ
    on which files they cover.
    """
    offenders = []
    for path, rel in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if marker.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    return offenders


def test_no_stale_rc_or_pre_ga_citations():
    offenders = _offending_lines(_STALE_MARKERS)
    assert not offenders, (
        "Stale pre-GA AdCP citations found (GA 3.1.0/3.1.1 are published). "
        "Refresh these to cite the GA storyboard:\n" + "\n".join(offenders)
    )


def test_no_citations_under_the_dangling_prose_root():
    offenders = _offending_lines(_DANGLING_PROSE_ROOT)
    assert not offenders, (
        "Citation(s) under a spec prose root that resolves at no tag. "
        "dist/docs/ stops at 3.1.0, so dist/docs/3.1.1/<path> cannot be read at "
        "v3.1.1. Prose for the pinned version lives in the repository-root docs/ "
        "tree at the tag — for example docs/protocol/get_adcp_capabilities.mdx. "
        "Verify the replacement with `git cat-file -e v3.1.1:<path>` before "
        "relying on it, and re-derive any line anchors against the new file "
        "rather than carrying the old ones over:\n" + "\n".join(offenders)
    )


def test_the_dangling_root_marker_still_matches_a_citation():
    """The marker must match a real citation, or its green means nothing.

    A citation guard that matches nothing passes for the same reason a broken
    one does. This pins the discriminator in BOTH directions against the exact
    lines that motivated it: the pre-fix BR-UC-010 citation form must match,
    and the two in-scan-root warning forms must not.
    """
    cited = "    # @source repo=adcp ref=v3.1.1 path=dist/docs/3.1.1/building/implementation/x.mdx (L23)"
    assert _DANGLING_PROSE_ROOT.search(cited), "marker no longer matches a known-bad citation"

    for warning in (
        "does not: `dist/docs/` stops at `3.1.0`, so `dist/docs/3.1.1/` resolves at no tag. Prose",
        "``v3.1.1:docs/building/by-layer/L1/security.mdx`` (there is no ``dist/docs/3.1.1/`` at",
    ):
        assert not _DANGLING_PROSE_ROOT.search(warning), f"marker reddens a warning form: {warning}"

    for resolving in (
        "``dist/docs/3.1.0/reference/url-canonicalization.mdx``",
        "``dist/docs/3.1.0-rc.15/building/by-layer/L1/security.mdx``",
        "# dist/docs/3.1.0-beta.3/ tree",
    ):
        assert not _DANGLING_PROSE_ROOT.search(resolving), f"marker reddens a resolving root: {resolving}"


# ---------------------------------------------------------------------------
# Current-tense pin claims must equal the actual pin
# ---------------------------------------------------------------------------

# A version token: 3.1.1, 6.6.0, 3.1.0-beta.3, …
_VERSION = r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-[A-Za-z0-9.]+)?"

# Claim forms. Each requires a present-tense connective binding THIS project to
# a version — "targets X", "via the adcp==X SDK", "(an) implementation of …
# (AdCP X)". Past-tense prose, release notes and era references carry none of
# these connectives and are therefore not claims. ``kind`` selects which pin the
# captured version is graded against.
_SPEC = "spec"
_SDK = "sdk"

_PIN_CLAIM_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "targets-adcp-spec",
        _SPEC,
        re.compile(rf"\btargets?\s+(?:the\s+)?AdCP(?:\s+spec)?(?:\s+version)?\s+({_VERSION})"),
    ),
    (
        "implementation-of-adcp",
        _SPEC,
        re.compile(rf"\bimplementation\s+of\s+[^.]{{0,120}}?\(\s*AdCP\s+({_VERSION})\s*\)"),
    ),
    (
        "via-the-adcp-sdk",
        _SDK,
        re.compile(rf"\bvia\s+the\s+adcp==({_VERSION})"),
    ),
)

# Markdown emphasis / code fencing / blockquote markers are replaced by a SPACE
# (never deleted) so match offsets still map to real line numbers, while
# "AdCP spec **3.1.1**" and "`adcp==6.6.0`" read as plain prose to the patterns.
_MARKUP_NOISE = str.maketrans({"*": " ", "`": " ", "_": " ", ">": " "})

_PIN_CLAIM_SUFFIXES = _TEXT_SUFFIXES | {".toml", ".mdc"}


class PinClaim(NamedTuple):
    """A present-tense assertion about which AdCP spec / SDK this project targets."""

    lineno: int
    pattern: str
    kind: str
    version: str
    line: str


def iter_pin_claims(text: str) -> Iterator[PinClaim]:
    """Yield every present-tense pin claim in *text*, with the line it sits on."""
    normalized = text.translate(_MARKUP_NOISE)
    lines = text.splitlines()
    for name, kind, pattern in _PIN_CLAIM_PATTERNS:
        for match in pattern.finditer(normalized):
            # Line of the VERSION token, not of the sentence start: README wraps
            # its claim across two lines and the version is on the second.
            lineno = normalized.count("\n", 0, match.start(1)) + 1
            source_line = lines[lineno - 1].strip() if lineno <= len(lines) else ""
            yield PinClaim(lineno, name, kind, match.group(1), source_line)


def pinned_spec_version() -> str:
    """The AdCP spec version this repo pins, read from docs/adcp-spec-version.md."""
    return pinned_version(REPO_ROOT)


def pinned_sdk_version() -> str:
    """The ``adcp==`` SDK version this repo pins, read from pyproject.toml."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = [dep for dep in pyproject["project"]["dependencies"] if dep.replace(" ", "").startswith("adcp==")]
    assert len(pins) == 1, f"expected exactly one adcp== pin in pyproject.toml, got {pins}"
    return pins[0].replace(" ", "").split("==", 1)[1]


def _expected_for(kind: str) -> str:
    return pinned_spec_version() if kind == _SPEC else pinned_sdk_version()


def _wrong_pin_claims(text: str) -> list[PinClaim]:
    """Claims in *text* whose version does not equal the pin they assert."""
    return [claim for claim in iter_pin_claims(text) if claim.version != _expected_for(claim.kind)]


def _pin_claim_files():
    for path in iter_git_tracked_files(REPO_ROOT):
        if path.resolve() == _THIS_FILE:
            continue  # this guard names the claim forms on purpose
        if path.is_symlink():
            continue  # e.g. AGENTS.md -> CLAUDE.md: same bytes, one fix site
        if path.suffix not in _PIN_CLAIM_SUFFIXES:
            continue
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            continue
        yield path, rel


def test_current_tense_pin_claims_match_the_actual_pin():
    """No file may assert a spec/SDK target that differs from the resolved pin."""
    expected_spec = pinned_spec_version()
    expected_sdk = pinned_sdk_version()

    claims_seen = 0
    offenders: list[str] = []
    for path, rel in _pin_claim_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for claim in iter_pin_claims(text):
            claims_seen += 1
            expected = _expected_for(claim.kind)
            if claim.version != expected:
                offenders.append(
                    f"{rel}:{claim.lineno}: claims {claim.kind} {claim.version}, pin is {expected} "
                    f"[{claim.pattern}] {claim.line}"
                )

    assert claims_seen, "non-vacuity: no pin claims matched at all — the claim patterns have rotted"
    assert not offenders, (
        f"Files assert an AdCP target that is not the pin (spec {expected_spec}, adcp=={expected_sdk}).\n"
        "The pin has two sources — docs/adcp-spec-version.md and pyproject.toml; every other\n"
        "file repeats them and must be corrected, not re-pinned:\n" + "\n".join(offenders)
    )


def _not_the_pin(version: str) -> str:
    """A version that is definitely not *version* — derived, never a literal pin."""
    return f"{version}-notthepin.1"


def test_detector_flags_a_current_tense_claim_with_the_wrong_version():
    """Positive meta-case: each claim form is caught when it names a non-pin version."""
    wrong_spec = _not_the_pin(pinned_spec_version())
    wrong_sdk = _not_the_pin(pinned_sdk_version())
    text = "\n".join(
        [
            f"This project targets AdCP spec **{wrong_spec}** via the `adcp=={wrong_sdk}` Python SDK.",
            "> A pre-1.0 implementation of a pre-release protocol",
            f"> (AdCP {wrong_spec}), under active development.",
        ]
    )

    offenders = _wrong_pin_claims(text)

    assert [(c.lineno, c.pattern, c.version) for c in offenders] == [
        (1, "targets-adcp-spec", wrong_spec),
        (3, "implementation-of-adcp", wrong_spec),
        (1, "via-the-adcp-sdk", wrong_sdk),
    ]


def test_detector_ignores_historical_references_and_correct_claims():
    """Negative meta-case: past-tense history stays green; a correct claim is seen but not flagged."""
    spec = pinned_spec_version()
    sdk = pinned_sdk_version()
    text = "\n".join(
        [
            # Historical / dated records — no present-tense claim connective.
            "The original setup used `mirrors-mypy` with `additional_dependencies: [adcp==3.2.0]`.",
            "- **Spec target** — `adcp==5.7.0`, AdCP spec `3.1.0-beta.3`, pinned in `pyproject.toml`.",
            "adcontextprotocol/adcp@04f59d2d5 / tag v3.1-04f59d2d5, the 3.1.0-beta.3 era",
            "Pins `adcp==5.7.0` (was 4.3.0); `EXPECTED_SPEC_VERSION` -> `3.1.0-beta.3`.",
            # Correct present-tense claims — detected, and correct.
            f"Prebid Sales Agent targets **AdCP spec version {spec}**.",
            f"This project targets AdCP spec **{spec}** via the `adcp=={sdk}` Python SDK.",
        ]
    )

    claims = list(iter_pin_claims(text))

    assert [c.lineno for c in claims] == [5, 6, 6], (
        f"detector must see the two present-tense claims (and only those): {[(c.lineno, c.pattern) for c in claims]}"
    )
    assert _wrong_pin_claims(text) == []
