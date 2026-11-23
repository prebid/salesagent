"""BDD scenario binding for BR-ADMIN-UNEXPECTED-ERRORS (#2571).

Unexpected failures answered by the admin app's error handler, driven through
``AdminAccountEnv`` on both admin transports. Step definitions are in
tests/bdd/steps/domain/admin_unexpected_errors.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-UNEXPECTED-ERRORS.feature")
