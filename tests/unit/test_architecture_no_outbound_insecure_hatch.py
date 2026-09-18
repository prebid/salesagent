"""Structural guard: ADCP_OUTBOUND_ALLOW_INSECURE never comes back.

GH #1757 deleted the scheme escape hatch entirely — both production
read sites (``src/core/security/outbound_http.py``'s ``_require_tls``,
``src/core/webhook_validator.py``'s ``_require_https``) now require https
unconditionally, and every test/infra site that used to set the flag either
migrated to a real TLS-fronted origin (GH #1757's primitive) or had its
assertion rewritten. A future change re-adding the env var to production code
or to the compose/host-script/tox.ini config would silently reintroduce the
plaintext-http escape hatch this ticket closed — this guard catches that at
``make quality`` time.

``.github/workflows/ci.yml``'s "creative" integration matrix group was the
last holdout (GH #1802). It no longer runs on the HOST at all: it runs
IN-NETWORK via ``./run_all_tests.sh`` against ``docker-compose.e2e.yml`` and
reaches the reference agent by service name
(``CREATIVE_AGENT_URL=http://creative-agent:8080/...``), so there is no
inline hatch and no plaintext ``localhost:9999`` URL left in ``ci.yml`` —
and so the flag has no remaining legitimate use anywhere in the repo.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit._architecture_helpers import assert_scanned_paths_exist

_REPO_ROOT = Path(__file__).resolve().parents[2]

_FLAG = "ADCP_OUTBOUND_ALLOW_INSECURE"

# Files that must NEVER reference the flag again — the two production read
# sites, plus every infra file that used to declare it.
_MUST_NOT_CONTAIN = (
    "src/core/security/outbound_http.py",
    "src/core/webhook_validator.py",
    "docker-compose.e2e.yml",
    "tox.ini",
    "run_all_tests_host.sh",
    ".github/workflows/ci.yml",
)


def test_every_file_this_guard_scans_still_exists() -> None:
    """The scan set is a subject too, and it fails the same silent way.

    These are file PATHS, not symbols. Rename any of them and this guard keeps
    passing -- it reads nothing, so it finds nothing, which is exactly what
    compliance looks like. Measured before this was added: mutating the tuple to
    garbage changed no result anywhere.
    """
    assert_scanned_paths_exist(
        _MUST_NOT_CONTAIN,
        why=(
            "This guard asserts the insecure-egress flag never returns to these files. A path it "
            "cannot read is a file it cannot check, and the flag could come back there unnoticed."
        ),
    )


def find_flag_reintroductions(repo_root: Path, must_not_contain: tuple[str, ...]) -> list[str]:
    """Return the relpaths (of ``must_not_contain``) that DECLARE the flag again.

    Comment lines (stripped content starting with ``#``) are skipped — this
    guard bans reintroducing the actual escape hatch (a YAML key, a shell
    export, a Python read), not historical prose explaining that it was
    deleted (several files, deliberately, still say so).
    """
    hits = []
    for relpath in must_not_contain:
        path = repo_root / relpath
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if line.strip().startswith("#"):
                continue
            if _FLAG in line:
                hits.append(relpath)
                break
    return hits


def test_flag_does_not_reappear_in_production_or_infra_files() -> None:
    """None of the pinned production/infra files reference the deleted flag."""
    hits = find_flag_reintroductions(_REPO_ROOT, _MUST_NOT_CONTAIN)
    assert hits == [], (
        f"ADCP_OUTBOUND_ALLOW_INSECURE reappeared in {hits} — GH #1757 deleted this "
        "escape hatch; re-adding it anywhere in these files silently reopens a plaintext-http "
        "bypass. If a file genuinely needs it again, that is a scope decision, not a silent revert."
    )


def test_detector_catches_a_reintroduced_flag() -> None:
    """The live detector reports a file that has the flag reintroduced, synthetically."""
    synthetic_dir_marker = "src/core/security/outbound_http.py"
    # Build a fake root with one file containing the flag, to prove the
    # detector fires without touching the real tree.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / synthetic_dir_marker
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'if _env_flag("{_FLAG}"):\n    return\n')
        hits = find_flag_reintroductions(tmp_path, (synthetic_dir_marker,))
    assert hits == [synthetic_dir_marker]


def test_detector_ignores_a_file_that_never_had_the_flag() -> None:
    """The live detector does not false-positive on an unrelated file."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        marker = "docker-compose.e2e.yml"
        (tmp_path / marker).write_text("ADCP_OUTBOUND_ALLOW_PRIVATE: 'true'\n")
        hits = find_flag_reintroductions(tmp_path, (marker,))
    assert hits == []


# ---------------------------------------------------------------------------
# The sibling hatch: ADCP_OUTBOUND_ALLOW_PRIVATE, pinned rather than deleted
# ---------------------------------------------------------------------------
#
# ADCP_OUTBOUND_ALLOW_INSECURE was DELETED, and the detector above asks the
# deletion question. Its sibling was not deleted and should not be: the e2e
# stack's origins are on a private bridge network by construction, so the flag
# has legitimate, load-bearing uses. That asymmetry is the finding (#1802
# review, Chris F1): one hatch got deletion plus a guard, the other got a
# comment at its read site (`outbound_http.py`) and nothing else.
#
# So this half asks the PIN question instead: which files may name the flag at
# all. A hatch that can disable the address gate is a production concern the
# moment a file outside the pinned set can set it -- `docker-compose.yml`, the
# dev/production stack, is one `ADCP_OUTBOUND_ALLOW_PRIVATE: "true"` away from
# opening it today, and nothing in the tree would notice.

_ALLOW_PRIVATE_FLAG = "ADCP_OUTBOUND_ALLOW_PRIVATE"

# The pinned allowed set, with the reason each entry is here.
#
# GRADUATION DISCIPLINE, and it binds: entries leave this set ONE AT A TIME, in
# the change that removes the flag from that file, with the reason in the commit
# message -- never in bulk, and never as a side effect of a refactor. An entry
# is ADDED only as a recorded scope decision, stated at the entry, in the same
# change. This is the discipline EXPECTED_XFAIL_ROUTES and
# EXPECTED_E2E_REST_PARAMETRIZE_GATE already carry ("update this pin IN THE SAME
# CHANGE and say why in the commit"); the flag is a security hatch, so it earns
# the same treatment rather than a looser one.
_ALLOWED_ALLOW_PRIVATE_SITES: frozenset[str] = frozenset(
    {
        # The seam no longer READS the variable, so it is no longer a
        # declaration site: it consults ``limits.adcp_outbound_allow_private``
        # on the settings object, and only the settings loader touches the
        # environment ("Environment read once"). The comment naming the
        # variable at outbound_http.py:182 is prose, which this detector skips
        # on purpose -- explaining the hatch is not the hatch.
        # Every origin in the e2e compose stack is on a private bridge network
        # by construction, so the address gate would refuse all of them. TWO
        # immunities survive the open hatch, and the BDD egress scenarios pin
        # it open to grade them live rather than mocking a verdict:
        # cloud-metadata addresses (the SDK's own check, ahead of the flag it
        # reads) and the six #974 supplement ranges (this repo's own predicate,
        # which sits outside the hatch entirely -- GH #1802). The
        # second is the one this repo can regress on its own, and a regression
        # there does NOT surface as a fast refusal: the address is accepted and
        # dialled, so the scenario fails on a connect timeout instead.
        "docker-compose.e2e.yml",
        # pass_env, so the suites that run against that stack inherit it.
        "tox.ini",
        # The host runner stands up the same stack outside compose.
        "run_all_tests_host.sh",
        # NOTE: .github/workflows/ci.yml was removed from this pin at the #1802 merge.
        # Its "creative" integration group used to run on the host and set the flag
        # inline; the group now runs IN-NETWORK via ./run_all_tests.sh, so the compose
        # file and run_all_tests_host.sh are the only declaration sites left. Dropping a
        # site that genuinely stopped declaring is a shrink, not a relaxation.
    }
)

# Where a DEPLOYMENT can acquire an environment variable from. `tests/**` is
# deliberately absent: a test naming the constant is not a deployment setting
# it, and banning the name there would ban this guard's own vocabulary and the
# single-spelling helper (`tests/helpers/egress_hatches.py`) that keeps the
# test side from re-spelling it.
_ALLOW_PRIVATE_SCAN_GLOBS: tuple[str, ...] = (
    "src/**/*.py",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "Dockerfile*",
    "app.json",
    "Makefile",
    "tox.ini",
    "run_all_tests*.sh",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "config/**/*",
    "scripts/**/*",
)


def find_allow_private_declaring_files(repo_root: Path) -> set[str]:
    """Return the relpaths, across the scanned surface, that DECLARE the flag.

    Comment lines are skipped for the same reason the sibling detector skips
    them: prose explaining the hatch is not the hatch. What counts is a line
    that could put the variable into a process environment or read it out of
    one -- a YAML key, a shell export, a workflow env entry, a Python read.
    """
    hits: set[str] = set()
    for glob in _ALLOW_PRIVATE_SCAN_GLOBS:
        for path in sorted(repo_root.glob(glob)):
            if not path.is_file():
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for line in text.splitlines():
                if line.strip().startswith("#"):
                    continue
                if _ALLOW_PRIVATE_FLAG in line:
                    hits.add(str(path.relative_to(repo_root)))
                    break
    return hits


def test_allow_private_sites_match_the_pin_exactly() -> None:
    """The files that may name ADCP_OUTBOUND_ALLOW_PRIVATE are exactly the pin.

    Set IDENTITY, not a count: an add-plus-drop must fail, because a count that
    stays at three while `docker-compose.yml` replaces `tox.ini` is precisely the
    change this guard exists to refuse.
    """
    found = find_allow_private_declaring_files(_REPO_ROOT)
    unpinned = found - _ALLOWED_ALLOW_PRIVATE_SITES
    missing = _ALLOWED_ALLOW_PRIVATE_SITES - found
    assert found == _ALLOWED_ALLOW_PRIVATE_SITES, (
        f"{_ALLOW_PRIVATE_FLAG} declaration sites drifted from the pin.\n"
        f"  declared but NOT pinned: {sorted(unpinned)}\n"
        f"  pinned but no longer declaring: {sorted(missing)}\n"
        "This flag can disable the outbound address gate. A new site is a scope "
        "decision recorded at the entry, not a silent addition; a site that no "
        "longer needs it leaves the pin in the same change that removes it."
    )


def test_allow_private_detector_catches_an_unpinned_site() -> None:
    """The live detector reports a file outside the pin, synthetically.

    `docker-compose.yml` is the concrete case: the dev/production stack is one
    line away from opening the hatch, and before this guard nothing noticed.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "docker-compose.yml").write_text(f'      {_ALLOW_PRIVATE_FLAG}: "true"\n')
        # A Dockerfile ENV is the same hatch by another route, and the first
        # version of this scan missed it entirely -- measured: appending
        # `ENV ADCP_OUTBOUND_ALLOW_PRIVATE=true` to ./Dockerfile left all eight
        # tests in this file green.
        (tmp_path / "Dockerfile").write_text(f"ENV {_ALLOW_PRIVATE_FLAG}=true\n")
        found = find_allow_private_declaring_files(tmp_path)
    assert found == {"docker-compose.yml", "Dockerfile"}
    assert found - _ALLOWED_ALLOW_PRIVATE_SITES == {"docker-compose.yml", "Dockerfile"}


def test_allow_private_detector_ignores_prose_and_unrelated_files() -> None:
    """The live detector does not fire on a comment, or on a file without the flag."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "docker-compose.yml").write_text(f"      # {_ALLOW_PRIVATE_FLAG} was removed here\n")
        (tmp_path / "tox.ini").write_text("[tox]\nenvlist = unit\n")
        found = find_allow_private_declaring_files(tmp_path)
    assert found == set()


def test_every_pinned_allow_private_site_still_exists() -> None:
    """The pin is a subject too, and it fails the same silent way.

    A renamed file drops silently out of the scan, and the identity test above
    would report it as "pinned but no longer declaring" -- which reads like a
    cleanup rather than a broken guard. Assert the paths resolve.
    """
    assert_scanned_paths_exist(
        tuple(sorted(_ALLOWED_ALLOW_PRIVATE_SITES)),
        why=(
            "This guard pins which files may name the private-address hatch. A path it "
            "cannot read is a file it cannot check."
        ),
    )
