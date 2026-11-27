# Troubleshooting guide

This guide is a symptom lookup for the local Docker stack and its test
tooling. You reach every service through the nginx proxy at
`http://localhost:8000`. Each entry gives the symptom, the immediate fix, and
a link to the document that explains the mechanism. Find your symptom in the
contents, apply the fix, and follow the link if the fix isn't enough.

## Contents

Startup and Docker:

- [The stack won't come up, or the app crash-loops](#the-server-does-not-start) — decision tree
- [Container exits at startup](#container-does-not-start)
- [`ModuleNotFoundError` or `ImportError` after `docker compose up`](#import-errors-after-docker-compose-up)
- [Port 8000 is already allocated](#port-8000-already-in-use)
- [Permission denied inside a container](#permission-denied-inside-a-container)
- [Containers use too much memory](#high-memory-usage)

Database:

- [`column ... does not exist`](#column-does-not-exist)
- [`Can't locate revision identified by ...`](#broken-migration-chain)
- [`operator does not exist: text < timestamp with time zone`](#operator-does-not-exist-type-mismatch-in-queries)
- [App can't reach PostgreSQL](#postgresql-connection-failed)
- [Queries are slow](#slow-queries)

Authentication:

- [Which layer rejected the request](#authentication-issues) — decision tree
- ["Access denied" when logging in to the Admin UI](#access-denied-in-the-admin-ui)
- [404 after the Google OAuth redirect](#oauth-callback-404)
- [Login loops back to the OAuth screen](#oauth-redirect-loop)
- [Rejected with a valid token](#rejected-with-a-valid-token)
- [MCP requests rejected as unauthorized](#invalid-token-for-the-mcp-api)
- [A2A requests rejected as unauthenticated](#a2a-authentication-failed)

MCP and A2A:

- ["Tool not found" from the MCP server](#tool-not-found)
- [`get_products` returns an empty array](#mcp-returns-an-empty-products-array)
- [`Input validation error: '...' is a required property`](#contract-validation-errors)
- [A2A endpoint doesn't respond](#a2a-server-not-responding)
- [JSON-RPC rejects the `messageId`](#invalid-messageid-errors)

Admin UI:

- [Blank page or 500 error](#blank-page-or-500-error)
- [403 "Your role does not allow this"](#your-role-does-not-allow-this)
- [403 "Cross-origin request refused"](#cross-origin-request-refused)
- [Activity feed doesn't update](#activity-feed-not-updating)
- [Slack notifications don't arrive](#slack-notifications-not-arriving)

GAM integration:

- ["Could not determine client ID from request"](#could-not-determine-client-id-from-request)
- [Choosing between OAuth and a service account](#oauth-compared-with-service-account)
- [GAM refresh token stopped working](#oauth-token-invalid)
- [Wrong GAM network code](#network-code-mismatch)
- [Inventory sync runs for 30+ minutes](#inventory-sync-timeout)

Outbound requests:

- [An outbound HTTP call is refused](#outbound-request-refused)

Testing and quality gates:

- [`make quality` fails — which gate](#a-make-quality-run-fails) — decision tree
- [A `test_architecture_*` guard test fails](#a-structural-guard-fails)
- [`TID251` import ban on `httpx`, `requests`, or `aiohttp`](#an-egress-import-ban-fires)
- [Integration tests are slow or flaky](#integration-tests-slow-or-flaky)
- [Async tests fail with coroutine warnings](#async-test-failures)
- [`AttributeError: '...' object has no attribute ...`](#attributeerror-on-model-fields)

Reference:

- [Recover a broken production deployment](#production-recovery)
- [API error quick reference](#api-error-quick-reference)
- [Check system health](#check-system-health)
- [Read the logs](#read-the-logs)
- [Get help](#get-help)

## The server does not start

The following decision tree routes a failed `docker compose up -d` — or an
app that starts and then crash-loops — to the right entry by what the logs
say.

```mermaid
flowchart TD
    Start["docker compose up -d fails,\nor the app crash-loops"] --> Logs["docker compose logs adcp-server"]
    Logs --> Import{"ModuleNotFoundError\nor ImportError?"}
    Import -->|Yes| Rebuild["Rebuild the image:\nsee Import errors after\ndocker compose up"]
    Import -->|No| Rev{"Can't locate revision\nidentified by ...?"}
    Rev -->|Yes| Chain["Repair the migration chain:\nsee Broken migration chain"]
    Rev -->|No| Bind{"port is already allocated\nor bind failed?"}
    Bind -->|Yes| Port["Free port 8000:\nsee Port 8000 already in use"]
    Bind -->|No| DB{"Connection refused\nto postgres?"}
    DB -->|Yes| PG["Check the database container:\nsee PostgreSQL connection failed"]
    DB -->|No| Recent["Read the specific error, then check\nrecent changes: git log --oneline -10"]
```

## Server and Docker issues

### Container does not start

Read the logs first. Rebuild if the error is vague:

```bash
# Check logs
docker compose logs adcp-server
docker compose logs postgres

# Rebuild containers
docker compose down
docker compose build --no-cache
docker compose up -d
```

Migrations run automatically at startup: the `db-init` service runs
`python scripts/ops/migrate.py` and `adcp-server` waits for it to complete.
A migration error therefore appears in `docker compose logs db-init`.

### Import errors after `docker compose up`

**Symptoms**: `ModuleNotFoundError: No module named 'flask'` or
`ImportError: cannot import name ... from 'adcp...'` when running
`docker compose up` with source bind mounts.

The image installs the virtualenv at `/opt/venv` (outside `/app`), so the
`.:/app` bind mount never shadows installed packages. Import errors therefore
mean the image itself is stale — for example, after a dependency bump in
`uv.lock`. Rebuild without cache:

```bash
docker compose build --no-cache db-init adcp-server
docker compose up
```

If you add your own `docker-compose.override.yml`, mount source at `/app`
only — don't add a `/app/.venv` volume. The image provides packages on
`PATH` from `/opt/venv` and source on `PYTHONPATH=/app`.

### Port 8000 already in use

The proxy publishes one host port, `8000` by default. Set `CONDUCTOR_PORT`
to move it:

```bash
# Find the process holding the port
lsof -i :8000

# Or start the stack on a different host port
CONDUCTOR_PORT=8001 docker compose up -d
```

The `ADCP_SALES_PORT` variable (default `8080`) is the app's port inside the
Compose network, behind the proxy. Changing it doesn't resolve a host port
conflict.

### Permission denied inside a container

```bash
# Fix volume ownership
docker compose exec adcp-server chown -R $(id -u):$(id -g) /app

# Or run with your user ID
docker compose run --user $(id -u):$(id -g) adcp-server
```

### High memory usage

```bash
# Check container stats
docker stats
```

To cap a service, add limits in `docker-compose.override.yml`:

```yaml
services:
  adcp-server:
    mem_limit: 512m
    mem_reservation: 256m
```

## Database issues

### Column does not exist

The schema is behind the code. Run migrations:

```bash
# Locally
uv run python scripts/ops/migrate.py

# In Docker
docker compose exec adcp-server python scripts/ops/migrate.py

# Check migration status
docker compose exec adcp-server python scripts/ops/migrate.py status
```

If migrations fail, check for overlapping revisions:

```bash
grep -r "revision = " alembic/versions/
```

The `test_architecture_single_migration_head.py` guard fails `make quality`
when the migration graph has more than one head, so a chain conflict usually
surfaces there first.

### Broken migration chain

**Symptoms**: `Can't locate revision identified by '[revision_id]'` — the app
crashes at startup or a deployment fails.

1. Check the chain: `uv run alembic history`.
2. Identify the last known good revision: `uv run alembic current`.
3. Reset to it: `uv run alembic stamp [good_revision]`.
4. Create a new migration with the correct `down_revision`.
5. Test the fix locally.
6. Deploy the migration fix before any code change.

Never modify a committed migration file — create a new one.

### Operator does not exist: type mismatch in queries

**Symptoms**: `operator does not exist: text < timestamp with time zone`.

A query compares a column against a value of a different type, which means
the column's type in the database doesn't match what the ORM model declares.
Find the failing query in the logs, compare the column's type in the model
(`src/core/database/models.py`) against the live schema, and write a
migration that aligns them. The `test_architecture_query_type_safety.py`
guard catches queries you add whose filter types don't match the column
definitions — see [Structural guards](structural-guards.md).

### PostgreSQL connection failed

```bash
# Check PostgreSQL is running
docker ps | grep postgres

# Test the connection
docker compose exec postgres psql -U adcp_user adcp -c "SELECT 1;"

# Check the environment variable
echo $DATABASE_URL
```

### Slow queries

```bash
# Inspect the query plan
docker compose exec postgres psql -U adcp_user adcp -c \
  "EXPLAIN ANALYZE SELECT * FROM media_buys WHERE tenant_id='test';"
```

Add an index through a migration (`uv run alembic revision -m "add index"`),
not by hand — a hand-created index exists only in that one database.

## Authentication issues

Three entry points authenticate differently. The following decision tree
routes an authentication failure by where the request came in. The
[request lifecycle](request-lifecycle.md) document explains how
`_resolve_identity` — one function, private to the boundary — and the
middleware order make this work.

```mermaid
flowchart TD
    Fail["Authentication fails"] --> Which{"Which entry point?"}
    Which -->|"Admin UI (browser)"| UI{"What do you see?"}
    UI -->|"Access denied, a 403 page,\nor no account to select"| Denied["Check the super admin lists\nand your user rows:\nsee Access denied in the Admin UI"]
    UI -->|"404 after Google login"| CB["Fix the redirect URI:\nsee OAuth callback 404"]
    UI -->|"Login loops"| Loop["Check your SSO access\nand the session cookie:\nsee OAuth redirect loop"]
    UI -->|"Your role does\nnot allow this"| Role["Ask a tenant admin to change\nyour role: see Your role\ndoes not allow this"]
    UI -->|"Cross-origin\nrequest refused"| Origin["Check the proxy headers:\nsee Cross-origin request refused"]
    Which -->|"MCP / REST / A2A\n(Authorization: Bearer)"| MCP{"Does the token's hash match\na principal of the tenant\nthe request addressed?"}
    MCP -->|No| Token["Rotate the token in the Admin UI:\nsee Invalid token for the MCP API"]
    MCP -->|Yes| Active["Check the tenant is active:\nsee Rejected with a valid token"]
```

### Access denied in the Admin UI

**Symptoms**: after you sign in, a tenant page answers `403`, or the
**Select Account** page lists no account. After a sign-in through a tenant's
own single sign-on (SSO), the login page shows "Access denied. You don't have
permission to access this tenant." When the sign-in starts over instead, see
[OAuth redirect loop](#oauth-redirect-loop).

```bash
# Check the super admin lists the app reads
docker compose exec adcp-server env | grep SUPER_ADMIN

# Check your user rows and roles
docker compose exec postgres psql -U adcp_user adcp -c \
  "SELECT tenant_id, role, is_active FROM users WHERE email='you@example.com';"
```

**Cause**: your address is neither a super admin nor an active user of the
tenant. [Super admin check](../security.md#super-admin-check) and
[Tenant access guard](../security.md#tenant-access-guard) describe both
checks. The checks ignore `SUPER_ADMIN_DOMAIN` (singular). A session from a
tenant's own SSO never counts as a super admin and reaches no other tenant, as
[Super admins](../security.md#super-admins) explains.

**Fix**: ask an admin of the tenant, or a super admin, to add your address on
the **Users & Access** page. A `403` page can also come from the role check;
see [Your role does not allow this](#your-role-does-not-allow-this).

### OAuth callback 404

Google returns the browser to the redirect URI that the app sends with the
sign-in request. A 404 at that address means that the URI names a host or a
path that doesn't reach the Admin UI. To see the URI, read the `redirect_uri`
parameter in the address of Google's sign-in page.

[Deployment OAuth](../deployment/environment-variables.md#deployment-oauth)
describes how the app builds the URI for each kind of sign-in.

Correct `GOOGLE_OAUTH_REDIRECT_URI` or `SALES_AGENT_DOMAIN`, or route the path
to the app in your proxy, so that the URI reaches this deployment. Register the
same URI in the Google Cloud Console. For a URI that the console doesn't list,
Google shows a `redirect_uri_mismatch` error instead of redirecting.

### OAuth redirect loop

Clear the session cookies in the browser, or use a private window, and sign in
again. If the loop continues, look for one of the following causes:

- **The tenant's SSO refuses the sign-in.** When a tenant's SSO is enabled,
  the login page starts it by itself on the tenant's own host, and in
  single-tenant mode. A refusal or an error in the SSO callback returns you to
  the login page, which starts the SSO again. To stop the loop and read the
  stored message, open the login page with `?logged_out=1`, for example
  `http://localhost:8000/admin/login?logged_out=1`. Then ask an admin of the
  tenant to add or activate you on the **Users & Access** page, or to correct
  the SSO configuration.
- **The browser drops the session cookie.** In production, the cookie is
  `Secure`, so the browser keeps it only over HTTPS. In multi-tenant mode, it
  also covers only `SALES_AGENT_DOMAIN` and its subdomains, as
  [OAuth cross-domain authentication](../security.md#oauth-cross-domain-authentication)
  describes.
- **The session key changes, or differs between processes.** When
  `FLASK_SECRET_KEY` is unset, each process generates its own key at startup.
  A restart then invalidates every session cookie, and two processes reject
  each other's cookies. The local Docker stack always sets the key, because
  `docker-compose.yml` falls back to a fixed development value.

### Rejected with a valid token

**Symptoms**: the token is correct, but the server still rejects the request.

```bash
# Verify the tenant is active
docker compose exec postgres psql -U adcp_user adcp -c \
  "SELECT is_active FROM tenants WHERE tenant_id='your_tenant_id';"
```

The server reads the credential from `Authorization: Bearer` only. It does not
recognize `x-adcp-auth`, so a request that sends only the alias presents
nothing and gets `AUTH_MISSING`. The server identifies the tenant from the
host before it looks the token up, and it looks the token up only inside that
tenant. A token issued for one tenant is therefore `AUTH_INVALID` on another.
The [request lifecycle](request-lifecycle.md) document describes the exact
order.

### Invalid token for the MCP API

The Admin UI shows an advertiser's full API token once: when you create the
advertiser, and when you rotate its token. The **Advertisers** section of the
tenant settings shows only the token's first characters. The database holds
only a hash of each token (`principals.token_hash`), so you cannot read a lost
token back. To get a full token, click the rotate button next to the token in
the advertiser's row, and copy the token from the dialog that opens. The
previous token stops working immediately, so give the replacement to every
client that used it.

### A2A authentication failed

Send the token as a Bearer token in the `Authorization` header:

```bash
curl -X POST http://localhost:8000/a2a \
  -H "Authorization: Bearer ${ADCP_AUTH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "message/send", ...}'
```

## MCP server issues

### Tool not found

List the tools the server actually registers:

```bash
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools
```

If a tool is missing, check its registration in `src/core/main.py` and its
implementation under `src/core/tools/`:

```bash
grep -rn "def get_products" src/core/tools/
```

The [request lifecycle](request-lifecycle.md) document explains the path from
the MCP wrapper to the `_impl` function.

### MCP returns an empty products array

Products are tenant-specific: create them for each tenant.

```bash
# Check whether products exist for the tenant
docker compose exec postgres psql -U adcp_user adcp -c \
  "SELECT COUNT(*) FROM products WHERE tenant_id='your_tenant_id';"
```

Create products through the Admin UI. A product requires the tenant to have a
USD `CurrencyLimit` and the `all_inventory` property tag — see the tenant
setup dependencies in the repository `CLAUDE.md`.

### Contract validation errors

**Symptoms**: `Input validation error: 'brief' is a required property` or a
similar parameter validation failure.

```bash
# Reproduce the failing request construction locally
uv run python -c "
from src.core.schemas import GetProductsRequest
req = GetProductsRequest(promoted_offering='test product')
print('Request creation successful')
"

# Run the contract validation tests (starts a database for you)
scripts/run-test.sh tests/integration/test_mcp_contract_validation.py

# Run the same check the pre-commit stage runs
pre-commit run adcp-contract-tests --all-files
```

Schemas extend the `adcp` library types by inheritance, and the schema
inheritance guard grades redeclarations against the library parent. Read
that guard's entry in [Structural guards](structural-guards.md) and the
repository `CLAUDE.md` before you change a request model.

## A2A protocol issues

### A2A server not responding

The A2A handler runs inside the unified server behind the proxy — there is no
separate container:

```bash
# Fetch the agent card
curl http://localhost:8000/.well-known/agent-card.json

# Check the logs
docker compose logs adcp-server | grep -i a2a
```

### Invalid `messageId` errors

The A2A specification requires string identifiers. Send `id` and `messageId`
as strings, not numbers:

```json
{"id": "123", "params": {"message": {"messageId": "456"}}}
```

## Admin UI issues

### Blank page or 500 error

When a request fails with an error that the app doesn't expect, the Admin UI
shows a message that ends with `The details have been logged (reference <code>).`
Find the log line with that code:

```bash
docker compose logs adcp-server | grep "<code>"
```

The app logs the error at the `ERROR` level, with its traceback. It writes each
log record on one line, so the matching line holds the whole traceback, with
`\n` between the frames. When the page shows no reference, search the logs for
`ERROR`.

### Your role does not allow this

**Symptoms**: a page or an action in the Admin UI fails with `403`. A page load
or a form post gets the default `403 Forbidden` page, which doesn't show the
message. Any other request, such as one from a page script or from `curl`, gets
the body `{"success": false, "error": "Your role does not allow this"}`.

**Cause**: the role on your user row in the tenant doesn't permit the request.
[Tenant users](../security.md#tenant-users) lists what each role can do.

To confirm the cause, find the refusal in the logs:

```bash
docker compose logs adcp-server | grep "Refused"
```

The line reads `Refused <method> <path> for tenant <tenant_id>: role too low`.

**Fix**: ask an admin of the tenant, or a super admin, to change your role on
the **Users & Access** page.

### Cross-origin request refused

**Symptoms**: saving a form, or another change in the Admin UI, fails with
`403`. A form post gets the default `403 Forbidden` page with the text
"Cross-origin request refused". A request from a page script gets the body
`{"success": false, "error": "Forbidden"}`.

**Cause**: the request didn't come from the Admin UI's own origin, so the
cross-site request check refused it.

To confirm the cause, find the refusal in the logs:

```bash
docker compose logs adcp-server | grep "Refused a cross-origin"
```

The line reads
`Refused a cross-origin <method> <path> (Sec-Fetch-Site=<value>, Origin=<value>)`.
Read the two values as follows:

- `Sec-Fetch-Site='cross-site'` or `Sec-Fetch-Site='same-site'`: a page on
  another site, or on a sibling subdomain, sent the request. Open the Admin UI
  at its own address, and repeat the change from its page.
- `Sec-Fetch-Site=None`, with an `Origin` equal to the address in your browser:
  the app sees a different scheme or host than the browser used.

**Fix**: in the second case, correct the forwarded headers that the reverse
proxy sends.
[Cross-site request forgery](../security.md#cross-site-request-forgery)
describes the check and the headers that the proxy must send.

### Activity feed not updating

The **Recent Activity** feed on the tenant dashboard polls the server every
five seconds for the tenant's most recent `audit_logs` rows. Check the
following three things:

- The poll address, `http://localhost:8000/admin/tenant/{tenant_id}/activity`,
  returns JSON when you open it while you're signed in.
- Rows arrive in the `audit_logs` table for the tenant.
- The status next to the feed's heading starts with `Connected`. It reads
  `Access denied` when a poll gets a `403`, and `Connection error - will retry`
  when a poll fails.

### Slack notifications not arriving

1. Verify the **Task Notifications Webhook URL** (`slack_webhook_url`) in the
   **Integrations** section of the tenant settings.
2. For audit log messages, check the **Audit Log Webhook URL**
   (`slack_audit_webhook_url`) in the same section. The app sends audit log
   messages only to that URL, and only when you set it.
3. Test the webhook directly:

   ```bash
   curl -X POST "your-webhook-url" \
     -H "Content-Type: application/json" \
     -d '{"text": "Test notification"}'
   ```

## GAM integration issues

These entries cover the Google Ad Manager (GAM) adapter.

### Could not determine client ID from request

**Symptoms**: a GAM request that uses the tenant's OAuth refresh token fails
with this error, for example an inventory sync. **Test Connection**,
**Auto-detect Network**, and **Get OAuth Token** report the same cause as
`GAM OAuth credentials not configured`.

**Cause**: `GAM_OAUTH_CLIENT_ID` or `GAM_OAUTH_CLIENT_SECRET` isn't set.

1. Check whether the variables are set:

   ```bash
   docker compose exec adcp-server env | grep GAM_OAUTH
   ```

2. If they're missing, add them to the `.env` file, which `docker-compose.yml`
   reads:

   ```bash
   GAM_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GAM_OAUTH_CLIENT_SECRET=GOCSPX-your-client-secret
   ```

   The app refuses to start when the client ID doesn't end in
   `.apps.googleusercontent.com`, or the secret doesn't start with `GOCSPX-`.

3. Create OAuth credentials in the
   [Google Cloud Console credentials page](https://console.cloud.google.com/apis/credentials):
   create an OAuth 2.0 client ID of type Web application. Add the redirect URI
   that matches the address of your Admin UI:
   `http://localhost:8000/admin/auth/gam/callback` under `/admin/`, or
   `http://localhost:8000/auth/gam/callback` at the root. In production, the
   app uses `https://<SALES_AGENT_DOMAIN>/admin/auth/gam/callback`.

4. Recreate the containers so that they read the changed `.env` file:

   ```bash
   docker compose up -d
   ```

5. Verify the configuration:

   ```bash
   docker compose exec adcp-server python scripts/gam_prerequisites_check.py
   ```

**Alternative**: use service account authentication, which needs no OAuth
setup. In the Admin UI, configure it under Service Account Integration.

### OAuth compared with service account

The following table compares the two GAM authentication methods.

| Feature | OAuth (refresh token) | Service account |
|---------|----------------------|-----------------|
| Setup complexity | Higher — requires OAuth credentials | Lower — paste a JSON key, or create the account in the Admin UI |
| Token expiration | Tokens can expire | Never expires |
| Use case | Local testing | Production deployments |
| Security | Tied to a user account | Isolated service identity |

Use a service account for production, and OAuth for local testing only.

### OAuth token invalid

Store a fresh refresh token from the Admin UI. In the **Ad Server** section of
the tenant settings, click **Refresh OAuth Token**, or **Get OAuth Token** when
no token is stored. Approve the Google consent screen, and the callback
replaces the stored token. The flow needs the GAM OAuth client that
[Could not determine client ID from request](#could-not-determine-client-id-from-request)
sets up.

```bash
# Verify in the database (the token is stored encrypted, so this shows only whether one is set)
docker compose exec postgres psql -U adcp_user adcp -c \
  "SELECT tenant_id, gam_refresh_token IS NOT NULL AS has_token FROM adapter_config;"
```

### Network code mismatch

```bash
docker compose exec postgres psql -U adcp_user adcp -c \
  "UPDATE adapter_config SET gam_network_code='123456' WHERE tenant_id='tenant_id';"
```

### Inventory sync timeout

**Symptoms**: a sync job shows "running" and never completes, or a large
account (hundreds of custom targeting keys with thousands of values each)
takes more than 30 minutes.

A sync from the Admin UI, and a `full` sync through the Inventory Sync API, fetch
custom targeting **keys** only. The Admin UI reads a key's **values** live from GAM
each time you browse the key, and stores none of them. Other Inventory Sync API syncs
also store values, subject to `custom_targeting_limit`.

```bash
# Trigger an inventory sync (authenticated with the SYNC_API_KEY value)
curl -X POST http://localhost:8000/admin/api/sync/trigger/{tenant_id} \
  -H "X-API-Key: ${SYNC_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"sync_type": "full", "force": true}'

# Check recent sync jobs
curl "http://localhost:8000/admin/api/sync/history/{tenant_id}?limit=5" \
  -H "X-API-Key: ${SYNC_API_KEY}"
```

Check what the sync loaded:

```sql
SELECT
  COUNT(*) FILTER (WHERE inventory_type = 'custom_targeting_key') AS keys_count,
  COUNT(*) FILTER (WHERE inventory_type = 'custom_targeting_value') AS values_count
FROM gam_inventory
WHERE tenant_id = '{tenant_id}';
```

`values_count` counts only the values that a selective sync stored. Browsing a
key in the Admin UI adds none.

A sync that still runs long is stuck in GAM API calls — check
`docker compose logs adcp-server` for the request the sync is waiting on. For a
selective sync with `custom_targeting`, lower `custom_targeting_limit`, or run a
full sync, which skips values.

## Outbound request refused

**Symptoms**: an outbound HTTP call fails with an egress error, or the app
rejects a webhook or creative-agent URL at save time.

Every outbound request goes through the single gateway in
`src/core/security/outbound_http.py`, which refuses private, loopback, and
metadata addresses. This is intended behavior, not a network fault — don't
add a bypass or a hand-rolled IP check at the call site.
[Outbound egress](../security/outbound-egress.md) documents what the gateway
refuses, what it never refuses, how tests exercise egress through the TLS
terminator, and how to add an outbound call.

## Testing and quality-gate issues

### A `make quality` run fails

`make quality` runs formatting, linting, the egress import ban, type
checking, ratchet checks, and the unit suite in that order. The following
decision tree routes a failure by which gate reported it.

```mermaid
flowchart TD
    Q["make quality fails"] --> Gate{"Which gate failed?"}
    Gate -->|"ruff format --check"| Fmt["Run: make lint-fix"]
    Gate -->|"ruff check"| Lint["Fix the named rule;\ncomplexity rules mean\nextract helpers"]
    Gate -->|"ruff check --config ruff-egress.toml\n(TID251)"| Egress["Route the call through the\negress seam: see An egress\nimport ban fires"]
    Gate -->|"mypy"| Types["Fix types in the files\nyou changed"]
    Gate -->|"check_code_duplication.py"| Dry["Extract a shared helper —\nthe baseline only shrinks"]
    Gate -->|"pytest tests/unit/"| Unit{"Is it a\ntest_architecture_* test?"}
    Unit -->|Yes| Guard["A structural guard fired:\nsee A structural guard fails"]
    Unit -->|No| Test["Fix the code or the test —\nnever skip it"]
```

### A structural guard fails

A failing `tests/unit/test_architecture_*.py` test means your change violates
an enforced architecture invariant: a raw `select()` outside a repository, a
transport import in an `_impl` function, a weakened schema redeclaration. Fix
it by changing your code to satisfy the invariant, not by growing the guard's
allowlist — allowlists only shrink.
[Structural guards](structural-guards.md) documents every guard, what it
catches, and why it exists.
[Architecture principles](architecture-principles.md) and the
[patterns reference](patterns-reference.md) explain the layering rules the
guards enforce.

### An egress import ban fires

**Symptoms**: `TID251` on an import of `httpx`, `requests`, `urllib.request`,
or `aiohttp` under `src/` or `scripts/`.

Outbound HTTP goes through `send`/`asend` in
`src/core/security/outbound_http.py` — import that instead of a raw client.
[Outbound egress](../security/outbound-egress.md) explains the seam and the
narrow, reviewed exemption process.

### Integration tests slow or flaky

Create and clean up test data in factory-based fixtures
(`tests/factories/`), not by mocking database calls. Run one test against a
real database with:

```bash
scripts/run-test.sh tests/integration/test_foo.py -x
```

For the full containerized suite, its databases, and its failure modes, read
[End-to-end testing](e2e-testing.md). Read its "Failure modes and what they
mean" section before you re-run anything.

### Async test failures

Mark async tests with `@pytest.mark.asyncio` and mock async dependencies with
`AsyncMock`. Use `async with` for async context managers and `await` every
async call — a bare coroutine assertion passes vacuously.

### `AttributeError` on model fields

**Symptoms**: `AttributeError: 'Creative' object has no attribute
'format_id'` — often after a schema change, or passing locally but failing
in CI.

- A refactor must update all three layers together: the database schema (a
  migration), the ORM model, and the Pydantic schemas.
- In-place mutations of a `JSONType` column don't persist unless you call
  `attributes.flag_modified(obj, "field_name")` — see the uses in
  `src/core/database/repositories/` for the pattern.
- Test both model creation and updates; an update-path bug survives
  creation-only tests.

The schema-alignment pre-commit stage (`pre-commit run mcp-schema-alignment
--all-files`) and the guards in
[Structural guards](structural-guards.md) catch most drift before commit.

## Production recovery

This project deploys to whatever platform you host it on, so the commands
are your platform's — the sequence is what matters:

1. Read the platform logs and identify the specific error.
2. Check recent changes: `git log --oneline -10`.
3. Reproduce and fix locally with Docker.
4. Deploy the migration fix first, then the code change — a code deploy
   against an unmigrated schema crash-loops.
5. Deploy the minimal fix to restore service; ship broader changes
   incrementally.

For migration-chain failures, follow
[Broken migration chain](#broken-migration-chain) before deploying anything.

## API error quick reference

The following table maps HTTP errors to their usual cause.

| Error | Cause | Solution |
|-------|-------|----------|
| `401 Unauthorized` | Invalid token | Check the `Authorization: Bearer` header |
| `404 Not Found` | Wrong endpoint | Check the URL and method |
| `422 Validation Error` | Invalid request | Check the request schema |
| `400 Invalid ID format` | Malformed IDs | Ensure IDs match the expected pattern |
| `500 Internal Error` | Server error | Check the server logs |

## Check system health

```bash
# Service health through the nginx proxy
curl http://localhost:8000/health

# Database health
docker compose exec postgres pg_isready

# Container health
docker compose ps
```

## Read the logs

```bash
# All services, following
docker compose logs -f

# The app (MCP, A2A, REST, and Admin UI run in this one service)
docker compose logs -f adcp-server

# The migration runner
docker compose logs db-init
```

The app also writes audit rows to the `audit_logs` table: operation type,
timestamp, principal and tenant IDs, success or failure, and security
violations. Only the operations whose code writes a row appear in it. For
admin actions, coverage is per route, as [Audit trail](../security.md#audit-trail)
explains, so don't treat the table as a complete record. The **Recent
Activity** feed on the tenant dashboard reads from it.

Test runs persist JSON reports in `test-results/<ddmmyy_HHmm>/` — read those
instead of re-running a suite whose terminal output is gone.

## Get help

Check these sources in order:

1. The `/docs` directory — start with the
   [architecture principles](architecture-principles.md) and the
   [request lifecycle](request-lifecycle.md).
2. The GitHub issue tracker.
3. The tests — `tests/` is the largest set of working examples.

When reporting an issue, include the full stack trace, your environment
(Docker or standalone, OS, versions), minimal reproduction steps, the
relevant log entries, and a sanitized configuration.
