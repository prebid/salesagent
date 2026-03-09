# Architecture Proposal: Go Deterministic Core + Python Agentic Sidecar

**Status**: Internal discussion draft — not for public distribution
**Date**: 2026-02-23
**Author**: Konstantin Mirin
**Context**: Prebid Sales Agent / AdCP Reference Implementation

---

## Executive Summary

The Prebid Sales Agent is a 95K LOC Python system where **92.6% of code is deterministic** — protocol handling, schema validation, database CRUD, adapter translation, and admin UI. Only 5-7% performs agentic work (product ranking, creative review, naming generation). This proposal advocates decomposing the system into:

1. **Go Core Server** — All deterministic logic: MCP/A2A/REST protocol handling, AdCP schema enforcement, business rules, database operations, adapter layer, admin API
2. **Python Agentic Microservice** — Isolated, sandboxed: LLM orchestration, recommendation engines, creative review, negotiation (future)

The Go core handles 100% of external traffic. Python is internal-only, pre-authorized, and stateless.

---

## Part 1: Current Architecture

### Deployment Topology

```
nginx (:8000)
  /mcp/*    →  adcp-server (:8080)   [FastMCP, Python]
  /a2a/*    →  adcp-server (:8091)   [Starlette/A2A SDK, Python]
  /admin/*  →  admin-ui (:8001)      [Flask, Python]
  /api/*    →  admin-ui (:8001)      [Flask REST, Python]
```

Two Python processes. Three frameworks (FastMCP, Starlette, Flask). No shared middleware.

### Request Flow

```
External Client (AI Agent / Programmatic)
    │
    ▼
nginx (path routing, TLS termination)
    │
    ├─── /mcp/* ──→ FastMCP Server
    │                  │
    │                  ▼
    │              @mcp.tool decorator
    │                  │
    │                  ▼
    │              _tool_impl() ──→ Pydantic validation
    │                  │              │
    │                  │              ▼
    │                  │          get_adapter()
    │                  │              │
    │                  ▼              ▼
    │              Database ←──→ AdServerAdapter
    │              (PostgreSQL)      │
    │                               ▼
    │                          GAM / Kevel / etc.
    │
    ├─── /a2a/* ──→ A2A RPC Server (hand-rolled dispatch)
    │                  │
    │                  ▼
    │              500+ lines of manual routing, auth,
    │              validation, serialization per handler
    │                  │
    │                  ▼
    │              Same _tool_impl() functions
    │
    └─── /admin/* ──→ Flask (separate process)
                       │
                       ▼
                   OAuth + CRUD forms
```

### The Three-Transport Problem (Issue #1050)

Every handler reimplements framework concerns:

| Capability              | MCP (FastMCP)  | A2A               | Admin (Flask)  |
|-------------------------|----------------|--------------------|----------------|
| Routing                 | @mcp.tool      | Hand-rolled        | @app.route     |
| Request validation      | TypeAdapter    | Manual per-handler | Manual         |
| Response serialization  | to_jsonable_py | model_dump() calls | jsonify        |
| Error handling          | ToolError exc  | Dict returns       | abort()        |
| Auth/DI                 | Context inject | Manual per-handler | Decorators     |
| Middleware              | Middleware API | None               | before_request |

Adding a REST API (which AdCP promises) would mean a **fourth reimplementation**.

### Code Distribution

| Component | LOC | % | Nature |
|-----------|-----|---|--------|
| Core (protocol, auth, orchestration) | 31,063 | 32.7% | Deterministic |
| Adapters (GAM, Kevel, Mock, etc.) | 23,648 | 24.9% | Deterministic |
| Admin UI (Flask) | 23,341 | 24.6% | Deterministic |
| Services (business logic) | 13,906 | 14.6% | Mostly deterministic |
| A2A Server (RPC gateway) | 2,701 | 2.8% | Deterministic |
| Landing pages | 358 | 0.4% | Deterministic |
| **AI/Agentic code** | **~3,000-5,000** | **~5%** | **LLM-dependent** |

### What the Agentic Code Actually Does

Only 4 agents exist, all single-turn (no iterative loops):

1. **NamingAgent** (~150 LOC) — Generates campaign/line item names from templates
2. **RankingAgent** (~200 LOC) — Scores products by relevance to advertiser brief
3. **ReviewAgent** (~200 LOC) — Creative brand safety review with confidence scoring
4. **PolicyCheckAgent** (~150 LOC) — Brief compliance against advertising policies

All have deterministic fallbacks:
```python
if ai_enabled and has_policy:
    confidence = review_agent.review_creative(...)
    if confidence > auto_approve_threshold:
        approve()
    else:
        human_review_required = True  # fallback
else:
    human_review_required = True      # AI disabled = human review
```

**None are on the critical path.** The system works without them.

### Test Coverage

- 84,051 LOC of tests (47% test-to-code ratio)
- 2,181+ unit tests
- 11 pre-commit hooks
- `make quality` gate: ruff format + lint + mypy + pytest

---

## Part 2: The Case for Go

### Why the Current Architecture Has a Ceiling

1. **Performance**: Python's GIL limits concurrent request handling. At 5,000 RPS, Go gateways show ~4ms p95 vs Python's ~5,789ms p95 (2026 benchmarks from dasroot.net for LLM gateway workloads — not inference, but orchestration/routing).

2. **Dependency bloat**: FastMCP, Pydantic, SQLAlchemy, Flask, Starlette, python-a2a — all loaded in every request path. Even deterministic endpoints pay the import tax. (See the DSPy problem: prompt optimization library loaded for user profile requests.)

3. **Type safety is runtime only**: Pydantic catches schema violations at runtime. Go structs catch them at compile time. For a protocol reference implementation, compile-time guarantees matter.

4. **Three frameworks, no unification**: Issue #1050 proposes FastAPI as a unified framework. That solves the duplication problem but doesn't solve the performance or type safety problems. It's the right step within Python, but doesn't address the fundamental mismatch.

5. **AdCP is a protocol, not an AI system**: Protocol servers should be fast, reliable, and boring. The interesting work (negotiation, recommendations, optimization) is a layer on top.

### Why Go Specifically

| Criteria | Go | Rust | TypeScript | Elixir |
|----------|-----|------|------------|--------|
| **Prebid ecosystem** | prebid-server is Go | None | Prebid.js (client) | None |
| **Contributor pool** | Large, growing | Smaller | Different community | Small |
| **Deployment** | Single binary | Single binary | Node runtime | BEAM VM |
| **Concurrency** | Goroutines | async (complex) | Event loop | BEAM (overkill) |
| **Ad tech precedent** | Strong (PubMatic, Prebid) | Growing | Common | Rare |
| **MCP SDK** | Official v1.x stable | Community | Official | None |
| **JSON handling** | Struct tags | Serde (verbose) | Native | Jason |
| **Compile-time types** | Yes | Yes | Partial (TS) | No |

**Go wins on pragmatism**: Prebid community knows it, the MCP SDK is official and stable, single binary deployment, goroutines map naturally to concurrent ad requests.

### Industry Context (Honest Assessment)

**Note on research quality**: Most "Go + AI" references found are press releases or marketing material, not technical architectures. We evaluated them critically:

#### What's actually technically substantive

**Reddit: Python → Go Migration (Comments System)** — Real engineering case study with technical detail.
- Migrated their highest-write-throughput model from Python to Go microservices
- p99 latency cut in half
- Used "sister datastores" pattern (Postgres, Memcached, Redis mirrors) for zero-downtime migration
- Key warning: "Avoid rewriting in a new language during the same migration" — behavior differences are hard to debug
- [bytebytego.com](https://blog.bytebytego.com/p/how-reddit-migrated-comments-functionality)

**Eli Bendersky (Google): ML in Go with a Python Sidecar** — Reference architecture with measured latencies.
- Documented the exact pattern we're proposing: Go core calling Python for ML/inference
- Measured IPC latencies: HTTP ~0.35ms, UDS ~10μs, gRPC ~0.1ms
- Key insight: when inference takes seconds, transport overhead is irrelevant. Optimize later.
- [eli.thegreenplace.net](https://eli.thegreenplace.net/2024/ml-in-go-with-a-python-sidecar/)

#### What exists but lacks technical depth

**Uber GenAI Gateway** — Go inference gateway, but fundamentally an infrastructure play (like a private Amazon Bedrock), not an agentic architecture. The blog post describes routing LLM calls to different providers with PII redaction. Useful for the "Go handles high-throughput routing" data point, but the agentic design complexity is zero — it's summarization of support chats. [uber.com/blog/genai-gateway](https://www.uber.com/blog/genai-gateway/)

**PubMatic AgenticOS** — Press release only. Claims three-layer architecture (infrastructure → application → transaction) with AdCP/MCP support, but no technical implementation details published. Interesting as a signal that ad tech is moving in this direction, not as an architecture reference. [pubmatic.com](https://pubmatic.com/news/pubmatic-launches-agenticos-the-operating-system-for-agent-to-agent-advertising/)

#### What's interesting but not our path

**ByteDance Eino Framework** (9.5k GitHub stars) — Go-first agentic framework powering Douyin and Doubao. Proves Go *can* be the primary language for agentic apps at ByteDance scale. However, this is the "replace Python entirely" approach — viable when you have ByteDance's engineering army to maintain a proprietary Go agent framework. For our case:
- We need flexibility as the agentic surface evolves rapidly
- Framework is supported by essentially one company
- LangGraph/LangChain have orders of magnitude more community adoption for agent orchestration
- **The microservice separation (Go + Python sidecar) is architecturally superior to a monolith** even if you could do everything in Go — see next section

### Why Microservice Separation Is a Feature, Not a Compromise

The instinct might be "if Go can do agentic work (Eino proves it), why keep Python at all?" The answer: **fault isolation is an architectural advantage, not a deployment tax.**

**Stability guarantee**: If an LLM call crashes, hangs, or returns garbage, it happens in the Python process. The Go core — which serves the UI, handles protocol traffic, manages state — is completely unaffected. The user sees "AI recommendation unavailable, retrying..." not a 500 error on their dashboard.

**Resource isolation**: LLM calls are memory-hungry and unpredictable in latency (1-30 seconds). In a monolith, a burst of LLM requests starves deterministic paths. With separation, Go serves deterministic requests at consistent sub-millisecond latency regardless of what Python is doing.

**Deployment independence**: Ship Go fixes without touching the agent. Ship new agent capabilities without risking the core. Zero-replica Python = the system works, just without AI enhancements. This is impossible in a monolith.

**Team independence**: Go developers own the deterministic core. Python/ML developers own the agentic layer. Clean interface contract (gRPC/HTTP). No merge conflicts across concerns. The Python agentic layer can use whatever framework makes sense — LangGraph today, something else tomorrow — without Go knowing or caring.

**The real-world pattern**: Only a fraction of requests need agentic capabilities. The majority are deterministic. The architecture should reflect this reality — fast path for the majority, isolated slow path for the minority.

---

## Part 3: Proposed Architecture

### High-Level View

```
┌──────────────────────────────────────────────────────────────────┐
│                        Go Core Server                            │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ MCP Gateway  │  │ A2A Gateway   │  │ REST API (AdCP v1)     │ │
│  │ (Go MCP SDK) │  │ (A2A Go SDK)  │  │ (native, free)         │ │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬──────────────┘ │
│         │                │                      │                │
│  ┌──────┴────────────────┴──────────────────────┴──────────────┐ │
│  │              Unified Middleware Pipeline                      │ │
│  │  • Auth extraction (token → principal → tenant)              │ │
│  │  • AdCP schema validation (compile-time struct guarantees)   │ │
│  │  • Version compatibility transforms                         │ │
│  │  • Request logging + OpenTelemetry tracing                  │ │
│  │  • Rate limiting                                            │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │                                    │
│  ┌──────────────────────────┴──────────────────────────────────┐ │
│  │              Business Rules Engine                           │ │
│  │  • Budget validation (remaining_budget - cost)              │ │
│  │  • Pricing model enforcement (CPM/VCPM/CPC/FLAT_RATE)      │ │
│  │  • Geographic targeting translation                         │ │
│  │  • Availability checking                                    │ │
│  │  • Workflow state machine                                   │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │                                    │
│  ┌──────────────────────────┴──────────────────────────────────┐ │
│  │              Adapter Layer (interface-based)                  │ │
│  │  • GoogleAdManager  • Kevel  • Triton  • Mock              │ │
│  │  • Each implements AdServerAdapter interface                 │ │
│  │  • Adapters translate AdCP ↔ platform API                   │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │                                    │
│  ┌──────────────────────────┴──────────────────────────────────┐ │
│  │              Data Layer                                      │ │
│  │  • PostgreSQL via pgx (or sqlc for type-safe queries)       │ │
│  │  • Tenant isolation at query level                          │ │
│  │  • Migration via golang-migrate                             │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              Agent Gateway (internal only)                   │ │
│  │  • Routes to Python sidecar when cognitive work needed      │ │
│  │  • Pre-authorized: Go decides WHEN to call, not Python     │ │
│  │  • Circuit breaker: if Python is down, fallback to human   │ │
│  │  • Transport: gRPC (type-safe) or HTTP (simpler)           │ │
│  └──────────────────────┬──────────────────────────────────────┘ │
└─────────────────────────┼────────────────────────────────────────┘
                          │ internal only (gRPC / HTTP)
                          │ never exposed externally
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│  Python Agentic Microservice (isolated, stateless)               │
│                                                                  │
│  ┌──────────────────────┴──────────────────────────────────────┐ │
│  │  Capabilities (current)                                      │ │
│  │  • Product ranking (brief → scored product list)            │ │
│  │  • Creative review (content → approval + confidence)        │ │
│  │  • Order naming (context → campaign name)                   │ │
│  │  • Policy checking (brief → compliance report)              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Capabilities (future, as agentic surface grows)             │ │
│  │  • Negotiation agent (multi-turn, counter-offers)           │ │
│  │  • Dynamic pricing optimization                             │ │
│  │  • Audience segment recommendations                         │ │
│  │  • Creative generation / variation                          │ │
│  │  • Campaign performance analysis                            │ │
│  │  • Cross-campaign budget optimization                       │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Properties:                                                     │
│  • NO database access (reads via Go API, writes via Go API)    │
│  • NO protocol awareness (doesn't know MCP/A2A/REST)           │
│  • Stateless (Go owns all state)                               │
│  • Multi-provider (Gemini, Claude, OpenAI — configured by Go)  │
│  • Independently scalable (0 replicas if AI disabled)          │
│  • Can be replaced without touching the core                   │
│  └──────────────────────────────────────────────────────────────┘
└──────────────────────────────────────────────────────────────────┘
```

### Key Design Properties

**1. The outer world never touches Python.**
Every request enters Go. Go handles auth, validation, routing, serialization, error formatting. Python is an internal implementation detail.

**2. The agent can't write directly.**
Python returns recommendations/decisions to Go, which validates and persists. This prevents the agentic layer from corrupting state even if the LLM produces unexpected output.

**3. Protocol handling is transport-agnostic in Go.**
One handler, three transports. This is what Issue #1050 proposed with FastAPI, but Go gives us this natively:

```go
// One implementation
func CreateMediaBuy(ctx context.Context, req *adcp.CreateMediaBuyRequest) (*adcp.CreateMediaBuyResponse, error) {
    // validate, business rules, adapter call, persist
    return response, nil
}

// Exposed via all transports automatically:
// - MCP: via Go MCP SDK tool registration
// - A2A: via A2A Go SDK skill registration
// - REST: via standard HTTP handler
```

**4. AdCP types are Go structs with compile-time guarantees.**
No more Pydantic runtime validation. Schema violations are caught at build time:

```go
type Product struct {
    ID              string          `json:"id"`
    Name            string          `json:"name"`
    PricingOptions  []PricingOption `json:"pricing_options"`
    AvailableStart  time.Time       `json:"available_start"`
    // Internal-only fields: not serialized
    internalConfig  map[string]any  `json:"-"`
}
```

**5. REST API comes for free.**
No fourth framework. Standard `net/http` or chi/echo/fiber handlers pointing at the same business logic.

**6. Single binary deployment.**
No pip, no virtualenv, no dependency resolution. `go build` → deploy.

### Go MCP Ecosystem (Ready for Production)

| Library | Stars | Status | Notes |
|---------|-------|--------|-------|
| **modelcontextprotocol/go-sdk** (official) | ~3.9k | v1.3.1 stable, compatibility guarantee | Maintained by Anthropic + Google |
| **mark3labs/mcp-go** (community) | ~8.2k | Mature, 1,307 importers | Pioneer, inspired the official SDK |
| **google/adk-go** (Agent Dev Kit) | — | Released Nov 2025 | Native MCP + A2A support |

Google is also proposing **gRPC as a native MCP transport** (spec issue #966), which would make Go MCP servers even more performant. Go has excellent native gRPC support.

### Communication Between Go and Python

| Method | Latency | When to Use |
|--------|---------|-------------|
| HTTP/REST | ~0.35ms/roundtrip | Default. When LLM calls take seconds, transport overhead is irrelevant |
| gRPC | ~0.1ms/roundtrip | When Python adds fast ML models (embeddings, classifiers) |
| Unix Domain Socket | ~10μs/roundtrip | Co-located containers, maximum performance |

**Recommendation**: Start with HTTP/REST. The LLM call dominates latency (1-30 seconds). Optimize the transport later if/when the agentic layer adds fast inference paths.

Reference: Eli Bendersky (Google) documented this exact pattern with measured latencies. ([eli.thegreenplace.net](https://eli.thegreenplace.net/2024/ml-in-go-with-a-python-sidecar/))

---

## Part 4: Future-Proofing the Agentic Layer

### The Core Concern

> "The concern that in the future this 10% may grow into 50% is very real, and actually, this is where it should go, but not at the expense of the performance or stability of the deterministic layer."

### How Production Systems Handle This

**CrewAI's "Deterministic Backbone" pattern** (deployed at DocuSign, US DoD, AB InBev):

> "The winning pattern uses a deterministic backbone dictating core logic with individual steps leveraging different levels of agents — from ad-hoc LLM calls to single agents to complete Crews."

The Go core is the backbone. It decides **when** and **whether** to invoke the agentic layer. The agentic layer can grow in capability without affecting the backbone's stability.

### Growth Path

```
Today (5% agentic):
  Go Core ──[HTTP]──→ Python: rank products, review creatives

Near-term (15% agentic):
  Go Core ──[HTTP]──→ Python: + negotiation, dynamic pricing, signal optimization
  Go Core: unchanged, same stability, same performance

Medium-term (30% agentic):
  Go Core ──[gRPC]──→ Python: + multi-agent workflows, campaign optimization
  Go Core ──[MCP]───→ Python: Python registers its own MCP tools for agent-to-agent
  Go Core: still owns all state, all protocol handling, all external interfaces

Long-term (50% agentic):
  Go Core ──[Agent Mesh]──→ Multiple Python services (specialized agents)
  Go Core: becomes the "agent gateway" — routing, auth, state, observability
  Python: multiple specialized microservices (negotiation, creative, analytics)
  Go Core: STILL stable, STILL handles 100% of external protocol traffic
```

### The Key Insight

**The agentic layer growing from 10% to 50% doesn't mean Python handles 50% of requests.** It means 50% of the *capabilities* involve LLM reasoning. But every request still enters Go, gets validated by Go, and has its state managed by Go.

Think of it as:
- **Go = the nervous system** — fast, reliable, handles all reflexes (protocol, validation, routing)
- **Python = the prefrontal cortex** — slower, smarter, handles reasoning when the nervous system delegates

The brain can grow without the nervous system losing its speed.

### Architectural Guardrails

1. **Python never owns state.** Go is the source of truth. Python gets read-only context and returns structured decisions.

2. **Python never handles protocols.** It doesn't know about MCP, A2A, or REST. It receives domain objects and returns domain objects.

3. **Go decides when to invoke Python.** The agent gateway has a circuit breaker. If Python is slow/down, the system degrades gracefully (human review, default rankings, template names).

4. **The contract between Go and Python is explicit.** gRPC protobuf or OpenAPI spec. If the agentic layer needs new data, the Go team must extend the contract. This prevents scope creep.

5. **Python is independently deployable.** Zero-replica = no AI features. One replica = basic AI. Multiple replicas = full agentic capabilities. Go is unaffected.

---

## Part 5: Migration Strategy

### Phase 0: Stabilize Python (Current — Issue #1050)

Complete the FastAPI unification proposed in Issue #1050. This:
- Solves the three-transport duplication
- Creates clean handler boundaries (`_impl()` functions become the Go port targets)
- Establishes the middleware pattern (auth, version compat, error handling)
- Makes the codebase easier to reason about before porting

**This is valuable regardless of the Go migration.** If the Go migration doesn't happen, the Python codebase is still better.

### Phase 1: Go Protocol Gateway (Proxy Mode)

Build a Go server that handles all protocol concerns and proxies business logic to Python:

```
External Client
    │
    ▼
Go Gateway (:8000)
    ├── MCP protocol handling (Go MCP SDK)
    ├── A2A protocol handling (A2A Go SDK)
    ├── REST endpoints (native Go)
    ├── Auth extraction & validation
    ├── Request logging & tracing
    │
    └──[HTTP proxy]──→ Python Backend (:8080)
                           └── Business logic (_impl functions)
                           └── Database layer
                           └── Adapter layer
```

**What this buys immediately:**
- Unified protocol handling (MCP + A2A + REST from one server)
- Go-speed auth and validation
- Single binary for the gateway
- REST API for free
- Python backend simplifies (no more protocol concerns)

**What stays in Python:**
- All business logic
- Database operations
- Adapter layer
- Agentic capabilities

### Phase 2: Port Deterministic Core to Go

Move business logic, database, and adapters to Go. Python shrinks to agentic-only:

```
Go Core Server (:8000)
    ├── Protocol gateways (from Phase 1)
    ├── Business rules engine (ported)
    ├── Database layer (pgx/sqlc)
    ├── Adapter layer (Go interfaces)
    ├── Admin API (ported)
    │
    └──[gRPC]──→ Python Agentic Sidecar
                     └── Product ranking
                     └── Creative review
                     └── Policy checking
                     └── (future) Negotiation
```

**Port order** (by dependency, lowest risk first):
1. Schema types (Go structs from AdCP spec)
2. Database models + migrations (pgx/sqlc + golang-migrate)
3. Auth module (token → principal → tenant)
4. Business rules (budget, pricing, targeting)
5. Adapter interface + Mock adapter (enables testing)
6. GAM adapter (most complex, do last)
7. Admin API endpoints

### Phase 3: Isolate & Scale the Agentic Layer

Python becomes a true microservice:
- gRPC contract (protobuf definition shared between Go and Python)
- Independent scaling (Kubernetes HPA on LLM latency metrics)
- Multi-agent support (negotiation crews, optimization agents)
- Provider flexibility (swap Gemini → Claude → OpenAI per tenant)

### Timeline Considerations

- **Phase 0** (FastAPI unification): Already proposed, could be done in current Python
- **Phase 1** (Go gateway): Can start in parallel with Phase 0. Low risk — pure proxy.
- **Phase 2** (Port core): Requires Phase 0 completion (clean _impl boundaries). Largest effort.
- **Phase 3** (Agentic isolation): Can start as soon as Phase 2 has the agent gateway wired.

Each phase delivers value independently. Can stop at any phase if priorities shift.

---

## Part 6: Risks & Mitigations

### Risk 1: "Avoid rewriting in a new language during the same migration" (Reddit's lesson)

**Mitigation**: Phase 1 is a proxy, not a rewrite. Phase 2 ports one module at a time with comprehensive test coverage already in place. The 84K LOC test suite serves as the behavioral specification.

### Risk 2: AdCP Go library doesn't exist

**Mitigation**: The AdCP types are Pydantic models extending a Python library. The Go equivalent is straightforward struct definitions with json tags. The types are well-specified by the protocol. This is a one-time porting effort, not ongoing maintenance.

### Risk 3: Two-language operational complexity

**Mitigation**: Go binary + Python container is simpler than the current setup (nginx + two Python processes + three frameworks). Observability: both Go and Python have mature OpenTelemetry SDKs. The key is distributed tracing across the boundary (propagate trace context in gRPC/HTTP headers).

### Risk 4: Hiring / contributor friction

**Mitigation**: Prebid already has Go contributors (prebid-server). The Python agentic layer is small and self-contained — a Python developer doesn't need to know Go, and vice versa. The boundary is explicit.

### Risk 5: The agentic surface grows faster than expected

**Mitigation**: The architectural guardrails (Section 4) ensure that growth in agentic capabilities doesn't affect the Go core's stability. Python can scale independently. The Go core remains the stable substrate.

### Risk 6: Go MCP ecosystem immaturity

**Mitigation**: The official MCP Go SDK (maintained by Anthropic + Google) reached v1.0 with a formal compatibility guarantee. It's not experimental. Additionally, Google's gRPC transport proposal for MCP would further strengthen Go's position.

---

## Part 7: References

### Technically Substantive References

| Reference | What It Demonstrates | Link |
|-----------|---------------------|------|
| **Reddit Python→Go** | Real migration case study, p99 halved, "sister datastores" pattern | [bytebytego.com](https://blog.bytebytego.com/p/how-reddit-migrated-comments-functionality) |
| **Eli Bendersky (Google)** | Go+Python sidecar with measured IPC latencies | [eli.thegreenplace.net](https://eli.thegreenplace.net/2024/ml-in-go-with-a-python-sidecar/) |
| **CrewAI "Deterministic Backbone"** | Pattern: deterministic flow controller + agentic steps (DocuSign, US DoD) | [blog.crewai.com](https://blog.crewai.com/agentic-systems-with-crewai/) |
| **Google gRPC for MCP** | gRPC as native MCP transport, "agent mesh" concept | [cloud.google.com](https://cloud.google.com/blog/products/networking/grpc-as-a-native-transport-for-mcp) |

### Directional Signals (Not Technical References)

| Signal | What It Tells Us | Caveat |
|--------|-----------------|--------|
| **Uber GenAI Gateway** | Go is viable for high-throughput LLM routing | Infrastructure play, not agentic design |
| **PubMatic AgenticOS** | Ad tech industry moving toward Go + agentic layers | Press release only, no technical detail |
| **ByteDance Eino** | Go-first agentic is possible at massive scale | "Replace Python entirely" approach, single-company framework |

### Go MCP/Agent Ecosystem

| Project | Status | Notes |
|---------|--------|-------|
| [modelcontextprotocol/go-sdk](https://github.com/modelcontextprotocol/go-sdk) | v1.3.1 stable | Official, Anthropic + Google |
| [mark3labs/mcp-go](https://github.com/mark3labs/mcp-go) | 8.2k stars, 1,307 importers | Community pioneer |
| [google/adk-go](https://github.com/google/adk-go) | Released Nov 2025 | Agent Dev Kit with MCP + A2A |

### Performance Data (2026)

From dasroot.net benchmarks (LLM gateway workloads, not inference):

| RPS | Go (Bifrost) p95 | Python (LiteLLM) p95 | Factor |
|-----|-------------------|----------------------|--------|
| 5,000 | ~4ms | ~5,789ms | 1,447x |
| 10,000 | ~35ms | ~12,000ms | 343x |

**Caveat**: When the LLM call itself takes 1-30 seconds, gateway latency is irrelevant for that path. The Go advantage matters for deterministic paths (API calls, schema validation, database queries) that should never wait for Python.

### Key Architectural Patterns

- **"Deterministic Backbone"** (CrewAI, deployed at DocuSign/DoD/AB InBev): Deterministic flow controller invokes agents at specific steps. Go is the backbone; Python agents are capabilities.
- **"Python Sidecar"** (Eli Bendersky/Google): Go core + Python subprocess for ML. Measured IPC: HTTP ~0.35ms, gRPC ~0.1ms, UDS ~10μs. When LLM calls take seconds, transport is irrelevant.
- **"Agent Mesh"** (Google gRPC-for-MCP proposal): Evolution of service mesh where agents communicate over MCP/A2A with gRPC transport. Natural fit for Go.
- **"Sister Datastores"** (Reddit): During migration, create parallel datastores that mirror production. Only the new service writes. Zero-downtime migration pattern.

---

## Appendix A: prebid-server Alignment

prebid-server (Go) uses a modular architecture with a 7-stage hook system:

```
Entrypoint → RawAuctionRequest → ProcessedAuctionRequest →
BidderRequest (parallel) → RawBidderResponse (parallel) →
AllProcessedBidResponses → AuctionResponse
```

Bidder adapters implement the `adapters.Bidder` interface with `MakeRequests` and `MakeBids`.

**Alignment opportunity**: The salesagent Go core could share adapter interface patterns, configuration approaches, and potentially even hook/module infrastructure with prebid-server. This reduces cognitive load for Prebid contributors who work across both projects.

## Appendix B: Comparison with Issue #1050 (FastAPI Unification)

Issue #1050 proposes FastAPI as a unified Python framework. That proposal and this one are **complementary, not competing**:

| Concern | #1050 (FastAPI) | This Proposal (Go Core) |
|---------|-----------------|------------------------|
| Three-transport duplication | Solves via FastAPI | Solves via Go handlers |
| Performance | Marginal improvement | Orders of magnitude improvement |
| Type safety | Runtime (Pydantic) | Compile-time (Go structs) |
| REST API | Adds as fourth transport | Native, free |
| Deployment | Still two Python processes | Single binary + optional Python sidecar |
| Migration effort | Medium (refactor) | High (rewrite deterministic core) |
| Immediate value | High (cleaner codebase) | High (performance + simplicity) |

**Recommendation**: Do #1050 first. It creates clean `_impl()` boundaries that become the Go port targets. Then Phase 1 (Go gateway) can start in parallel.

## Appendix C: Open Questions for Team Discussion

1. **AdCP Go types**: Port the `adcp` Python library to Go, or define Go structs independently from the spec? (A Go `adcp` module could be published for the community.)

2. **Admin UI**: Keep Flask (mount via reverse proxy) or rewrite in Go (templ/htmx)? The admin UI is 23K LOC — significant effort to port, but also the simplest code (CRUD forms).

3. **Database migration strategy**: Run Go and Python against the same PostgreSQL during transition? Or use the "sister datastores" approach (Reddit)?

4. **Adapter porting priority**: GAM adapter is the most complex (1,778 LOC). Port it first (highest value) or last (highest risk)?

5. **Community contribution model**: If the Go core is a new repo, how do we structure the monorepo vs multi-repo? How do Python-only contributors participate?

6. **Testing**: Port the 84K LOC test suite to Go, or maintain a behavioral test suite that runs against both implementations during migration?

7. **Timeline vs. #1050**: Do we complete the FastAPI unification first (cleaner port targets) or start the Go gateway in parallel?
