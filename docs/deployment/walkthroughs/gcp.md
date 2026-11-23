# GCP Cloud Run Deployment

This walkthrough covers deploying the Prebid Sales Agent to Google Cloud Run with Cloud SQL PostgreSQL.

## Prerequisites

1. [Google Cloud Project](https://console.cloud.google.com) with billing enabled
2. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated

## Step 1: Enable Required APIs

Enable the necessary GCP APIs:

```bash
gcloud services enable sqladmin.googleapis.com sql-component.googleapis.com run.googleapis.com
```

## Step 2: Create Cloud SQL PostgreSQL

Create a PostgreSQL instance (sandbox tier is fine for testing):

```bash
# Create instance
gcloud sql instances create adcp-sales-agent \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --root-password=YOUR_SECURE_PASSWORD

# Create database
gcloud sql databases create salesagent --instance=adcp-sales-agent
```

Or use the [Cloud SQL Console](https://console.cloud.google.com/sql/instances/create;engine=PostgreSQL).

Note the **Connection name** from the instance overview (e.g., `your-project:us-central1:adcp-sales-agent`).

## Step 3: Deploy

Deploy the application. Step 4 configures the first sign-in, and each tenant then configures its own SSO in the Admin UI.

### Option A: Use Prebuilt Image (Recommended)

Fastest way to get started - uses the official Docker Hub image:

```bash
gcloud run deploy adcp-sales-agent \
  --image prebid/salesagent:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --port 8000 \
  --no-cpu-throttling \
  --min-instances=1 \
  --add-cloudsql-instances YOUR_PROJECT:us-central1:adcp-sales-agent \
  --set-env-vars "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@/salesagent?host=/cloudsql/YOUR_PROJECT:us-central1:adcp-sales-agent,ENCRYPTION_KEY=YOUR_ENCRYPTION_KEY,FLASK_SECRET_KEY=YOUR_SESSION_KEY"
```

Replace:
- `YOUR_PROJECT`: Your GCP project ID
- `YOUR_PASSWORD`: The password you set when creating the Cloud SQL instance
- `YOUR_ENCRYPTION_KEY`: A Fernet key. Generate one with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. See [`ENCRYPTION_KEY`](../environment-variables.md#security) for what it encrypts and why it must stay stable.
- `YOUR_SESSION_KEY`: A long random value that signs session cookies, such as the output of `openssl rand -hex 32`. Without it, each instance generates its own, and sessions end at every restart.

### Option B: Build Your Own Image

If you want to build from source or make custom modifications:

```bash
# Build and push image
gcloud builds submit --tag gcr.io/YOUR_PROJECT/adcp-sales-agent

# Deploy
gcloud run deploy adcp-sales-agent \
  --image gcr.io/YOUR_PROJECT/adcp-sales-agent \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --port 8000 \
  --no-cpu-throttling \
  --min-instances=1 \
  --add-cloudsql-instances YOUR_PROJECT:us-central1:adcp-sales-agent \
  --set-env-vars "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@/salesagent?host=/cloudsql/YOUR_PROJECT:us-central1:adcp-sales-agent,ENCRYPTION_KEY=YOUR_ENCRYPTION_KEY,FLASK_SECRET_KEY=YOUR_SESSION_KEY"
```

**Important Flags Explained:**
- `--no-cpu-throttling`: Required because the app runs multiple background services (MCP server, Admin UI, A2A server, nginx) that need continuous CPU allocation
- `--min-instances=1`: Keeps at least one instance warm so the services stay running

Note your service URL from the output (e.g., `https://adcp-sales-agent-abc123-uc.a.run.app`).

## Step 4: Initial Setup

The first sign-in uses the deployment's own OAuth client, with an address listed in
`SUPER_ADMIN_EMAILS`. Set both on the service with the command for your provider.

**Google OAuth:**
```bash
gcloud run services update adcp-sales-agent \
  --region us-central1 \
  --update-env-vars "GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com" \
  --update-env-vars "GOOGLE_CLIENT_SECRET=your-client-secret" \
  --update-env-vars "SUPER_ADMIN_EMAILS=you@example.com"
```

**Other OIDC Providers (Okta, Auth0, Azure AD):**
```bash
gcloud run services update adcp-sales-agent \
  --region us-central1 \
  --update-env-vars "OAUTH_CLIENT_ID=your-client-id" \
  --update-env-vars "OAUTH_CLIENT_SECRET=your-client-secret" \
  --update-env-vars "OAUTH_DISCOVERY_URL=https://your-provider/.well-known/openid-configuration" \
  --update-env-vars "SUPER_ADMIN_EMAILS=you@example.com"
```

Register both of the following redirect URIs with the OAuth client.
[Deployment OAuth](../environment-variables.md#deployment-oauth) explains which callback each
sign-in uses.

- `https://YOUR-SERVICE-URL.run.app/admin/auth/google/callback`
- `https://YOUR-SERVICE-URL.run.app/auth/google/callback`

Then sign in with the following steps:

1. Open `https://YOUR-SERVICE-URL.run.app/admin/`
2. Log in with the deployment's own OAuth account (the address you put in
   `SUPER_ADMIN_EMAILS`). Setup Mode, enabled by default for new tenants, lets you verify
   a tenant's OIDC login before you enable it.
3. Verify you can access the Admin UI

## Step 5: Configure SSO (Production)

Configure SSO via the Admin UI for production use.

Follow the [SSO quick start](../../user-guide/sso-setup.md#quick-start). In its first step,
set **Custom Domain** to your service's host, for example
`adcp-sales-agent-abc123-uc.a.run.app`. **Users & Access** then shows the **Required Redirect
URI** `https://adcp-sales-agent-abc123-uc.a.run.app/admin/auth/oidc/callback`, which you add
to your SSO provider (Google, Microsoft, Okta, Auth0, Keycloak, or any OIDC provider).

See [SSO Setup Guide](../../user-guide/sso-setup.md) for detailed provider-specific instructions.

## Step 6: Custom Domain (Optional)

```bash
gcloud beta run domain-mappings create \
  --service adcp-sales-agent \
  --domain sales-agent.yourcompany.com \
  --region us-central1
```

If you use a custom domain, register the two callback URLs from Step 4 on that domain with the OAuth client as well.

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Cloud SQL connection string |
| `ENCRYPTION_KEY` | Yes | Fernet key that encrypts stored secrets. The container refuses to start without it |
| `FLASK_SECRET_KEY` | Yes | Signs session cookies. Unset, each instance generates its own, and sessions end at every restart |
| `GEMINI_API_KEY` | No | Platform-level AI key (tenants can configure their own) |
| `SUPER_ADMIN_EMAILS` | For sign-in | Comma-separated super admin emails |
| `GOOGLE_CLIENT_ID` | For sign-in | Google OAuth client ID of the deployment's own sign-in, or use the `OAUTH_*` variables from Step 4 |
| `GOOGLE_CLIENT_SECRET` | For sign-in | Google OAuth client secret, read with `GOOGLE_CLIENT_ID` |

Each tenant configures its own SSO in the Admin UI. Until a tenant enables SSO, its users sign in through the deployment's OAuth client. See [SSO Setup Guide](../../user-guide/sso-setup.md).

## Troubleshooting

### Database connection failed - "No such file or directory"

The DATABASE_URL format should use the Cloud SQL connector:
```
postgresql://user:pass@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE
```

If you prefer using public IP instead:
1. Get the instance IP: `gcloud sql instances describe adcp-sales-agent --format="value(ipAddresses[0].ipAddress)"`
2. Enable authorized networks: `gcloud sql instances patch adcp-sales-agent --authorized-networks=0.0.0.0/0`
3. Use: `postgresql://user:pass@INSTANCE_IP:5432/dbname`

### Password authentication failed

Special characters in passwords need URL encoding:
- `&` → `%26`
- `=` → `%3D`
- `*` → `%2A`
- `#` → `%23`

### 502 Bad Gateway errors

If you can access `/health` but get 502 errors on `/admin` or `/mcp`, the deployment is missing required flags:
```bash
gcloud run services update adcp-sales-agent \
  --region us-central1 \
  --no-cpu-throttling \
  --min-instances=1
```

The `--no-cpu-throttling` flag is required because the application runs multiple background services that need continuous CPU allocation.

### Redeploy after configuration changes

```bash
gcloud run services update adcp-sales-agent \
  --region us-central1 \
  --update-env-vars "DATABASE_URL=postgresql://..."
```

### View logs

```bash
gcloud run services logs read adcp-sales-agent --region us-central1
```

## Cost Considerations

- **Cloud SQL db-f1-micro**: ~$10/month (can stop when not in use)
- **Cloud Run with --no-cpu-throttling and --min-instances=1**:
  - Always-allocated CPU: ~$30-40/month for 1 vCPU running 24/7
  - Memory (1Gi): Included in CPU cost
  - These settings are required for the application to work correctly
- **Container Registry**: ~$0.10/GB storage

For production, consider upgrading Cloud SQL to a larger tier.

**Note**: Cloud Run's `--no-cpu-throttling` and `--min-instances=1` flags result in higher costs than typical Cloud Run deployments, but are necessary because this application runs multiple background services (MCP server, Admin UI, A2A server, nginx) that require continuous CPU allocation.
