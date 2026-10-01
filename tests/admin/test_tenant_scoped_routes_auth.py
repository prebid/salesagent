"""In-process transport for the authorization contract of prebid/salesagent#2203.

The contract classes live in tests/helpers/admin_tenant_scoping_contract.py, which says
what each one grades. Importing them here collects them on the Flask ``test_client``
through the ``scoping_env`` fixture in tests/admin/conftest.py. The harness
(``tests/harness/admin_tenant_scoping.py``) logs in through ``/test/auth`` against a home
tenant that is never the target, so the decorator's test-mode bypass cannot grant and the
target tenant's ``User`` row is the only thing that decides; each rejection test fails if
the decorator is removed. Test data comes from factory-boy factories (``tests/CLAUDE.md``).
"""

from __future__ import annotations

import pytest

# Collected here, not just imported: pytest picks up every Test* class in the module namespace.
from tests.helpers.admin_tenant_scoping_contract import (  # noqa: F401
    TestActiveMemberUnchanged,
    TestAnonymousDenied,
    TestNonMemberDenied,
)

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]
