# Quickstart Guide

Run the Prebid Sales Agent locally, sign in to the Admin UI, and make a first call from an
AI agent. Follow the steps in order: the Admin UI has no password login, so Google OAuth has
to be configured before the first start.

## Prerequisites

- Docker with Docker Compose v2 (`docker compose version`)
- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/), to run the example agent
  call in step 7. It fetches Python and the `fastmcp` client for that one command. Without
  uv, use Python 3.12 or later with `pip install fastmcp`.
- A Google account, and access to the [Google Cloud Console](https://console.cloud.google.com/)
  to create an OAuth client. This is required: sign-in to the Admin UI is OAuth only.

## 1. Clone the repository and create `.env`

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
cp .env.template .env
```

`docker compose` reads `.env` from the repository root (the directory holding
`docker-compose.yml`). Both the `db-init` and `adcp-server` services load it with
`env_file`, so every setting below, secrets included, goes in `.env`. For other compose
files and production, see [Which file holds secrets](security.md#which-file-holds-secrets).

## 2. Create a Google OAuth client

In the Google Cloud Console:

1. Select a project, or create one.
2. Open **Google Auth Platform** (also listed as **APIs & Services > OAuth consent screen**)
   and complete the setup if the project has none: an app name, a support email, and an
   audience. With **Internal**, any account in your Google Workspace can sign in. With
   **External** in the "Testing" publishing status, only accounts listed under
   **Audience > Test users** can sign in, so add your own address there.
3. Open **Clients** (also listed as **APIs & Services > Credentials**) and choose
   **Create client** (or **Create credentials > OAuth client ID**).
4. Set **Application type** to **Web application**.
5. Under **Authorized redirect URIs**, add both of these:

   ```
   http://localhost:8000/auth/google/callback
   http://localhost:8000/admin/auth/google/callback
   ```

   [Deployment OAuth](deployment/environment-variables.md#deployment-oauth) explains why
   both are needed. If you change the published port with `CONDUCTOR_PORT`, use that port in both URIs.
   No authorized JavaScript origins are needed.
6. Choose **Create**, then copy the **Client ID** and **Client secret**.

The application requests the `openid email profile` scopes, which need no Google
verification.

## 3. Configure `.env`

Add these lines to `.env`:

```bash
# The OAuth client from step 2
GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...

# The Google account(s) that may sign in as super admin (comma-separated)
SUPER_ADMIN_EMAILS=you@example.com

# Fernet key for secrets the application stores encrypted (SSO client secrets,
# GAM credentials). Generate once and keep it: changing it makes stored secrets unreadable.
ENCRYPTION_KEY=
```

Generate the `ENCRYPTION_KEY` value with either command:

```bash
# With OpenSSL (a Fernet key is 32 random bytes in URL-safe base64)
openssl rand -base64 32 | tr '+/' '-_'

# With Python and the cryptography package
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

`.env.template` already sets `ADCP_SIGNING_KEY_PASSPHRASE_ENV` and `ADCP_SIGNING_DEV_KEK`,
the development-only key encryption key for request-signing keys. Leave them for local use.

Every variable is documented in
[Environment variables](deployment/environment-variables.md#authentication). Instead of the
Google variables you can use any OIDC provider with `OAUTH_DISCOVERY_URL`,
`OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET`; the callback paths are the same.

## 4. Start the stack

```bash
docker compose up -d
```

The first run builds the image, which takes several minutes. The `db-init` service runs the
database migrations and then creates the `default` tenant; `adcp-server` starts after it
finishes. The proxy starts as soon as `adcp-server` has started, not when it is ready, so a
request sent too early gets `502 Bad Gateway`. Wait until `adcp-server` reports `(healthy)`,
which takes about a minute after the build:

```bash
docker compose ps        # repeat until adcp-server shows (healthy)
curl http://localhost:8000/health
```

If you edit `.env` later, run `docker compose up -d --force-recreate` so the containers pick
up the change.

## 5. Sign in

1. Open http://localhost:8000/admin
2. You are sent to Google. Sign in with an address listed in `SUPER_ADMIN_EMAILS`.
3. You land on the dashboard of the `default` tenant.

If the login page says "Authentication not configured" instead of sending you to Google,
the OAuth variables did not reach the container; see
[The login page offers no way to sign in](development/troubleshooting.md#the-login-page-offers-no-way-to-sign-in).
Other sign-in failures are in
[Troubleshooting: Authentication issues](development/troubleshooting.md#authentication-issues).

There is no password login. A route that accepted a fixed password existed for local use and
is gone: it was composed only when a test-mode flag was set, which made the app under test a
different app from the deployed one, and it minted an operator session from a default
password.

## 6. Use the `default` tenant, or create one

The stack runs in single-tenant mode (`ADCP_MULTI_TENANT` unset), and `db-init` creates one
tenant on first start:

| Field | Value |
|---|---|
| Tenant id | `default` |
| Name | Default Publisher |
| Adapter | Mock (`ad_server=mock`), which simulates an ad server |
| `virtual_host` | `localhost:8080` (the application's internal port) |
| Authorized emails | the `SUPER_ADMIN_EMAILS` addresses at the time of creation |

It starts with no currencies, products or advertisers. The tenant id is the path segment
after `/tenant/` in the Admin UI address bar: the dashboard of this tenant is
http://localhost:8000/tenant/default. The **Setup Checklist** on the dashboard lists what is
still missing.

To get an agent to the point of seeing products:

1. **A currency**: **Settings > Policies & Workflows > Add Currency** (for example USD).
2. **A publisher**: **Settings > Publishers**. A product sells the inventory of a publisher
   that authorizes this agent: the application fetches
   `https://<domain>/.well-known/adagents.json` and accepts the publisher only if that file
   lists this tenant's agent URL. The agent URL is the tenant's `virtual_host` with a scheme;
   for `default` on this stack it is `http://localhost:8080`. The product form refuses to
   save without an authorized publisher, even with the Mock adapter, and nothing on a fresh
   stack seeds one. So this step needs a domain where you can publish an `adagents.json`.
   There is no local shortcut yet; [#2624](https://github.com/prebid/salesagent/issues/2624)
   proposes one.
3. **Products**: **Settings > Products > Create Product**, selecting the publisher's
   properties. `get_products` returns an empty list until the tenant has at least one
   product. See the [products guide](user-guide/products.md).
4. **An advertiser and its token**: **Settings > Advertisers > Add Advertiser**. Copy the
   API token when it is shown; see [API Token](user-guide/advertisers.md#api-token).

To configure SSO for the tenant (so that sign-in no longer depends on the deployment-wide
OAuth client), follow the [SSO quick start](user-guide/sso-setup.md#quick-start). In its
first step, set **Custom Domain** to `localhost:8000`, the host where you open the Admin UI.

### Creating a tenant from the command line

`scripts/setup/setup_tenant.py` creates a tenant with the mock adapter, USD/EUR/GBP
currency limits and one advertiser, and prints that advertiser's token:

```bash
docker compose exec adcp-server python -m scripts.setup.setup_tenant "Demo Publisher" \
  --tenant-id demo --virtual-host demo.localhost:8000
```

`--virtual-host` is required and must be unique across tenants. An agent addresses this
tenant with `x-adcp-tenant: demo`. The script creates no products; add them in the Admin UI
as above. A super admin can open the new tenant at http://localhost:8000/tenant/demo.

### About `CREATE_DEMO_TENANT`

`CREATE_DEMO_TENANT=true` has no effect on this stack. It is read only by `init_db()`, which
the image's own entrypoint (`scripts/deploy/run_all_services.py`) runs and
`docker-compose.yml` does not, and `init_db()` only builds the demo tenant when no `default`
tenant exists yet. In single-tenant mode the migration step creates `default` first, so the
demo tenant, its `ci-test-token` advertiser and the `CREATE_SAMPLE_DATA` products are never
created.

## 7. Connect an AI agent

A request has to say which seller it is for. A tenant is served at the host it declares in
`virtual_host`, but the `Host` a local agent sends is `localhost:8000`, which no tenant
declares. So a local call names the tenant explicitly with the `x-adcp-tenant` header,
carrying the tenant id. This is the same header the test suites use. A request naming no
tenant is refused (see
[No seller is served at the address this request named](development/troubleshooting.md#no-seller-is-served-at-the-address-this-request-named)).

Save this as `first_call.py`:

```python
import asyncio

from fastmcp.client import Client, StreamableHttpTransport

transport = StreamableHttpTransport(
    url="http://localhost:8000/mcp/",
    headers={
        # The advertiser token from step 6 (Settings > Advertisers)
        "Authorization": "Bearer <advertiser-token>",
        # The tenant id from the Admin UI address bar: /tenant/<tenant_id>
        "x-adcp-tenant": "default",
    },
)


async def main():
    async with Client(transport=transport) as client:
        result = await client.call_tool("get_products", {"brief": "video ads"})
        print(result)


asyncio.run(main())
```

Run it with uv. You are still inside the clone; `--no-project` stops uv from installing the
repository's own dependencies there:

```bash
uv run --no-project --with fastmcp python first_call.py
```

With Python and `pip install fastmcp` instead, run `python first_call.py`.

To list the server's tools instead, replace the `call_tool` line with
`print(await client.list_tools())`.

The token is needed even for `get_products`; see
[Agent calls are refused with `AUTH_MISSING`](development/troubleshooting.md#agent-calls-are-refused-with-auth_missing).
A2A clients send the same two headers to http://localhost:8000/a2a.

## Endpoints

All services are reached through the nginx proxy on port 8000:

| Service | URL | Names a tenant how |
|---------|-----|---|
| Admin UI | http://localhost:8000/ | its own session |
| Admin UI (alternate) | http://localhost:8000/admin | its own session |
| MCP Server | http://localhost:8000/mcp/ | `x-adcp-tenant` header |
| A2A Server | http://localhost:8000/a2a | `x-adcp-tenant` header |
| Health Check | http://localhost:8000/health | no tenant needed |

## Running the published image without compose

If you already run PostgreSQL, the published image runs the whole application in one
container. Put the same variables from step 3 in a file (or reuse `.env`) and add the
database URL and `FLASK_SECRET_KEY`. `docker-compose.yml` supplies a session-signing key and
`docker run` does not, so without one every restart signs everybody out. From inside a
container, `localhost` is the container itself, so a database on your machine is
`host.docker.internal`:

```bash
# In the env file, alongside the variables from step 3:
# DATABASE_URL=postgresql://user:pass@host.docker.internal:5432/dbname
# FLASK_SECRET_KEY=<a long random value>

docker run -p 8000:8000 --env-file .env --add-host host.docker.internal:host-gateway \
  ghcr.io/prebid/salesagent:latest

curl http://localhost:8000/health
```

The image's entrypoint refuses to start without `DATABASE_URL` and `ENCRYPTION_KEY`. To
generate the key with the image itself, override the entrypoint, because arguments after
the image name are passed to the entrypoint script rather than run as a command:

```bash
docker run --rm --entrypoint python ghcr.io/prebid/salesagent:latest \
  -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Register the same two redirect URIs; sign-in, the `default` tenant and the agent call work
as described above.

## Common Commands

```bash
# View logs
docker compose logs -f

# Stop services
docker compose down

# Reset everything (including database)
docker compose down -v

# Rebuild after code changes
docker compose build && docker compose up -d
```

## Troubleshooting

Each problem below is documented once, in the
[troubleshooting guide](development/troubleshooting.md):

- Signing in to the Admin UI:
  [The login page offers no way to sign in](development/troubleshooting.md#the-login-page-offers-no-way-to-sign-in),
  [Google shows "Error 400: redirect_uri_mismatch"](development/troubleshooting.md#google-shows-error-400-redirect_uri_mismatch),
  [Google restricts the app to test users](development/troubleshooting.md#google-restricts-the-app-to-test-users),
  [Access denied in the Admin UI](development/troubleshooting.md#access-denied-in-the-admin-ui)
- The first agent call:
  [No seller is served at the address this request named](development/troubleshooting.md#no-seller-is-served-at-the-address-this-request-named),
  [Agent calls are refused with `AUTH_MISSING`](development/troubleshooting.md#agent-calls-are-refused-with-auth_missing),
  [MCP returns an empty products array](development/troubleshooting.md#mcp-returns-an-empty-products-array)
- The stack:
  [Container does not start](development/troubleshooting.md#container-does-not-start),
  [Port 8000 already in use](development/troubleshooting.md#port-8000-already-in-use)

## Next Steps

- **Developer setup**: See [Getting Started](development/GETTING_STARTED.md) for local development with tests and pre-commit hooks
- **Deploy to the cloud**: See [deployment/](deployment/) for production deployment guides
- **Configure GAM**: See [adapters/gam/](adapters/gam/) to connect Google Ad Manager
- **User guide**: See [user-guide/](user-guide/) for setting up products, advertisers, and creatives
