#!/usr/bin/env python3
"""Refresh the pinned AdCP artifacts vendored under ``tests/fixtures/``.

Three root sets, three DIFFERENT reasons to exist. Everything else that used to
be vendored here is gone: the general schema-SHAPE closure now comes from the
installed adcp SDK's own tree (see below), so there is exactly ONE upstream pin
for it — pyproject's ``adcp`` version.

1. ``enums/error-code.json`` — the SHA-pinned enumMetadata survivor
-------------------------------------------------------------------
Source of truth: adcontextprotocol/adcp @ commit
    467fd93d77112baf9e094e18980119edcd3a4d07  (tag v3.1.1)

This is an INTENTIONAL, frozen reference point, pinned by SHA and therefore
DELIBERATELY independent of the installed adcp SDK's own pin (see
docs/adcp-spec-version.md "Pinned schema sources"): advancing the SDK pin must
not move this file, which is why ``PINNED_SHA``/``PINNED_VERSION`` are literals
here rather than ``tests.helpers.adcp_pin``'s constants, even though the two
name the same revision today.

It exists for enums/error-code.json's ``enumMetadata`` ``suggestion`` text.
History, and why that reason is now spent:

- The pin previously sat at 04f59d2d5 (2026-05-13), which predates 3.1.1 and
  shipped the version-free ``/schemas/<category>/<name>.json`` ``$id`` form.
  The vendored copy had drifted from the v3.1.1 enum it claimed to be: 64 codes
  vs 92, 4 of the 64 shared enumMetadata entries divergent
  (CREDENTIAL_IN_ARGS, MEDIA_BUY_NOT_FOUND, PACKAGE_NOT_FOUND,
  REQUOTE_REQUIRED), and an unversioned ``$id``. It was re-vendored from v3.1.1
  verbatim and the pin advanced to match.
- That re-vendor discharged the divergence the fixture was kept for: measured
  against the installed SDK's own ``3.1/enums/error-code.json``, the two now
  agree on all 92 codes with zero ``suggestion`` and zero ``recovery``
  differences. #1721 then deleted the one reader that consumed the frozen
  ``suggestion`` text (test_architecture_error_suggestion_enum_conformance.py),
  when CODE_TABLE (src/core/errors/codes.py) became the sole authority for
  buyer-facing text.
- So this root set is now graded only by
  tests/unit/test_pinned_fixture_id_convention.py. Retiring it — dropping
  ``ROOTS``, the vendored file, and the flat half of that guard — is the
  reconciliation tracked as github.com/prebid/salesagent/issues/1883; it is a
  reviewed deletion, not something to do in passing.

Every OTHER pinned SCHEMA-SHAPE consumer — structural request/response shape,
``$ref`` resolution, AND the ``recovery`` half of this same enumMetadata block
(so tests/harness/transport.py and
tests/unit/test_architecture_error_recovery_enum_conformance.py both migrated)
— reads through tests/helpers/pinned_schema.py, which resolves from the
installed SDK's own tree. scripts/verify_feature_error_codes.py also migrated
(it only reads the ``enum`` code list, not enumMetadata content). This
directory no longer vendors that flat schema-shape closure at all;
``core/activation-key.json`` is the one flat artifact left over from it, kept
as the version-free ``$id`` exemplar the guard grades against.

2. ``3.1.1/`` — the explicitly-versioned trust-root document set
-----------------------------------------------------------------
(#1291 A3, salesagent-z6nr.9 step 7.) The trust-root documents A3 publishes are
graded against the spec version the SDK pin names, read from
``tests.helpers.adcp_pin``: ``adagents.json``, ``brand.json`` and
``core/authorized-agent-base.json`` — where ``signing_keys[]`` actually lives —
differ STRUCTURALLY from the SDK's generated copies, so the producer must be
graded against the spec repo's own documents. They are equally NOT part of
(1)'s retired schema-shape closure, which is why the SDK-tree migration left
them here: they are a version-namespaced pin in their own right, consumed by
tests/integration/test_trust_root_documents.py through
tests/helpers/pinned_schema.py.

This is a SEPARATE root set from (1) even though both resolve to v3.1.1 today:
(1) is frozen at a literal SHA and stays put when the SDK pin moves, (2) FOLLOWS
that pin, and the two land at different paths (flat vs version-namespaced).
They coincide at this revision and part again at the next bump.

The ``$id`` namespace at this revision carries the version
(``/schemas/3.1.1/...``), so these land under ``3.1.1/`` by the layout rule
below, and a resolver keyed on the VERSIONED URI needs no special case.

3. ``tests/fixtures/adcp_conformance_vectors/`` — the request-signing vectors
------------------------------------------------------------------------------
(#1291 B3, salesagent-z6nr.14 — see :func:`vendor_signing_vectors`.) NOT
schemas: the graded conformance DATA for the RFC 9421 request-signing profile
(12 positive + 28 negative request vectors, the runner keypairs, 31
URL-canonicalization cases). Deliberately the SAME mechanism as the schema pins
(local clone -> GitHub raw, committed snapshot, offline reads) rather than a
submodule or a fetch step; ``tests/`` runs offline by construction. They live in
their own fixture tree because they are loaded by a different loader
(tests/helpers/signing_vectors.py) and pinned by a different guard
(tests/unit/test_adcp_conformance_vectors_pin.py), but they are vendored by THIS
script so there is one refresh command.

Layout
------
A schema is written to ``<this dir>/<its ref minus the /schemas/ prefix>`` (so
``/schemas/enums/error-code.json`` -> ``enums/error-code.json`` and
``/schemas/3.1.1/brand.json`` -> ``3.1.1/brand.json``). Only the transitive
``$ref`` closure of the listed roots is vendored.

Root set (1) is the one place the ref and upstream's ``$id`` differ: it is
fetched at the v3.1.1 tag, whose ``$id``s carry the version, but it keeps the
flat path its readers and its citations address. :func:`expected_id` puts the
version back for the ``$id`` check only, never for the path.

``$id`` convention (GH #1881)
----------------------------
Vendored files keep upstream's ``$id`` **verbatim** — whatever the revision
they were fetched from ships, byte for byte. Two forms are therefore live in
this directory, and both are legitimate:

- the version-free ``/schemas/<category>/<name>.json`` that upstream wrote
  before 3.1.1 (``core/activation-key.json``, vendored at 04f59d2d5);
- the version-stamped ``/schemas/<version>/<category>/<name>.json`` — so
  ``/schemas/3.1.1/enums/error-code.json`` — that upstream's own ``$id``
  carries at tag ``v3.1.1``, for BOTH ``enums/error-code.json`` and the
  ``3.1.1/`` tree.

:func:`check_id_convention` refuses to write a file whose fetched ``$id`` is
anything other than the URI it is vendored as — which is what makes "verbatim"
checkable rather than asserted in text, for every root set.

Two reasons upstream's own ``$id`` is the rule, rather than a form of our
choosing:

- The point of this directory is to preserve frozen upstream artifacts for
  byte-comparison. Any field _refresh.py rewrote would no longer be evidence of
  what upstream said — stripping the version segment out of (1)'s or (2)'s
  ``$id`` would be exactly that edit.
- Stripping (2)'s version would also erase the one thing that distinguishes
  those documents from their pre-3.1.1 namesakes, and would collide (1)'s and
  (2)'s output paths.

Enforced offline by tests/unit/test_pinned_fixture_id_convention.py.

To refresh (e.g. to advance a pin — a deliberate, reviewed change that for (1)
must also re-check the recovery/suggestion divergence against the SDK):
    uv run python -m tests.fixtures.adcp_schemas_pinned._refresh

It reads from a local clone at ~/projects/adcp if present (faster), else GitHub raw.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.request
from pathlib import Path

from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION, SPEC_REV

# Root set 1's pin. Literals, NOT adcp_pin's constants: this pin is frozen by SHA
# and must stay put when the SDK pin advances (see the module docstring, section 1).
PINNED_SHA = "467fd93d77112baf9e094e18980119edcd3a4d07"
PINNED_VERSION = "3.1.1"  # the spec version PINNED_SHA tags; stamped into upstream's own `$id`
REPO = "adcontextprotocol/adcp"
SRC_PREFIX = f"dist/schemas/{PINNED_VERSION}"  # repo path that backs the `/schemas/...` namespace
LOCAL_CLONE = Path.home() / "projects" / "adcp"
FIXTURE_DIR = Path(__file__).parent

# Root set 1: the sole surviving flat root — error-code enumMetadata (see module
# docstring for why this is a deliberately independent pin, not part of the general
# schema-shape closure, which the installed SDK's own tree now serves).
ROOTS = [
    "/schemas/enums/error-code.json",
]

# Root set 2: the explicitly-versioned trust-root documents (#1291 A3,
# salesagent-z6nr.9 step 7). Not covered by the SDK-tree migration — see the
# module docstring's section 2 for why these stay vendored and version-namespaced.
V311_REV = SPEC_REV
V311_SRC_PREFIX = "dist/schemas"  # backs the `/schemas/...` namespace at this revision
V311_ROOTS = [
    f"/schemas/{EXPECTED_SPEC_VERSION}/brand.json",
    f"/schemas/{EXPECTED_SPEC_VERSION}/adagents.json",
    f"/schemas/{EXPECTED_SPEC_VERSION}/core/agent-signing-key.json",
]

# Root set 3: request-signing conformance vectors (#1291 B3, salesagent-z6nr.14).
# Not schemas and not $ref-walked — a whole upstream directory, byte-verbatim.
VECTORS_REV = SPEC_REV
VECTORS_SPEC_VERSION = EXPECTED_SPEC_VERSION
VECTORS_SRC = f"dist/compliance/{EXPECTED_SPEC_VERSION}/test-vectors/request-signing"
VECTORS_DIR = Path(__file__).parent.parent / "adcp_conformance_vectors" / EXPECTED_SPEC_VERSION / "request-signing"


def _read_local(rev: str, src_prefix: str, rel: str) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(LOCAL_CLONE), "show", f"{rev}:{src_prefix}{rel}"],
        capture_output=True,
        text=True,
    )
    return r.stdout if r.returncode == 0 else None


def _read_github(rev: str, src_prefix: str, rel: str) -> str:
    url = f"https://raw.githubusercontent.com/{REPO}/{rev}/{src_prefix}{rel}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (pinned host)
        return resp.read().decode()


def fetch(ref: str, *, rev: str = PINNED_SHA, src_prefix: str = SRC_PREFIX) -> str:
    rel = ref[len("/schemas") :]  # "/schemas/core/x.json" -> "/core/x.json"
    return _read_local(rev, src_prefix, rel) or _read_github(rev, src_prefix, rel)


class IdConventionError(RuntimeError):
    """A fetched schema's ``$id`` does not follow the vendoring convention."""


def expected_id(ref: str, version: str = "") -> str:
    """Upstream's own ``$id`` for *ref*, at the revision *ref* is fetched from.

    For every root set but (1) the ref IS the ``$id``: upstream's version segment
    is already part of the ref, or upstream stamps none. Root set (1) is fetched
    at a tag whose ``$id``s carry the version while keeping the flat output path
    its readers address, so *version* puts that segment back — for the ``$id``
    check only, never for the path (module docstring, "Layout").

    Derived from the caller's pin rather than hardcoded, so advancing
    ``PINNED_SHA``/``PINNED_VERSION`` together cannot leave the check asserting
    a form the pinned commit stopped shipping.
    """
    if not version:
        return ref
    return f"/schemas/{version}" + ref[len("/schemas") :]


def check_id_convention(ref: str, schema: dict) -> None:
    """Raise unless *schema*'s ``$id`` is verbatim the ``ref`` it is vendored as.

    See the module docstring's "$id convention" section. Applied to BOTH schema
    root sets: the rule is "upstream's own ``$id``, untouched", and — modulo the
    one version segment :func:`expected_id` restores — it is the same ``$id`` the
    layout rule derives the output path from, so a mismatch means the file would
    land somewhere its own ``$id`` does not name. Called before writing, so a
    refresh that would change a vendored ``$id`` aborts loudly instead of
    silently regressing the file and being caught (if at all) by a downstream
    reader much later.
    """
    actual = schema.get("$id")
    if actual != ref:
        raise IdConventionError(
            f"{ref}: upstream $id is {actual!r}, expected {ref!r}. Vendored fixtures keep "
            f"upstream's own /schemas/<category>/<name>.json form verbatim, version segment "
            f"included or omitted exactly as upstream wrote it (GH #1881). If upstream "
            f"deliberately changed its $id convention, update this script's docstring and "
            f"tests/unit/test_pinned_fixture_id_convention.py in the same reviewed change — "
            f"do not vendor the new form silently."
        )


def vendor(roots: list[str], *, rev: str, src_prefix: str, id_version: str = "") -> int:
    """Walk the transitive ``$ref`` closure of *roots* at *rev* and write it out.

    One BFS for every root set — the layout rule (the ref minus the ``/schemas/``
    prefix) and the ``$id`` check are identical at both revisions, so a second
    copy parameterised by revision would be pure duplication.

    *id_version* is the version segment upstream stamps into ``$id`` but the
    output path does not carry (root set 1 only). Known ceiling: it is applied to
    every ref in the walk, so it is only correct while that root set stays a
    single leaf enum with no ``$ref``s of its own — a closure fetched at a
    version-stamped revision would already carry the segment in its refs and
    double-stamp. If root set 1 ever grows a ``$ref``, resolve the ref namespace
    per root instead of per walk.
    """
    seen: set[str] = set()
    stack = list(roots)
    written = 0
    while stack:
        ref = stack.pop().split("#")[0]
        if not ref.startswith("/schemas/") or ref in seen:
            continue
        seen.add(ref)
        body = fetch(ref, rev=rev, src_prefix=src_prefix)
        schema = json.loads(body)
        check_id_convention(expected_id(ref, id_version), schema)
        out = FIXTURE_DIR / ref[len("/schemas/") :]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(schema, indent=2) + "\n")
        written += 1
        stack.extend(re.findall(r'"\$ref"\s*:\s*"([^"]+)"', body))
    print(f"vendored {written} schema files from {REPO}@{rev[:9]} into {FIXTURE_DIR}")
    return written


def _list_local(rev: str, path: str) -> list[str] | None:
    r = subprocess.run(
        ["git", "-C", str(LOCAL_CLONE), "ls-tree", "-r", "--name-only", rev, "--", path],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    return [line for line in r.stdout.splitlines() if line.strip()]


def _read_local_path(rev: str, path: str) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(LOCAL_CLONE), "show", f"{rev}:{path}"],
        capture_output=True,
        text=True,
    )
    return r.stdout if r.returncode == 0 else None


def _read_github_path(rev: str, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{REPO}/{rev}/{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (pinned host)
        return resp.read().decode()


def vendor_signing_vectors() -> int:
    """Vendor the request-signing conformance vectors + a sha256 MANIFEST.

    The vector tree is upstream-owned and byte-pinned: the drift guard
    (``tests/unit/test_adcp_conformance_vectors_pin.py``) re-hashes every file
    against ``MANIFEST.json`` and ties ``spec_version`` to
    ``adcp.get_adcp_spec_version()``, so a local edit to a vector — or an
    ``adcp`` pin bump without a re-vendor — is a loud failure.

    Files are written BYTE-VERBATIM (no JSON re-indent, and no ``$id`` check —
    these are vectors, not schemas, and carry no ``$id``): the vectors grade
    byte-level canonicalization, so reformatting them would be editing the
    evidence.
    """
    # GitHub raw cannot list a directory. With no local clone we re-fetch exactly
    # the file set the committed MANIFEST already records — enough to re-verify a
    # snapshot offline-first, while a NEW upstream file needs a clone. The drift
    # guard's explicit counts (12 positive / 28 negative / 31 canonicalization)
    # are what stop that from silently shrinking the graded set.
    paths = _list_local(VECTORS_REV, VECTORS_SRC)
    if paths is None:
        prior = VECTORS_DIR / "MANIFEST.json"
        if not prior.exists():
            raise SystemExit(
                f"No local adcp clone at {LOCAL_CLONE} and no committed "
                f"{prior} to enumerate from — clone adcontextprotocol/adcp first."
            )
        paths = [f"{VECTORS_SRC}/{rel}" for rel in json.loads(prior.read_text())["files"]]
    manifest: dict[str, str] = {}
    for path in sorted(paths):
        rel = path[len(VECTORS_SRC) + 1 :]
        body = _read_local_path(VECTORS_REV, path) or _read_github_path(VECTORS_REV, path)
        out = VECTORS_DIR / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body)
        manifest[rel] = hashlib.sha256(body.encode()).hexdigest()
    (VECTORS_DIR / "MANIFEST.json").write_text(
        json.dumps(
            {
                "spec_version": VECTORS_SPEC_VERSION,
                "source_tag": VECTORS_REV,
                "source_path": VECTORS_SRC,
                "files": manifest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"vendored {len(manifest)} conformance-vector files from {REPO}@{VECTORS_REV} into {VECTORS_DIR}")
    return len(manifest)


def main() -> None:
    vendor(ROOTS, rev=PINNED_SHA, src_prefix=SRC_PREFIX, id_version=PINNED_VERSION)
    vendor(V311_ROOTS, rev=V311_REV, src_prefix=V311_SRC_PREFIX)
    vendor_signing_vectors()


if __name__ == "__main__":
    main()
