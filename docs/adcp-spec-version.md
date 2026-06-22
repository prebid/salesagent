# AdCP Spec Version

Prebid Sales Agent targets **AdCP spec version 3.1.0-beta.3**.

## Verifying the current target

```python
import adcp
adcp.get_adcp_spec_version()  # "3.1.0-beta.3"
adcp.get_adcp_sdk_version()   # "5.7.0"
```

## Why this version

The `adcp` Python SDK is pinned in `pyproject.toml` to `==5.7.0`. SDK 5.7.0
is code-generated from AdCP spec 3.1.0-beta.3 and ships Pydantic models that
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
| 6.x (beta) | 3.1.0-beta |

To check what spec version any installed SDK release targets:

```bash
uv run python -c "import adcp; print(adcp.get_adcp_spec_version())"
```

## CI guard

`tests/unit/test_adcp_spec_version.py` asserts the installed SDK targets
`3.1.0-beta.3`. A pin shift will fail this test, forcing a deliberate update
across `pyproject.toml`, the test's `EXPECTED_SPEC_VERSION` constant, and
this document.

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
