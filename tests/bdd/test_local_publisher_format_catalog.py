"""BDD binding for the locally-added publisher-format-catalog feature (RFC S3).

Grades that the adagents.json this seller serves at its own host publishes its reusable
publisher format options as ``formats[]``, and that a product's publisher option resolves in
the ``formats[]`` of the publisher it names (canonical-formats-migration.mdx § Seller
migration, step 3; adagents.json#/properties/formats).

Local rather than a BR-* storyboard: adagents.json is not a tool, and no conformance
storyboard grades this seller publishing one.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-publisher-format-catalog.feature")
