# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Changeset system for automated version management
- CI workflows to enforce changeset requirements on PRs
- Automated version bump PR creation when changesets are merged

## [0.1.0] - 2025-01-29

Initial release of the AdCP Sales Agent reference implementation.

### Added
- MCP server implementation with AdCP v2.3 support
- A2A (Agent-to-Agent) protocol support
- Multi-tenant architecture with PostgreSQL
- Google Ad Manager (GAM) adapter
- Mock ad server adapter for testing
- Admin UI with Google OAuth authentication
- Comprehensive testing backend with dry-run support
- Real-time activity dashboard with SSE
- Workflow management system
- Creative management and approval workflows
- Audit logging
- Docker deployment support
- Extensive documentation

[Unreleased]: https://github.com/adcontextprotocol/salesagent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/adcontextprotocol/salesagent/releases/tag/v0.1.0
