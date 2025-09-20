# AdCP Sales Agent Server - Development Guide

## 🚨 DEPLOYMENT ARCHITECTURE - CRITICAL TO UNDERSTAND 🚨

This project has **TWO COMPLETELY SEPARATE** deployment environments:

### 1. LOCAL DEVELOPMENT (Docker Compose)
- **Control**: `docker-compose up/down`
- **URLs**: localhost:8001 (Admin), localhost:8080 (MCP), localhost:8091 (A2A)
- **Database**: Local PostgreSQL container on port 5526
- **Config**: `docker-compose.yml`, `.env`
- **Purpose**: Development and testing only

### 2. PRODUCTION (Fly.io)
- **Control**: `fly deploy`, `fly status`, `fly logs`
- **URL**: https://adcp-sales-agent.fly.dev
- **Database**: Fly PostgreSQL cluster (completely separate)
- **Config**: `config/fly/*.toml`
- **Status**: ✅ **CURRENTLY DEPLOYED AND RUNNING**
- **App Name**: `adcp-sales-agent`

**⚠️ IMPORTANT**: Docker and Fly.io are INDEPENDENT. Starting/stopping Docker does NOT affect production on Fly.io!

## 🚨 GIT WORKFLOW - ABSOLUTELY CRITICAL 🚨

### ❌ NEVER PUSH DIRECTLY TO MAIN ❌

**MANDATORY WORKFLOW - NO EXCEPTIONS:**

1. **Always work on feature branches**:
   ```bash
   git checkout -b feature/your-feature-name
   # Make your changes
   git add .
   git commit -m "your commit message"
   ```

2. **Push feature branch and create Pull Request**:
   ```bash
   git push origin feature/your-feature-name
   # Then create PR via GitHub UI or gh CLI
   gh pr create --title "Your PR Title" --body "Description"
   ```

3. **NEVER do this**:
   ```bash
   # ❌ FORBIDDEN - DO NOT DO THIS
   git push origin feature-branch:main
   git push origin HEAD:main
   ```

**WHY THIS MATTERS:**
- **Code Review**: All changes must be reviewed before merging
- **CI/CD**: PRs trigger automated testing and validation
- **Production Safety**: Direct pushes bypass safety checks
- **Team Coordination**: PRs allow discussion and collaboration
- **Audit Trail**: PRs provide proper change documentation

**CONSEQUENCES OF DIRECT PUSH TO MAIN:**
- ⚠️ **Bypasses code review** - potential bugs reach production
- ⚠️ **Breaks CI/CD pipeline** - automated checks are skipped
- ⚠️ **No rollback strategy** - harder to revert problematic changes
- ⚠️ **Team disruption** - other developers may face merge conflicts

**IF YOU ACCIDENTALLY PUSH TO MAIN:**
1. **Immediately notify team** - communicate the mistake
2. **Check if revert needed** - assess impact of direct push
3. **Create retroactive PR** - document the changes properly
4. **Follow proper workflow** - use feature branches going forward

**📋 PROPER WORKFLOW CHECKLIST:**
- [ ] Create feature branch from main
- [ ] Make changes and commit to feature branch
- [ ] Push feature branch to origin
- [ ] Create Pull Request via GitHub
- [ ] Wait for code review and approval
- [ ] Merge via GitHub (never command line)

## Project Overview

This is a Python-based reference implementation of the Advertising Context Protocol (AdCP) V2.3 sales agent. It demonstrates how publishers expose advertising inventory to AI-driven clients through a standardized MCP (Model Context Protocol) interface.

The server provides:
- **MCP Server**: FastMCP-based server exposing tools for AI agents (port 8080)
- **Admin UI**: Secure web interface with Google OAuth authentication (port 8001)
- **A2A Server**: Standard python-a2a server for agent-to-agent communication (port 8091)
- **Multi-Tenant Architecture**: Database-backed tenant isolation with subdomain routing
- **Advanced Targeting**: Comprehensive targeting system with overlay and managed-only dimensions
- **Creative Management**: Auto-approval workflows, creative groups, and admin review
- **Human-in-the-Loop**: Optional manual approval mode for sensitive operations
- **Security & Compliance**: Audit logging, principal-based auth, adapter security boundaries
- **Slack Integration**: Per-tenant webhook configuration (no env vars needed)
- **Production Ready**: PostgreSQL database, Docker deployment, health monitoring

## Key Architecture Decisions

### 0. AdCP Protocol Compliance - MANDATORY FOR ALL MODELS (IMPORTANT)
**🚨 CRITICAL**: All data models that represent AdCP protocol objects MUST be fully spec-compliant and tested.

**AdCP Compliance Requirements:**
- **Response Models**: All models returned to clients must include ONLY AdCP spec-defined fields
- **Field Names**: Use exact field names from AdCP schema (e.g., `format` not `format_id`, `url` not `content_uri`)
- **Required Fields**: All AdCP-required fields must be present and non-null
- **Internal Fields**: Database/processing fields must be excluded from external responses
- **Schema Validation**: Each model must have AdCP contract tests in `tests/unit/test_adcp_contract.py`

**Mandatory Test Pattern for New Models:**
```python
def test_[model]_adcp_compliance(self):
    \"\"\"Test that [Model] complies with AdCP [schema-name] schema.\"\"\"
    # 1. Create model with all required + optional fields
    model = YourModel(...)

    # 2. Test AdCP-compliant response
    adcp_response = model.model_dump()

    # 3. Verify required AdCP fields present
    required_fields = ["field1", "field2"]  # From AdCP spec
    for field in required_fields:
        assert field in adcp_response
        assert adcp_response[field] is not None

    # 4. Verify optional AdCP fields present (can be null)
    optional_fields = ["optional1", "optional2"]  # From AdCP spec
    for field in optional_fields:
        assert field in adcp_response

    # 5. Verify internal fields excluded
    internal_fields = ["tenant_id", "created_at"]  # Not in AdCP spec
    for field in internal_fields:
        assert field not in adcp_response

    # 6. Verify field count matches expectation
    assert len(adcp_response) == EXPECTED_FIELD_COUNT
```

**When Adding New Models:**
1. ✅ Check AdCP spec at https://adcontextprotocol.org/docs/
2. ✅ Add AdCP compliance test BEFORE implementing model
3. ✅ Use `model_dump()` for external responses, `model_dump_internal()` for database
4. ✅ Test with both minimal and full field sets
5. ✅ Verify no internal fields leak to external responses

**Existing AdCP-Compliant Models (All Tested):**
- ✅ `Product` - AdCP product schema
- ✅ `Creative` - AdCP creative-asset schema
- ✅ `Format` - AdCP format schema
- ✅ `Principal` - AdCP auth schema
- ✅ `Signal` - AdCP get-signals-response schema (with SignalDeployment, SignalPricing)
- ✅ `Package` - AdCP package schema
- ✅ `Targeting` - AdCP targeting schema (with managed field filtering)
- ✅ `Budget` - AdCP budget schema
- ✅ `Measurement` - AdCP measurement schema
- ✅ `CreativePolicy` - AdCP creative-policy schema
- ✅ `CreativeStatus` - AdCP creative-status schema
- ✅ `CreativeAssignment` - AdCP creative-assignment schema

**🚨 MANDATORY**: Every client-facing model MUST have a corresponding `test_[model_name]_adcp_compliance` test in `tests/unit/test_adcp_contract.py`

### 1. Admin UI Route Architecture (IMPORTANT FOR DEBUGGING)
**⚠️ CRITICAL**: The admin interface has confusing route handling that can waste debugging time:

- **`src/admin/blueprints/settings.py`**: Handles TENANT MANAGEMENT settings and POST operations for tenant settings
  - Functions: `admin_settings()` (GET) and `update_admin_settings()` (POST) for tenant management settings
  - Also contains POST-only routes for updating tenant settings (`update_adapter()`, `update_general()`, etc.)
- **`src/admin/blueprints/tenants.py`**: Handles TENANT-SPECIFIC settings GET requests
  - Function: `tenant_settings()` - Renders the main tenant settings page

**Route Architecture**:
- **GET** `/admin/tenant/{id}/settings` → `tenants.py::tenant_settings()` (displays the page)
- **POST** `/admin/tenant/{id}/settings/adapter` → `settings.py::update_adapter()` (updates data, redirects back)

**Route Mapping**:
```
/admin/settings                           → src/admin/blueprints/settings.py::admin_settings()
/admin/tenant/{id}/settings               → src/admin/blueprints/tenants.py::tenant_settings()
/admin/tenant/{id}/settings/adapter       → src/admin/blueprints/settings.py::update_adapter()
/admin/tenant/{id}/settings/general       → src/admin/blueprints/settings.py::update_general()
/admin/tenant/{id}/settings/slack         → src/admin/blueprints/settings.py::update_slack()
```

### 1. Database-Backed Multi-Tenancy
- **Tenant Isolation**: Each publisher is a tenant with isolated data
- **Principal System**: Within each tenant, principals (advertisers) have unique tokens
- **Subdomain Routing**: Optional routing like `sports.localhost:8080`
- **Configuration**: Per-tenant adapter config, features, and limits
- **Database Support**: SQLite (dev) and PostgreSQL (production)

### 2. Adapter Pattern for Ad Servers
- Base `AdServerAdapter` class defines the interface
- Implementations for GAM, Kevel, Triton, and Mock
- Each adapter handles its own API call logging in dry-run mode
- Principal object encapsulates identity and adapter mappings
- Adapters can provide custom configuration UIs via Flask routes
- Adapter-specific validation and field definitions

#### Google Ad Manager (GAM) Modular Architecture
The GAM adapter has been refactored into a clean modular architecture:

**Main Orchestrator (`google_ad_manager.py`)**:
- 250-line clean orchestrator class (reduced from 2800+ lines)
- Delegates operations to specialized manager classes
- Maintains full backward compatibility
- Focus on initialization and method orchestration

**Modular Components (`src/adapters/gam/`)**:
- `auth.py`: **GAMAuthManager** - OAuth and service account authentication
- `client.py`: **GAMClientManager** - API client lifecycle and service access
- `managers/targeting.py`: **GAMTargetingManager** - AdCP→GAM targeting translation
- `managers/orders.py`: **GAMOrdersManager** - Order creation and lifecycle management
- `managers/creatives.py`: **GAMCreativesManager** - Creative upload and association

**Architectural Benefits**:
- **Single Responsibility**: Each manager handles one functional area
- **Independent Testing**: Managers can be unit tested in isolation
- **Maintainable**: Bug fixes and features isolated to specific areas
- **Clean Interfaces**: Clear APIs between components
- **Shared Resources**: Client and auth management shared across operations

### 3. FastMCP Integration
- Uses FastMCP for the server framework
- HTTP transport with header-based authentication (`x-adcp-auth`)
- Context parameter provides access to HTTP request headers
- Tools are exposed as MCP methods

### 4. Unified Workflow System (Key Architecture)
- **Single Source of Truth**: All work tracking uses `WorkflowStep` and `ObjectWorkflowMapping` tables
- **No Task Models**: Eliminated `Task` and `HumanTask` models that caused schema conflicts
- **Human-in-the-Loop**: Workflow steps with `requires_approval` status for manual intervention
- **Activity Tracking**: Dashboard shows workflow progression and pending approvals
- **Database Consistency**: Unified schema prevents type mismatches and query errors

### 5. A2A Protocol Support
- Standard `python-a2a` library implementation
- No custom protocol code - using library's base classes only
- Supports both REST and JSON-RPC transports
- Compatible with `a2a` CLI and other standard A2A clients
- Dual skill invocation patterns: natural language and explicit skill calls (AdCP PR #48)
- All responses validated against AdCP schemas for compliance

## Core Components

### `main.py` - Server Implementation
- FastMCP server exposing AdCP tools
- Authentication via `x-adcp-auth` header
- Principal resolution and adapter instantiation
- In-memory state management for media buys

### `schemas.py` - Data Models
- Pydantic models for all API contracts
- `Principal` model with `get_adapter_id()` method
- Request/Response models for all operations
- Adapter-specific response models

### `adapters/` - Ad Server Integrations
- `base.py`: Abstract base class defining the interface
- `mock_ad_server.py`: Mock implementation with realistic simulation
- `google_ad_manager.py`: Clean GAM orchestrator (250 lines) delegating to modular components
- `gam/`: Modular GAM implementation with specialized managers
  - `auth.py`: Authentication and credential management
  - `client.py`: API client initialization and lifecycle
  - `managers/`: Business logic managers for targeting, orders, and creatives
- Each adapter accepts a `Principal` object for cleaner architecture

### `src/a2a_server/adcp_a2a_server.py` - A2A Server
- Standard `python-a2a` server implementation
- Extends `A2AServer` base class from python-a2a library
- Handles product, targeting, and pricing queries
- Integrates with MCP server for real data when available
- Uses Waitress WSGI server for production reliability
- **Authentication**: Supports Bearer tokens via Authorization headers
- **Security**: Query parameter auth deprecated in favor of headers

## Recent Major Changes

### Google Ad Manager Adapter Refactoring (Latest - Sep 2025)
- **Complete Modular Refactoring**: Broke down monolithic 2800+ line GAM adapter into focused manager classes
- **90% Code Reduction**: Main orchestrator reduced to 250 lines with clear delegation patterns
- **Modular Architecture**: Separated authentication, client management, targeting, orders, and creatives into distinct managers
- **Backward Compatibility**: All public methods and properties preserved for existing code
- **Clean Interfaces**: Each manager has single responsibility and focused API
- **Testing Benefits**: Managers can be unit tested independently with mock clients
- **Development Efficiency**: New GAM features can be added to appropriate managers without touching orchestrator
- **Maintenance Improvements**: Bug fixes and enhancements isolated to specific functional areas

### Tenant-Specific Subdomain Architecture (Sep 2025)
- **Production Domain**: Implemented `sales-agent.scope3.com` with tenant-specific subdomains
- **Authentication Architecture**: Cross-domain OAuth with Google supporting both super admin and tenant logins
- **Subdomain Pattern**: `https://[tenant].sales-agent.scope3.com` for tenant-specific access
- **Nginx Configuration**: Comprehensive regex-based routing for tenant detection and path handling
- **Session Management**: Cross-subdomain session cookies for seamless OAuth flow
- **Hostname-Based Routing**: Automatic tenant detection from hostname without headers or path parameters
- **SSL Certificates**: Wildcard certificate `*.sales-agent.scope3.com` for all tenant subdomains
- **Admin Interface**: Dedicated `admin.sales-agent.scope3.com` for super admin functions
- **DNS Setup**: A/AAAA records for base domain, CNAME wildcard for tenant subdomains
- **Production Deployment**: Live on Fly.io with proper certificate and routing configuration

### AdCP Testing Specification Implementation (Aug 2025)
- **Full Testing Backend**: Complete implementation of AdCP Testing Specification (https://adcontextprotocol.org/docs/media-buy/testing/)
- **Testing Hooks System**: All 9 request headers (X-Dry-Run, X-Mock-Time, X-Jump-To-Event, etc.) with session isolation
- **Response Headers**: Required AdCP response headers (X-Next-Event, X-Next-Event-Time, X-Simulated-Spend)
- **Campaign Lifecycle Simulation**: 16-event campaign progression with time controls and spend tracking
- **Session Management**: Isolated test sessions with `TestSessionManager` for parallel testing
- **Mock Server Focus**: Test files clearly named with `mock_server` prefix to indicate scope
- **Consolidation Pattern**: Avoided file proliferation by updating existing docs instead of creating new ones
- **FastMCP Integration**: Proper context header extraction and response header injection patterns

### Task System Elimination & Workflow Unification (Aug 2025)
- **Architecture Simplification**: Eliminated dual task systems (`Task` and `HumanTask` models) in favor of unified `WorkflowStep` system
- **Production Fix**: Resolved critical database schema errors causing production crashes
- **Migration Chain Repair**: Fixed broken Alembic migration chain preventing deployments
- **Workflow-Based Dashboard**: Transformed admin dashboard into activity stream showing workflow steps requiring attention
- **Code Cleanup**: Removed all deprecated task-related code from MCP server and Admin UI
- **Database Schema**: Cleaned up orphaned task tables and schema inconsistencies
- **Activity Stream**: Real-time dashboard now shows workflow activity instead of generic tasks

### JSON-RPC 2.0 Protocol Fixes & Security Enhancements (Dec 2024)
- **A2A Protocol Compliance**: Fixed JSON-RPC 2.0 implementation to use string `messageId` per spec
- **Removed Proxy Workaround**: Eliminated unnecessary `/a2a-internal` endpoint and messageId conversion
- **Backward Compatibility**: Added middleware to handle both numeric and string messageId formats
- **Security Fix**: Added tenant validation to prevent access to disabled/deleted tenants
- **Authentication Enhancement**: Added explicit transaction management for database consistency
- **Test Infrastructure**: Added `--skip-docker` option for E2E tests with external services
- **Token Security**: Removed hard-coded test tokens, now uses environment variables

### Workflow-Based Activity System (Replaces Task Management)
- **Unified Architecture**: Single `WorkflowStep` model replaces dual `Task`/`HumanTask` systems
- **Activity Dashboard**: Live workflow activity stream showing steps requiring human intervention
- **Database Schema**: Uses existing `workflow_steps` and `object_workflow_mappings` tables
- **Template Updates**: Enhanced `tenant_dashboard.html` to show workflow activity feed
- **Blueprint Cleanup**: Removed deprecated `tasks.py` blueprint and related templates
- **Production Ready**: Eliminates schema conflicts and simplifies operational monitoring

### A2A Protocol Integration with python-a2a
- **Standard Library**: Now using `python-a2a` library for all A2A protocol handling
- **No Custom Protocol Code**: Removed all custom protocol implementations
- **Server Implementation**: Using `python_a2a.server.A2AServer` base class
- **Authentication**: Bearer token auth via Authorization headers (secure approach)
- **Client Script**: `scripts/a2a_query.py` provides authenticated CLI access
- **Intelligent Responses**: Server responds to product, targeting, and pricing queries
- **Production Deployment**: Live at https://adcp-sales-agent.fly.dev/a2a

### AdCP v2.4 Protocol Updates
- **Renamed Endpoints**: `list_products` renamed to `get_products` to align with signals agent spec
- **Signal Discovery**: Added optional `get_signals` endpoint for discovering available signals
- **Enhanced Targeting**: Added `signals` field to targeting overlay for direct signal activation
- **Terminology Updates**: Renamed `provided_signals` to `aee_signals` for improved clarity

### AI-Powered Product Management
- **Default Products**: 6 standard products automatically created for new tenants
- **Industry Templates**: Specialized products for news, sports, entertainment, ecommerce
- **AI Configuration**: Uses Gemini 2.5 Flash to analyze descriptions and suggest configs
- **Bulk Operations**: CSV/JSON upload, template browser, quick-create API

### Principal-Level Advertiser Management
- **Architecture Change**: Advertisers are now configured per-principal, not per-tenant
- **Each Principal = One Advertiser**: Clear separation between publisher and advertisers
- **GAM Integration**: Each principal selects their own GAM advertiser ID during creation
- **UI Improvements**: "Principals" renamed to "Advertisers" throughout UI

## 🔧 A2A Implementation Patterns & Best Practices

### ⚠️ CRITICAL: Always Use `create_flask_app()` for A2A Servers

**Problem**: Custom Flask app creation bypasses standard A2A protocol endpoints.

**❌ WRONG - Custom Flask App:**
```python
# This bypasses standard A2A endpoints
from flask import Flask
app = Flask(__name__)
agent.setup_routes(app)
```

**✅ CORRECT - Standard Library App:**
```python
# This provides all standard A2A endpoints automatically
from python_a2a.server.http import create_flask_app
app = create_flask_app(agent)
# Agent's setup_routes() is called automatically by create_flask_app()
```

### Standard A2A Endpoints Provided by `create_flask_app()`

When using `create_flask_app()`, you automatically get these A2A spec-compliant endpoints:

- **`/.well-known/agent.json`** - Standard agent discovery endpoint (A2A spec requirement)
- **`/agent.json`** - Agent card endpoint
- **`/a2a`** - Main A2A endpoint with UI/JSON content negotiation
- **`/`** - Root endpoint (redirects to A2A info)
- **`/stream`** - Server-sent events streaming endpoint
- **`/a2a/health`** - Library's health check
- **CORS support** - Proper headers for browser compatibility
- **OPTIONS handling** - CORS preflight support

### Custom Route Integration

Your custom routes are added via `setup_routes(app)` which is called automatically:

```python
class MyA2AAgent(A2AServer):
    def setup_routes(self, app):
        """Add custom routes to the standard A2A Flask app."""

        # Don't redefine standard routes - they're already provided
        # ❌ Don't add: /agent.json, /.well-known/agent.json, /a2a, etc.

        # ✅ Add your custom business logic routes
        @app.route("/custom/endpoint", methods=["POST"])
        @self.require_auth
        def custom_business_logic():
            return jsonify({"custom": "response"})
```

### Function Naming Conflicts

**Problem**: Function names can conflict with library's internal functions.

**❌ Avoid These Function Names:**
- `health_check` (conflicts with library's `/a2a/health`)
- `get_agent_card` (conflicts with standard agent card handling)
- `handle_request` (conflicts with library's request handling)

**✅ Use Descriptive Names:**
```python
@app.route("/health", methods=["GET"])
def custom_health_check():  # Different from library's health_check
    return jsonify({"status": "healthy"})
```

### A2A Agent Card Structure

Ensure your agent card includes all required A2A fields:

```python
agent_card = AgentCard(
    name="Your Agent Name",
    description="Clear description of agent capabilities",
    url="http://your-server:port",
    version="1.0.0",
    authentication="bearer-token",  # REQUIRED for auth
    skills=[
        AgentSkill(name="skill1", description="What skill1 does"),
        AgentSkill(name="skill2", description="What skill2 does"),
    ],
    capabilities={
        "google_a2a_compatible": True,  # REQUIRED for Google A2A clients
        "parts_array_format": True,     # REQUIRED for Google A2A clients
    }
)
```

### Testing Requirements

**ALWAYS** add these tests when implementing A2A servers to prevent regression:

```python
def test_well_known_agent_json_endpoint(client):
    """Test A2A spec compliance - agent discovery."""
    response = client.get('/.well-known/agent.json')
    assert response.status_code == 200
    data = response.get_json()
    assert 'name' in data
    assert 'skills' in data

def test_standard_a2a_endpoints(client):
    """Test all standard A2A endpoints exist."""
    endpoints = ['/.well-known/agent.json', '/agent.json', '/a2a', '/stream']
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code != 404  # Should exist
```

### Troubleshooting MCP Issues

**Issue**: MCP returns empty products array
- **Cause**: No products exist in database for the tenant
- **Fix**: Create products for the tenant using Admin UI or database scripts

**Issue**: "Missing or invalid x-adcp-auth header for authentication"
- **Cause 1**: Token doesn't exist in database
- **Cause 2**: Tenant is disabled or deleted
- **Cause 3**: FastMCP SSE transport not forwarding headers properly
- **Fix**: Verify token exists, tenant is active, and use direct HTTP requests for debugging

**Issue**: Policy check blocking requests
- **Cause**: Gemini API key invalid or policy service returning BLOCKED status
- **Fix**: Check GEMINI_API_KEY environment variable and review policy settings

### Troubleshooting Testing Backend Issues

**Issue**: Testing hooks not working (X-Dry-Run, X-Mock-Time, etc.)
- **Cause**: Headers not being extracted from FastMCP context properly
- **Fix**: Use `context.meta.get("headers", {})` to extract headers from FastMCP context

**Issue**: Response headers missing (X-Next-Event, X-Next-Event-Time, X-Simulated-Spend)
- **Cause**: Response headers not being set after apply_testing_hooks
- **Fix**: Ensure `campaign_info` dict is passed to testing hooks for event calculation

**Issue**: Session isolation not working in parallel tests
- **Cause**: Missing or incorrect X-Test-Session-ID header
- **Fix**: Generate unique session IDs per test and include in all requests

### Troubleshooting Production Issues

**Issue**: "operator does not exist: text < timestamp with time zone"
- **Cause**: Database schema mismatch - columns created as TEXT instead of TIMESTAMP WITH TIME ZONE
- **Root Cause**: Deprecated task system with conflicting schema definitions
- **Fix**: Migrate to unified workflow system and eliminate task tables
- **Prevention**: Use consistent schema definitions and avoid dual systems

**Issue**: "Can't locate revision identified by '[revision_id]'"
- **Cause**: Broken Alembic migration chain with missing or incorrect revision links
- **Symptoms**: App crashes on startup, deployment failures, migration errors
- **Fix Process**:
  1. Check migration history: `alembic history`
  2. Identify last known good revision
  3. Reset to good revision: `alembic stamp [good_revision]`
  4. Create new migration with correct `down_revision`
  5. Deploy migration fix before code changes
- **Prevention**: Never modify committed migration files, always test migrations locally

**Issue**: Production crashes after PR merge
- **Debugging Process**:
  1. Check deployment status: `fly status --app adcp-sales-agent`
  2. Review logs: `fly logs --app adcp-sales-agent`
  3. Identify specific error patterns (database, import, runtime)
  4. Check git history for recent changes
  5. Test fixes locally before deploying
- **Recovery**: Deploy minimal fix first, then implement broader changes

### Troubleshooting Test Issues

**Issue**: Pre-commit hook "excessive mocking" failure
- **Cause**: Test file has more than 10 mocks (detected via `@patch|MagicMock|Mock()` count)
- **Fix**: Apply mock reduction patterns from "Test Quality & Mocking Best Practices" section:
  1. Create centralized `MockSetup` class for duplicate mock creation
  2. Use `patch.multiple()` helper methods to consolidate patches
  3. Move database testing to integration tests with real DB connections
  4. Focus mocking on external dependencies only (APIs, third-party services)

**Issue**: Tests failing after mock refactoring
- **Common Causes**:
  - Missing imports: Add `from src.core.main import function_name`
  - Mock return type mismatches: Ensure mocks return correct data types (list, dict, not Mock)
  - Schema validation errors: Update test data to match current model requirements
  - Test class naming: Rename `TestModel` classes to `ModelClass` to avoid pytest collection

**Issue**: Integration tests slow or flaky
- **Fix**: Use proper database session management and isolation
- **Pattern**: Create/cleanup test data in fixtures rather than mocking database calls

**Issue**: Async test failures
- **Fix**: Ensure proper `@pytest.mark.asyncio` and `AsyncMock` usage
- **Pattern**: Use `async with` for async context managers, `await` for all async calls

### Troubleshooting A2A Issues

**Issue**: "404 NOT FOUND" for `/.well-known/agent-card.json`
- **Cause**: Using custom Flask app instead of `create_flask_app()`
- **Fix**: Use `create_flask_app(agent)` as shown above

**Issue**: "View function mapping is overwriting an existing endpoint"
- **Cause**: Function name conflicts with library functions
- **Fix**: Use unique function names (e.g., `custom_health_check` not `health_check`)

**Issue**: A2A clients can't discover agent
- **Cause**: Missing `/.well-known/agent.json` endpoint
- **Fix**: Ensure using `create_flask_app()` and agent card has required fields

**Issue**: Authentication not working
- **Cause**: Agent card doesn't specify `authentication="bearer-token"`
- **Fix**: Add authentication field to AgentCard constructor

### nginx Configuration

**When using `create_flask_app()`, you don't need nginx workarounds:**

```nginx
# ❌ Don't add these - library provides standard endpoints automatically
# location /.well-known/agent-card.json { ... }  # Wrong endpoint name anyway
# location /.well-known/agent.json { ... }       # Library handles this

# ✅ Just proxy to A2A server - it handles standard endpoints
location /a2a/ {
    proxy_pass http://a2a_backend;
    # Standard proxy headers...
}
```

### Deployment Checklist

Before deploying A2A servers:

1. ✅ **Use `create_flask_app(agent)`** - not custom Flask app
2. ✅ **Test `/.well-known/agent.json`** - should return 200 with agent card
3. ✅ **Test agent card structure** - includes name, skills, authentication
4. ✅ **Test Bearer token auth** - protected endpoints reject invalid tokens
5. ✅ **Test CORS headers** - client browsers can access endpoints
6. ✅ **Run regression tests** - prevent future breaking changes
7. ✅ **Verify with A2A client** - can discover and communicate with agent

## Configuration

### Docker Setup (Primary Method)

```yaml
# docker-compose.yml services:
postgres      # PostgreSQL database
adcp-server   # MCP server on port 8080
admin-ui      # Admin interface on port 8001
```

### Required Configuration (.env file)

```bash
# API Keys
GEMINI_API_KEY=your-gemini-api-key-here

# OAuth Configuration
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# GAM OAuth Configuration (required for Google Ad Manager functionality)
GAM_OAUTH_CLIENT_ID=your-gam-client-id.apps.googleusercontent.com
GAM_OAUTH_CLIENT_SECRET=your-gam-client-secret

# Admin Configuration
SUPER_ADMIN_EMAILS=user1@example.com,user2@example.com
SUPER_ADMIN_DOMAINS=example.com

# Port Configuration (optional)
ADCP_SALES_PORT=8080
ADMIN_UI_PORT=8001
```

### Important Configuration Notes

1. **GAM OAuth**: GAM OAuth credentials are configured via environment variables only (no longer stored in database)
2. **Slack Webhooks**: Configure per-tenant in Admin UI, NOT via environment variables
3. **Database**: Docker Compose manages PostgreSQL automatically
4. **OAuth**: Mount your `client_secret*.json` file (see docker-compose.yml)
5. **Ports**: 8080 (MCP), 8001 (Admin UI), 5432 (PostgreSQL)

### Database Schema

```sql
-- Multi-tenant tables (active)
tenants (tenant_id, name, subdomain, config, billing_plan)
principals (tenant_id, principal_id, name, access_token, platform_mappings)
products (tenant_id, product_id, name, formats, targeting_template)
media_buys (tenant_id, media_buy_id, principal_id, status, config, budget, dates)
creatives (tenant_id, creative_id, principal_id, status, format)
audit_logs (tenant_id, timestamp, operation, principal_id, success, details)

-- Workflow system (unified work tracking)
workflow_steps (step_id, tenant_id, workflow_id, status, step_type, created_at)
object_workflow_mappings (object_type, object_id, workflow_id, tenant_id)

-- Legacy tables (deprecated - may exist but not used by application)
-- tasks (eliminated in favor of workflow_steps)
-- human_tasks (eliminated in favor of workflow_steps with requires_approval status)
```

## Common Operations

### Running the Server
```bash
# Start all services with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Managing Tenants
```bash
# Create new publisher/tenant
docker exec -it adcp-buy-server-adcp-server-1 python setup_tenant.py "Publisher Name" \
  --adapter google_ad_manager \
  --gam-network-code 123456 \
  --gam-refresh-token YOUR_REFRESH_TOKEN

# Access Admin UI
open http://localhost:8001
```

### Running Tests
```bash
# Run all tests
uv run pytest

# Run by category
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/e2e/

# Run with coverage
uv run pytest --cov=. --cov-report=html

# Run E2E tests with Docker services
docker-compose -f docker-compose.test.yml up -d
uv run pytest tests/e2e/ -v
docker-compose -f docker-compose.test.yml down
```

### End-to-End Testing with Strategy System

The E2E test suite provides comprehensive testing of AdCP protocol operations:

**Test Files:**
- `test_adcp_full_lifecycle.py` - Complete campaign lifecycle testing with all AdCP tools
- `test_strategy_simulation_end_to_end.py` - Strategy-based simulation testing
- `test_testing_hooks.py` - Protocol testing hooks (X-Dry-Run, X-Mock-Time, etc.)

**Key Features:**
- Strategy-based testing with deterministic time progression
- Testing hooks from AdCP spec (PR #34) for controlled testing
- Both MCP and A2A protocol testing with official clients
- Parallel test execution with isolated test sessions

**Testing Hooks:**
- `X-Dry-Run`: Validate operations without executing
- `X-Mock-Time`: Control time for deterministic testing
- `X-Jump-To-Event`: Jump to specific campaign events
- `X-Test-Session-ID`: Isolate parallel test sessions

**Running Specific Tests:**
```bash
# Run full lifecycle test
uv run pytest tests/e2e/test_adcp_full_lifecycle.py -v

# Run strategy simulation
uv run pytest tests/e2e/test_strategy_simulation_end_to_end.py -v

# Run with specific markers
uv run pytest -m "not requires_server" tests/e2e/
```

### Using MCP Client
```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

headers = {"x-adcp-auth": "your_token"}
transport = StreamableHttpTransport(url="http://localhost:8080/mcp/", headers=headers)
client = Client(transport=transport)

async with client:
    # Get products
    products = await client.tools.get_products(brief="video ads for sports content")

    # Create media buy
    result = await client.tools.create_media_buy(
        product_ids=["prod_1"],
        total_budget=5000.0,
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28"
    )
```

## Database Migrations

The project uses Alembic for database migrations:

```bash
# Run migrations
uv run python migrate.py

# Create new migration
uv run alembic revision -m "description"
```

**Important**: Never modify existing migration files after they've been committed.

## Testing Guidelines

### Test Organization
```
tests/
├── unit/           # Fast, isolated unit tests
├── integration/    # Tests requiring database/services
├── e2e/           # End-to-end full system tests
└── ui/            # Admin UI interface tests
```

### Running Tests in CI
Tests marked with `@pytest.mark.requires_server` or `@pytest.mark.skip_ci` are automatically skipped in CI.

### Test Quality & Mocking Best Practices

**🚨 MOCK LIMIT ENFORCEMENT**: Pre-commit hook enforces maximum 10 mocks per test file to prevent excessive mocking that makes tests brittle and hard to maintain.

**Unit vs Integration Test Guidelines:**
- **Unit Tests**: Focus on pure logic, mock external dependencies only
  - Mock external APIs, database connections, file I/O
  - Test business logic, algorithms, data transformations
  - Should be fast (< 1ms per test) and isolated
- **Integration Tests**: Use real dependencies where practical
  - Real database connections with test data
  - Real internal service calls and business logic
  - Mock only external services (APIs, third-party systems)

**Recommended Mock Reduction Patterns:**

1. **Centralized Mock Setup** - Eliminate duplicate mocking
```python
class MockSetup:
    """Centralized mock setup to reduce duplicate mocking."""

    @staticmethod
    def create_standard_context():
        """Single method creates consistent mock context."""
        context = Mock(spec=FastMCPContext)
        context.meta = {"headers": {"x-adcp-auth": "test-token"}}
        return context

    @staticmethod
    def get_test_data():
        """Standard test data objects."""
        return {"tenant": {...}, "principal_id": "test_principal"}
```

2. **Helper Methods for Context Patching** - Consolidate repeated patches
```python
def _mock_auth_context(self, tenant_data):
    """Helper to create authentication context patches."""
    return patch.multiple(
        "src.core.main",
        _get_principal_id_from_context=Mock(return_value=tenant_data["principal"].principal_id),
        get_current_tenant=Mock(return_value={"tenant_id": tenant_data["tenant"]["tenant_id"]}),
        get_principal_object=Mock(return_value=tenant_data["principal"]),
    )
```

3. **Factory Fixtures** - Reusable test data creation
```python
@pytest.fixture
def test_context_factory(self) -> Callable[[str, str], Mock]:
    """Factory for creating test contexts with authentication."""
    def _create_context(token="test-token", context_id="test-ctx"):
        context = Mock(spec=Context)
        context.meta = {"headers": {"x-adcp-auth": token, "x-context-id": context_id}}
        return context
    return _create_context
```

4. **Class-Level Patches** - Reduce method-level patch decorators
```python
@patch.multiple(
    "src.core.module",
    dependency1=Mock(),
    dependency2=Mock(),
    dependency3=Mock()
)
class TestMyClass:
    # All methods share these patches automatically
```

**Type Safety in Tests:**
- Add type hints to fixture methods: `-> Dict[str, Any]`, `-> Callable[...]`
- Use `Mock(spec=OriginalClass)` to prevent attribute errors
- Import types for better IDE support: `from typing import Any, Callable, Dict`

**Async Test Patterns:**
- Use `@pytest.mark.asyncio` for async test methods
- Use `AsyncMock` for mocking async functions and methods
- Proper async fixture design with cleanup
```python
@pytest.fixture
async def async_resource(self) -> AsyncGenerator[Resource, None]:
    resource = await create_resource()
    try:
        yield resource
    finally:
        await resource.cleanup()
```

**Test Naming Conventions:**
- Avoid `Test*` class names for non-test classes (use `*Model`, `*Factory` instead)
- Use descriptive test method names: `test_auth_failure_returns_401_error`
- Group related tests in classes: `TestDatabaseHealthLogic`, `TestSignalsWorkflow`

### Critical Lesson: A2A Server Regression Prevention (Dec 2024)

**🚨 CASE STUDY**: Two critical bugs slipped through test coverage that caused production failures:

1. **Agent Card URL Trailing Slash**: URLs ending with `/a2a/` caused redirects that stripped Authorization headers
2. **Function Call Error**: `core_get_signals_tool.fn()` caused 'FunctionTool' object is not callable error

**Root Causes Identified:**
- **Over-Mocking**: Tests mocked the very functions that had bugs (`_handle_get_signals_skill` mocked instead of testing actual function calls)
- **Skipped Critical Tests**: Main A2A endpoints test was completely disabled with `pytest.skip()`
- **Missing HTTP-Level Testing**: No validation of actual agent card URL formats or redirect behavior

**Prevention Measures Implemented:**
- **Regression Tests**: Added `test_a2a_regression_prevention.py` with specific URL format and function call validation
- **Pre-commit Hooks**: Added `a2a-regression-check` and `no-fn-calls` to existing pre-commit setup
- **Function Call Validation**: Added `test_a2a_function_call_validation.py` to test imports without excessive mocking
- **Working Endpoints Test**: Replaced skipped test with `test_a2a_endpoints_working.py` that actually runs

**Key Learnings:**
- **Mock Only External Dependencies**: Database, APIs, file I/O - not internal function calls
- **Test What You Import**: If you import a function, test that it's actually callable
- **HTTP-Level Integration Tests**: URL formats, redirects, and header behavior can't be unit tested
- **Never Skip Critical Tests**: Disabled tests accumulate technical debt and hide regressions
- **Static Analysis Helps**: Simple pattern matching (`.fn()` calls) catches many bugs
- **Validate Response Formats**: Agent card URLs, endpoint responses must match expected patterns

**❌ Anti-Pattern (What Caused Bugs)**:
```python
# This hides import/call errors
@patch.object(handler, "_handle_get_signals_skill", new_callable=AsyncMock)
def test_skill(self, mock_skill):
    mock_skill.return_value = {"signals": []}
    # Test passes even if core_get_signals_tool.fn() is broken
```

**✅ Better Pattern (What Catches Bugs)**:
```python
# Test actual function imports and HTTP behavior
def test_core_function_callable(self):
    from src.a2a_server.adcp_a2a_server import core_get_signals_tool
    assert callable(core_get_signals_tool)  # Would catch .fn() bug

@pytest.mark.integration
def test_agent_card_url_format(self):
    response = requests.get("http://localhost:8091/.well-known/agent.json")
    url = response.json()["url"]
    assert not url.endswith("/")  # Would catch trailing slash bug
```

### Common Test Patterns
- Use `get_db_session()` context manager for database access
- Import `Principal` from `schemas` (not `models`) for business logic
- Use `TenantManagementConfig` in fixtures for admin access
- Set `sess["user"]` as a dictionary with email and role

### AdCP Compliance Testing (MANDATORY)
**🚨 ABSOLUTELY REQUIRED**: Every client-facing model must have AdCP compliance tests in `tests/unit/test_adcp_contract.py`

**WHY THIS IS CRITICAL:**
- **Production Failures**: Non-compliant models cause runtime errors and API failures
- **Client Integration Issues**: AdCP clients expect exact schema compliance
- **Data Leakage**: Internal fields exposed to clients create security risks
- **Protocol Violations**: Non-compliant responses break AdCP specification contracts

**ZERO TOLERANCE POLICY:**
- ❌ **No model can be client-facing without a compliance test**
- ❌ **No PR can merge if it adds client-facing models without tests**
- ❌ **No exceptions for "temporary" or "prototype" models**

**Comprehensive Test Requirements:**
1. **Field Coverage**: Test all required and optional AdCP fields are present
2. **Field Exclusion**: Test internal fields are excluded from external responses
3. **Field Types**: Test field types match AdCP schema expectations
4. **Field Values**: Test default values and transformations work correctly
5. **Response Structure**: Test overall response structure matches AdCP spec
6. **Enum Validation**: Test enum values match AdCP specification exactly
7. **Nested Object Validation**: Test complex nested objects (SignalDeployment, etc.)
8. **Backward Compatibility**: Test property aliases work correctly

**Current Coverage Status: 19/19 tests passing ✅**

**Test Template for New Models:**
```python
def test_[model_name]_adcp_compliance(self):
    \"\"\"Test that [ModelName] model complies with AdCP [schema-name] schema.\"\"\"
    # 1. Create model with all required + optional fields
    model = ModelName(
        required_field="value",
        optional_field="value",
        internal_field="internal_value"  # Should be excluded
    )

    # 2. Test AdCP-compliant response
    adcp_response = model.model_dump()

    # 3. Verify required AdCP fields present and non-null
    required_fields = ["field1", "field2"]  # From AdCP spec
    for field in required_fields:
        assert field in adcp_response
        assert adcp_response[field] is not None

    # 4. Verify optional AdCP fields present (can be null)
    optional_fields = ["optional1", "optional2"]  # From AdCP spec
    for field in optional_fields:
        assert field in adcp_response

    # 5. Verify internal fields excluded from external response
    internal_fields = ["tenant_id", "created_at", "metadata"]
    for field in internal_fields:
        assert field not in adcp_response

    # 6. Verify AdCP-specific business rules
    assert adcp_response["enum_field"] in ["valid", "values"]
    assert adcp_response["numeric_field"] >= 0

    # 7. Test internal model_dump includes all fields
    internal_response = model.model_dump_internal()
    for field in internal_fields:
        assert field in internal_response

    # 8. Verify field count expectations
    assert len(adcp_response) == EXPECTED_EXTERNAL_COUNT
    assert len(internal_response) >= EXPECTED_INTERNAL_COUNT
```

**Run Compliance Tests:**
```bash
# Test all AdCP contract compliance (MUST pass before any commit)
uv run pytest tests/unit/test_adcp_contract.py -v

# Test specific model compliance
uv run pytest tests/unit/test_adcp_contract.py::TestAdCPContract::test_signal_adcp_compliance -v

# Run with coverage to ensure no gaps
uv run pytest tests/unit/test_adcp_contract.py --cov=src.core.schemas --cov-report=html
```

**Development Workflow:**
1. 🔍 **Before Creating Model**: Check AdCP spec at https://adcontextprotocol.org/docs/
2. ✏️ **Write Test First**: Add compliance test before implementing model
3. 🏗️ **Implement Model**: Use `model_dump()` and `model_dump_internal()` pattern
4. ✅ **Verify Test Passes**: Ensure all assertions pass
5. 🔄 **Run Full Suite**: Verify no regressions in other tests

## Development Best Practices

### Code Style
- Use `uv` for dependency management
- Run pre-commit hooks: `pre-commit run --all-files`
- Follow existing patterns in the codebase
- Use type hints for all function signatures
- **🚨 ZERO HARDCODED IDs**: NO hardcoded external system IDs (GAM, Kevel, etc.) in code - use configuration/database only
- **🛡️ TEST SAFETY**: Never test against production systems - use dedicated test configuration files (e.g., `.gam-test-config.json`) with validation

### ⛔ NO QUIET FAILURES - CONTRACT FULFILLMENT POLICY

**CRITICAL**: We NEVER quietly fail or skip requested features. This is a CONTRACT VIOLATION with buyers.

**REQUIRED BEHAVIOR:**
1. **Requested Features MUST Work or Fail Loudly**
   - If a buyer requests device targeting, it MUST be applied or the request MUST fail
   - If geo targeting is requested, it MUST be applied or the request MUST fail
   - If any targeting dimension cannot be fulfilled, the entire request MUST fail

2. **No Silent Skipping or Graceful Degradation**
   - ❌ NEVER: Log a warning and continue without the feature
   - ❌ NEVER: Return success when requested features were skipped
   - ❌ NEVER: Silently downgrade capabilities
   - ✅ ALWAYS: Raise an exception when unable to fulfill a request
   - ✅ ALWAYS: Return an error response with clear explanation

3. **Exception Handling Pattern**
   ```python
   # ❌ WRONG - Silent failure
   if not self.supports_device_targeting:
       logger.warning("Device targeting not supported, skipping...")
       # Continue without device targeting

   # ✅ CORRECT - Explicit failure
   if not self.supports_device_targeting and targeting.device_type_any_of:
       raise TargetingNotSupportedException(
           "Device targeting requested but not supported by this adapter. "
           "Cannot fulfill buyer contract."
       )
   ```

4. **Test Requirements**
   - Every test MUST verify that requested features are actually applied
   - Tests MUST fail if features are silently skipped
   - Mock adapters MUST match real adapter behavior exactly
   - Tests MUST check for explicit errors when features are unsupported

5. **Response Validation**
   - Responses MUST include confirmation of applied features
   - Missing confirmations MUST be treated as failures
   - Partial fulfillment is NOT acceptable without explicit buyer consent

**WHY THIS MATTERS:**
- Buyers pay for specific targeting and features
- Silent failures lead to incorrect campaign delivery
- Trust erosion when contracts aren't fulfilled
- Legal and financial liability for unfulfilled contracts

### Pre-Commit Quality Gates

The project enforces several quality gates via pre-commit hooks:

**Test Quality Enforcement:**
- **`no-excessive-mocking`**: Prevents more than 10 mocks per test file
  - Encourages better test architecture (unit vs integration separation)
  - Prevents brittle, hard-to-maintain test suites
  - Forces focus on testing behavior rather than implementation details
- **`no-skip-tests`**: Prevents `@pytest.mark.skip` decorators (except `skip_ci`)
  - Ensures test suite completeness
  - Prevents accumulation of disabled tests
- **`adcp-contract-tests`**: Validates AdCP protocol compliance
  - All client-facing models must have compliance tests
  - Prevents protocol violations and data leakage
- **`a2a-regression-check`**: Prevents A2A server regressions (Dec 2024)
  - Validates agent card URL formats (no trailing slashes)
  - Tests function import/call patterns without excessive mocking
  - Runs when A2A server files change
- **`no-fn-calls`**: Prevents problematic function call patterns
  - Blocks `.fn()` call patterns that caused production bugs
  - Enforces direct function calls instead of FunctionTool wrappers

**Code Quality:**
- **Black formatting**: Consistent code style
- **Ruff linting**: Code quality and error detection
- **Type checking**: Static type validation
- **YAML/JSON validation**: Configuration file correctness

**Database Safety:**
- **`test-migrations`**: Validates database migrations can run cleanly
- **`no-tenant-config`**: Prevents deprecated `tenant.config` references

**Running Pre-commit Hooks:**
```bash
# Run all hooks on all files
pre-commit run --all-files

# Run specific hook
pre-commit run no-excessive-mocking --all-files
pre-commit run adcp-contract-tests --all-files
pre-commit run a2a-regression-check --all-files
pre-commit run no-fn-calls --all-files

# Run manual-only hooks
pre-commit run test-migrations --all-files
pre-commit run smoke-tests --all-files
```

**When Pre-commit Fails:**
1. **Fix the underlying issue** rather than bypassing with `--no-verify`
2. **For test mocking violations**: Refactor tests using patterns from "Test Quality & Mocking Best Practices"
3. **For AdCP compliance**: Add required compliance tests before merging
4. **For migration issues**: Fix migrations locally before pushing

### Import Path Patterns
- **Always use absolute imports** following project structure:
  ```python
  # ✅ Core business logic
  from src.core.schemas import Principal
  from src.core.database.database_session import get_db_session

  # ✅ Adapters and services
  from src.adapters import get_adapter
  from src.services.gam_inventory_service import GAMInventoryService

  # ✅ Operational scripts
  from scripts.ops.gam_helper import get_ad_manager_client_for_tenant
  ```
- **Service Registration Pattern**: New Flask services should register endpoints:
  ```python
  # In src/admin/app.py
  try:
      from src.services.your_service import create_endpoints
      create_endpoints(app)
      logger.info("Registered your service endpoints")
  except ImportError:
      logger.warning("your_service not found")
  ```

### Database Patterns
- Always use context managers for database sessions
- Explicit commits required (`session.commit()`)
- Handle JSONB fields properly for PostgreSQL vs SQLite

### UI Development
- Extend base.html for all templates
- Use Bootstrap classes for styling
- Test templates with `pytest tests/integration/test_template_rendering.py`
- Check form field names match backend expectations

## Deployment

### LOCAL Docker Deployment (Development Only)
```bash
# Start local development environment
docker-compose up -d

# Check health
docker-compose ps
docker-compose logs --tail=50

# Stop local environment
docker-compose down

# NOTE: This is LOCAL ONLY - does not affect Fly.io production!
```

### PRODUCTION Fly.io Deployment
```bash
# Check current production status
fly status --app adcp-sales-agent

# Deploy changes to production
fly deploy --app adcp-sales-agent

# View production logs
fly logs --app adcp-sales-agent

# SSH into production (BE CAREFUL!)
fly ssh console --app adcp-sales-agent

# Set/update secrets in production
fly secrets set GEMINI_API_KEY="your-key" --app adcp-sales-agent
fly secrets set GAM_OAUTH_CLIENT_ID="your-gam-client-id.apps.googleusercontent.com" --app adcp-sales-agent
fly secrets set GAM_OAUTH_CLIENT_SECRET="your-gam-client-secret" --app adcp-sales-agent

# IMPORTANT: Production database is separate from local!
# Production URLs:
# - Base/MCP/A2A: https://adcp-sales-agent.fly.dev
# - Custom Domain: https://sales-agent.scope3.com (tenant subdomains)
# - Admin: https://admin.sales-agent.scope3.com
```

### Domain Setup for sales-agent.scope3.com
```bash
# DNS Configuration (already configured)
# A/AAAA records for sales-agent.scope3.com → 66.241.125.123 / 2a09:8280:1::4:3c9b
# CNAME *.sales-agent.scope3.com → adcp-sales-agent.fly.dev

# SSL Certificates (configured)
fly certs create "sales-agent.scope3.com" --app adcp-sales-agent
fly certs create "*.sales-agent.scope3.com" --app adcp-sales-agent

# Verify domain setup
curl https://sales-agent.scope3.com/health
curl https://admin.sales-agent.scope3.com/health
curl https://scribd.sales-agent.scope3.com/health
```

### Deployment Checklist Before Going to Production

**🚨 CRITICAL**: ALL deployments must go through Pull Request workflow - NEVER push directly to main!

1. ✅ **Create feature branch** and work on changes
2. ✅ Test changes locally with `docker-compose up`
3. ✅ Run tests: `uv run pytest`
4. ✅ Check migrations work: `uv run python migrate.py`
5. ✅ Verify no hardcoded secrets or debug code
6. ✅ **Create Pull Request** - MANDATORY for all changes
7. ✅ **Wait for code review and approval**
8. ✅ **Merge via GitHub UI** (not command line)
9. ✅ Deploy: `fly deploy --app adcp-sales-agent`
10. ✅ Monitor logs: `fly logs --app adcp-sales-agent`
11. ✅ Verify health: `fly status --app adcp-sales-agent`

## Troubleshooting

### Common Issues and Solutions

#### "Error loading dashboard" (FIXED)
- **Historical Issue**: Admin dashboard was querying deprecated `Task` model causing schema errors
- **Resolution**: Dashboard now uses `WorkflowStep` queries for activity feed
- **Current State**: Dashboard shows workflow activity instead of task lists

#### Database schema errors with task queries (FIXED)
- **Historical Issue**: `tasks.due_date` column type mismatch (TEXT vs TIMESTAMP WITH TIME ZONE)
- **Root Cause**: Dual task systems with inconsistent schema definitions
- **Resolution**: Eliminated all task-related code, unified on workflow system
- **Lesson Learned**: Avoid duplicate data models for similar concepts

#### Port conflicts
- **Solution**: Update `.env` file:
```bash
ADMIN_UI_PORT=8001  # Change from 8052 or other conflicting port
```

#### GAM Inventory Sync Issues (FIXED - Sep 2025)
- **Issue**: Inventory browser returns `{"error": "Not yet implemented"}`
- **Root Causes**:
  1. **Import Path Issues**: Code reorganization broke import paths
  2. **Missing Endpoint Registration**: `create_inventory_endpoints()` not called during app init
  3. **Route Conflicts**: Duplicate `/inventory` routes in multiple blueprints
- **Solutions Applied**:
  1. **Fixed Import Paths**: Updated to absolute imports following project structure:
     ```python
     # ✅ Correct patterns
     from scripts.ops.gam_helper import get_ad_manager_client_for_tenant
     from src.core.database.database_session import get_db_session
     from src.core.schemas import Principal
     from src.adapters import get_adapter
     ```
  2. **Added Endpoint Registration** in `src/admin/app.py`:
     ```python
     try:
         from src.services.gam_inventory_service import create_inventory_endpoints
         create_inventory_endpoints(app)
         logger.info("Registered GAM inventory endpoints")
     except ImportError:
         logger.warning("gam_inventory_service not found")
     ```
  3. **Fixed Route Conflicts**: Commented out conflicting placeholder routes in `operations_bp`
- **Debugging Process**:
  1. Check logs for "Not yet implemented" error source
  2. Search codebase for all instances of error message
  3. Identify route registration order and conflicts
  4. Test import paths systematically
- **Prevention**: Always use absolute imports and verify endpoint registration for new services

## Schema Validation

The project includes AdCP protocol schema validation for compliance testing:

### Features
- **Automatic validation** of all AdCP protocol requests/responses
- **Multi-version support** (v1 schemas cached, ready for v2)
- **Offline validation** for reliable CI without network dependencies
- **37 cached schemas** (~160KB) for complete protocol coverage
- **Protocol layering awareness** - correctly handles MCP/A2A wrapper fields

### Protocol Layering
The validation system understands the distinction between protocol layers:

- **AdCP Application Layer**: The actual data payload defined by AdCP schemas (e.g., `products` field for get_products)
- **MCP/A2A Transport Layer**: Protocol wrapper fields added by transport protocols (e.g., `message`, `context_id`)

**Server Compliance**: Response models now strictly follow AdCP spec, containing only spec-defined fields. Transport-layer concerns are handled by FastMCP/A2A protocols.

**Validation Intelligence**: The validator automatically extracts the AdCP payload from protocol wrappers before validation, ensuring:
- Server responses are strictly AdCP-compliant (no extra fields)
- MCP/A2A can add transport metadata without breaking validation
- Clear separation between application and transport layers

### Usage
```bash
# Run schema compliance tests
uv run pytest tests/e2e/test_adcp_schema_compliance.py -v

# E2E tests with automatic validation (default)
uv run pytest tests/e2e/test_adcp_full_lifecycle.py -v
```

### Schema Management
- **Location**: `tests/e2e/schemas/v1/` (checked into git)
- **Update**: Manual updates when AdCP specification changes
- **Versions**: Currently v1, structured for future v2+ support

## Security & Authentication

### Admin Authentication Architecture

The admin authentication system uses a **environment-first approach** with database fallback for robust, secure access control:

#### Current Implementation ✅
```python
def is_super_admin(email):
    """Environment-first authentication with database fallback."""
    # 1. Check environment variables first (deployment-time config)
    env_emails = os.environ.get("SUPER_ADMIN_EMAILS", "")
    if env_emails:
        emails_list = [e.strip().lower() for e in env_emails.split(",") if e.strip()]
        if email.lower() in emails_list:
            return True

    # 2. Fallback to database configuration (runtime config)
    try:
        with get_db_session() as db:
            config = db.query(TenantManagementConfig).filter_by(
                config_key="super_admin_emails"
            ).first()
            if config and config.config_value:
                db_emails = [e.strip().lower() for e in config.config_value.split(",")]
                return email.lower() in db_emails
    except Exception as e:
        logger.error(f"Database auth check failed: {e}")

    return False
```

#### Session Optimization ✅
- **Session Caching**: Super admin status cached in session to avoid redundant database calls
- **Trust Session State**: `require_tenant_access()` checks session first, then validates if needed
- **Automatic Caching**: Session updated when admin status is confirmed

#### Security Recommendations for Future Enhancement

**HIGH Priority:**
1. **Session Timeout & Re-validation**:
   ```python
   # Add to require_tenant_access decorator
   max_session_age = 3600  # 1 hour
   session_start = session.get("authenticated_at", 0)
   if time.time() - session_start > max_session_age:
       session.clear()
       return redirect(url_for("auth.login"))

   # Re-validate admin status every 5 minutes
   last_check = session.get("admin_check_at", 0)
   if time.time() - last_check > 300:
       session["is_super_admin"] = is_super_admin(email)
       session["admin_check_at"] = time.time()
   ```

2. **Enhanced Audit Logging**:
   ```python
   def audit_admin_access(email, tenant_id, action, success=True):
       """Log all admin access attempts with IP and user agent."""
       audit_log = AuditLog(
           email=email,
           tenant_id=tenant_id,
           action=f"admin_access_{action}",
           success=success,
           ip_address=request.remote_addr,
           user_agent=request.user_agent.string,
           timestamp=datetime.utcnow()
       )
   ```

**MEDIUM Priority:**
3. **Secure Session Configuration**:
   ```python
   app.config.update(
       SESSION_COOKIE_SECURE=True,      # HTTPS only
       SESSION_COOKIE_HTTPONLY=True,    # No JavaScript access
       SESSION_COOKIE_SAMESITE='Lax',   # CSRF protection
       PERMANENT_SESSION_LIFETIME=3600  # 1 hour timeout
   )
   ```

4. **Secrets Management**: Move from `.env` files to proper secrets management (Fly.io secrets, Kubernetes secrets, etc.)

#### Security Testing Requirements

All authentication changes must include tests for:
- Session timeout behavior
- Re-validation logic
- Environment vs database precedence
- Audit logging completeness
- Session security headers
- CSRF protection

**Test Location**: `tests/integration/test_product_deletion.py` contains comprehensive authentication tests including environment-first approach validation.

### Access Control Patterns

- **Super Admins**: Full access to all tenants via environment/database configuration
- **Tenant Users**: Limited access to specific tenants via User model
- **Principal Isolation**: Each advertiser (principal) has isolated access tokens
- **Audit Trail**: All admin actions logged to `audit_logs` table

## Support

For issues or questions:
- Check existing documentation in `/docs`
- Review test examples in `/tests`
- Consult adapter implementations in `/adapters`
