# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# SUBJECT. What a buyer gets back when the seller cannot reach a tool at all: a
# document that is not a request, or a name that is not a tool. Two layers
# answer those, and every scenario says which one it grades.
#
# 1. A body the seller reads but no request model accepts -- not a JSON object,
#    or not JSON at all -- is an AdCP outcome. ``validated_request``
#    (src/core/tools/_boundary.py) refuses it through the one failure path every
#    other refusal takes, so it is recorded, carries the required ``status``,
#    and answers INVALID_REQUEST: pinned enums/error-code.json classifies it as
#    "malformed, missing required fields, or violates schema constraints",
#    recovery ``correctable``.
# 2. A message the TRANSPORT cannot route -- a name the registry does not know,
#    an A2A message naming no skill or two -- names no tool, so there is no
#    AdCP response to build and the transport's own protocol answers. On A2A
#    that is JSON-RPC 2.0 §5.1: ``-32600 Invalid Request`` for a message that
#    is not an invocation, ``-32601 Method not found`` for a skill the card does
#    not carry (the codes are the a2a SDK's own ``JSON_RPC_ERROR_CODE_MAP``). On
#    REST it is HTTP 404. On MCP it is the server's tool error. These scenarios
#    pin that such a refusal carries NO AdCP body: the pinned response schemas
#    describe answers to a named tool, and none exists here.
#
# ONE TRANSPORT PER SCENARIO. The refused shape IS the transport's own frame --
# an HTTP body, a JSON-RPC message, an MCP arguments object -- so one sentence
# cannot mean the same bytes on all three. Each scenario carries its transport
# tag (tests/bdd/conftest.py ``_SINGLE_TRANSPORT_TAGS``), and the transport it
# grades is visible in the test id. MCP has no "not an object" scenario: the
# MCP protocol types ``arguments`` as an object and the client refuses to send
# anything else, so the seller never sees that shape.
#
# The transport-side faults that share the same failure builder -- an exception
# raised while a transport reads its headers or frames its answer -- have no
# natural trigger a buyer can send, and are graded by unit tests instead
# (tests/integration/test_prkv8_untyped_exception_wire_leak.py for A2A).
Feature: A request that cannot reach a tool is refused where it fails (local)

  # ── Malformed bodies: refused by the request model, answered as AdCP ──
  # Routed to ProductEnv by the @predispatch tag (tests/bdd/conftest.py).

  @T-PREDISPATCH-rest-not-json @predispatch @predispatch-rest @error
  Scenario: A REST body that is not JSON is refused as malformed
    # The HTTP status is REST's own failure marker, and it is the code's own:
    # INVALID_REQUEST is 400 in the repo's error table (src/core/errors/codes.py).
    Given the buyer sends bytes that are not JSON as the request body
    When the Buyer Agent sends the document as written
    Then the response arrives
    And the response contains error code INVALID_REQUEST
    And the error recovery should be "correctable"
    And the HTTP status is 400

  @T-PREDISPATCH-rest-list-body @predispatch @predispatch-rest @error
  Scenario: A REST body that is a JSON list is refused as malformed
    Given the buyer sends a JSON list as the request body
    When the Buyer Agent sends the document as written
    Then the response arrives
    And the response contains error code INVALID_REQUEST
    And the error recovery should be "correctable"
    And the HTTP status is 400

  @T-PREDISPATCH-a2a-list-input @predispatch @predispatch-a2a @error
  Scenario: An A2A skill input that is a JSON list is refused as malformed
    Given the buyer sends a JSON list as the request body
    When the Buyer Agent sends the document as written
    Then the response arrives
    And the response contains error code INVALID_REQUEST
    And the error recovery should be "correctable"

  # ── Mistaken routing: refused by the transport, no AdCP body ──────────

  @T-PREDISPATCH-a2a-no-skill @predispatch @predispatch-a2a @error
  Scenario: An A2A message that names no skill is refused by the A2A layer
    Given the buyer's A2A message names no skill
    When the Buyer Agent sends the document as written
    Then the transport refuses the message with JSON-RPC error -32600
    And no AdCP response was produced

  @T-PREDISPATCH-a2a-two-skills @predispatch @predispatch-a2a @error
  Scenario: An A2A message that names two skills is refused whole by the A2A layer
    # The pinned 3.1.1 text describes an A2A invocation as one DataPart naming
    # one skill and says nothing about batching; a message naming two is
    # malformed rather than a batch, and neither skill runs.
    Given the buyer's A2A message names two skills
    When the Buyer Agent sends the document as written
    Then the transport refuses the message with JSON-RPC error -32600
    And no AdCP response was produced

  @T-PREDISPATCH-a2a-unknown-skill @predispatch @predispatch-a2a @error
  Scenario: An A2A message naming a skill the seller does not have is refused by the A2A layer
    Given the buyer names a tool the seller does not have
    When the Buyer Agent sends the document as written
    Then the transport refuses the message with JSON-RPC error -32601
    And no AdCP response was produced

  @T-PREDISPATCH-rest-unknown-route @predispatch @predispatch-rest @error
  Scenario: A REST request to a route the seller does not have is not found
    Given the buyer names a tool the seller does not have
    When the Buyer Agent sends the document as written
    Then the route is not found
    And no AdCP response was produced

  @T-PREDISPATCH-mcp-unknown-tool @predispatch @predispatch-mcp @error
  Scenario: An MCP call to a tool the seller does not have is refused by the MCP server
    Given the buyer names a tool the seller does not have
    When the Buyer Agent sends the document as written
    Then the MCP server reports the tool unknown
    And no AdCP response was produced
