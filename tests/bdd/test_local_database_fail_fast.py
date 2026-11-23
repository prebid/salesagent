"""BDD binding for the locally-added database fail-fast feature.

Grades that a statement the database cancels or a connection it drops fails one request
and no other, and that a connect the database never answers still makes the next request
fail fast. Transport parametrization is conftest's: the fail-fast sits below every
transport, so each must observe the same blast radius.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-database-fail-fast.feature")
