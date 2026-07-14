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

## Related files

- `pyproject.toml` — SDK pin
- `tests/unit/test_adcp_spec_version.py` — CI guard
- `docs/adcp-spec-version.md` — this document
