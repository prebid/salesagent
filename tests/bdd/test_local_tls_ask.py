"""BDD binding for the locally-added on-demand TLS gate feature.

Grades that ``GET /tls/ask`` allows a certificate for exactly the hosts this deployment
serves, and that it fails closed. Local rather than a BR-* storyboard: this is deployment
infrastructure, and no storyboard grades it.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-tls-ask.feature")
