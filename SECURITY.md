# Security policy

## Supported versions

We support the `main` branch and the most recent tagged release. Older releases
do not receive backported fixes; please upgrade to receive security updates.

| Version        | Supported |
|----------------|-----------|
| `main`         | Yes       |
| Latest release | Yes       |
| Older releases | No        |

## Reporting a vulnerability

Please report vulnerabilities through GitHub's private security advisory channel:

> https://github.com/prebid/salesagent/security/advisories/new

Do not file public issues for suspected vulnerabilities. Public PRs that fix
non-trivial security issues should also be coordinated through the advisory
channel before opening.

A useful report includes:

- A description of the issue and which component is affected (admin UI, MCP
  server, A2A server, GAM adapter, multi-tenant boundary, etc.)
- Reproduction steps or a proof-of-concept
- Impact assessment (data exposure, privilege escalation, denial of service)
- Suggested mitigations if you have them

## Triage SLA

- **Acknowledgement:** within 5 business days of submission.
- **Initial triage:** within 10 business days (severity assessment, scope
  confirmation, owner assigned).
- **Fix timeline:** case-by-case based on severity and scope. Critical issues
  affecting tenant isolation or authentication are prioritized over lower-impact
  findings.

## Scope

In scope:

- Admin UI authentication, session handling, CSRF, SSRF
- MCP server (`/mcp/`) authentication and authorization
- A2A server (`/a2a`) authentication and authorization
- GAM adapter — credential handling, OAuth flows, network isolation
- Mock adapter — only when used in non-test environments by mistake
- Multi-tenant isolation — tenant boundary enforcement, cross-tenant data
  access, subdomain routing
- Creative agent integration — webhook handling, push-notification handlers
- CI and supply-chain — `.pre-commit-config.yaml`, `.github/workflows/`,
  `pyproject.toml`, `uv.lock`, `Dockerfile`, `docker-compose*.yml`,
  `.python-version`

Out of scope:

- Vulnerabilities in third-party dependencies — please report directly to the
  upstream maintainers. We track and update dependencies via Dependabot.
- Theoretical issues without a reproduction or proof-of-concept.
- Findings that require an already-compromised maintainer machine, leaked
  credentials, or other prerequisites equivalent to administrative access.

## CI and hook modification policy

Files that influence what runs on contributor and maintainer machines, or what
gates the merge process, are CODEOWNERS-protected. Changes to any of the
following must be reviewed by `@chrishuie` and discussed for supply-chain
implications before merge:

- `.pre-commit-config.yaml`
- `.github/workflows/`
- `pyproject.toml`, `uv.lock`
- `Dockerfile`, `docker-compose*.yml`
- `.python-version`

External hook references and GitHub Actions are SHA-pinned. PRs that switch a
SHA to a tag, or downgrade SHA pinning to a less-strict form, will be rejected.

## Disclosure timeline

The default coordinated disclosure window is 90 days from the date of the
acknowledgement. We are willing to negotiate this case-by-case based on fix
complexity and the reporter's needs. We do not require a CVE to be assigned
before publishing a fix.
