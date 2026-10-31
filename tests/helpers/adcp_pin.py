"""The AdCP spec version, read from the SDK that declares it.

pyproject.toml declares which adcp wheel we install. That wheel declares which
AdCP version it implements. Those are the only two facts, and the second is read
here rather than copied.

Nothing is asserted about the value. There is no second declaration for it to
agree with, which is the point: a pin bump is a one-line change to the
``adcp==`` line in ``pyproject.toml`` and everything downstream follows.
"""

from __future__ import annotations

from adcp import get_adcp_spec_version

#: The AdCP spec version the installed ``adcp`` wheel implements.
EXPECTED_SPEC_VERSION: str = get_adcp_spec_version()

#: The same value in the git-tag form the upstream spec repository uses, for the
#: vendored trees that are fetched at a tag.
SPEC_REV: str = f"v{EXPECTED_SPEC_VERSION}"
