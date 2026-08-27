# AdCP Spec Version

Prebid Sales Agent targets **AdCP spec version 3.1.1** via the `adcp==6.6.0`
Python SDK (pinned exactly in `pyproject.toml`).

## Verifying the current target

```bash
uv run python -c "import adcp; print(adcp.get_adcp_spec_version(), adcp.get_adcp_sdk_version())"
# 3.1.1 6.6.0
```

The same command tells you what spec version any other SDK release targets —
use it instead of looking for a version table.

## CI guard

`tests/unit/test_adcp_spec_version.py` asserts the installed SDK targets
`3.1.1`. A pin shift fails that test, forcing a deliberate update across
`pyproject.toml`, the test's `EXPECTED_SPEC_VERSION` constant, and this
document.

## Where the spec lives

`github.com/adcontextprotocol/adcp`, read at the pinned tag only:

```bash
git -C ~/projects/adcp show v3.1.1:dist/schemas/3.1.1/<path>      # type shapes
git -C ~/projects/adcp show v3.1.1:dist/compliance/3.1.1/<path>   # graded storyboards
git -C ~/projects/adcp show v3.1.1:docs/<path>                    # prose
```

Note the asymmetry in that third line, because it is the one that has been copied
wrong. Schemas and storyboards live under a version-numbered `dist/` root, and prose
does not: `dist/docs/` stops at `3.1.0`, so `dist/docs/3.1.1/` resolves at no tag. Prose
is read from the repository-root `docs/` tree at the pinned ref, which is what makes the
ref rather than the path carry the version.

Verify a citation resolves before you rely on it — `git cat-file -e v3.1.1:<path>` exits
non-zero when it does not.

The checked-out working tree of that repo is **not** the pinned version. The
installed `adcp` SDK is a cross-check, never the authority — it can diverge
from the spec.

## `status` vs `media_buy_status` on media-buy responses

The two are different namespaces and are **not** identical:

- top-level `status` is the PROTOCOL `TaskStatus` (`submitted` / `completed`),
  set by `TaskResultEnvelope._serialize`;
- `media_buy_status` is the DOMAIN status, mirrored by
  `_mirror_media_buy_status` (`src/core/schemas/_base.py`).

3.1.1's `pending_creatives_to_start.yaml` storyboard grades both as
`field_value`, which is what we emit. The `_dual_emit_media_buy_status`
validator additionally backfills the deprecated **body** `status` from
`media_buy_status` for the deprecation window; it never touches the wire
top-level `status`. Behavior is pinned by
`tests/bdd/features/BR-UC-002-media-buy-status-dual-emit.feature` and the
`then_dual_emit_media_buy_status` step — `test_adcp_spec_version.py` guards
only the SDK pin, not this behavior.

## Wire negotiation

AdCP wire values for `adcp_version` are release-precision (`"3.0"`, `"3.1"`).
The SDK accepts patch-precision input for backwards compatibility but
normalizes to release-precision on the wire.

## Bumping the spec version

1. Read the AdCP spec changelog for the target version.
2. Update the `adcp` pin in `pyproject.toml` (confirm its spec target with the
   command above).
3. `uv lock --upgrade-package adcp`.
4. Update `EXPECTED_SPEC_VERSION` in `tests/helpers/adcp_pin.py` (the constant lives
   there; `tests/unit/test_adcp_spec_version.py` imports it).
5. Re-vendor the **pinned schema tree** to the new version — the directory under
   `tests/fixtures/adcp_schemas_pinned/` is named for the spec version, and
   `test_the_vendored_schema_tree_matches_the_pin` fails until it matches. A stale tree
   grades our trust-root documents against the previous version's schemas while every
   version literal in production has already moved.
6. Re-vendor the request-signing conformance vectors and their `MANIFEST.json`:

   ```bash
   uv run python -m tests.fixtures.adcp_schemas_pinned._refresh
   ```

   Nothing to edit in that script first: `VECTORS_REV`, `VECTORS_SPEC_VERSION`,
   `VECTORS_SRC` and the root sets all derive from `EXPECTED_SPEC_VERSION` and
   `SPEC_REV`, so step 4 already moved them. `tests/unit/test_adcp_conformance_vectors_pin.py`
   ties the vendored snapshot to the pin, so skipping the re-vendor fails CI rather
   than silently grading the verifier against the previous version's conformance data.
7. Update this document.
8. Run `make quality` and address Pydantic field/type changes.

Nothing needs updating for the served `$schema` values: `_SCHEMA_BASE`
(`src/core/signing/trust_root.py`) derives them from `adcp.get_adcp_spec_version()`, so
they move with the pin. They were literals until #1757, and the `$schema` assertion in
`tests/integration/test_trust_root_documents.py` graded only that the KEY was present —
so a bump would have left every trust-root document pointing at the previous version with
nothing to catch it.
8. Re-verify integration and BDD coverage.

## Pinned schema sources

Every JSON-schema-SHAPE consumer in the repo resolves through one module,
`tests/helpers/pinned_schema.py`, which reads the installed `adcp` SDK's own
"plain" tree (`adcp/_schemas/<major.minor>/`, sibling of the SDK's `bundled/`
subset). That tree moves automatically with the `pyproject.toml` SDK pin —
there is exactly one upstream pin for schema *structure* (request/response
shapes, `$ref` graphs, `required`/`properties`), and the CI guard above
(`tests/unit/test_adcp_spec_version.py`) keeps it honest. Consumers:
`tests/unit/test_pydantic_schema_alignment.py`, `tests/helpers/adcp_schema_validator.py`,
and the schema-validating integration tests (`tests/integration/test_get_products_placement_schema.py`
and friends). The plain tree is deliberately used over `bundled/`: `bundled/`
only physically ships 8 of the SDK's 16 top-level categories (no `account/`,
`enums/`, `governance/`, etc.), so validating a task in a missing category
against `bundled/` alone would raise "not found" even though the schema
exists. `pinned_schema.py` resolves the plain tree's relative `$ref`s
(`../core/x.json`) by stamping each loaded schema with its own `file://` URI
(`path.as_uri()`) as its `$id`, wired through a `referencing.Registry`. It also
owns the single ref-normalization rule (`normalize_ref`): the only accepted
form is the category-qualified, version-root-relative one the SDK index itself
uses. An absolute URL or a `/schemas/<version>/…` path raises rather than being
rewritten onto the pin — a ref naming a version means the caller believes it is
grading something other than the pin, and quietly redirecting it hides that.

A second, DELIBERATELY separate and independent pin remains for exactly one
thing: error-code **enumMetadata `suggestion` text**, read from the vendored
fixture (`tests/fixtures/adcp_schemas_pinned/enums/error-code.json`, at the
upstream commit recorded in its `_refresh.py` `PINNED_SHA`) by
`tests/unit/test_architecture_error_suggestion_enum_conformance.py` only.
Verified at migration time: the installed SDK's error-code enum is a strict
superset of the fixture's (92 vs. 64 codes, fixture-only set empty), and its
`recovery` classification is IDENTICAL across all 64 shared codes (0
divergences; 30 `AdCPError` subclasses graded, unchanged before/after).
Reproduce the fixture's code count: `uv run python3 -c "import json;
print(len(json.load(open('tests/fixtures/adcp_schemas_pinned/enums/error-code.json'))['enum']))"`
-> 64 (65 `enumMetadata` keys, one of which is `$comment`). So
every OTHER error-code reader migrated off the fixture:
`tests/harness/transport.py` and
`tests/unit/test_architecture_error_recovery_enum_conformance.py` read through
`tests/helpers/pinned_schema.py` alongside the schema-shape consumers above,
and `scripts/verify_feature_error_codes.py`, which needs only the code list,
reads `adcp.ErrorCode` — the SDK's own generated enum — directly. Only `suggestion` wording diverges (4 codes:
`CREDENTIAL_IN_ARGS`, `MEDIA_BUY_NOT_FOUND`, `PACKAGE_NOT_FOUND`,
`REQUOTE_REQUIRED`) — moving the one remaining reader onto the SDK tree
requires first reconciling that divergence (tracked as
[#1883](https://github.com/prebid/salesagent/issues/1883)); until it lands,
this fixture is the correct, intentional source for that one consumer, and a
spec bump must consider it separately from the schema-shape pin above.

## Related files

- `pyproject.toml` — SDK pin
- `tests/unit/test_adcp_spec_version.py` — CI guard
- `tests/unit/test_adcp_conformance_vectors_pin.py` — conformance-vector pin guard
- `tests/fixtures/adcp_conformance_vectors/` — the vendored, sha256-pinned vectors
- `tests/helpers/pinned_schema.py` — single source of truth for schema-SHAPE resolution (the installed SDK's plain tree)
- `tests/unit/test_pinned_schema_single_source.py` — pins that `pinned_schema.py` tracks the SDK's own version, not an independently vendored one
- `tests/helpers/adcp_schema_validator.py` — e2e request/response validation, delegates to `pinned_schema.py`
- `tests/fixtures/adcp_schemas_pinned/` — vendored error-code `enumMetadata` `suggestion` text, sole remaining consumer `test_architecture_error_suggestion_enum_conformance.py` (independent pin, error-code reconciliation epic only — NOT a general schema-shape source)
- `docs/adcp-spec-version.md` — this document
