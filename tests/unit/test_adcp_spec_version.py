"""CI guard: assert the adcp SDK pin targets the expected AdCP spec version.

Also guards the PROSE around the pin: README.md, CLAUDE.md and
docs/adcp-spec-version.md each advertise the spec/SDK version to
contributors, and all three drifted to a stale version once already while
the constant-only guard stayed green (GH #1728 fallout). The prose checks
below are static (local files, no network, no DB), so they belong here
rather than in a review checklist.
"""

import re
from pathlib import Path

import adcp

EXPECTED_SPEC_VERSION = "3.1.1"

# The commit tests/fixtures/adcp_schemas_pinned/_refresh.py vendors offline schema
# fixtures from (tag v3.1.1 on adcontextprotocol/adcp). Update this in the SAME
# change as EXPECTED_SPEC_VERSION and _refresh.py's own PINNED_SHA — see
# docs/adcp-spec-version.md "Bumping the spec version" step 7. This pin previously
# targeted "AdCP 3.1" (04f59d2d5) while EXPECTED_SPEC_VERSION had already moved to
# 3.1.1, silently blinding scripts/verify_feature_error_codes.py's --casing-only
# gate to every code added between 3.1 and 3.1.1.
_EXPECTED_PINNED_SHA = "467fd93d77112baf9e094e18980119edcd3a4d07"

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Files whose prose claims the pinned versions. docs/adcp-spec-version.md also
# carries deliberately-HISTORICAL lines that must keep naming old versions:
# the SDK-to-spec mapping table rows and dist/compliance/<old>/ storyboard
# paths. Those line shapes are excluded from the drift scan below.
_PROSE_FILES = ("README.md", "CLAUDE.md", "docs/adcp-spec-version.md")


def _pyproject_sdk_pin() -> str:
    """The adcp==X.Y.Z pin as written in pyproject.toml dependencies."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text()
    match = re.search(r'"adcp==(\d+\.\d+\.\d+)"', pyproject)
    assert match, "pyproject.toml no longer carries an exact adcp==X.Y.Z pin"
    return match.group(1)


def _is_historical_line(line: str) -> bool:
    """Lines allowed to name OLD versions: mapping-table rows + storyboard paths.

    Table rows (leading ``|``) cover the SDK-to-spec mapping in
    docs/adcp-spec-version.md; ``dist/compliance/`` covers pinned storyboard
    paths of earlier spec versions cited as history.
    """
    stripped = line.lstrip()
    return stripped.startswith("|") or "dist/compliance/" in line


def _refresh_pinned_sha() -> str:
    """The PINNED_SHA constant as written in _refresh.py."""
    src = (_REPO_ROOT / "tests" / "fixtures" / "adcp_schemas_pinned" / "_refresh.py").read_text()
    match = re.search(r'PINNED_SHA = "([0-9a-f]{40})"', src)
    assert match, '_refresh.py no longer carries a PINNED_SHA = "<40-char sha>" constant'
    return match.group(1)


def _alignment_test_pinned_sha() -> str:
    """The informational _PINNED_SHA mirror in test_pydantic_schema_alignment.py.

    A second hand-maintained copy of the same commit, used only in an error
    message there — guarded too so it can't drift silently.
    """
    src = (_REPO_ROOT / "tests" / "unit" / "test_pydantic_schema_alignment.py").read_text()
    match = re.search(r'_PINNED_SHA = "([0-9a-f]{40})"', src)
    assert match, 'test_pydantic_schema_alignment.py no longer carries a _PINNED_SHA = "<40-char sha>" constant'
    return match.group(1)


def test_vendored_schema_pin_matches_spec_version() -> None:
    """The offline-vendored schema fixture must track EXPECTED_SPEC_VERSION.

    tests/fixtures/adcp_schemas_pinned/_refresh.py has its OWN commit pin
    (PINNED_SHA), separate from EXPECTED_SPEC_VERSION above, because the
    fixtures it vendors are read offline (CI has no ~/projects/adcp clone or network
    access to verify a tag->commit mapping live). That pin drifted silently for a
    full spec cycle (3.1 -> 3.1.1) with nothing to catch it. This is a static
    cross-check between two hand-maintained constants, not a live git/network
    lookup — bump BOTH together (docs/adcp-spec-version.md step 7) and this stays
    green; bump only one and it fails.
    """
    assert _refresh_pinned_sha() == _EXPECTED_PINNED_SHA, (
        f"_refresh.py's PINNED_SHA ({_refresh_pinned_sha()}) does not match this test's "
        f"_EXPECTED_PINNED_SHA ({_EXPECTED_PINNED_SHA}). If you just bumped EXPECTED_SPEC_VERSION "
        f"to {EXPECTED_SPEC_VERSION}, also update _refresh.py's PINNED_SHA to the new tag's commit, "
        "re-run tests/fixtures/adcp_schemas_pinned/_refresh.py to re-vendor the fixtures, and update "
        "_EXPECTED_PINNED_SHA here to match. See docs/adcp-spec-version.md 'Bumping the spec version'."
    )
    assert _alignment_test_pinned_sha() == _EXPECTED_PINNED_SHA, (
        f"test_pydantic_schema_alignment.py's _PINNED_SHA ({_alignment_test_pinned_sha()}) does not "
        f"match this test's _EXPECTED_PINNED_SHA ({_EXPECTED_PINNED_SHA}). Update it in the same change."
    )


def test_adcp_spec_version_matches_pin() -> None:
    """Verify SDK pin targets the spec version this codebase expects.

    Failure here means the adcp Python SDK pin in pyproject.toml has shifted
    to a version that targets a different AdCP spec version. Either revert
    the pin or follow docs/adcp-spec-version.md to update
    EXPECTED_SPEC_VERSION and the related references it lists.
    """
    actual = adcp.get_adcp_spec_version()
    assert actual == EXPECTED_SPEC_VERSION, (
        f"adcp SDK targets spec {actual}, but this codebase expects "
        f"{EXPECTED_SPEC_VERSION}. See docs/adcp-spec-version.md for "
        f"reconciliation steps."
    )


def _prose_claims(pin: str) -> dict[str, tuple[str, ...]]:
    """Required substrings per prose file, keyed by the same names as ``_PROSE_FILES``.

    Single table driving both ``test_prose_claims_current_pin`` (presence) and
    ``test_prose_names_no_stale_versions`` (drift, via ``_PROSE_FILES`` == this
    table's keys, checked by test_meta_prose_claims_keys_match_prose_files) — a
    file added to one check is added to both, never silently to just one.
    """
    return {
        "README.md": (
            f"AdCP spec version {EXPECTED_SPEC_VERSION}",
            f"(AdCP {EXPECTED_SPEC_VERSION})",
            f"adcp=={pin}",
        ),
        "CLAUDE.md": (
            f"AdCP spec **{EXPECTED_SPEC_VERSION}**",
            f"adcp=={pin}",
        ),
        "docs/adcp-spec-version.md": (f"The SDK **pin** ({EXPECTED_SPEC_VERSION})",),
    }


def test_meta_prose_claims_keys_match_prose_files() -> None:
    """``_prose_claims``' keys must exactly match ``_PROSE_FILES``.

    Catches a file added to one table but not the other — the asymmetry that
    previously let a fourth prose file get drift-scanning with silently no
    presence-checking (or vice versa).
    """
    assert set(_prose_claims("0.0.0")) == set(_PROSE_FILES)


def test_prose_claims_current_pin() -> None:
    """Each prose file must advertise the CURRENT spec + SDK versions.

    Presence checks — deleting the claim instead of updating it fails too.
    Phrasings are the ones each file actually uses; if a file legitimately
    rewords its claim, update the expectation in ``_prose_claims``.
    """
    pin = _pyproject_sdk_pin()
    claims = _prose_claims(pin)
    for rel in _PROSE_FILES:
        text = (_REPO_ROOT / rel).read_text()
        for claim in claims[rel]:
            assert claim in text, f"{rel} must contain {claim!r}"


def test_prose_names_no_stale_versions() -> None:
    """No non-historical prose line may claim a DIFFERENT spec/SDK version.

    Scans every AdCP-context spec-version token (``AdCP [spec [version]] X``)
    and every ``adcp==X`` token in the three prose files. Historical lines —
    mapping-table rows and dist/compliance/<old>/ storyboard paths — are the
    only place old versions may appear. This is the check whose absence let
    README/docs advertise 3.1.0-beta.3 for a full spec cycle while the
    constant-only guard stayed green.
    """
    pin = _pyproject_sdk_pin()
    spec_token = re.compile(r"AdCP(?: spec(?: version)?)?[ *(]+(\d+\.\d+\.\d+(?:-beta\.\d+)?)")
    sdk_token = re.compile(r"adcp==(\d+\.\d+\.\d+)")

    offences: list[str] = []
    for rel in _PROSE_FILES:
        for lineno, line in enumerate((_REPO_ROOT / rel).read_text().splitlines(), 1):
            if _is_historical_line(line):
                continue
            for found in spec_token.findall(line):
                if found != EXPECTED_SPEC_VERSION:
                    offences.append(f"{rel}:{lineno} names spec {found}: {line.strip()!r}")
            for found in sdk_token.findall(line):
                if found != pin:
                    offences.append(f"{rel}:{lineno} names SDK {found}: {line.strip()!r}")

    assert not offences, (
        "Stale spec/SDK versions in prose (update them, or move the line into a "
        "historical shape — mapping-table row / dist/compliance path):\n" + "\n".join(offences)
    )
