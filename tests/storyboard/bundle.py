"""The pinned AdCP compliance bundle, put on disk from wherever it can be found.

The grading suite (``test_storyboard_conformance.py``) and the storyboard tenant seeder
(``scripts/setup/seed_storyboard_tenant.py``, which reads the test kits) both need the
tree, and the seeder runs first, in tox's ``commands_pre``. One resolver serves both, so a
fresh tree is not ready for one and refused by the other.
"""

from __future__ import annotations

import hashlib
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

from scripts.audit import storyboard_spec

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_DIR = Path(__file__).parent / "runner"

# The pinned bundle is a release asset of the spec repo, not something this repo vendors.
_BUNDLE_REPO = "adcontextprotocol/adcp"
_BUNDLE_URL = "https://github.com/{repo}/releases/download/v{version}/{asset}"
_BUNDLE_FETCH_TIMEOUT = 120


def materialize_bundle() -> str | None:
    """Put the pinned compliance tree on disk, fetching the release asset if needed.

    THE GRADING SUITE RESOLVES ITS OWN BUNDLE, rather than requiring that a separate step
    downloaded and extracted the tree first. ``adcp_home()`` looks for
    ``tests/storyboard/runner/adcp-<version>/``, which is gitignored, and otherwise falls
    through to ``~/projects/adcp`` -- one maintainer's personal clone. An environment that
    has not run ``.github/actions/_adcp-bundle`` therefore resolves to a path that has never
    existed, every check de-collects, and the job exits 0 having graded nothing.

    Three sources, first hit wins, so every environment lands somewhere:

    1. The extracted tree. Nothing to do.
    2. A tarball already beside the runner -- what the CI action leaves behind, and what a
       second run in the same container reuses.
    3. The pinned release asset, over plain https. The version comes from the installed SDK,
       so the asset cannot disagree with the code under audit, and the archive is public: no
       ``gh``, no token, no environment variable pointing anywhere.

    The checksum is verified BEFORE extracting. A corrupt or truncated archive that unpacks
    far enough to look like a tree would otherwise grade a buyer contract against whatever it
    contained.

    Returns a reason string when the tree cannot be produced, or None on success. Callers
    treat that reason as a FAILURE, never a skip.
    """
    version = storyboard_spec.pinned_version(_REPO_ROOT)
    target = _RUNNER_DIR / f"adcp-{version}"
    if target.is_dir():
        return None

    archive = _RUNNER_DIR / f"{version}.tgz"
    checksum = _RUNNER_DIR / f"{version}.tgz.sha256"
    if not archive.is_file() or not checksum.is_file():
        fetch_failure = _fetch_bundle(version, archive, checksum)
        if fetch_failure is not None:
            return fetch_failure

    expected = checksum.read_text(encoding="utf-8").split()[0]
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        return f"bundle checksum mismatch for {archive}: expected {expected}, got {digest}"

    with tarfile.open(archive, "r:gz") as tar:
        _safe_extract(tar, _RUNNER_DIR)
    if not target.is_dir():
        return f"bundle extracted, but {target} is not a directory"
    return None


def _fetch_bundle(version: str, archive: Path, checksum: Path) -> str | None:
    """Download the pinned release asset and its checksum. Returns a reason on failure."""
    _RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    for path, asset in ((archive, archive.name), (checksum, checksum.name)):
        url = _BUNDLE_URL.format(repo=_BUNDLE_REPO, version=version, asset=asset)
        try:
            with urllib.request.urlopen(url, timeout=_BUNDLE_FETCH_TIMEOUT) as response:  # noqa: S310 - fixed https URL
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return (
                f"could not fetch {url}: {exc}. Run .github/actions/_adcp-bundle's two "
                f"commands, or place {archive.name} beside the runner"
            )
        path.write_bytes(body)
    return None


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract *tar* under *dest*, refusing any member that escapes it.

    The archive is a published artifact rather than buyer input, so this is not a defence
    against an attacker. It keeps a malformed archive from scattering files across the repo,
    which is the failure that is hard to diagnose afterwards.
    """
    root = dest.resolve()
    for member in tar.getmembers():
        if (root / member.name).resolve().is_relative_to(root):
            continue
        raise RuntimeError(f"refusing tar member outside {root}: {member.name}")
    tar.extractall(dest)  # noqa: S202 - every member checked above
