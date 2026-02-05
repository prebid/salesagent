# Prebid Sales Agent Documentation

The Prebid Sales Agent is the Prebid.org reference implementation of an AdCP-compliant sales agent.

## Quick Start

- **[Quickstart Guide](quickstart.md)** - Get running locally in 5 minutes

## Deployment

- **[Single-Tenant](deployment/single-tenant.md)** - Standard deployment (recommended)
- **[Multi-Tenant](deployment/multi-tenant.md)** - Multiple publishers on one deployment

### Cloud Walkthroughs

- **[Google Cloud Run](deployment/walkthroughs/gcp.md)**
- **[Fly.io](deployment/walkthroughs/fly.md)**

## User Guide

- **[Overview](user-guide/)** - Using the sales agent after deployment
- **[SSO Setup](user-guide/sso-setup.md)** - Configure Single Sign-On with Google, Microsoft, Okta, Auth0, or Keycloak
- **[Products](user-guide/products.md)** - Setting up your product catalog
- **[Advertisers](user-guide/advertisers.md)** - Managing principals and API access
- **[Creatives](user-guide/creatives.md)** - Creative approval workflow

## Adapters

- **[Overview](adapters/)** - Choosing and configuring adapters
- **[Google Ad Manager](adapters/gam/)** - GAM integration
- **[Mock Adapter](adapters/mock/)** - Testing and development

## Security & Configuration

- **[Security](security.md)** - Authentication and security best practices
- **[Encryption](encryption.md)** - API key encryption with Fernet

## Development

- **[Overview](development/)** - Contributing to the codebase
- **[Architecture](development/architecture.md)** - System design
- **[Contributing](development/contributing.md)** - Development workflows
- **[Troubleshooting](development/troubleshooting.md)** - Common issues

## Documentation Structure

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
    ├── contributing.md         # Development workflows
    └── troubleshooting.md      # Common issues
```

## Finding Information

### By Role

**New Users**
1. [Quickstart](quickstart.md) - Get running locally
2. [Deployment](deployment/) - Deploy to production
3. [User Guide](user-guide/) - Configure and use

**Publishers/Operators**
1. [User Guide](user-guide/) - Day-to-day usage
2. [Adapters](adapters/) - Configure ad server
3. [Security](security.md) - Security configuration

**Developers**
1. [Development](development/) - Contributing guide
2. [Architecture](development/architecture.md) - System design
3. [CLAUDE.md](../CLAUDE.md) - AI assistant patterns

## System Overview

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

## Key Components

- **MCP Server** (port 8080) - FastMCP-based tools for AI agents
- **Admin UI** (port 8001) - OAuth secured web interface
- **A2A Server** (port 8091) - Agent-to-agent communication
- **Database** - PostgreSQL

## External Links

- [AdCP Protocol Specification](https://adcontextprotocol.org/docs/)
- [MCP Protocol Documentation](https://modelcontextprotocol.io)
- [GitHub Repository](https://github.com/prebid/salesagent)
