"""BDD binding for the locally-added pre-dispatch refusals feature.

Grades what a buyer receives when the seller cannot reach a tool at all: a body no request
model accepts is refused as an AdCP INVALID_REQUEST through the boundary's one failure path,
and a message the transport cannot route is refused by the transport's own protocol with no
AdCP body. The authority for each is stated in the feature's header.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-pre-dispatch-refusals.feature")
