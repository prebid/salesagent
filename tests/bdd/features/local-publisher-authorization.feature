# A publisher's adagents.json authorizes THIS agent when an entry names this agent's
# origin or an endpoint this agent serves, compared under the spec's canonicalization.
#
# PINNED AUTHORITY (adcp==6.6.0, AdCP 3.1.1 -- docs/adcp-spec-version.md).
#   docs/governance/property/adagents.mdx: authorized_agents[].url is the "Agent's API
#   endpoint URL", and docs/brand-protocol/seller-setup.mdx authorizes
#   https://ads.streamhaus.example/mcp -- so a publisher may name this agent's MCP or A2A
#   endpoint rather than its bare origin.
#   docs/reference/url-canonicalization.mdx :7 makes authorized_agents[].url one of the
#   surfaces its algorithm governs, and the algorithm decides each Examples row below:
#   step 1 keeps the scheme (http:// and https:// "MUST NOT match"; :67 repeats it for
#   adagents.json), step 2 lowercases the host, step 4 strips :443 and keeps :8443,
#   step 5 keeps the path, step 7 keeps the query.
#   Ungraded by the conformance storyboards: no storyboard grades a seller reading a
#   publisher's file about itself.
#
# WHO OBSERVES IT. No AdCP tool reads the outcome. The operator does, through three
# admin actions that each read the publisher's file: syncing publisher partners, verifying
# pending authorized properties, and opening a partner's properties. Each scenario drives
# the admin route, and every Then reads what that route wrote or answered.
#
# BOTH ADMIN TRANSPORTS. admin_integration drives the routes in process, where the one
# replaced piece is the publisher's origin (the SDK refuses to dial a private address,
# and an in-process origin has nothing else). e2e_admin drives them on the live stack,
# whose server dials a real TLS origin the runner serves on the stack's non-private
# subnet.
#
# SPELLINGS. "{origin}" is the tenant's agent_url, "{ORIGIN}" the same string upper-cased,
# "{host}" its host alone.
#
# AUTO-VERIFY. Each scenario's seller runs with PUBLISHER_AUTO_VERIFY=false, because when it
# is unset partner sync verifies every partner without reading its file anywhere but
# production. admin_integration sets the settings field; the e2e stack's servers are
# started with it.

@pubauth
Feature: A publisher's adagents.json authorizes this agent by its origin or an endpoint it serves

  Background:
    Given the seller does not auto-verify publisher partners
    And the publisher "pub.example" lists property "front_page" named "Front page" in its adagents.json

  @T-ADMIN-PUBAUTH-sync
  Scenario Outline: Partner sync reads the publisher's entry for this agent
    Given the tenant runs the "google_ad_manager" ad server
    And the tenant has unverified publisher partnerships with domains "pub.example"
    And the publisher "pub.example" authorizes "<entry>" for property "front_page"
    When the operator syncs publisher partners
    Then the partnership with "pub.example" is <partnership>
    And the tenant holds <properties> from "pub.example"

    Examples: entries naming this agent
      | entry            | partnership | properties              |
      | {origin}         | verified    | properties "Front page" |
      | {origin}/        | verified    | properties "Front page" |
      | {origin}/mcp     | verified    | properties "Front page" |
      | {origin}/mcp/    | verified    | properties "Front page" |
      | {origin}/a2a     | verified    | properties "Front page" |
      | {origin}/a2a/    | verified    | properties "Front page" |
      | {ORIGIN}/mcp/    | verified    | properties "Front page" |
      | {origin}:443/mcp | verified    | properties "Front page" |

    Examples: entries naming something else
      | entry                                | partnership | properties    |
      | http://{host}                        | refused     | no properties |
      | http://{host}/mcp                    | refused     | no properties |
      | {origin}:8443/mcp                    | refused     | no properties |
      | {origin}/api                         | refused     | no properties |
      | {origin}/mcp?x=1                     | refused     | no properties |
      | https://other-seller.example.com/mcp | refused     | no properties |

  # A mock tenant's partners are verified without reading the file, and a file that gives
  # it nothing leaves it the one fallback property the sync names after the domain. What
  # the entry decides is whether the file's own property is taken.
  @T-ADMIN-PUBAUTH-sync-mock
  Scenario Outline: A mock tenant's partner sync takes the publisher's properties only for an entry naming this agent
    Given the tenant runs the "mock" ad server
    And the tenant has unverified publisher partnerships with domains "pub.example"
    And the publisher "pub.example" authorizes "<entry>" for property "front_page"
    When the operator syncs publisher partners
    Then the tenant holds <properties> from "pub.example"

    Examples:
      | entry                                | properties              |
      | {origin}/mcp/                        | properties "Front page" |
      | {origin}/a2a                         | properties "Front page" |
      | http://{host}/mcp                    | the fallback property   |
      | https://other-seller.example.com/mcp | the fallback property   |

  @T-ADMIN-PUBAUTH-verify
  Scenario Outline: Property verification reads the publisher's entry for this agent
    Given the tenant has authorized property "front_page" of "pub.example" pending verification
    And the publisher "pub.example" authorizes "<entry>" for property "front_page"
    When the operator verifies the pending authorized properties
    Then authorized property "front_page" is <status>

    Examples:
      | entry                                | status   |
      | {origin}                             | verified |
      | {origin}/mcp/                        | verified |
      | {origin}/a2a                         | verified |
      | http://{host}/mcp                    | failed   |
      | https://other-seller.example.com/mcp | failed   |

  @T-ADMIN-PUBAUTH-view
  Scenario Outline: The partner's properties view reads the publisher's entry for this agent
    Given the tenant has unverified publisher partnerships with domains "pub.example"
    And the publisher "pub.example" authorizes "<entry>" for property "front_page"
    When the operator opens the properties of the partnership with "pub.example"
    Then the view reports this agent <view>

    Examples:
      | entry                                | view                        |
      | {origin}                             | authorized for "front_page" |
      | {origin}/mcp/                        | authorized for "front_page" |
      | {origin}/a2a                         | authorized for "front_page" |
      | http://{host}/mcp                    | not authorized              |
      | https://other-seller.example.com/mcp | not authorized              |
