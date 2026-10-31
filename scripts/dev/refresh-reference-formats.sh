#!/usr/bin/env bash
# Re-capture tests/fixtures/creative_formats/reference_formats.json from the pinned
# reference creative agent. `make creative-formats-refresh` runs this.
#
# The capture runs INSIDE the e2e compose network, where the agent is reached through the
# shared TLS front (creative-agent.adcp.test:8443) at a public-range address, so the egress
# gateway accepts it on its own terms. The host-published agent.localhost front resolves to
# loopback, which the gateway refuses, so a capture from the host cannot work.
#
# Brings up only creative-pg, creative-agent and tls-proxy from docker-compose.e2e.yml, runs
# scripts/refresh-reference-formats.py in the tests image on that network, and tears the
# services down on exit, success or not.
set -euo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )/../.."

PROJECT="refresh-formats"
URL="https://creative-agent.adcp.test:8443/api/creative-agent/mcp"
dc=(docker compose -f docker-compose.e2e.yml -p "$PROJECT")

scripts/creative-agent-stack.sh build    # the pinned agent image
scripts/dev/ensure-test-tls.sh           # the CA the front serves
eval "export $(scripts/dev/alloc-e2e-subnet.sh)"

"${dc[@]}" --profile runner build tests
trap '"${dc[@]}" down -v' EXIT
# creative-agent's own depends_on brings creative-pg up healthy first; --wait returns once
# the agent's healthcheck passes. tls-proxy's depends_on names every upstream it fronts, so
# it starts alone.
"${dc[@]}" up -d --wait creative-agent
"${dc[@]}" up -d --no-deps tls-proxy
docker run --rm --network "${PROJECT}_default" -v "$PWD":/app -w /app \
    -e SSL_CERT_FILE=/app/.test-tls/combined-ca.pem "${PROJECT}-tests" \
    python scripts/refresh-reference-formats.py --url "$URL"
