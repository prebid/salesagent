# AdCP Spec Version

Prebid Sales Agent targets **AdCP spec version 3.1.1**.

## Verifying the current target

```python
import adcp
adcp.get_adcp_spec_version()  # "3.1.1"
adcp.get_adcp_sdk_version()   # "6.6.0"
```

## Why this version

The `adcp` Python SDK is pinned in `pyproject.toml` to `==6.6.0`. SDK 6.6.0
is code-generated from AdCP spec 3.1.1 and ships Pydantic models that
encode that spec version's request/response shapes.

The SDK-to-spec mapping (verified via each wheel's bundled `ADCP_VERSION`
file):

| adcp SDK release | AdCP spec |
|---|---|
| 4.3.x | 3.0.1 |
| 4.4.x | 3.0.5 |
| 4.5.x – 4.6.x | 3.0.5 |
| 5.0.x – 5.6.x | 3.0.7 |
| 5.7.x | 3.1.0-beta.3 |
| 6.x (stable) | 3.1.1 |

To check what spec version any installed SDK release targets:

```bash
uv run python -c "import adcp; print(adcp.get_adcp_spec_version())"
```

## CI guard

`tests/unit/test_adcp_spec_version.py` asserts the installed SDK targets
`3.1.1`. A pin shift will fail this test, forcing a deliberate update
across `pyproject.toml`, the test's `EXPECTED_SPEC_VERSION` constant, and
this document.

## Behavior target vs SDK pin

The SDK **pin** (3.1.0-beta.3) fixes the request/response *type shapes* we
build against. It does **not** always fix the graded *behavior*. One field
diverges deliberately: the `media_buy_status` dual-emit on
create-/update-media-buy responses.

- **beta.3 storyboard** (`dist/compliance/3.1.0-beta.3/.../pending_creatives_to_start.yaml`,
  ~L131-134) grades the body `status` as `field_value_or_absent` that MUST equal
  `media_buy_status` — the deprecated "both identical" model (#4908).
- **Target GA** — graded by the published **3.1.0** compliance
  (`dist/compliance/3.1.0/.../pending_creatives_to_start.yaml`, ~L146-153;
  `3.1.1` is byte-identical for this storyboard) — grades `media_buy_status`
  as `field_value` (the DOMAIN status) and the top-level `status` as
  `field_value` `'completed'` (the PROTOCOL `TaskStatus`, protocol envelope).
  The two are DIFFERENT namespaces and are NOT identical.

Our wire already implements the divergent (target GA) model:
`TaskResultEnvelope._serialize` sets the top-level `status` to the protocol
`TaskStatus`, while the domain status survives under `media_buy_status`
(`src/core/schemas/_base.py` `_mirror_media_buy_status`). The dual-emit
validator only backfills the deprecated **body** `status` from the domain
`media_buy_status` for the deprecation window; it does not touch the wire
top-level `status`.

**Known SDK type defect (SDK not authoritative):** adcp 5.7 types the response
`status` as `MediaBuyStatus | None`, but the wire top-level `status` carries a
protocol `TaskStatus` (`submitted` / `completed`). This is fine because that
protocol value lives on `TaskResultEnvelope.status` (typed `str`), never on the
SDK-typed body field. Grounding for the divergent behavior is the value-pinned
`media_buy_status` assertions in
`tests/bdd/features/BR-UC-002-media-buy-status-dual-emit.feature` and the
`then_dual_emit_media_buy_status` step in
`tests/bdd/steps/domain/uc002_create_media_buy.py` (see PR #1417).
`tests/unit/test_adcp_spec_version.py` only guards the SDK pin, not this behavior.

## Wire negotiation

AdCP wire values for `adcp_version` are release-precision (`"3.0"`,
`"3.1"`). The SDK accepts patch-precision input for backwards
compatibility but normalizes to release-precision on the wire.

## Bumping the spec version

A spec version bump is a deliberate change with downstream impact:

1. Read the AdCP spec changelog for the target version.
2. Update `pyproject.toml` SDK pin to a release that targets the new spec
   version (see mapping above).
3. Run `uv lock --upgrade-package adcp`.
4. Update `EXPECTED_SPEC_VERSION` in `tests/unit/test_adcp_spec_version.py`.
5. Update this document.
6. Run `make quality` and address Pydantic field/type changes.
7. Re-verify integration and BDD test coverage.

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
- `tests/helpers/pinned_schema.py` — single source of truth for schema-SHAPE resolution (the installed SDK's plain tree)
- `tests/unit/test_pinned_schema_single_source.py` — pins that `pinned_schema.py` tracks the SDK's own version, not an independently vendored one
- `tests/helpers/adcp_schema_validator.py` — e2e request/response validation, delegates to `pinned_schema.py`
- `tests/fixtures/adcp_schemas_pinned/` — vendored error-code `enumMetadata` `suggestion` text, sole remaining consumer `test_architecture_error_suggestion_enum_conformance.py` (independent pin, error-code reconciliation epic only — NOT a general schema-shape source)
- `docs/adcp-spec-version.md` — this document
