# A publisher's adagents.json authorizes THIS agent when an entry names this agent's
# origin or one of the endpoints this agent PUBLISHES, compared under the spec's
# canonicalization and under nothing else. ONE spelling per endpoint, no tolerance.
#
# PINNED AUTHORITY (the pin is the authority -- docs/adcp-spec-version.md; read it with
# `uv run python -c "import adcp; print(adcp.get_adcp_spec_version())"`).
#   docs/governance/property/adagents.mdx: authorized_agents[].url is the "Agent's API
#   endpoint URL", and docs/brand-protocol/seller-setup.mdx authorizes
#   https://ads.streamhaus.example/mcp -- so a publisher may name this agent's MCP or A2A
#   endpoint rather than its bare origin. That example is also WHICH spelling we publish:
#   it writes /mcp, with no trailing slash, in both of the places a publisher copies from
#   (brand.json agents[].url and adagents.json authorized_agents[].url), so
#   AGENT_ENDPOINT_PATHS publishes /mcp and /a2a.
#
#   THERE IS NO TOLERANCE AROUND THAT SPELLING, and the pin says so twice.
#   core/authorized-agent-base.json, on authorized_agents[].url: callers "MUST
#   canonicalize both sides per the AdCP URL canonicalization rules, not byte-equality --
#   two URLs that differ only in case, default port, or percent-encoding of unreserved
#   characters are the same agent". That list is exhaustive and a trailing slash is not on
#   it. security.mdx's key-discovery step 5 closes it: "A trailing slash remains
#   significant under the shared algorithm, while scheme/host case, default ports, dot
#   segments, and percent-encoded unreserved characters normalize as specified there."
#   So {origin}/mcp/ names a DIFFERENT agent from the one we publish at {origin}/mcp, and
#   the refusal rows below are as much the contract as the verification rows.
#
#   docs/reference/url-canonicalization.mdx :7 makes authorized_agents[].url one of the
#   surfaces its algorithm governs, and the algorithm decides each Examples row below:
#   step 1 keeps the scheme (http:// and https:// "MUST NOT match"; :67 repeats it for
#   adagents.json), step 2 lowercases the host, step 4 strips :443 and keeps :8443,
#   step 5 keeps a non-empty path byte-for-byte and renders an empty one as "/",
#   step 7 keeps the query. Its closing sentence is what forbids a tolerance at all:
#   "After all eight steps, comparison is byte-for-byte. Implementations MUST NOT apply
#   additional transformations before comparison."
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
    And the tenant has registered publisher partnerships with domains "pub.example"
    And the publisher "pub.example" authorizes "<entry>" for property "front_page"
    When the operator syncs publisher partners
    Then the partnership with "pub.example" is <partnership>
    And the tenant holds <properties> from "pub.example"

    # The origin and the two published endpoints, plus the three equivalences
    # core/authorized-agent-base.json names: empty path renders as "/" (step 5), host
    # case folds (step 2), and the default port drops (step 4).
    Examples: entries naming this agent
      | entry            | partnership | properties              |
      | {origin}         | verified    | properties "Front page" |
      | {origin}/        | verified    | properties "Front page" |
      | {origin}/mcp     | verified    | properties "Front page" |
      | {origin}/a2a     | verified    | properties "Front page" |
      | {ORIGIN}/mcp     | verified    | properties "Front page" |
      | {origin}:443/mcp | verified    | properties "Front page" |

    # The first two rows are the tolerance that used to exist and no longer does: a
    # trailing slash on a non-empty path is a different canonical form, so an entry
    # naming {origin}/mcp/ authorizes some other agent, not the one published at
    # {origin}/mcp. The rest are the axes the algorithm keeps distinct for other
    # reasons -- scheme, non-default port, a path we do not publish, and a query.
    Examples: entries naming something else
      | entry                                | partnership | properties    |
      | {origin}/mcp/                        | refused     | no properties |
      | {origin}/a2a/                        | refused     | no properties |
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
    And the tenant has registered publisher partnerships with domains "pub.example"
    And the publisher "pub.example" authorizes "<entry>" for property "front_page"
    When the operator syncs publisher partners
    Then the tenant holds <properties> from "pub.example"

    Examples:
      | entry                                | properties              |
      | {origin}/mcp                         | properties "Front page" |
      | {origin}/a2a                         | properties "Front page" |
      | {origin}/mcp/                        | the fallback property   |
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
      | {origin}/mcp                         | verified |
      | {origin}/a2a                         | verified |
      | {origin}/mcp/                        | failed   |
      | http://{host}/mcp                    | failed   |
      | https://other-seller.example.com/mcp | failed   |

  @T-ADMIN-PUBAUTH-view
  Scenario Outline: The partner's properties view reads the publisher's entry for this agent
    Given the tenant has registered publisher partnerships with domains "pub.example"
    And the publisher "pub.example" authorizes "<entry>" for property "front_page"
    When the operator opens the properties of the partnership with "pub.example"
    Then the view reports this agent <view>

    Examples:
      | entry                                | view                        |
      | {origin}                             | authorized for "front_page" |
      | {origin}/mcp                         | authorized for "front_page" |
      | {origin}/a2a                         | authorized for "front_page" |
      | {origin}/mcp/                        | not authorized              |
      | http://{host}/mcp                    | not authorized              |
      | https://other-seller.example.com/mcp | not authorized              |
