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

## Behavior target and historical migration note

The SDK pin and the behavior target are both AdCP 3.1.1 GA. There is no current
deliberate divergence between this seller's media-buy status wire shape, the
SDK 6.6.0 types, and the 3.1.1 graded behavior.

The following history explains the regression guards retained in this
repository; it is not a present compatibility exception:

- **beta.3 storyboard** (`dist/compliance/3.1.0-beta.3/.../pending_creatives_to_start.yaml`,
  ~L131-134) grades the body `status` as `field_value_or_absent` that MUST equal
  `media_buy_status` — the deprecated "both identical" model (#4908).
- **Target GA** — graded by the published **3.1.0** compliance
  (`dist/compliance/3.1.0/.../pending_creatives_to_start.yaml`, ~L146-153;
  `3.1.1` is byte-identical for this storyboard) — grades `media_buy_status`
  as `field_value` (the DOMAIN status) and the top-level `status` as
  `field_value` `'completed'` (the PROTOCOL `TaskStatus`, protocol envelope).
  The two are DIFFERENT namespaces and are NOT identical.

Our current wire implements the GA model:
`TaskResultEnvelope._serialize` sets the top-level `status` to the protocol
`TaskStatus`, while the domain status survives under `media_buy_status`
(`src/core/schemas/_base.py` `_mirror_media_buy_status`). The dual-emit
validator only backfills the deprecated **body** `status` from the domain
`media_buy_status` for the deprecation window; it does not touch the wire
top-level `status`.

Historically, adcp 5.7 (the 3.1.0-beta.3 SDK) typed the response body `status`
as `MediaBuyStatus | None`. SDK 6.6.0's GA models now represent the protocol
completion status and domain `media_buy_status` consistently with 3.1.1. The
value-pinned regression coverage remains in
`tests/bdd/features/BR-UC-002-media-buy-status-dual-emit.feature` and the
`then_dual_emit_media_buy_status` step in
`tests/bdd/steps/domain/uc002_create_media_buy.py` (see PR #1417).
`tests/unit/test_adcp_spec_version.py` guards the SDK pin; the BDD scenarios
guard behavior.

## Wire negotiation

AdCP wire values for `adcp_version` are release-precision (`"3.0"`,
`"3.1"`). Buyer-supplied patch-precision pins such as `"3.1.1"` are rejected;
they are not silently normalized. The SDK's internal spec metadata remains the
full build target (`3.1.1`), while the seller advertises and negotiates the
release-precision value (`3.1`) on the wire.

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
