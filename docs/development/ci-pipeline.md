# CI Pipeline

GitHub Actions workflow: `.github/workflows/test.yml`

## Integration Test Shards

Integration tests are split into 5 parallel shards by entity marker:

| Shard | Markers |
|-------|---------|
| creative | `creative` |
| product | `product` |
| media-buy | `media_buy or delivery` |
| infra | `transport or auth or adapter or schema or admin or infra or inventory or targeting or workflow or policy or agent` |
| other | everything not in the above |

Each shard runs against a GitHub Actions service container (Postgres 15).

## Reference Creative Agent

The `creative` shard starts a reference creative agent from the upstream
[adcontextprotocol/adcp](https://github.com/adcontextprotocol/adcp) server.
This is the full adcp monolith — the creative agent is one route within it.

### Pinning to a known-good commit

The upstream repo is actively developed and its migrations can break without
warning. We pin to a specific commit SHA via the GitHub archive API:

```yaml
curl -sL https://github.com/adcontextprotocol/adcp/archive/<SHA>.tar.gz \
  | tar xz -C /tmp/adcp-server --strip-components=1
```

**Why archive API instead of `git clone`?** GitHub's smart HTTP protocol does
not allow `git fetch` of arbitrary SHAs on repositories you don't own. A shallow
clone (`--depth 1`) only gets HEAD. The archive endpoint works for any public
commit without authentication.

**When to update the pin:** After verifying that upstream HEAD's migrations run
cleanly. Check the `community_points` / `users` table FK ordering — this was the
failure that prompted pinning (April 2026).

### Creative agent infrastructure

The agent runs in its own Docker network (`creative-net`) with a separate
Postgres 16 instance (`adcp-postgres`). It is not connected to the test
database used by our integration tests.

```
creative-net:
  adcp-postgres (Postgres 16, user=adcp, db=adcp_registry)
  creative-agent (port 9999 → 8080)
```

## Security Audit

The `Security Audit` job runs `uv run pip-audit` against pinned dependencies.
Any known vulnerability in a direct dependency fails the build. Fix by bumping
the affected package in `pyproject.toml` and running `uv lock`.

## Postgres Health Check

The service container uses `pg_isready -U adcp_user` for health checks. The
`-U` flag is required — without it, `pg_isready` defaults to the OS user
(`root` on GitHub Actions runners), which produces noisy "role root does not
exist" log entries.
