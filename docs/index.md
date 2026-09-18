# Prebid Sales Agent documentation

The Prebid Sales Agent is the Prebid.org reference implementation of a sales agent that complies with the Ad Context Protocol (AdCP).

## Quick start

The [Quickstart guide](quickstart.md) gets the agent running locally in 5 minutes.

## Deployment

- **[Single-tenant deployment](deployment/single-tenant.md)** — the standard deployment, and the recommended one
- **[Multi-tenant deployment](deployment/multi-tenant.md)** — multiple publishers on one deployment

### Cloud walkthroughs

- **[Google Cloud Run](deployment/walkthroughs/gcp.md)**
- **[Fly.io](deployment/walkthroughs/fly.md)**

## User guide

- **[User guide overview](user-guide/)** — use the sales agent after deployment
- **[Single sign-on (SSO) setup](user-guide/sso-setup.md)** — configure single sign-on with Google, Microsoft, Okta, Auth0, or Keycloak
- **[Products](user-guide/products.md)** — set up your product catalog
- **[Advertisers](user-guide/advertisers.md)** — manage principals and API access
- **[Creatives](user-guide/creatives.md)** — the creative approval workflow

## Adapters

- **[Adapter overview](adapters/)** — choose and configure an adapter
- **[Google Ad Manager](adapters/gam/)** — the GAM integration
- **[Mock adapter](adapters/mock/)** — testing and development

## Security and configuration

- **[Security](security.md)** — authentication and security best practices
- **[Encryption](encryption.md)** — API key encryption with Fernet

## Message signing (RFC 9421)

- **[Signing posture and key discovery](signing/posture-and-discovery.md)** — for counterparties: what we advertise, the enforcement ladder, and the brand.json walk to our JWKS
- **[Verifying our outbound webhooks](signing/verifying-our-webhooks.md)** — for counterparties: the profile tag, where to fetch the key, and the legacy HMAC removal
- **[Signing key runbook](operations/signing-key-runbook.md)** — for operators: provisioning, rotation, revocation, rollout and rollback

## Development

- **[Development overview](development/)** — contributing to the codebase, and the map of every development document
- **[Architecture](development/architecture.md)** — system design
- **[Request lifecycle](development/request-lifecycle.md)** — how a request reaches business logic
- **[Building a tool](development/building-tools.md)** — add or change an AdCP tool
- **[Engineering standards](development/engineering-standards.md)** — the standards every change is held to
- **[Troubleshooting](development/troubleshooting.md)** — common issues

## Architecture decision records (ADRs)

An ADR explains *why* a significant technical decision was made, not only what
was decided. Each record captures the context, the chosen approach, the
trade-offs, and when to revisit the decision.

- **[ADR index](decisions/)** — every ADR in one list
- **[ADR-001](decisions/adr-001-single-source-pre-commit-deps.md)** — uv.lock as the single source of truth for pre-commit deps
- **[ADR-002](decisions/adr-002-solo-maintainer-bypass.md)** — solo-maintainer branch protection with bypass
- **[ADR-003](decisions/adr-003-pull-request-target-trust.md)** — the pull_request_target trust boundary for CLA and PR-title workflows

## Documentation structure

```
docs/
├── index.md                    # This file
├── quickstart.md               # Local setup guide
├── security.md                 # Security & authentication
├── encryption.md               # API key encryption
├── deployment/
│   ├── single-tenant.md        # Standard deployment
│   ├── multi-tenant.md         # Multi-tenant configuration
│   └── walkthroughs/
│       ├── gcp.md              # Google Cloud Run
│       └── fly.md              # Fly.io
├── user-guide/
│   ├── README.md               # Overview
│   ├── sso-setup.md            # SSO configuration guide
│   ├── products.md             # Product management
│   ├── advertisers.md          # Principal management
│   └── creatives.md            # Creative workflow
├── adapters/
│   ├── README.md               # Adapter overview
│   ├── mock/                   # Mock adapter docs
│   └── gam/                    # GAM adapter docs
└── development/
    ├── README.md               # Development overview
    ├── architecture.md         # System design
    └── troubleshooting.md      # Common issues
```

## Find information

### By role

**New users**
1. [Quickstart guide](quickstart.md) — get running locally
2. [Deployment](deployment/) — deploy to production
3. [User guide](user-guide/) — configure and use the agent

**Publishers and operators**
1. [User guide](user-guide/) — day-to-day usage
2. [Adapters](adapters/) — configure the ad server
3. [Security](security.md) — security configuration

**Developers**
1. [Development guide](development/) — how to contribute
2. [Architecture](development/architecture.md) — system design
3. [Root CLAUDE.md](../CLAUDE.md) — development patterns, condensed for AI agents

## System overview

```
┌─────────────────┐     ┌──────────────────┐
│   AI Agent      │────▶│  Prebid Sales Agent│
└─────────────────┘     └──────────────────┘
                              │
                ┌─────────────┼─────────────┐
                ▼             ▼             ▼
        ┌──────────────┐ ┌────────┐ ┌──────────────┐
        │ Google Ad    │ │ Kevel  │ │ Mock         │
        │ Manager      │ │        │ │ Adapter      │
        └──────────────┘ └────────┘ └──────────────┘
```

## Key components

- **MCP server** (port `8080`) — FastMCP-based tools for AI agents
- **Admin UI** (port `8001`) — OAuth secured web interface
- **A2A server** (port `8091`) — agent-to-agent communication
- **Database** — PostgreSQL

## External links

- [AdCP protocol specification](https://adcontextprotocol.org/docs/)
- [MCP protocol documentation](https://modelcontextprotocol.io)
- [GitHub repository](https://github.com/prebid/salesagent)
