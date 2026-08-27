"""CI guard: the vendored request-signing conformance vectors are pinned and intact.

#1291 B3 (``salesagent-z6nr.14``), direct sibling of
``tests/unit/test_adcp_spec_version.py``. That guard pins the ``adcp`` SDK to a spec
version; this one pins the GRADED DATA to the same version, so bumping the SDK
without re-vendoring the vectors is a loud failure rather than a suite that keeps
grading against last release's conformance set.

What each assertion buys:

* ``spec_version`` — the tie to the repo's ``adcp`` pin. THE trap, measured: the
  vendored ``canonicalization.json`` self-reports ``"version": "3.0"``. Only
  ``MANIFEST["spec_version"]`` may ever be compared to
  ``adcp.get_adcp_spec_version()``; a vector-internal version field is not a pin.
* per-file sha256 + exact file set — a local edit to a vector is editing the
  evidence, and a re-vendor that silently drops files shrinks the graded set.
* explicit counts — the file set could be intact while a bucket lost members.
* the L1(a) set — the SET of files carrying ``expected_signature_base``, spelled
  out. If an upstream re-vendor adds or removes the field, L1 silently changes
  size; here it fails by name.

Refresh command (also listed in ``docs/adcp-spec-version.md``'s bump list)::

    uv run python -m tests.fixtures.adcp_schemas_pinned._refresh
"""

from __future__ import annotations

import hashlib

import adcp

from tests.helpers.adcp_pin import SPEC_REV
from tests.helpers.signing_vectors import (
    VECTORS_DIR,
    load_canonicalization_cases,
    load_signing_keys,
    load_signing_vectors,
    load_vector_manifest,
    vectors_with_expected_signature_base,
)

#: The 13 vector files that ship ``expected_signature_base`` at v3.1.1, measured.
#:
#: The B3 design said 12 ("11 positives + negative/015"). That is WRONG: it missed
#: ``negative/010-content-digest-mismatch``, which ships the field too (it is graded
#: at step 11, past crypto, so its base is spec-authored data like 015's). The
#: correction is recorded here rather than in prose because this list is what the
#: L1(a) parametrization consumes.
#:
#: ``positive/004-multiple-signature-labels`` does NOT ship it — the vector README's
#: "present on positive vectors" claim is stale. 004's canonicalization is graded
#: only at L2 and transitively by ``positive/001`` (byte-identical URL).
EXPECTED_L1_SET = frozenset(
    {
        "negative/010-content-digest-mismatch",
        "negative/015-signature-invalid",
        "positive/001-basic-post",
        "positive/002-post-with-content-digest",
        "positive/003-es256-post",
        "positive/005-default-port-stripped",
        "positive/006-dot-segment-path",
        "positive/007-query-byte-preserved",
        "positive/008-percent-encoded-path",
        "positive/009-percent-encoded-unreserved-decoded",
        "positive/010-percent-encoded-slash-preserved",
        "positive/011-ipv6-authority",
        "positive/012-ipv6-authority-default-port-stripped",
    }
)

_REFRESH_HINT = "Re-run: uv run python -m tests.fixtures.adcp_schemas_pinned._refresh"


def test_manifest_spec_version_matches_the_adcp_pin() -> None:
    """The vendored vectors target the same AdCP spec version as the SDK pin."""
    manifest = load_vector_manifest()
    assert manifest["spec_version"] == adcp.get_adcp_spec_version(), (
        f"Vendored conformance vectors target spec {manifest['spec_version']}, but the "
        f"adcp SDK targets {adcp.get_adcp_spec_version()}. {_REFRESH_HINT}"
    )
    assert manifest["source_tag"] == SPEC_REV


def test_vendored_file_set_matches_the_manifest() -> None:
    """No file added, dropped or renamed under the vendored tree."""
    manifest = load_vector_manifest()
    on_disk = {
        str(path.relative_to(VECTORS_DIR))
        for path in VECTORS_DIR.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json"
    }
    assert on_disk == set(manifest["files"]), (
        f"Vendored vector file set drifted from MANIFEST.json.\n"
        f"  only on disk: {sorted(on_disk - set(manifest['files']))}\n"
        f"  only in manifest: {sorted(set(manifest['files']) - on_disk)}\n{_REFRESH_HINT}"
    )


def test_every_vendored_file_hashes_to_the_manifest() -> None:
    """A local edit to a vector is editing the evidence — fail on it."""
    manifest = load_vector_manifest()
    drifted = {}
    for relpath, expected_sha in sorted(manifest["files"].items()):
        actual = hashlib.sha256((VECTORS_DIR / relpath).read_bytes()).hexdigest()
        if actual != expected_sha:
            drifted[relpath] = (expected_sha, actual)
    assert not drifted, f"Vendored conformance vectors were modified locally: {drifted}. {_REFRESH_HINT}"


def test_vector_counts_are_exactly_the_graded_set() -> None:
    """12 positive + 28 negative request vectors, 31 canonicalization cases."""
    vectors = load_signing_vectors()
    positives = sorted(v for v in vectors if v.startswith("positive/"))
    negatives = sorted(v for v in vectors if v.startswith("negative/"))
    assert len(positives) == 12, f"expected 12 positive vectors, found {len(positives)}: {positives}"
    assert len(negatives) == 28, f"expected 28 negative vectors, found {len(negatives)}: {negatives}"
    assert len(load_canonicalization_cases()) == 31


def test_l1_signature_base_set_is_pinned_by_name() -> None:
    """Exactly these 13 files carry ``expected_signature_base`` — the L1(a) set."""
    actual = frozenset(vectors_with_expected_signature_base())
    assert actual == EXPECTED_L1_SET, (
        "The set of vectors shipping expected_signature_base changed, so L1(a) now grades a "
        "different set.\n"
        f"  newly carrying it: {sorted(actual - EXPECTED_L1_SET)}\n"
        f"  no longer carrying it: {sorted(EXPECTED_L1_SET - actual)}"
    )


def test_canonicalization_internal_version_is_not_the_spec_pin() -> None:
    """The measured trap: ``canonicalization.json`` self-reports ``"version": "3.0"``.

    Pinned as a fact so a future reader does not "helpfully" wire that field into
    the spec-version assertion above and make the guard pass for the wrong reason.
    """
    import json

    raw = json.loads((VECTORS_DIR / "canonicalization.json").read_text())
    assert raw["version"] == "3.0" != adcp.get_adcp_spec_version()


def test_runner_keys_cover_every_keyid_the_vectors_reference() -> None:
    """Every ``jwks_ref`` keyid resolves in ``keys.json``, offline."""
    available = {key["kid"] for key in load_signing_keys()["keys"]}
    referenced = {kid for vector in load_signing_vectors().values() for kid in vector.get("jwks_ref", [])}
    unresolvable = referenced - available - {"not-a-real-kid"}
    assert not unresolvable, f"vectors reference keyids absent from keys.json: {sorted(unresolvable)}"
