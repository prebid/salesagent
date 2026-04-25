# A2A and MCP Agent Flows

This guide shows how the Prebid Sales Agent behaves on the protocol side for the three flows people usually ask about:
- buyer agent flows
- governance-related flows
- creative flows

The diagrams below are based on the current implementation in:
- `src/core/main.py`
- `src/core/mcp_auth_middleware.py`
- `src/a2a_server/adcp_a2a_server.py`
- `src/core/creative_agent_registry.py`
- `src/core/tools/creatives/`
- `src/services/ai/agents/`

## 1. Protocol Overview

```mermaid
flowchart LR
    Buyer["Buyer agent"]
    MCP["MCP endpoint\n/mcp"]
    A2A["A2A endpoint\n/a2a"]
    MW["Transport boundary\nidentity + context resolution"]
    Wrap["Wrapper / raw handler"]
    Impl["Shared _impl business logic"]
    Repo["Repositories / UoW"]
    DB["PostgreSQL"]
    Ext["External systems\nad server / creative agent / AI"]

    Buyer --> MCP
    Buyer --> A2A
    MCP --> MW
    A2A --> MW
    MW --> Wrap
    Wrap --> Impl
    Impl --> Repo
    Repo --> DB
    Impl --> Ext
```

### What changes by protocol

- `MCP`: `MCPAuthMiddleware` resolves identity once, stores `identity` and optional `context_id`, then the MCP tool wrapper calls the shared implementation.
- `A2A`: `AdCPRequestHandler` resolves identity once, builds task/context metadata, then dispatches the explicit skill handler to the corresponding raw function or shared implementation.
- `Shared core`: business behavior is intended to stay in `_impl` functions so MCP and A2A stay aligned.

## 2. Buyer Agent Flow

### Buyer flow across MCP and A2A

```mermaid
flowchart TD
    Buyer["Buyer agent"]

    subgraph Discovery["Discovery + planning"]
        Cap["get_adcp_capabilities"]
        Prod["get_products"]
        Props["list_authorized_properties"]
        Accts["list_accounts / sync_accounts"]
        Formats["list_creative_formats"]
    end

    subgraph Execution["Campaign + creative execution"]
        Create["create_media_buy"]
        Update["update_media_buy"]
        Buys["get_media_buys"]
        Delivery["get_media_buy_delivery"]
        Perf["update_performance_index"]
        SyncC["sync_creatives"]
        ListC["list_creatives"]
    end

    subgraph Protocols["Protocol entry points"]
        MCP["MCP tool call"]
        A2A["A2A message/send\nexplicit skill or natural language"]
    end

    subgraph Core["Shared sales-agent core"]
        Auth["Identity/context resolution"]
        Impl["Shared _impl logic"]
        Data["Repositories + adapters + DB"]
    end

    Buyer --> MCP
    Buyer --> A2A

    MCP --> Auth
    A2A --> Auth

    Auth --> Cap
    Auth --> Prod
    Auth --> Props
    Auth --> Accts
    Auth --> Formats
    Auth --> Create
    Auth --> Update
    Auth --> Buys
    Auth --> Delivery
    Auth --> Perf
    Auth --> SyncC
    Auth --> ListC

    Cap --> Impl
    Prod --> Impl
    Props --> Impl
    Accts --> Impl
    Formats --> Impl
    Create --> Impl
    Update --> Impl
    Buys --> Impl
    Delivery --> Impl
    Perf --> Impl
    SyncC --> Impl
    ListC --> Impl

    Impl --> Data
```

### Buyer-facing tools available now

These are the tools actually registered on the MCP server in `src/core/main.py` and used by the A2A server skill handlers:

| Area | Tools |
| --- | --- |
| Discovery | `get_adcp_capabilities`, `get_products`, `list_authorized_properties`, `list_accounts`, `sync_accounts`, `list_creative_formats` |
| Media buy lifecycle | `create_media_buy`, `update_media_buy`, `get_media_buys`, `get_media_buy_delivery`, `update_performance_index` |
| Creative library | `sync_creatives`, `list_creatives` |
| Task management | `list_tasks`, `get_task`, `complete_task` on MCP only |

### A2A note

The A2A agent card currently advertises a few extra skills such as `approve_creative`, `get_media_buy_status`, and `optimize_media_buy`, but those are not fully implemented in the current request handler path. For the diagrams above, the "available now" list reflects the working tool path rather than every advertised skill.

## 3. Governance Flow

### Current governance reality

The codebase has governance-related concepts, but not a full public governance protocol surface yet.

- `get_adcp_capabilities` reports `supported_protocols=["media_buy"]`
- `media_buy.features.content_standards` is currently `false`
- account `governance_agents` are stored and passed through, but there is no implemented governance MCP/A2A toolset yet
- governance today mainly shows up as internal policy checks and review workflows, not as a separate buyer-facing governance agent API

### Governance flow as implemented today

```mermaid
flowchart TD
    Buyer["Buyer agent"]
    Cap["get_adcp_capabilities"]
    Accounts["sync_accounts / list_accounts\npass through governance_agents metadata"]
    Sales["Prebid Sales Agent"]
    Policy["Policy agent\nsrc/services/ai/agents/policy_agent.py"]
    Review["Review agent\nsrc/services/ai/agents/review_agent.py"]
    Human["Human/admin review queue"]
    DB["Tenant config + accounts + creative reviews"]

    Buyer --> Cap
    Buyer --> Accounts

    Cap --> Sales
    Accounts --> Sales

    Sales --> DB
    Sales --> Policy
    Sales --> Review
    Review --> Human
    Policy --> Sales
    Human --> Sales
```

### Governance-related tools available per agent

| Agent | Tools available now |
| --- | --- |
| Buyer agent talking to sales agent | `get_adcp_capabilities`, `list_accounts`, `sync_accounts` |
| Sales agent public governance API | None yet for dedicated governance/content-standards management |
| Internal governance helpers | Policy analysis and creative review agents are internal service components, not MCP/A2A tools |

### What the governance path is doing today

- Discovery tells the buyer what is and is not supported.
- Account sync can carry `governance_agents` metadata through storage.
- Creative review can invoke internal AI review or human review, which is governance-adjacent but not a standalone governance protocol implementation.

## 4. Creative Flow

### Creative protocol flow

```mermaid
flowchart TD
    Buyer["Buyer agent"]

    subgraph Sales["Prebid Sales Agent"]
        MCPA2A["MCP or A2A entry"]
        Sync["sync_creatives"]
        List["list_creatives"]
        Formats["list_creative_formats"]
        Processing["creative processing\nvalidation + persistence + status"]
        Registry["CreativeAgentRegistry"]
        Reviews["auto-approve / AI review / human review"]
        Repo["CreativeRepository + DB"]
    end

    subgraph ExternalCreative["External creative agent over MCP"]
        ExtFormats["list_creative_formats"]
        Preview["preview_creative"]
        Build["build_creative"]
    end

    Buyer --> MCPA2A
    MCPA2A --> Formats
    MCPA2A --> Sync
    MCPA2A --> List

    Formats --> Registry
    Sync --> Processing
    List --> Repo

    Processing --> Registry
    Processing --> Reviews
    Processing --> Repo

    Registry --> ExtFormats
    Registry --> Preview
    Registry --> Build
```

### Creative decision flow inside `sync_creatives`

```mermaid
flowchart TD
    In["sync_creatives request"] --> Fmt["Resolve format via CreativeAgentRegistry"]
    Fmt --> Kind{"Generative format?"}

    Kind -- Yes --> Build["Call external creative agent\n`build_creative`"]
    Kind -- No --> Preview["Call external creative agent\n`preview_creative`"]

    Build --> Data["Merge generated output\nwhile preserving user data when needed"]
    Preview --> Data

    Data --> Review{"Approval mode"}
    Review -- Auto approve --> Approved["status=approved"]
    Review -- AI powered --> AI["submit async AI review"]
    Review -- Human --> Pending["status=pending_review"]

    AI --> Pending
    Approved --> Save["Persist creative + assignments"]
    Pending --> Save
```

### Creative tools available per agent

| Agent | Tools available now |
| --- | --- |
| Buyer agent talking to sales agent | `list_creative_formats`, `sync_creatives`, `list_creatives` |
| Sales agent talking to external creative agent | `list_creative_formats` via AdCP client, plus MCP-only `preview_creative` and `build_creative` for non-standard creative-agent features |
| Internal review/governance helpers | AI review is internal service logic, not a public MCP/A2A tool |

## 5. Quick Agent Map

```mermaid
flowchart LR
    Buyer["Buyer agent"] -->|MCP or A2A| Sales["Prebid Sales Agent"]
    Sales -->|MCP client| Creative["Creative agent"]
    Sales -->|internal service call| Gov["Policy/review agents"]
    Sales --> DB["PostgreSQL"]
```

## 6. Practical Summary

- The buyer agent primarily interacts with one public agent: the Prebid Sales Agent.
- MCP and A2A are two protocol wrappers around the same intended business logic.
- Creative flows are the richest multi-agent path today because the sales agent actively calls external creative agents.
- Governance is partially represented in metadata, policy checks, and review workflows, but not yet exposed as a full buyer-facing governance MCP/A2A protocol domain.
