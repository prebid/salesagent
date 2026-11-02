"""BDD binding for the locally-added context-echo feature.

Grades the one obligation the pinned ``core/protocol-envelope.json`` puts on
every response — the buyer's ``context`` object comes back unchanged — across
the five outcomes a seller can produce: a success, a refusal by its own rules,
a schema rejection, a version rejection and an auth rejection.

Retire together with the local feature once adcp-req grows the equivalent
storyboard scenarios; the upstream gap is named in the feature's header.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-context-echo-every-outcome.feature")
