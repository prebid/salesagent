# Fly.io Deployment

This walkthrough covers deploying the Prebid Sales Agent to Fly.io. The reference implementation maintained by Prebid uses this setup.

> **Single-Tenant by Default**: Fly.io deployments run in single-tenant mode by default, which is appropriate for most publishers deploying their own sales agent. Session cookies use the actual request domain, so authentication works with any custom domain. For multi-tenant mode with subdomain routing, see [Multi-Tenant Setup](../multi-tenant.md).

> **Template**: A ready-to-use `fly.toml` template is available at [`fly.toml.template`](fly.toml.template). Copy it to your project root and customize.

## Prerequisites

1. [Fly.io account](https://fly.io)
2. Fly CLI installed: `brew install flyctl` (macOS) or see [installation docs](https://fly.io/docs/hands-on/install-flyctl/)

## Step 1: Authenticate

```bash
fly auth login
```

## Step 2: Create Application

```bash
fly apps create your-app-name
```

## Step 3: Create PostgreSQL Database

Choose one of these database options:

## Warning

Option A below is not yet fixed to work in the new repo location

### Option A: Fly Managed Postgres (Recommended)

[Fly Managed Postgres](https://fly.io/docs/mpg/) is Fly's fully-managed database service with automatic backups, high availability, and 24/7 support.

```bash
# Create Managed Postgres cluster
fly mpg create --name your-app-db --region iad --plan basic

# Attach to your app (automatically sets DATABASE_URL)
fly mpg attach your-app-db -a your-app-name
```

> **Plan Options**: `basic` ($38/month, 1GB RAM) is sufficient for most deployments. See [Fly MPG pricing](https://fly.io/docs/mpg/) for other plans.

### Option B: Fly Postgres (Self-Managed)

[Fly Postgres](https://fly.io/docs/postgres/) runs PostgreSQL as a Fly app that you manage yourself. Lower cost, but you handle backups and maintenance.

```bash
# Create PostgreSQL cluster
fly postgres create --name your-app-db \
  --region iad \
  --initial-cluster-size 1 \
  --vm-size shared-cpu-1x \
  --volume-size 10

# Attach to your app (automatically sets DATABASE_URL)
fly postgres attach your-app-db --app your-app-name
```

### Verify Database Connection

```bash
fly secrets list --app your-app-name
```

## Step 4: Set Required Secrets

Before deploying, set the encryption key used for storing sensitive data (like OIDC client secrets):

```bash
# Generate and set encryption key
fly secrets set ENCRYPTION_KEY="$(openssl rand -base64 32)"
```

> **Important**: Store this key securely. If lost, encrypted data (OIDC credentials) will need to be reconfigured.

## Step 5: Authentication

Authentication is configured **per-tenant** via the Admin UI. No OAuth environment variables are required for initial deployment.

### Initial Setup Flow

1. Deploy the application (Step 6 below)
2. Access the Admin UI at `https://your-app-name.fly.dev/admin`
3. Log in with test credentials (Setup Mode is enabled by default for new tenants):
   - Email: `test_super_admin@example.com`
   - Password: `test123`
4. Go to **Users & Access** and configure your SSO provider (Google, Microsoft, Okta, Auth0, Keycloak, or any OIDC provider)
5. Copy the **Redirect URI** shown and add it to your provider: `https://your-app-name.fly.dev/auth/oidc/callback`
6. **Add yourself**: Add your email as a user OR add your domain to Allowed Domains
7. Click **Save Configuration**, then **Test Connection** - SSO is automatically enabled on success
8. Click **Disable Setup Mode** to require SSO for all logins

> **Important**: You must add yourself as a user or add your email domain BEFORE testing. Otherwise you'll see "Access denied" after authenticating.

See [SSO Setup Guide](../../user-guide/sso-setup.md) for detailed provider-specific instructions.

### Legacy: Global Super Admin (Optional)

For backward compatibility or multi-tenant deployments where you need global admin access:

```bash
# Optional: Global super admin access
fly secrets set SUPER_ADMIN_EMAILS="admin@example.com,admin2@example.com"

# Optional: Grant admin to all users in a domain
fly secrets set SUPER_ADMIN_DOMAINS="example.com"
```

**Format:**
- `SUPER_ADMIN_EMAILS`: Comma-separated, no spaces: `user1@example.com,user2@example.com`
- `SUPER_ADMIN_DOMAINS`: Comma-separated domains: `example.com,company.org`

## Step 6: Deploy

**Option A: Use prebuilt image (recommended)**
```bash
fly deploy --image docker.io/prebid/salesagent:latest
```

**Option B: Build from source**
```bash
# Clone the repository first
git clone https://github.com/prebid/salesagent.git
cd salesagent
fly deploy
```

The first deploy runs database migrations automatically. Watch the logs:
```bash
fly logs
```

## Step 7: Verify

```bash
# Check health
curl https://your-app-name.fly.dev/health

# Check status
fly status --app your-app-name
```

## Accessing Services

| Service | URL |
|---------|-----|
| Admin UI | https://your-app-name.fly.dev/admin |
| MCP Server | https://your-app-name.fly.dev/mcp/ |
| Health Check | https://your-app-name.fly.dev/health |

> **Authentication**: Visiting `/admin` without being logged in will redirect you to the login page. After successful authentication, you'll be redirected back to the Admin UI.

## Monitoring

```bash
# View logs
fly logs

# Check status
fly status

# SSH into machine
fly ssh console

# Open dashboard
fly dashboard
```

## Scaling

```bash
# Horizontal scaling
fly scale count 2 --region iad

# Vertical scaling
fly scale vm shared-cpu-2x
fly scale memory 2048
```

## Troubleshooting

### Database connection issues

```bash
# Verify DATABASE_URL is set
fly secrets list --app your-app-name | grep DATABASE

# Check attached databases
fly mpg list        # For Managed Postgres
fly postgres list   # For self-managed Postgres

# Test database connectivity
fly ssh console --app your-app-name -C "python -c \"from src.core.database.db_config import get_db_connection; print(get_db_connection())\""
```

### Migrations not running

Migrations run automatically on startup. To run manually:
```bash
fly ssh console --app your-app-name -C "cd /app && python scripts/ops/migrate.py"
```

### Cannot access Admin UI

1. **Using Setup Mode (recommended):** Log in with test credentials:
   - Email: `test_super_admin@example.com`
   - Password: `test123`

   Then configure SSO in **Users & Access** and disable Setup Mode.

2. **"Access denied" after SSO login:** You need to add yourself first:
   - Go back to the login page (use test credentials if Setup Mode is still enabled)
   - Go to **Users & Access**
   - Add your email as a user OR add your domain to Allowed Domains
   - Try the SSO test again

3. **Using legacy global super admin:** If you set `SUPER_ADMIN_EMAILS`, verify the format:
   ```bash
   fly ssh console --app your-app-name -C "echo \$SUPER_ADMIN_EMAILS"
   ```
   - Correct: `user1@example.com,user2@example.com`
   - Wrong: `["user1@example.com"]` (JSON array)
   - Wrong: `user1@example.com, user2@example.com` (spaces)

4. Restart to pick up changes:
   ```bash
   fly apps restart your-app-name
   ```

### Force restart

```bash
fly apps restart your-app-name
```

## Configuration Files

The deployment uses these files from the repository:
- `fly.toml` - Main Fly.io configuration
- `Dockerfile` - Docker image with nginx and supercronic
- `scripts/deploy/run_all_services.py` - Service orchestration

## Costs

**With Managed Postgres:**
- App VM (shared-cpu-2x): ~$10/month
- Managed Postgres (basic): $38/month
- Storage: ~$0.28/GB/month
- **Total: ~$50/month**

**With Self-Managed Postgres:**
- App VM (shared-cpu-1x): ~$5/month
- Postgres VM (shared-cpu-1x): ~$7/month
- Volume storage: ~$0.15/GB/month
- **Total: ~$15/month**
