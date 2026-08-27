#!/usr/bin/env python3
"""Refresh the pinned AdCP artifacts vendored under ``tests/fixtures/``.

Three root sets, three DIFFERENT reasons to exist. Everything else that used to
be vendored here is gone: the general schema-SHAPE closure now comes from the
installed adcp SDK's own tree (see below), so there is exactly ONE upstream pin
for it — pyproject's ``adcp`` version.

1. ``enums/error-code.json`` — the SHA-pinned enumMetadata survivor
-------------------------------------------------------------------
Source of truth: adcontextprotocol/adcp @ commit
    04f59d2d56d3d77033162c310e99a1188e4eb419  (tag v3.1-04f59d2d5, 2026-05-13)

This commit is an INTENTIONAL, frozen reference point, DELIBERATELY independent
of the installed adcp SDK's own pin (see docs/adcp-spec-version.md "Pinned
schema sources"). It exists ONLY for enums/error-code.json's ``enumMetadata``
``suggestion`` text, read by
tests/unit/test_architecture_error_suggestion_enum_conformance.py. The
installed SDK's error-code enum has grown independently (92+ codes vs. this
fixture's 64) and its ``suggestion`` wording diverges from this fixture's on
4 codes (CREDENTIAL_IN_ARGS, MEDIA_BUY_NOT_FOUND, PACKAGE_NOT_FOUND,
REQUOTE_REQUIRED, verified at migration time) — moving that reader onto the
SDK tree requires first reconciling that divergence (tracked as
github.com/prebid/salesagent/issues/1883; see docs/adcp-spec-version.md),
not a mechanical resolver swap.

Every OTHER pinned SCHEMA-SHAPE consumer — structural request/response shape,
``$ref`` resolution, AND the ``recovery`` half of this same enumMetadata
block (verified byte-identical across all 64 shared codes, so
tests/harness/transport.py and
tests/unit/test_architecture_error_recovery_enum_conformance.py both migrated)
— reads through tests/helpers/pinned_schema.py, which resolves from the
installed SDK's own tree. scripts/verify_feature_error_codes.py also
migrated (it only reads the ``enum`` code list, not enumMetadata content).
This directory no longer vendors that flat schema-shape closure at all.

2. ``3.1.1/`` — the explicitly-versioned trust-root document set
-----------------------------------------------------------------
(#1291 A3, salesagent-z6nr.9 step 7.) The trust-root documents A3 publishes are
graded against v3.1.1, NOT against ``PINNED_SHA``: ``adagents.json`` and
``brand.json`` differ between the two revisions, and
``core/authorized-agent-base.json`` — where ``signing_keys[]`` actually lives —
does not exist at ``PINNED_SHA`` at all. Vendoring them from the old pin would
grade the producer against a schema that predates the shape it must emit. They
are equally NOT part of (1)'s retired schema-shape closure, which is why the
SDK-tree migration left them here: they are a version-namespaced pin in their
own right, consumed by tests/integration/test_trust_root_documents.py.

The ``PINNED_SHA`` pin is NOT moved to cover them — that would churn the frozen
wording (1) exists to preserve. Two coexisting root sets instead.

The ``$id`` namespace at this revision carries the version
(``/schemas/3.1.1/...``), so these land under ``3.1.1/`` by the same layout rule
as (1), and a resolver keyed on the VERSIONED URI needs no special case.
``core/agent-signing-key.json`` is byte-identical to the ``PINNED_SHA`` copy
except for that ``$id`` — the changed namespace is exactly why the versioned URI
has to be the registry key.

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
A schema's ``$id``/``$ref`` namespace is ``/schemas/<rest>``; each is written to
``<this dir>/<rest>`` (so ``/schemas/enums/error-code.json`` ->
``enums/error-code.json`` and ``/schemas/3.1.1/brand.json`` ->
``3.1.1/brand.json``). Only the transitive ``$ref`` closure of the listed roots
is vendored.

``$id`` convention (GH #1881)
----------------------------
Vendored files keep upstream's ``$id`` **verbatim**: the site-rooted form
``/schemas/<category>/<name>.json`` at ``PINNED_SHA``, and the
version-qualified ``/schemas/3.1.1/<category>/<name>.json`` that upstream's own
``$id`` carries at tag ``v3.1.1``. :func:`check_id_convention` refuses to write
a file whose fetched ``$id`` is anything other than the ref it was fetched as —
which is what makes "verbatim" checkable rather than asserted in prose, and what
keeps the layout rule above and the ``$id`` in agreement for BOTH schema root
sets.

Two reasons upstream's own ``$id`` is the rule, rather than a form of our choosing:

- The point of this directory is to preserve frozen upstream artifacts for
  byte-comparison. Any field _refresh.py rewrote would no longer be evidence of
  what upstream said.
- The pin for (1) is a SHA, not a spec version. Stamping a version into its
  ``$id`` would assert a spec identity the commit does not carry — 04f59d2d5
  predates 3.1.1, and the number would silently go stale the moment the SHA
  advances. Conversely, STRIPPING the version from (2)'s ``$id`` would erase the
  one thing that distinguishes those documents from their ``PINNED_SHA``
  namesakes, and would collide (1)'s and (2)'s output paths.

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

PINNED_SHA = "04f59d2d56d3d77033162c310e99a1188e4eb419"
REPO = "adcontextprotocol/adcp"
SRC_PREFIX = "static/schemas/source"  # repo path that backs the `/schemas/...` namespace
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


def check_id_convention(ref: str, schema: dict) -> None:
    """Raise unless *schema*'s ``$id`` is verbatim the ``ref`` it was fetched as.

    See the module docstring's "$id convention" section. Applied to BOTH schema
    root sets, not just the SHA-pinned one: the rule is "upstream's own ``$id``,
    untouched", and it is the same ``$id`` the layout rule derives the output
    path from, so a mismatch means the file would land somewhere its own ``$id``
    does not name. Called before writing, so a refresh that would change a
    vendored ``$id`` aborts loudly instead of silently regressing the file and
    being caught (if at all) by a downstream reader much later.
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


def vendor(roots: list[str], *, rev: str, src_prefix: str) -> int:
    """Walk the transitive ``$ref`` closure of *roots* at *rev* and write it out.

    One BFS for every root set — the layout rule (``$id`` namespace path minus
    the ``/schemas/`` prefix) and the ``$id`` check are identical at both
    revisions, so a second copy parameterised by revision would be pure
    duplication.
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
        check_id_convention(ref, schema)
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
    vendor(ROOTS, rev=PINNED_SHA, src_prefix=SRC_PREFIX)
    vendor(V311_ROOTS, rev=V311_REV, src_prefix=V311_SRC_PREFIX)
    vendor_signing_vectors()


if __name__ == "__main__":
    main()
