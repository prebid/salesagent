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
git -C ~/projects/adcp show v3.1.1:dist/docs/3.1.1/<path>         # prose
```

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
4. Update `EXPECTED_SPEC_VERSION` in `tests/unit/test_adcp_spec_version.py`.
5. Update this document.
6. Run `make quality` and address Pydantic field/type changes.
7. Re-verify integration and BDD coverage.

## Related files

- `pyproject.toml` — SDK pin
- `tests/unit/test_adcp_spec_version.py` — CI guard
