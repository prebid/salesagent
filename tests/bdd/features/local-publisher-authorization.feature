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
#   the refusal rows below are as much the contract as the authorization rows.
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
# WHO OBSERVES IT. No AdCP tool reads the outcome directly. The operator does: adding a
# publisher and syncing every publisher each fetch the publisher's file through the one
# ingest service (src/services/publisher_ingest.py), which stores it and derives the
# properties it authorizes this agent for; the publisher's properties view reads what was
# stored. Every Then reads what the ingest stored or what the view answered.
#
# BOTH ADMIN TRANSPORTS. admin_integration drives the routes in process, where the one
# replaced piece is the publisher's origin (the SDK refuses to dial a private address,
# and an in-process origin has nothing else). e2e_admin drives them on the live stack,
# whose server fetches the file itself, on 443, from the stack's publisher-origin through
# tls-proxy (tests/harness/publisher_authorization.py).
#
# SPELLINGS. "{origin}" is the tenant's agent_url, "{ORIGIN}" the same string upper-cased,
# "{host}" its host alone.

@pubauth
Feature: A publisher's adagents.json authorizes this agent by its origin or an endpoint it serves

  Background:
    Given the publisher "pub-psatest.com" lists property "front_page" named "Front page" in its adagents.json

  @T-ADMIN-PUBAUTH-add
  Scenario Outline: Adding a publisher ingests its entry for this agent
    Given the publisher "pub-psatest.com" authorizes "<entry>" for property "front_page"
    When the operator adds the publisher "pub-psatest.com"
    Then the seller stored the adagents.json of "pub-psatest.com"
    And the seller holds <properties> from "pub-psatest.com"

    # The origin and the two published endpoints, plus the three equivalences
    # core/authorized-agent-base.json names: empty path renders as "/" (step 5), host
    # case folds (step 2), and the default port drops (step 4).
    Examples: entries naming this agent
      | entry            | properties              |
      | {origin}         | properties "Front page" |
      | {origin}/        | properties "Front page" |
      | {origin}/mcp     | properties "Front page" |
      | {origin}/a2a     | properties "Front page" |
      | {ORIGIN}/mcp     | properties "Front page" |
      | {origin}:443/mcp | properties "Front page" |

    # The first two rows are the tolerance that used to exist and no longer does: a
    # trailing slash on a non-empty path is a different canonical form, so an entry
    # naming {origin}/mcp/ authorizes some other agent, not the one published at
    # {origin}/mcp. The rest are the axes the algorithm keeps distinct for other
    # reasons -- scheme, non-default port, a path we do not publish, and a query.
    Examples: entries naming something else
      | entry                                | properties    |
      | {origin}/mcp/                        | no properties |
      | {origin}/a2a/                        | no properties |
      | http://{host}                        | no properties |
      | http://{host}/mcp                    | no properties |
      | {origin}:8443/mcp                    | no properties |
      | {origin}/api                         | no properties |
      | {origin}/mcp?x=1                     | no properties |
      | https://other-seller.example.com/mcp | no properties |

  @T-ADMIN-PUBAUTH-sync
  Scenario Outline: Syncing publishers ingests each publisher's entry for this agent
    Given the tenant runs the "google_ad_manager" ad server
    And the tenant has unverified publisher partnerships with domains "pub-psatest.com"
    And the publisher "pub-psatest.com" authorizes "<entry>" for property "front_page"
    When the operator syncs publisher partners
    Then the seller stored the adagents.json of "pub-psatest.com"
    And the seller holds <properties> from "pub-psatest.com"

    Examples:
      | entry                                | properties              |
      | {origin}/mcp                         | properties "Front page" |
      | {origin}/a2a                         | properties "Front page" |
      | {origin}/mcp/                        | no properties           |
      | http://{host}/mcp                    | no properties           |
      | https://other-seller.example.com/mcp | no properties           |

  # The view reads the stored document and fetches nothing: the publisher's origin answers
  # 404 by the time the operator opens it.
  @T-ADMIN-PUBAUTH-view
  Scenario Outline: The publisher's properties view reads the stored entry for this agent
    Given the publisher "pub-psatest.com" authorizes "<entry>" for property "front_page"
    And the operator added the publisher "pub-psatest.com"
    And the publisher "pub-psatest.com" now answers 404
    When the operator opens the properties of the partnership with "pub-psatest.com"
    Then the view reports this agent <view>

    Examples:
      | entry                                | view                        |
      | {origin}                             | authorized for "front_page" |
      | {origin}/mcp                         | authorized for "front_page" |
      | {origin}/a2a                         | authorized for "front_page" |
      | {origin}/mcp/                        | not authorized              |
      | http://{host}/mcp                    | not authorized              |
      | https://other-seller.example.com/mcp | not authorized              |

  # The operator reads what the seller holds for each publisher: the outcome of the last
  # fetch, when the file was last fetched successfully, and what the stored file authorizes
  # this agent for and declares in formats[]. Nothing here is typed by the operator.
  @T-ADMIN-PUBAUTH-list
  Scenario: The publishers page shows each publisher's fetch status, last successful fetch, properties and format options
    Given the publisher "pub-psatest.com" authorizes "{origin}" for property "front_page"
    And the publisher "pub-psatest.com" declares format option {"format_option_id": "pub_mrec", "format_kind": "image", "params": {"width": 300, "height": 250}} in its adagents.json
    And the operator added the publisher "pub-psatest.com"
    And the publisher "news-psatest.com" now answers 404
    And the operator added the publisher "news-psatest.com"
    When the operator opens the publishers page
    Then the publishers page shows "pub-psatest.com" last fetched "ok" with 1 authorized property and 1 format option
    And the publishers page shows "news-psatest.com" never fetched, recording "not_found", with 0 authorized properties and 0 format options

  # Properties come only from publishers' files: the authorized-properties page lists what
  # the stored files authorize and offers no way to type a property in by hand.
  @T-ADMIN-PUBAUTH-properties-page
  Scenario: The authorized-properties page lists the properties publishers' files authorize and offers no way to add one by hand
    Given the publisher "pub-psatest.com" authorizes "{origin}" for property "front_page"
    And the operator added the publisher "pub-psatest.com"
    When the operator opens the authorized properties page
    Then the page contains "Front page"
    And the page offers no way to add a property by hand

  # A stored file counts for 24 hours after its last successful fetch (managed-networks.mdx
  # :623-626); past that its publisher authorizes nothing. The setup checklist tells the
  # operator why a publisher stopped authorizing, seeded and fetched publishers alike.
  @T-ADMIN-PUBAUTH-checklist-stale
  Scenario: The setup checklist says a publisher stopped authorizing because its stored file is more than 24 hours old
    Given the publisher "pub-psatest.com" authorizes "{origin}" for property "front_page"
    And the operator added the publisher "pub-psatest.com"
    And the seller's last successful fetch of "pub-psatest.com" was 25 hours ago
    When the operator opens the setup checklist
    Then the page contains "stopped authorizing this agent: their stored adagents.json is older than 24 hours"

  # WHO MAY CALL THE PUBLISHER ROUTES. Every scenario above signs the operator in as a super
  # admin, who passes every access check, so none of them would notice a route letting
  # anybody in. This one names the caller. require_tenant_access() guards all five routes
  # (src/admin/utils/helpers.py): a caller with no session is refused with 401, a user of
  # another tenant with 403 "Access denied", and an active member's role decides the rest.
  # Any member may read; adding, removing and syncing are writes, which role_permits gives
  # to a manager or an admin. The routes are the pages' script API, so the caller sends what
  # a client that is not a browser sends (no Accept, Origin or Sec-Fetch header) and every
  # refusal is JSON. A refused request leaves every publisher row, and every row derived from
  # one, as it found them.
  #
  # The write boundary refuses a publisher domain that is not a host name with an optional
  # port (is_publisher_domain, src/core/http_utils.py), and stores nothing.
  #
  # What follows the add makes each write observable: only an add stores
  # "partner-psatest.com", only a removal drops "pub-psatest.com", and only a sync stores
  # the "sports" property its file lists from now on.
  @T-ADMIN-PUBAUTH-access
  Scenario Outline: Only the tenant's own users reach the publisher routes, and a viewer may only read them
    Given the publisher "pub-psatest.com" authorizes "{origin}" for property "front_page"
    And the operator added the publisher "pub-psatest.com"
    And the publisher "pub-psatest.com" lists property "sports" named "Sports" in its adagents.json
    And the publisher "pub-psatest.com" authorizes "{origin}" for property "sports"
    And the caller is <caller>
    When the caller <request>
    Then the page returns status <status>
    And <answer>
    And <stored>

    Examples: a caller with no session
      | caller            | request                                                        | status | answer                                                     | stored                                       |
      | not authenticated | opens the publishers page                                      | 401    | the JSON response is the refusal "Authentication required" | the target tenant's stored data is unchanged |
      | not authenticated | adds the publisher "partner-psatest.com"                       | 401    | the JSON response is the refusal "Authentication required" | the target tenant's stored data is unchanged |
      | not authenticated | removes the publisher "pub-psatest.com"                        | 401    | the JSON response is the refusal "Authentication required" | the target tenant's stored data is unchanged |
      | not authenticated | syncs publisher partners                                       | 401    | the JSON response is the refusal "Authentication required" | the target tenant's stored data is unchanged |
      | not authenticated | opens the properties of the partnership with "pub-psatest.com" | 401    | the JSON response is the refusal "Authentication required" | the target tenant's stored data is unchanged |

    # An admin there, so only the membership check can refuse.
    Examples: a user of another tenant
      | caller                                 | request                                                        | status | answer                                           | stored                                       |
      | an active member of a different tenant | opens the publishers page                                      | 403    | the JSON response is the refusal "Access denied" | the target tenant's stored data is unchanged |
      | an active member of a different tenant | adds the publisher "partner-psatest.com"                       | 403    | the JSON response is the refusal "Access denied" | the target tenant's stored data is unchanged |
      | an active member of a different tenant | removes the publisher "pub-psatest.com"                        | 403    | the JSON response is the refusal "Access denied" | the target tenant's stored data is unchanged |
      | an active member of a different tenant | syncs publisher partners                                       | 403    | the JSON response is the refusal "Access denied" | the target tenant's stored data is unchanged |
      | an active member of a different tenant | opens the properties of the partnership with "pub-psatest.com" | 403    | the JSON response is the refusal "Access denied" | the target tenant's stored data is unchanged |

    Examples: a viewer of the tenant
      | caller                                                     | request                                                        | status | answer                                                                                                        | stored                                       |
      | an active member of the target tenant with the viewer role | adds the publisher "partner-psatest.com"                       | 403    | the JSON response is the refusal "Your role does not allow this"                                              | the target tenant's stored data is unchanged |
      | an active member of the target tenant with the viewer role | removes the publisher "pub-psatest.com"                        | 403    | the JSON response is the refusal "Your role does not allow this"                                              | the target tenant's stored data is unchanged |
      | an active member of the target tenant with the viewer role | syncs publisher partners                                       | 403    | the JSON response is the refusal "Your role does not allow this"                                              | the target tenant's stored data is unchanged |
      | an active member of the target tenant with the viewer role | opens the publishers page                                      | 200    | the publishers page shows "pub-psatest.com" last fetched "ok" with 1 authorized property and 0 format options | the target tenant's stored data is unchanged |
      | an active member of the target tenant with the viewer role | opens the properties of the partnership with "pub-psatest.com" | 200    | the view reports this agent authorized for "front_page"                                                       | the target tenant's stored data is unchanged |

    # The least role that may write.
    Examples: a manager of the tenant
      | caller                                                      | request                                  | status | answer                                                              | stored                                                                    |
      | an active member of the target tenant with the manager role | adds the publisher "partner-psatest.com" | 201    | the JSON response has "message" as "Publisher added"                | the seller holds the publisher "partner-psatest.com"                      |
      | an active member of the target tenant with the manager role | removes the publisher "pub-psatest.com"  | 200    | the JSON response has "message" as "Publisher deleted successfully" | the seller holds no publisher "pub-psatest.com"                           |
      | an active member of the target tenant with the manager role | syncs publisher partners                 | 200    | the JSON response has "message" as "Sync completed"                 | the seller holds properties "Front page", "Sports" from "pub-psatest.com" |

    Examples: a publisher domain that is not a host name
      | caller                                                      | request                                           | status | answer                                                                                                                            | stored                                       |
      | an active member of the target tenant with the manager role | adds the publisher "<img src=x onerror=alert(1)>" | 400    | the JSON response is the refusal "Enter a domain name such as news.example.com; enter an internationalized name in its xn-- form" | the target tenant's stored data is unchanged |
      | an active member of the target tenant with the manager role | adds the publisher "a'b.example.com"              | 400    | the JSON response is the refusal "Enter a domain name such as news.example.com; enter an internationalized name in its xn-- form" | the target tenant's stored data is unchanged |
      | an active member of the target tenant with the manager role | adds the publisher "a"b.example.com"              | 400    | the JSON response is the refusal "Enter a domain name such as news.example.com; enter an internationalized name in its xn-- form" | the target tenant's stored data is unchanged |
      | an active member of the target tenant with the manager role | adds the publisher "two words.example.com"        | 400    | the JSON response is the refusal "Enter a domain name such as news.example.com; enter an internationalized name in its xn-- form" | the target tenant's stored data is unchanged |
