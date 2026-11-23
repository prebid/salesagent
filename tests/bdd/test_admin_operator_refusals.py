"""BDD scenario binding for BR-ADMIN-OPERATOR-REFUSALS (#2571).

Expected refusals answered by the admin app's error handler, driven through
``AdminAccountEnv`` on both admin transports. Step definitions are in
tests/bdd/steps/domain/admin_operator_refusals.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-OPERATOR-REFUSALS.feature")
