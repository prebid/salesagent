# Hand-authored feature — not compiled from adcp-req.
#
# The seller publishes its MCP endpoint as /mcp: security.mdx's key discovery compares a
# canonicalized agents[].url with the agent URL a counterparty holds, a trailing slash remains
# significant, and it specifies "no redirects". So a client holding what the seller publishes
# has to reach MCP at that string, and a client holding /mcp/ has to keep working.
#
# Graded on the real app with its lifespan running, because that is what installs the admin
# catch-all mount that answered a bare /mcp with a 404, and on e2e through the live stack. An
# app without the lifespan answers differently, which is how this got past the discovery tests.
#
# @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx

Feature: The MCP endpoint answers at the path this seller publishes

  @T-MCPPATH-both-paths @mcppath
  Scenario Outline: POST to the MCP endpoint reaches MCP with no redirect - <path>
    When a client posts an MCP initialize to "<path>"
    Then the answer is an MCP initialize result with no redirect

    Examples:
      | path  |
      | /mcp  |
      | /mcp/ |
