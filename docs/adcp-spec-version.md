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
document. That guard also reads this document and CLAUDE.md, so prose here
that presents a version other than the pin as **the** pin fails it too.

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

Tooling resolves the same tree through `adcp_home()` in
`scripts/audit/storyboard_spec.py`, which prefers `$ADCP_HOME`, then the
published, sha256-verified release bundle extracted at
`tests/storyboard/runner/adcp-<version>/` (what the storyboard-conformance CI
job downloads), and only falls back to a personal `~/projects/adcp` clone.

## `status` vs `media_buy_status` on media-buy responses

The SDK **pin** (`adcp==6.6.0`, spec **3.1.1**) fixes the request/response
*type shapes* we build against. It does **not** always fix the graded
*behavior*. One field is worth spelling out: the `media_buy_status` dual-emit
on create-/update-media-buy responses.

The two fields are different namespaces and are **not** identical:

- top-level `status` is the PROTOCOL `TaskStatus` (`submitted` / `completed`),
  set by `TaskResultEnvelope._serialize`;
- `media_buy_status` is the DOMAIN status, mirrored by
  `_mirror_media_buy_status` (`src/core/schemas/_base.py`).

What the storyboards grade:

- **Then (3.1.0-beta.3):** the storyboard graded the body `status` as
  `field_value_or_absent` that MUST equal `media_buy_status` — the deprecated
  "both identical" model (#4908). Our wire deliberately diverged from it.
- **Now (pinned 3.1.1):**
  `dist/compliance/3.1.1/domains/media-buy/scenarios/pending_creatives_to_start.yaml`
  grades `media_buy_status` as `field_value` (the DOMAIN status, L146-148) and
  separately grades `status` as `field_value` `'completed'` (the PROTOCOL
  `TaskStatus`, protocol envelope, L150-153). There are ZERO
  `field_value_or_absent` checks in that file — which is the model our wire
  already implements.

The `_dual_emit_media_buy_status` validator additionally backfills the
deprecated **body** `status` from `media_buy_status` for the deprecation
window; it never touches the wire top-level `status`. That backfill remains
live production code — it is the deprecation window, not a divergence from the
pin. Behavior is pinned by
`tests/bdd/features/BR-UC-002-media-buy-status-dual-emit.feature` and the
`then_dual_emit_media_buy_status` step in
`tests/bdd/steps/domain/uc002_create_media_buy.py` (see PR #1417).
`tests/unit/test_adcp_spec_version.py` guards the SDK pin and the version
claims in this document — not this behavior.

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
7. Refresh the two storyboard artifacts that are pin-coupled but do not move
   themselves:
   - `tests/fixtures/adcp_storyboards_pinned/index.json` — run its
     `_refresh.py` against a fresh `~/projects/adcp` clone at the new pin.
     `tests/unit/test_architecture_storyboard_binding.py`'s
     `test_fixture_index_version_matches_the_pin` fails until this is done.
   - `tests/storyboard/runner/package.json`'s `@adcp/sdk` dependency — bump to
     a release whose own `adcp_version` targets the new spec version (`npm
     view @adcp/sdk@<v> adcp_version`), then `npm ci` in
     `tests/storyboard/runner/`. `tests/storyboard/test_runner_sdk_pin.py`'s
     `test_runner_sdk_targets_the_pinned_adcp_version` fails until this is
     done.
8. Update this document.
9. Run `make quality` and address Pydantic field/type changes.
10. Re-verify integration and BDD coverage.

Nothing needs updating for the served `$schema` values: `_SCHEMA_BASE`
(`src/core/signing/trust_root.py`) derives them from `adcp.get_adcp_spec_version()`, so
they move with the pin. They were literals until #1757, and the `$schema` assertion in
`tests/integration/test_trust_root_documents.py` graded only that the KEY was present —
so a bump would have left every trust-root document pointing at the previous version with
nothing to catch it.

## Pinned schema sources

Every JSON-schema-SHAPE consumer in the repo resolves through one module,
`tests/helpers/pinned_schema.py`, which reads the installed `adcp` SDK's own
"plain" tree (`adcp/_schemas/<major.minor>/`, sibling of the SDK's `bundled/`
subset). That tree moves automatically with the `pyproject.toml` SDK pin —
there is exactly one upstream pin for schema *structure* (request/response
shapes, `$ref` graphs, `required`/`properties`), and the CI guard above
(`tests/unit/test_adcp_spec_version.py`) keeps it honest. Consumers:
`tests/unit/test_adcp_contract.py`, `tests/helpers/adcp_schema_validator.py`,
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
uses. An absolute URL or a site-rooted `/schemas/<version>/…` path raises rather
than being rewritten onto the pin — a ref in that form means the caller believes
it is grading something other than the pin, and quietly redirecting it hides
that.

A version-prefixed ref (`3.1.1/adagents.json`, spelled from
`EXPECTED_SPEC_VERSION` rather than as a literal) deliberately selects a SECOND
source instead: the vendored, version-namespaced tree at
`tests/fixtures/adcp_schemas_pinned/<version>/`, which holds the spec repo's own
documents fetched verbatim at tag `v3.1.1`. Of the 73 vendored files, 29 differ
STRUCTURALLY from the SDK's generated copies — including the trust-root
documents where `signing_keys[]` lives — which is why they are vendored at all,
and the spec-grounding gate is why they win for those documents. That tree is a
pin in its own right: `tests/integration/test_trust_root_documents.py` consumes
it, step 5 of the bump procedure re-vendors it, and
`test_the_vendored_schema_tree_matches_the_pin` fails until its directory name
matches the pin.

A THIRD root set survives in that same fixture directory, SHA-pinned rather than
version-pinned, for error-code **enumMetadata `suggestion` text**
(`tests/fixtures/adcp_schemas_pinned/enums/error-code.json`, at the upstream
commit recorded in its `_refresh.py` `PINNED_SHA`). It no longer has a content
reader. Its only one,
`tests/unit/test_architecture_error_suggestion_enum_conformance.py`, went with
the error-taxonomy suite #1721 deleted: `recovery`, `suggestion` AND `message`
are now LOADED per code by `CODE_TABLE` (`src/core/errors/codes.py`) from the
installed SDK wheel's own `enums/error-code.json`, so a loaded table cannot
drift from the file it came from and there is nothing left to grade. What still
reads the vendored copy is `tests/unit/test_pinned_fixture_id_convention.py`,
and only its `$id`.

Verified at migration time: the installed SDK's error-code enum is a strict
superset of the fixture's (92 vs. 64 codes, fixture-only set empty), and its
`recovery` classification is IDENTICAL across all 64 shared codes (0
divergences; 30 `AdCPSalesAgentError` subclasses graded, unchanged before/after).
Reproduce the fixture's code count: `uv run python3 -c "import json;
print(len(json.load(open('tests/fixtures/adcp_schemas_pinned/enums/error-code.json'))['enum']))"`
-> 64 (65 `enumMetadata` keys, one of which is `$comment`). Every other
error-code reader had already migrated off the fixture:
`tests/harness/transport.py` and
`tests/unit/test_architecture_error_recovery_enum_conformance.py` read through
`tests/helpers/pinned_schema.py` alongside the schema-shape consumers above,
and `scripts/verify_feature_error_codes.py` resolves through `CODE_TABLE` — the
codes a raise site can EMIT, which is the question the wire assertions it backs
ask. Only `suggestion` wording diverges (4 codes: `CREDENTIAL_IN_ARGS`,
`MEDIA_BUY_NOT_FOUND`, `PACKAGE_NOT_FOUND`, `REQUOTE_REQUIRED`) — with the last
reader gone, the reconciliation tracked as
[#1883](https://github.com/prebid/salesagent/issues/1883) now RETIRES this root
set rather than repointing it, which `test_the_fixture_set_is_not_empty` in
`tests/unit/test_pinned_fixture_id_convention.py` says in as many words. A spec
bump must still consider it separately from the schema-shape pin above.

## Related files

- `pyproject.toml` — SDK pin
- `tests/unit/test_adcp_spec_version.py` — CI guard
- `tests/unit/test_adcp_conformance_vectors_pin.py` — conformance-vector pin guard
- `tests/fixtures/adcp_conformance_vectors/` — the vendored, sha256-pinned vectors
- `tests/helpers/pinned_schema.py` — single source of truth for schema-SHAPE resolution (the installed SDK's plain tree by default; the vendored tree only for a version-prefixed ref)
- `tests/helpers/adcp_pin.py` — `EXPECTED_SPEC_VERSION`, the one constant the guards and the version-prefixed refs read
- `tests/unit/test_pinned_schema_single_source.py` — pins that `pinned_schema.py` tracks the SDK's own version, not an independently vendored one
- `tests/helpers/adcp_schema_validator.py` — e2e request/response validation, delegates to `pinned_schema.py`
- `tests/fixtures/adcp_schemas_pinned/<version>/` — the spec repo's own trust-root documents at tag `v3.1.1`, selected by a version-prefixed ref; consumer `tests/integration/test_trust_root_documents.py`. Pin-coupled: `test_the_vendored_schema_tree_matches_the_pin`
- `tests/fixtures/adcp_schemas_pinned/enums/error-code.json` — SHA-pinned `enumMetadata` `suggestion` text with no content reader left (see "Pinned schema sources"); retired by the #1883 reconciliation — NOT a general schema-shape source
- `tests/fixtures/adcp_storyboards_pinned/index.json` — vendored, offline snapshot of the pinned compliance tree's storyboard structure (paths, phases, gates); pin-coupled, refreshed via its `_refresh.py`. Guarded by `tests/unit/test_architecture_storyboard_binding.py`'s `test_fixture_index_version_matches_the_pin`
- `tests/storyboard/runner/package.json` — the TS conformance runner's `@adcp/sdk` pin; pin-coupled, independently of the Python SDK pin above. Guarded by `tests/storyboard/test_runner_sdk_pin.py`
- `docs/adcp-spec-version.md` — this document
