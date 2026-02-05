# Multi-Tenant Setup Guide

This guide covers setting up the Prebid Sales Agent in multi-tenant mode, where a single deployment hosts multiple publishers with subdomain-based routing.

> **Prerequisites**: This guide assumes you have a working single-tenant deployment. See [Single-Tenant Deployment](single-tenant.md) for Docker images, environment variables, and basic setup.

## When to Use Multi-Tenant Mode

**Single-Tenant (Default):** One publisher per deployment. Simple path-based routing (`/admin`, `/mcp`, `/a2a`). Most publishers should use this.

**Multi-Tenant:** Multiple publishers on one deployment. Subdomain-based routing (`publisher1.yourdomain.com`, `publisher2.yourdomain.com`). For platforms hosting multiple publishers.

## Step 1: Enable Multi-Tenant Mode

Set the `ADCP_MULTI_TENANT` environment variable:

```bash
# Fly.io
fly secrets set ADCP_MULTI_TENANT=true --app your-app-name

# Docker
ADCP_MULTI_TENANT=true docker compose up -d

# Cloud Run
gcloud run services update salesagent \
  --update-env-vars "ADCP_MULTI_TENANT=true"
```

## Step 2: Configure Domain Environment Variables

Set your base domain configuration:

```bash
# Your base domain
BASE_DOMAIN=yourdomain.com

# Where the sales agent is hosted
SALES_AGENT_DOMAIN=sales-agent.yourdomain.com

# Where the admin UI is accessible
ADMIN_DOMAIN=admin.sales-agent.yourdomain.com

# Domain for super admin emails (users from this domain get super admin access)
SUPER_ADMIN_DOMAIN=yourdomain.com
```

> **Session Cookies in Multi-Tenant Mode**: When `ADCP_MULTI_TENANT=true`, session cookies are automatically scoped to `SALES_AGENT_DOMAIN` to work across all tenant subdomains. This allows users to authenticate at `admin.sales-agent.yourdomain.com` and access tenant dashboards at `tenant.sales-agent.yourdomain.com`. In single-tenant mode (default), cookies use the actual request domain instead.

## Step 3: DNS Configuration

### Wildcard DNS (for subdomain routing)

Point a wildcard DNS record to your deployment:

```
*.sales-agent.yourdomain.com → your-deployment-ip
```

For Fly.io:
```bash
fly ips list --app your-app-name
# Add A/AAAA records for the IPs shown
```

### SSL Certificates

- **Fly.io**: Automatic wildcard SSL
- **Cloud Run**: Use Cloud Load Balancer with managed certificates
- **Docker**: Use Caddy, nginx with certbot, or a reverse proxy with wildcard cert

## Step 4: Custom Domains with Approximated (Optional)

Approximated is a proxy service that allows tenants to use their own custom domains (e.g., `sales.publisher.com`) instead of subdomains.

### Environment Variables

```bash
# Approximated API credentials
APPROXIMATED_API_KEY=your-approximated-api-key

# The backend URL Approximated will proxy to
APPROXIMATED_BACKEND_URL=sales-agent.yourdomain.com
```

### How It Works

1. Tenant sets their custom domain in Admin UI (Settings > Account > Virtual Host)
2. System configures Approximated proxy for that domain
3. Tenant adds a CNAME record: `sales.publisher.com → proxy.approximated.app`
4. Requests to `sales.publisher.com` are proxied to your deployment
5. The `Apx-Incoming-Host` header identifies which tenant

### Admin UI Configuration

1. Go to **Settings > Account**
2. Set **Virtual Host** to the tenant's custom domain (e.g., `sales.publisher.com`)
3. The system will show DNS instructions for the tenant
4. Click **Configure Custom Domain** to set up the Approximated proxy

## Step 5: Create Tenants

### Via Admin UI

1. Log in as a super admin at `https://admin.sales-agent.yourdomain.com`
2. Click **Create Tenant**
3. Enter:
   - **Name**: Publisher display name
   - **Subdomain**: e.g., `acme` → `acme.sales-agent.yourdomain.com`
   - **Virtual Host** (optional): Custom domain like `sales.acmepublisher.com`
4. Configure the ad server adapter (Mock or GAM)

### Via Script

```bash
# Docker
docker compose exec adcp-server python -m scripts.setup.setup_tenant \
  "Acme Publisher" \
  --subdomain acme \
  --adapter mock

# Fly.io
fly ssh console -C "python -m scripts.setup.setup_tenant 'Acme Publisher' \
  --subdomain acme \
  --adapter mock"
```

## Step 6: Per-Tenant GAM Setup

Each tenant using Google Ad Manager needs their own service account.

### Option A: Automatic Provisioning (Recommended)

If you've configured GCP service account provisioning:

1. Go to tenant **Settings > Ad Server**
2. Select **Google Ad Manager** adapter
3. Click **Provision Service Account**
4. The system creates a GCP service account and shows the email
5. Have the publisher add this email as a **Trafficker** in their GAM

See [GAM Service Account Setup](../adapters/gam/service-account-setup.md) for details.

### Option B: Manual Configuration

1. Publisher creates their own GCP service account
2. Publisher exports the JSON key file
3. In Admin UI, paste the service account JSON in **Settings > Ad Server**

## Step 7: Tenant Requirements

Before a tenant can create media buys, they need:

1. **Currency Limits**: At least USD configured (Settings > Currencies)
2. **Property Tags**: At least `all_inventory` tag (Settings > Property Tags)
3. **Products**: At least one product configured (Products page)
4. **Advertisers**: At least one advertiser/principal (Advertisers page)

**Note:** In multi-tenant mode, SSO is **optional** per-tenant. The platform manages authentication centrally, so individual tenants can skip SSO configuration. In single-tenant mode, SSO is required before accepting orders.

The Admin UI shows a setup checklist for each tenant.

## Subdomain Routing

In multi-tenant mode, the system routes requests based on:

1. **Host header**: `acme.sales-agent.yourdomain.com` → tenant `acme`
2. **Apx-Incoming-Host header**: For Approximated proxy requests
3. **x-adcp-tenant header**: Explicit tenant override (advanced)

Example MCP client configuration:

```python
# Subdomain-based routing
transport = StreamableHttpTransport(
    url="https://acme.sales-agent.yourdomain.com/mcp/",
    headers={"x-adcp-auth": "advertiser-token"}
)

# Or with custom domain
transport = StreamableHttpTransport(
    url="https://sales.acmepublisher.com/mcp/",
    headers={"x-adcp-auth": "advertiser-token"}
)
```

## Troubleshooting

### "No tenant context" error

- Verify the subdomain/domain is configured for a tenant
- Check that the Host header is being passed correctly
- For Approximated: verify `Apx-Incoming-Host` header is present

### Custom domain not working

1. Check DNS: `dig sales.publisher.com` should show Approximated IPs
2. Verify `APPROXIMATED_API_KEY` is set
3. Check tenant's `virtual_host` field is set correctly
4. Verify Approximated proxy is configured (Admin UI shows status)

### Tenant can't create media buys

Check the setup checklist in Admin UI:
- Currency limits configured?
- Property tags exist?
- Products configured?
- Adapter connected?

## Related Documentation

- [Single-Tenant Deployment](single-tenant.md) - Standard deployment guide
- [GAM Service Account Setup](../adapters/gam/service-account-setup.md) - Per-tenant GAM configuration
- [GCP Service Account Provisioning](../adapters/gam/gcp-provisioning.md) - Automatic service account creation
