"""CI guard: assert the adcp SDK pin targets the expected AdCP spec version."""

import adcp

EXPECTED_SPEC_VERSION = "3.1.1"


def test_adcp_spec_version_matches_pin() -> None:
    """Verify SDK pin targets the spec version this codebase expects.

    Failure here means the adcp Python SDK pin in pyproject.toml has shifted
    to a version that targets a different AdCP spec version. Either revert
    the pin or follow docs/adcp-spec-version.md to update
    EXPECTED_SPEC_VERSION and the related references it lists.
    """
    actual = adcp.get_adcp_spec_version()
    assert actual == EXPECTED_SPEC_VERSION, (
        f"adcp SDK targets spec {actual}, but this codebase expects "
        f"{EXPECTED_SPEC_VERSION}. See docs/adcp-spec-version.md for "
        f"reconciliation steps."
    )
