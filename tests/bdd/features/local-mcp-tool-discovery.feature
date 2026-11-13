# tools/list publishes each tool's request schema as the registry declares it.
#
# WHY LOCAL. AdCP grades which top-level fields a tool's inputSchema declares
# (runner-output-contract.yaml, input_schema_field_stripped), not the schema's form. The form is
# fixed by MCP and by the seller's own publishing policy:
#
#   MCP 2025-11-25 server/tools.mdx: inputSchema is a JSON Schema object, "Defaults to 2020-12
#   if no `$schema` field is present", and "MUST be a valid JSON Schema object".
#   MCP 2025-11-25 basic/index.mdx: "Clients and servers MUST support JSON Schema 2020-12 for
#   schemas without an explicit `$schema` field". 2020-12 defines `$defs` and `$ref`, so a
#   listing that keeps them is one every conforming client can load.
#   AdCP 3.1.1 docs/building/by-layer/L0/schemas.mdx: the inlined ("bundled") form exists
#   "For tools that don't support `$ref` resolution"; the `$ref` form is the primary one.
#
# This seller publishes each tool's request DTO JSON Schema (RegistryTool,
# docs/development/building-tools.md). The scenario grades that the listing IS that document.
#
# MCP ONLY. tools/list is MCP's own discovery frame and exists on no other transport.

@mcptools
Feature: tools/list publishes each tool's request schema as the registry declares it

  Background:
    Given a Seller Agent is operational and accepting requests

  @T-MCPTOOLS-refs-kept
  Scenario: tools/list publishes each tool's request schema with its definitions intact
    When the buyer lists the seller's MCP tools
    Then each listed input schema is the tool's request model JSON Schema
