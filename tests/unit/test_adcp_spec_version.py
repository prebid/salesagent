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


def test_prose_claims_current_pin() -> None:
    """Each prose file must advertise the CURRENT spec + SDK versions.

    Presence checks — deleting the claim instead of updating it fails too.
    Phrasings are the ones each file actually uses; if a file legitimately
    rewords its claim, update the expectation here in the same change.
    """
    pin = _pyproject_sdk_pin()
    readme = (_REPO_ROOT / "README.md").read_text()
    claude_md = (_REPO_ROOT / "CLAUDE.md").read_text()
    spec_doc = (_REPO_ROOT / "docs/adcp-spec-version.md").read_text()

    assert f"AdCP spec version {EXPECTED_SPEC_VERSION}" in readme, (
        f"README.md 'AdCP Compatibility' must name spec {EXPECTED_SPEC_VERSION}"
    )
    assert f"(AdCP {EXPECTED_SPEC_VERSION})" in readme, f"README.md status note must name AdCP {EXPECTED_SPEC_VERSION}"
    assert f"adcp=={pin}" in readme, f"README.md must name the SDK pin adcp=={pin}"
    assert f"AdCP spec **{EXPECTED_SPEC_VERSION}**" in claude_md, (
        f"CLAUDE.md 'AdCP Spec Version' must name spec {EXPECTED_SPEC_VERSION}"
    )
    assert f"adcp=={pin}" in claude_md, f"CLAUDE.md must name the SDK pin adcp=={pin}"
    assert f"The SDK **pin** ({EXPECTED_SPEC_VERSION})" in spec_doc, (
        f"docs/adcp-spec-version.md must name the pin {EXPECTED_SPEC_VERSION}"
    )


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
