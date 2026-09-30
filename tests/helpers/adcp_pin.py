"""The AdCP spec version this codebase is pinned to — one definition, shared.

This constant is read by two different suites: the unit pin-drift guard
(``tests/unit/test_adcp_spec_version.py``) and the e2e standalone schema
validation test (``tests/e2e/test_schema_validation_standalone.py``, which
asserts the loaded schema index's ``adcp_version`` agrees with the pin). It
lives here rather than in either test module because a test importing from a
sibling test module is a structural violation
(``tests/unit/test_architecture_no_cross_test_module_imports.py``) — suites are
collected independently, so such an import couples their collection order and
breaks when one suite is run alone.

See docs/adcp-spec-version.md for the bump procedure; changing this value alone
is not enough, that document lists every reference that must move with it.
"""

from __future__ import annotations

#: The AdCP spec version the pinned ``adcp`` SDK in pyproject.toml targets.
EXPECTED_SPEC_VERSION = "3.1.1"

#: The same pin in the git-tag form the upstream spec repository uses.
#:
#: Derived rather than written, so the tag and the version cannot disagree. Every site that
#: LOADS one of the vendored trees reads one of these two names instead of spelling a version
#: of its own: before this existed, ``_refresh.py`` alone spelled the pin eight times and
#: three readers spelled it four more, each able to drift from the pin independently.
#:
#: This is the tag-guarded pin, NOT ``adcp.get_adcp_spec_version()``. The two are different
#: authorities: the SDK call moves with pyproject.toml, while this value is what
#: ``tests/unit/test_adcp_spec_version.py`` guards against drift. The vendored trees are
#: fetched at a git TAG, so the tag-guarded constant is the correct authority for them.
SPEC_REV = f"v{EXPECTED_SPEC_VERSION}"
