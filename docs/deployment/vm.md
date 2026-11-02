# Single VM deployment

This walkthrough runs a multi-tenant Prebid Sales Agent on one Linux VM with Docker Compose. Caddy
terminates TLS and gets a certificate for each host the first time a client connects to it, so
adding a tenant needs no proxy change and no wildcard certificate.

The example files are in [`deploy/vm/`](../../deploy/vm/):

| File | What it is |
|---|---|
| `compose.yml` | Caddy, the app, PostgreSQL, and the optional reference creative agent |
| `Caddyfile` | One catch-all site with on-demand TLS, gated by the app's `GET /tls/ask` |
| `make-env.sh` | Writes `.env` once, generating every secret |
| `backup.sh` | Dumps the database into `backups/` |

> **Fixes this guide needs until they merge.** Build the image from a ref that includes them (step 4):
>
> - #2310: nginx starts as the image's non-root user, and the cron job runs the right script. Without it
>   nothing answers on port 8000 while the container reports healthy.
> - #2313: `GET /tls/ask`, mounted when `TLS_ASK_ENABLED=true`. Without it Caddy issues no certificate.
> - #2315: a creative agent configured by its base URL answers in under a second instead of
>   hanging or retrying.
> - #2305: pins the reference creative agent to AdCP v3.1.25.
> - #2311 and #2312 fix the sample format ids and the Admin UI "Verify all" button.

## Contents

- [Topology](#topology)
- [Step 1: Create the VM](#step-1-create-the-vm)
- [Step 2: DNS](#step-2-dns)
- [Step 3: Install Docker](#step-3-install-docker)
- [Step 4: Build the image](#step-4-build-the-image)
- [Step 5: Configure](#step-5-configure)
- [Step 6: Build the reference creative agent](#step-6-build-the-reference-creative-agent)
- [Step 7: Start](#step-7-start)
- [Step 8: First super-admin login](#step-8-first-super-admin-login)
- [Tenants and publishers](#tenants-and-publishers)
- [Step 9: Create the first tenant](#step-9-create-the-first-tenant)
- [Step 10: Verify the tenant](#step-10-verify-the-tenant)
- [Step 11: Publisher adagents.json](#step-11-publisher-adagentsjson)
- [Backups](#backups)
- [Upgrades and rollback](#upgrades-and-rollback)
- [Troubleshooting](#troubleshooting)

## Topology

```
internet --443/80--> caddy --> app:8000 (the image's nginx) --> FastAPI :8080 (MCP, A2A, Admin UI)
                       |  on-demand TLS: ask app:8000/tls/ask      |
                       |                                           +--> postgres
                       +--> creative-agent:8080 (optional; https://<apex>/api/creative-agent only)
```

The app picks the tenant from the `Host` header. Caddy passes it through unchanged.

Before Caddy requests a certificate for a host, it asks the app at `GET /tls/ask?domain=<host>`.
The app mounts that route only when `TLS_ASK_ENABLED=true`, and `compose.yml` sets it for the `app`
service. It is off by default because only a proxy that issues certificates on demand, as Caddy does
here, ever asks. With it off, `/tls/ask` answers 404 and Caddy issues no certificate, so keep the
setting if you edit `compose.yml` or move the app's environment into `.env`.

| Host | Serves |
|---|---|
| `<apex>`, for example `agents.example.com` | Sign-up page, Admin UI at `/admin/` |
| `admin.<apex>` | Admin UI |
| A tenant's host on the seller's domain, for example `sales.acme.com` | One tenant: `/mcp/`, `/a2a`, `/.well-known/agent-card.json`, `/.well-known/jwks.json` |
| `<tenant>.<apex>` | The same, for a tenant that declares a host under the apex |

## Step 1: Create the VM

- Ubuntu 24.04, 2 vCPU and 8 GB of memory (4 GB runs the stack; building the images on the VM needs
  more), 80 GB of disk. Building the reference creative agent from source (step 6) needs about 20 GB
  of scratch space on top of the 8 GB image; a 30 GB disk runs out.
- Inbound TCP 80 and 443 from anywhere. Let's Encrypt validates over them, and buyers' agents
  connect on 443. Inbound 22 only from where you administer it.
- The VM must reach its own public address on 443. The app reaches the creative agent through
  Caddy at `https://<apex>/api/creative-agent`, because its outbound HTTP is HTTPS-only.

## Step 2: DNS

Point these at the VM's public address:

| Record | Needed for |
|---|---|
| `A <apex>` | Sign-up page and Admin UI |
| `A admin.<apex>` | Admin UI host |
| `A <tenant>.<apex>`, one per tenant, or one `A *.<apex>` | Tenant hosts under the apex |

A wildcard record is safe: Caddy still asks the app before it requests a certificate, and the app
answers yes only for hosts that an active tenant declares. Without a wildcard, add one record per
tenant before you create it.

A tenant on the seller's own domain (for example `sales.acme.com`) points that name at the VM, with
an `A` record or a `CNAME` to `<apex>`, and declares it as its host (step 9).

Check before you continue. Every name must resolve to the VM:

```bash
dig +short agents.example.com admin.agents.example.com sales.acme.com
```

## Step 3: Install Docker

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx git
sudo usermod -aG docker "$USER"
sudo install -d -o "$USER" -g "$USER" /opt/salesagent
```

Log out and back in so the `docker` group applies, then check: `docker compose version`.

## Step 4: Build the image

Build from source, and pin the commit you deploy. `ghcr.io/prebid/salesagent:latest` lags `main`
and does not include the fixes listed at the top.

```bash
cd /opt/salesagent
REPO=https://github.com/prebid/salesagent.git
REF=main                                    # a release tag, or a branch carrying the fixes above
git clone "$REPO" src
git -C src checkout "$REF"
T=$(git -C src rev-parse --short=12 HEAD)
docker build -t "salesagent:$T" src         # about 5 minutes
docker tag "salesagent:$T" salesagent:current
```

Compose runs `salesagent:current`. Keeping the commit-named tag is what makes a rollback one
`docker tag` (see [Upgrades and rollback](#upgrades-and-rollback)).

Once a release includes the fixes, you can pull it instead:
`docker pull ghcr.io/prebid/salesagent:<version> && docker tag ghcr.io/prebid/salesagent:<version> salesagent:current`.
You still need the clone for the example files and the creative agent script.

## Step 5: Configure

Copy the example files next to the clone:

```bash
cd /opt/salesagent
cp src/deploy/vm/compose.yml src/deploy/vm/Caddyfile src/deploy/vm/make-env.sh src/deploy/vm/backup.sh .
```

Register the Admin UI as a client with your OIDC provider (Okta, Auth0, Keycloak, Authelia,
Microsoft Entra, Google, or another):

- Redirect URI: `https://<apex>/admin/auth/google/callback`. The path says `google` for every
  provider.
- Scopes: `openid email profile`; grant type authorization code; client authentication
  `client_secret_post` or `client_secret_basic`.
- The ID token must carry `email` (and `name` if you want it shown). Some providers put these only
  in the userinfo response by default; Authelia 4.39 is one, and needs a claims policy that adds them
  to the ID token.

Then write `.env`:

```bash
DOMAIN=agents.example.com \
SUPPORT_EMAIL=ops@example.com \
SUPER_ADMIN_EMAILS=you@example.com \
OAUTH_DISCOVERY_URL=https://idp.example.com/.well-known/openid-configuration \
OAUTH_CLIENT_ID=salesagent \
OAUTH_CLIENT_SECRET='<from your provider>' \
./make-env.sh
```

`make-env.sh` generates the database passwords, the Fernet `ENCRYPTION_KEY`, the Flask session
secret, the signing key encryption key (`SALESAGENT_SIGNING_KEK`) and `SYNC_API_KEY`, and writes them
to `.env` with mode 600. Set `CREATIVE_AGENT=no` to skip the reference creative agent (step 6).

**Copy `.env` somewhere off the VM now** (a password manager or a secrets store). `ENCRYPTION_KEY`
decrypts stored OIDC and ad-server credentials, and `SALESAGENT_SIGNING_KEK` decrypts every tenant's
private signing key. Never regenerate either one: a new key cannot read what the old one wrote, and
a database backup is useless without them. `make-env.sh` refuses to overwrite an existing `.env`.

`ADCP_TESTING` stays unset. It is the sandbox posture: it mounts debug routes, accepts loopback
webhook targets, serves creative formats from a checked-in fixture and counts the mock adapter as a
configured ad server. It is not for a deployment buyers use.

## Step 6: Build the reference creative agent

Products name creative formats, and the app lists them by asking a creative agent. The public agent
at `https://creative.adcontextprotocol.org` serves AdCP 3.2, whose catalog this release's AdCP 3.1.1
parser rejects (#2274), so `list_creative_formats` comes back empty. Run the reference agent at the
version the repository pins:

```bash
cd /opt/salesagent
CREATIVE_AGENT_GHCR_IMAGE=ghcr.io/prebid/salesagent/adcp-creative-agent src/scripts/creative-agent-stack.sh build
```

The script pulls the pinned image when it is published and otherwise builds it from the pinned
AdCP source (10 to 20 minutes). Either way it tags it `adcp-creative-agent:latest`, which
`compose.yml` runs. After a source build, reclaim the space it used:

```bash
docker builder prune -af
rm -rf /tmp/adcp-server-*
```

Skip this step if you ran `make-env.sh` with `CREATIVE_AGENT=no`, and leave out
`--profile creative-agent` below.

## Step 7: Start

```bash
cd /opt/salesagent
docker compose --profile creative-agent up -d
docker compose ps        # wait until app and creative-agent report (healthy)
```

The app runs the database migrations each time it starts; the first start takes two to three
minutes, and `app` shows `(unhealthy)` until the migrations finish and it starts answering. Follow it
with `docker compose logs -f app`.

Check from your workstation. The first request to each host takes a few seconds while Caddy gets the
certificate:

```bash
curl -s https://agents.example.com/health                         # {"status":"healthy",...}
curl -sI https://admin.agents.example.com/ | grep -i '^location'  # .../login
curl -s https://agents.example.com/api/creative-agent/health      # creative agent, if enabled
```

## Step 8: First super-admin login

Open `https://admin.<apex>/` and sign in through your OIDC provider with an address listed in
`SUPER_ADMIN_EMAILS`. A super admin sees every tenant and can create them.

If the login loops or says the email is missing, check the redirect URI and that the ID token
carries `email` (step 5).

## Tenants and publishers

A tenant is a seller: one sales agent, served at one URL. The URL's host is the tenant's virtual
host, which the Admin UI calls **Custom Domain** and the setup script takes as `--virtual-host`.

A tenant is not a publisher. One tenant sells for many publisher sites, hundreds in production.
Each site authorizes the tenant by serving `/.well-known/adagents.json` on its own domain, with an
entry that names the tenant's URL (step 11). The tenant holds the site as an authorized property
and verifies it against that file.

Put the tenant's host on the seller's own domain, for example `sales.acme.com`. A host under the
apex, such as `acme.<apex>`, also works.

- Adding a tenant takes one DNS record for its host (none under the apex if you have the wildcard
  record) and the virtual host on the tenant.
- Adding a publisher site to a tenant changes no DNS record and no certificate for the agent.

The app finds the tenant by matching the request's host against the tenant's virtual host.

## Step 9: Create the first tenant

The tenant's host must already resolve to the VM (step 2).

**In the Admin UI:** **Create New Account**, then enter a name, a **Subdomain** (for example `acme`)
and the **Custom Domain**, which is the host the tenant is served at: `sales.acme.com`, or
`acme.<apex>`. Pick the ad server adapter.

**From the shell:**

```bash
cd /opt/salesagent
docker compose exec app python -m scripts.setup.setup_tenant "Acme" \
  --tenant-id acme --subdomain acme --virtual-host sales.acme.com --adapter mock
```

The script prints an access token for a first advertiser (principal `acme_default`). It is shown
once; store it. More advertisers and tokens: Admin UI, **Advertisers**.

Give the tenant a request-signing key. Its public half is published at `/.well-known/jwks.json` and
in the tenant's `adagents.json` entries (step 11):

```bash
docker compose exec app python scripts/ops/provision_signing_key.py --tenant-id acme
```

Then finish the tenant in the Admin UI: the setup checklist on its dashboard lists what is missing
(currencies, the `all_inventory` property tag, products, authorized properties, an ad server).
The mock adapter is for trying the agent out: with `ADCP_TESTING` unset it does not count as a
configured ad server, so the tenant does not accept media buys. A real tenant connects Google Ad
Manager (see [GAM service account setup](../adapters/gam/service-account-setup.md)).

## Step 10: Verify the tenant

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sales.acme.com/.well-known/agent-card.json   # 200
curl -s https://sales.acme.com/.well-known/agent-card.json | grep -o '"url": *"[^"]*"' | head -3
```

The card's URLs must name `https://sales.acme.com`. Then call a tool over MCP, from the app container,
which has an MCP client installed:

```bash
docker compose exec -T -e TOKEN='<token from step 9>' -e URL=https://sales.acme.com/mcp/ app python - <<'EOF'
import asyncio, os
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

async def main():
    transport = StreamableHttpTransport(os.environ["URL"], headers={"Authorization": f"Bearer {os.environ['TOKEN']}"})
    async with Client(transport) as client:
        result = await client.call_tool("list_creative_formats", {})
        formats = result.structured_content["formats"]
        print(len(formats), "formats, e.g.", [f["format_id"]["id"] for f in formats[:3]])

asyncio.run(main())
EOF
```

A non-zero count (the first page of a paginated list) means the whole path works: TLS, host
routing, the tenant, the token and the creative agent. The app log names the agent it asked:
`docker compose logs app | grep list_all_formats`.

## Step 11: Publisher adagents.json

A buyer trusts that this agent may sell a publisher's inventory only when the publisher says so at
`https://<publisher-domain>/.well-known/adagents.json`. The entry names the tenant's agent URL,
which is its origin (`https://sales.acme.com`), and the properties it sells:

```json
{
  "$schema": "https://adcontextprotocol.org/schemas/3.1.1/adagents.json",
  "authorized_agents": [
    {
      "url": "https://sales.acme.com",
      "authorized_for": "Display inventory on publisher.com",
      "authorization_type": "property_tags",
      "property_tags": ["all_inventory"]
    }
  ],
  "properties": [
    {
      "property_id": "publisher_com",
      "property_type": "website",
      "name": "publisher.com",
      "identifiers": [{"type": "domain", "value": "publisher.com"}],
      "tags": ["all_inventory"]
    }
  ],
  "last_updated": "2026-01-01T00:00:00Z"
}
```

To pin the tenant's webhook signing key as well, add a `signing_keys` array to the entry holding
the keys from `https://sales.acme.com/.well-known/jwks.json`, and update it after every key rotation.

Then, in the Admin UI, add the publisher's properties (**Authorized Properties**) and verify them.
Verification fetches the publisher's `adagents.json` and looks for the tenant's agent URL. Repeat
both for every publisher site the tenant sells.

## Backups

`backup.sh` writes `backups/salesagent-<UTC>.sql.gz` and deletes dumps older than 14 days
(`KEEP_DAYS`). Run it nightly from the VM user's crontab (`crontab -e`):

```
17 3 * * * /opt/salesagent/backup.sh >> /opt/salesagent/backups/backup.log 2>&1
```

Copy `backups/` off the VM on your own schedule. A dump restores only with the `.env` that was in
use when it was written, because of `ENCRYPTION_KEY` and `SALESAGENT_SIGNING_KEK`. Restore into the
running stack:

```bash
docker compose stop app
gunzip -c backups/salesagent-<UTC>.sql.gz | docker compose exec -T postgres psql -q -U salesagent -d salesagent
docker compose start app
```

## Upgrades and rollback

Migrations run when the app starts, so an upgrade is: back up, build, switch.

```bash
cd /opt/salesagent
./backup.sh
docker image inspect salesagent:current --format '{{.RepoTags}}'   # note the current commit tag, for rollback
git -C src fetch origin && git -C src checkout <new ref>
T=$(git -C src rev-parse --short=12 HEAD)
docker build -t "salesagent:$T" src
docker tag "salesagent:$T" salesagent:current
docker compose up -d app
docker compose ps        # wait for (healthy), then repeat the checks in steps 7 and 10
```

If the repository's creative agent pin (`ADCP_PIN` in `scripts/creative-agent-stack.sh`) moved,
rerun step 6 and `docker compose --profile creative-agent up -d creative-agent`.

Compare `src/deploy/vm/` with your copies (`diff src/deploy/vm/compose.yml compose.yml`) and
carry over changes by hand. Never rerun `make-env.sh` on a deployment that has a `.env`.

**Rollback:** point `salesagent:current` at the previous commit tag and restart:

```bash
docker tag salesagent:<previous sha> salesagent:current
docker compose up -d app
```

Restore the backup taken before the upgrade only if the new version's migrations changed the
schema in a way the old version cannot read; check `docker compose logs app` first.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| TLS handshake fails (`tlsv1 alert internal error`) for a host | Caddy asked `/tls/ask` and got 403: the host is not an active tenant's host. The refusal is in the app's log, not Caddy's: `docker compose logs app \| grep tls/ask`. Ask it yourself: `docker compose exec app curl -s -o /dev/null -w '%{http_code}\n' 'http://localhost:8000/tls/ask?domain=<host>'` must print 200. A 404 for every host means the route is not mounted: the `app` service is missing `TLS_ASK_ENABLED=true` (`docker compose exec app printenv TLS_ASK_ENABLED`). If it prints 200 and the handshake still fails, the host's DNS does not point at the VM yet, so Let's Encrypt cannot validate it (`docker compose logs caddy`). |
| `app` is healthy but every request returns 502 | The image's nginx did not start. You built an image without #2310; rebuild from a ref that has it. |
| `list_creative_formats` returns no formats | The app cannot read the creative agent's catalog. With `CREATIVE_AGENT_URL` unset it asks the public agent, whose AdCP 3.2 catalog is rejected (#2274); run the reference agent (step 6). With it set, check `curl https://<apex>/api/creative-agent/health` from the VM, which also proves the VM reaches its own public address. |
| Admin login fails after the provider redirects back | The redirect URI registered with the provider differs from `GOOGLE_OAUTH_REDIRECT_URI` in `.env`, or the ID token has no `email`. |
| A tenant answers `CONFIGURATION_ERROR` | The `Host` the request named is not a tenant's host. Check the tenant's **Custom Domain** in the Admin UI. |
| "Verify all" in Authorized Properties verifies only the first property | Fixed by #2312. Until then, verify each property on its own. |
| Seeded sample products show formats no buyer can build | Fixed by #2311 (the seed scripts used format ids the creative agent does not publish). |

See also the [environment variables reference](environment-variables.md), the
[multi-tenant guide](multi-tenant.md) and the [signing key runbook](../operations/signing-key-runbook.md).
