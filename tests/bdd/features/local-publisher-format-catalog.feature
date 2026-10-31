# The seller publishes its reusable publisher format options in the adagents.json it serves at
# its own host, and a product names one by {publisher_domain, format_option_id} -- RFC S3.
#
# PINNED AUTHORITY (the pin is the authority -- docs/adcp-spec-version.md; read it with
# `uv run python -c "import adcp; print(adcp.get_adcp_spec_version())"`).
#   docs/creative/canonical-formats-migration.mdx § Seller migration, step 3: "Publish
#   reusable publisher options in /.well-known/adagents.json and reference them with
#   publisher_domain plus format_option_id."
#   static/schemas/source/adagents.json#/properties/formats: the "Publisher-authoritative
#   format catalog"; "Products selling this publisher's inventory SHOULD reference these
#   declarations by format_option_id ... via inline format_options whose format_option_id
#   matches an entry here".
#   docs/creative/formats.mdx § Discovery flow, step 2: "Resolve publisher-backed options from
#   the publisher's adagents.json.formats[] when needed" -- the buyer's resolution, which the
#   second scenario performs from the values on the wire.
#   Ungraded by the conformance storyboards: canonical_formats requires comply_test_controller,
#   which this seller does not include.
#
# WHY A LOCAL FEATURE. adagents.json is not a tool: it carries no envelope and is not a
# registry row, so it is fetched through the env (`fetch_agent_card`, which GETs any root
# document) the way the agent card is -- in process off the ASGI app the REST dispatcher uses,
# on e2e over HTTP against the live stack. get_products is dispatched on each transport.

@pubformats
Feature: The seller publishes its reusable publisher format options and its products reference them

  Background:
    Given a tenant is configured for product discovery

  @T-PUBFORMATS-published
  Scenario: The adagents.json the seller serves publishes its catalog as formats[]
    Given the seller's publisher format catalog declares option "homepage_mrec" of kind "image" at 300x250
    When the buyer fetches the adagents.json the seller serves at its own publisher domain
    Then the document's formats[] declares option "homepage_mrec" of kind "image" at 300x250
    And the document is valid against the pinned adagents.json schema

  @T-PUBFORMATS-no-catalog
  Scenario: A seller with no publisher format catalog publishes no formats[]
    Given the seller has no publisher format catalog
    When the buyer fetches the adagents.json the seller serves at its own publisher domain
    Then the document carries no formats[]

  @T-PUBFORMATS-reference-resolves
  Scenario: A product's publisher option resolves in the formats[] its publisher serves
    Given the seller's publisher format catalog declares option "homepage_mrec" of kind "image" at 300x250
    And the seller stores a product declaring publisher option "homepage_mrec" under its own publisher domain
    When the buyer requests products
    And the buyer fetches the adagents.json of the publisher the product's option names
    Then the product announces option "homepage_mrec" under the domain of the publisher it sells
    And that publisher's formats[] declares the product's option with the same format_kind
