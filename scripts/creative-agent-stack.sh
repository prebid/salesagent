#!/usr/bin/env bash
# Pinned reference creative-agent stack — SINGLE SOURCE OF TRUTH for the pin.
#
# salesagent-kczg: the local authoritative run (run_all_tests.sh) must NOT hit
# the live public creative agent (https://creative.adcontextprotocol.org). Its
# catalog drifts (it dropped display_image/html/js/video_standard), turning
# tests/integration/test_creative_agent_live.py red non-deterministically.
# CI isolates these via a containerized adcp monolith pinned to a known-good
# commit; this script mirrors that EXACTLY so CI and local cannot diverge.
# .github/workflows/ci.yml calls this same script (single source of the pin).
#
# Usage:
#   scripts/creative-agent-stack.sh up      # idempotent: build+run if needed, wait healthy
#   scripts/creative-agent-stack.sh build   # idempotent: fetch+build image only (in-network compose service)
#   scripts/creative-agent-stack.sh down    # stop+rm containers+network (keeps image/tarball cache)
#   scripts/creative-agent-stack.sh url     # print CREATIVE_AGENT_URL (the MCP endpoint)
#   scripts/creative-agent-stack.sh origin  # print CREATIVE_AGENT_ORIGIN (the agent's identity)
#   scripts/creative-agent-stack.sh env     # print the exports a host process needs to dial it:
#                                           #   eval "$(scripts/creative-agent-stack.sh env)"
#   scripts/creative-agent-stack.sh publish # build+push pin-keyed image to ghcr.io (CI publish workflow)
set -euo pipefail

# Pin to a known-good commit — bump deliberately, never to HEAD.
# The AGENT pin follows the adcp release that matches the spec version the installed SDK
# reports (see docs/adcp-spec-version.md); that release's tag resolves to this commit.
# Its formats are captured in tests/fixtures/creative_formats/reference_formats.json by
# scripts/refresh-reference-formats.py, which reads this line; re-capture on every bump.
ADCP_PIN="8d1d3244b2ef"

# The source patches applied to the pinned agent, each a function defined below. IMAGE_TAG
# carries a checksum of their text, so adding a patch means adding its name here.
_PATCH_FUNCTIONS=(_dockerfile_without_fly_size_check _raise_mcp_rate_limit _dockerfile_with_dependency_updates)

# The canonical local tag is pin-keyed so a cached image from a DIFFERENT pin
# can never satisfy _ensure_image (a bare `adcp-creative-agent` image left by
# another branch/worktree on a shared Docker host would silently substitute
# the wrong agent after a bump). The un-keyed alias is what
# docker-compose.e2e.yml and the standalone `up` path run; _ensure_image
# repoints it at this pin before every run.
#
# The tag also carries a checksum of the source patches in _PATCH_FUNCTIONS (the Fly size
# check, the MCP rate limit and the dependency updates), read from the script's own text.
# Changing any patch changes the tag, locally and on ghcr.io, so no host reuses an image built
# with an older patch or with none (an image from before the rate-limit patch carries the bare
# pin as its tag).
_PATCHES="$(awk -v names="$(IFS='|'; echo "${_PATCH_FUNCTIONS[*]}")" -v want="${#_PATCH_FUNCTIONS[@]}" '
                 $0 ~ "^(" names ")[(][)] [{]$" { n++; on = 1 }
                 on { print }
                 on && /^}$/ { on = 0 }
                 END { exit n == want ? 0 : 1 }' "${BASH_SOURCE[0]}")" || {
    echo "[creative-agent] ERROR: the source patch functions were not found in ${BASH_SOURCE[0]}" >&2
    exit 1
}
IMAGE_TAG="${ADCP_PIN}-p$(printf '%s\n' "$_PATCHES" | cksum | cut -d' ' -f1)"
IMAGE_ALIAS="adcp-creative-agent"
IMAGE="${IMAGE_ALIAS}:${IMAGE_TAG}"
NET="creative-net"
PG="adcp-postgres"
AGENT="creative-agent"
SRC="/tmp/adcp-server-${ADCP_PIN}"
HEALTH="http://localhost:9999/api/creative-agent/health"

# TLS front (salesagent-40qh): the same primitive scripts/test-stack.sh already
# uses for the server (agent.localhost + the generated private CA + stock
# nginx:stable + nginx-tls-test.conf.template), fronting THIS agent instead.
# `agent.localhost` is the host-published name gen_test_tls.py's SAN already
# reserves for exactly this; a fixed port is fine here (unlike test-stack.sh's
# dynamic allocation) because this script runs at most one stack at a time —
# the container names above are already fixed, not per-instance.
TLS_PROXY="creative-agent-tls"
TLS_PORT="9443"
HEALTH_TLS="https://agent.localhost:${TLS_PORT}/api/creative-agent/health"
# The MCP ENDPOINT the seller dials. The reference agent serves MCP only at
# POST <base>/api/creative-agent/mcp; the bare /api/creative-agent is refused.
CREATIVE_AGENT_URL="https://agent.localhost:${TLS_PORT}/api/creative-agent/mcp"
# The operator agent's IDENTITY, an origin. This container stands in for the
# public reference agent: every reference a test seeds, and the checked-in
# capture, names the public agent, so that is the origin a reference must be
# recognized by and rerouted from. Never this container's own origin.
CREATIVE_AGENT_ORIGIN="https://creative.adcontextprotocol.org"

_healthy() { curl -sf -m 3 "$HEALTH" >/dev/null 2>&1; }
_tls_healthy() { curl -sf -m 3 --cacert .test-tls/ca.pem "$HEALTH_TLS" >/dev/null 2>&1; }

# Retry transient network failures (ECONNRESET during npm/tarball fetch in CI).
_retry() {
    local max="${1:-3}"
    shift
    local attempt=1 delay=2
    while [ "$attempt" -le "$max" ]; do
        if "$@"; then
            return 0
        fi
        if [ "$attempt" -eq "$max" ]; then
            break
        fi
        echo "[creative-agent] attempt ${attempt}/${max} failed, retrying in ${delay}s..." >&2
        sleep "$delay"
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done
    return 1
}

_fetch_tarball() {
    mkdir -p "$SRC"
    curl -sLf "https://github.com/adcontextprotocol/adcp/archive/${ADCP_PIN}.tar.gz" \
        | tar xz -C "$SRC" --strip-components=1
}

_ensure_tarball() {
    if [ ! -f "$SRC/Dockerfile" ]; then
        echo "[creative-agent] fetching adcp@${ADCP_PIN}"
        _retry 3 _fetch_tarball
    fi
    _raise_mcp_rate_limit
}

_ghcr_ref() {
    echo "${CREATIVE_AGENT_GHCR_IMAGE}:${IMAGE_TAG}"
}

_ghcr_image_exists() {
    docker manifest inspect "$(_ghcr_ref)" >/dev/null 2>&1
}

_pull_from_ghcr() {
    local ref="$(_ghcr_ref)"
    echo "[creative-agent] pulling ${ref} from ghcr.io"
    docker pull "$ref"
    docker tag "$ref" "$IMAGE"
}

# adcp's Dockerfile ends with a size check for adcp's own Fly deployment (an 8 GiB root
# filesystem): one RUN that fails the build when /app exceeds its limit. The v3.2.7 release
# ships every historical schema version and fails it (8067 MiB against 7424 MiB); upstream
# prunes that history on main (adcp #7496), but no 3.2.x release tag carries the prune. This
# copy of the Dockerfile drops that one RUN and nothing else. The image is then about the
# size the v3.1.25 agent was, and nothing here deploys to Fly. Remove this once a release
# that carries the prune is pinned.
_dockerfile_without_fly_size_check() {
    local out="${SRC}/Dockerfile.salesagent"
    awk '/^RUN app_mib=\$\(du -sm \/app/ { skip = 1 }
         skip { if ($0 !~ /\\$/) skip = 0; next }
         { print }' "${SRC}/Dockerfile" > "$out"
    echo "$out"
}

# The reference agent limits POST /api/creative-agent/mcp to 60 requests a minute per client IP
# (mcpRateLimiter in server/src/creative-agent/index.ts). Every test reaches it from one address
# and one seller preview costs about five of those requests, so the serial in-network BDD run
# sends up to 155 a minute (CI run 40822932801) and the excess comes back 429. This copy of the
# source raises that one limiter's max to 1000, about six times the measured peak, so a growing
# suite or a local run with several workers still fits. Nothing else changes; the preview-asset
# limiter (30 a minute) is left alone because no run has tripped it. Upstream request to make
# both limits configurable: adcontextprotocol/adcp#8574. Remove this once a release that carries
# it is pinned.
#
# index.ts is rewritten from a pristine copy saved on first use, so a cached source tree is
# patched once and only once. The build fails when the limiter's text is not found: a pin that
# moved or renamed the limiter must not build an image that silently keeps the old limit.
_raise_mcp_rate_limit() {
    local file="${SRC}/server/src/creative-agent/index.ts"
    local pristine="${SRC}/creative-agent-index.ts.orig"
    [ -f "$pristine" ] || cp "$file" "$pristine"
    if ! awk '/^  const mcpRateLimiter = rateLimit[(][{]$/ { limiter = 1 }
              limiter && /^    max: 60,$/ { sub(/60/, "1000"); raised++ }
              limiter && /^  [}][)];$/ { limiter = 0 }
              { print }
              END { exit raised == 1 ? 0 : 1 }' "$pristine" > "$file"; then
        echo "[creative-agent] ERROR: 'max: 60,' in mcpRateLimiter not found in ${file}" \
            "(adcp@${ADCP_PIN}); update _raise_mcp_rate_limit for this pin" >&2
        return 1
    fi
}

# The image carries the reference agent's npm dependencies as locked at the pin, the npm that
# ships with the node base image, and the base image's Debian packages, and the Trivy scan in
# publish-creative-agent.yml reports known CVEs in all three. This copy of the Dockerfile (made
# from the one above) updates them without moving the pin, which follows the spec version:
#   - a first stage, salesagent-deps, merges the versions below into package.json and
#     regenerates package-lock.json; the builder and runtime stages copy both from it instead
#     of from the source, so their `npm ci` installs the updated tree. A key that names a direct
#     dependency of the same major sets that dependency to the version (with a "$name" override,
#     as npm requires); any other key is an override. A key with "@<major>" touches only that
#     line (undici has three: the agent's 8.x, @slack/bolt's 7.x, @adcp/sdk's 6.x);
#   - the runtime stage upgrades its Debian packages from bookworm-security and replaces the
#     bundled npm with npm_version, after running that bundled version through the same
#     check as the entries below (as an "npm" entry).
# Each version is the lowest release that fixes every alert open on that package;
# @modelcontextprotocol/core follows client and server so one copy is installed. The lockfile is
# resolved as of resolve_before, so a package a listed version newly requires gets the same
# version on every build; the date is part of IMAGE_TAG's checksum. The Debian upgrade is not
# pinned: the security archive keeps only the latest build of a package, so a pinned one would
# stop building at the next update. The build
# fails when the Dockerfile no longer has its two `COPY package*.json ./` lines. It also fails,
# naming the entry, when the pin's package-lock.json locks every copy of an entry's package at
# that version or newer, or any copy (or a direct dependency's declared range) above it: the
# entry is then redundant or would set the package back. The same holds for npm_version against
# the npm the runtime base image bundles. Drop such an entry, and the patch once
# none is left. Upstream updates these on main (adcp #9107, for #9106, merged 2026-11-19), but
# no release tag carries it yet. Remove this once a release that carries it is pinned.
_dockerfile_with_dependency_updates() {
    local src_dockerfile="$(_dockerfile_without_fly_size_check)"
    local out="${SRC}/Dockerfile.salesagent-deps"
    local npm_version="11.21.3"
    local resolve_before="2026-11-19T23:59:59Z"
    local last_from
    last_from="$(grep -n '^FROM ' "$src_dockerfile" | tail -n 1 | cut -d: -f1)"
    {
        awk '/^FROM / { print $2 " AS salesagent-deps"; exit }' "$src_dockerfile" | sed 's/^/FROM /'
        cat <<'STAGE'
WORKDIR /app
COPY package*.json ./
COPY <<'VERSIONS' /tmp/salesagent-dependency-updates.json
  {
    "undici@6": "6.29.1",
    "undici@7": "7.30.1",
    "undici@8": "8.12.2",
    "@grpc/grpc-js": "1.14.5",
    "@hono/node-server": "2.1.6",
    "@modelcontextprotocol/client": "2.3.0",
    "@modelcontextprotocol/core": "2.3.0",
    "@modelcontextprotocol/node": "2.1.0",
    "@modelcontextprotocol/sdk": "1.32.0",
    "@modelcontextprotocol/server": "2.3.0",
    "body-parser@2": "2.4.0",
    "brace-expansion@1": "1.1.21",
    "dompurify": "3.4.17",
    "fast-copy": "4.1.0",
    "fast-uri@3": "3.1.9",
    "fast-xml-parser": "5.12.0",
    "follow-redirects": "1.16.2",
    "hono": "4.13.16",
    "ip-address": "10.7.4",
    "multer": "2.5.0",
    "proxy-addr": "2.0.8",
    "qs@6": "6.16.1",
    "sharp@0.35": "0.35.5",
    "source-map-js": "1.2.2"
  }
VERSIONS
COPY <<'MERGE' /tmp/salesagent-dependency-updates.cjs
  const fs = require("fs");
  const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
  const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8"));
  const updates = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const major = (spec) => spec.replace(/^[^0-9]*/, "").split(".")[0];
  const parts = (v) => v.replace(/^[^0-9]*/, "").split(/[-+]/)[0].split(".").map(Number);
  const atLeast = (v, min) => {
    const [a, b] = [parts(v), parts(min)];
    const i = [0, 1, 2].find((n) => (a[n] || 0) !== (b[n] || 0));
    return i === undefined || (a[i] || 0) > (b[i] || 0);
  };
  const stale = [];
  pkg.overrides = pkg.overrides || {};
  for (const [key, version] of Object.entries(updates)) {
    const name = key.replace(/(.)@.*$/, "$1");
    const line = key.slice(name.length + 1);
    const field = ["dependencies", "devDependencies", "optionalDependencies"].find(
      (f) => pkg[f] && name in pkg[f] && major(pkg[f][name]) === major(version));
    const locked = Object.entries(lock.packages || {})
      .filter(([path, e]) => path.split("node_modules/").length > 1
        && path.split("node_modules/").pop() === name && e.version
        && (!line || (e.version + ".").startsWith(line + ".")))
      .map(([, e]) => e.version);
    const newer = locked.concat(field ? [pkg[field][name]] : []).filter((v) => !atLeast(version, v));
    if (newer.length || (locked.length && locked.every((v) => atLeast(v, version)))) {
      stale.push(`${key} (${version}; the pin has ${locked.concat(newer).join(", ")})`);
    }
    if (field) pkg[field][name] = version;
    pkg.overrides[key] = field ? "$" + name : version;
  }
  if (stale.length) {
    console.error("salesagent-deps: the pin already has these versions or newer; drop them from"
      + " _dockerfile_with_dependency_updates: " + stale.join("; "));
    process.exit(1);
  }
  fs.writeFileSync("package.json", JSON.stringify(pkg, null, 2) + "\n");
MERGE
STAGE
        echo "RUN npm install -g npm@${npm_version} \\"
        echo " && node /tmp/salesagent-dependency-updates.cjs /tmp/salesagent-dependency-updates.json \\"
        echo " && npm install --package-lock-only --ignore-scripts --no-audit --no-fund --before=\"${resolve_before}\""
        echo
    } > "$out"
    # The runtime stage's npm goes through the same merge script, as an "npm" entry whose
    # locked version is the one the runtime base image bundles, so a base image that already
    # ships npm_version or newer stops the build instead of being set back.
    local runtime_head
    runtime_head="$(cat <<RUNTIME
COPY --from=salesagent-deps /tmp/salesagent-dependency-updates.cjs /tmp/salesagent-npm-check/merge.cjs
RUN apt-get update \\
 && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \\
 && rm -rf /var/lib/apt/lists/* \\
 && cd /tmp/salesagent-npm-check \\
 && echo '{}' > package.json \\
 && printf '{"packages":{"node_modules/npm":{"version":"%s"}}}' "\$(npm --version)" > package-lock.json \\
 && printf '{"npm":"%s"}' "${npm_version}" > updates.json \\
 && node merge.cjs updates.json \\
 && cd / && rm -rf /tmp/salesagent-npm-check \\
 && npm install -g npm@${npm_version} \\
 && npm cache clean --force
RUNTIME
)"
    if ! RUNTIME_HEAD="$runtime_head" awk -v last="$last_from" '
             $0 == "COPY package*.json ./" { print "COPY --from=salesagent-deps /app/package*.json ./"; copied++; next }
             { print }
             NR == last { print ENVIRON["RUNTIME_HEAD"] }
             END { exit copied == 2 ? 0 : 1 }' "$src_dockerfile" >> "$out"; then
        echo "[creative-agent] ERROR: two 'COPY package*.json ./' lines not found in ${src_dockerfile}" \
            "(adcp@${ADCP_PIN}); update _dockerfile_with_dependency_updates for this pin" >&2
        return 1
    fi
    echo "$out"
}

# The one docker build both paths use, so the local image and the published one under
# IMAGE_TAG come from the same patched Dockerfile. Arguments: the tags to apply.
_docker_build() {
    local dockerfile tag tags=()
    dockerfile="$(_dockerfile_with_dependency_updates)" || return 1
    for tag in "$@"; do tags+=(-t "$tag"); done
    echo "[creative-agent] docker build $* (adcp@${ADCP_PIN})"
    docker build -f "$dockerfile" "${tags[@]}" "$SRC"
}

_build_image_local() { _docker_build "$IMAGE"; }

_build_for_ghcr() { _docker_build "$(_ghcr_ref)" "$IMAGE"; }

_push_to_ghcr() {
    local ref="$(_ghcr_ref)"
    echo "[creative-agent] pushing ${ref} to ghcr.io"
    docker push "$ref"
}

_ensure_image() {
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
        if [ -n "${CREATIVE_AGENT_GHCR_IMAGE:-}" ] && _ghcr_image_exists && _retry 3 _pull_from_ghcr; then
            :
        else
            if [ -n "${CREATIVE_AGENT_GHCR_IMAGE:-}" ]; then
                echo "[creative-agent] WARN: ghcr image unavailable; building locally" >&2
            fi
            _ensure_tarball
            _retry 3 _build_image_local
        fi
    fi
    # Repoint the un-keyed alias (consumed by docker-compose.e2e.yml and
    # `up`) at the current pin, clobbering any other-pin leftover.
    docker tag "$IMAGE" "$IMAGE_ALIAS"
}

cmd_publish() {
    [ -n "${CREATIVE_AGENT_GHCR_IMAGE:-}" ] || { echo "CREATIVE_AGENT_GHCR_IMAGE required" >&2; return 1; }
    local ref="$(_ghcr_ref)"
    if _ghcr_image_exists; then
        echo "[creative-agent] ${ref} already published; nothing to do"
    else
        _ensure_tarball
        _retry 3 _build_for_ghcr
        _retry 3 _push_to_ghcr
    fi
    if [ -n "${GITHUB_OUTPUT:-}" ]; then
        echo "ref=${ref}" >> "$GITHUB_OUTPUT"
    fi
}

cmd_url() { echo "$CREATIVE_AGENT_URL"; }
cmd_origin() { echo "$CREATIVE_AGENT_ORIGIN"; }

# The stack's coordinates as shell exports: the endpoint and identity above, and
# SSL_CERT_FILE, the COMBINED bundle (system CA + the generated private CA), so a host
# process trusts the TLS front without losing the public roots.
# It deliberately does not open ADCP_OUTBOUND_ALLOW_PRIVATE: that setting is being
# deleted (salesagent-0o5xs), and the egress guard pins the files that may still set it.
cmd_env() {
    echo "export CREATIVE_AGENT_URL=\"${CREATIVE_AGENT_URL}\""
    echo "export CREATIVE_AGENT_ORIGIN=\"${CREATIVE_AGENT_ORIGIN}\""
    echo "export SSL_CERT_FILE=\"$(pwd)/.test-tls/combined-ca.pem\""
}

# Fetch the pinned source + build the image only. Idempotent. Shared by `up`
# (standalone host run) and `build` (in-network: docker-compose.e2e.yml reuses
# the resulting `adcp-creative-agent` image as a network service, no :9999).
cmd_build() {
    # Delegate to _ensure_image: it is idempotent (returns early if the image
    # exists), prefers a ghcr pull when CREATIVE_AGENT_GHCR_IMAGE is set, and
    # falls back to a retried local build. No duplicated fetch/build logic here.
    _ensure_image
}

_emit_url_output() {
    if [ -n "${GITHUB_OUTPUT:-}" ]; then
        echo "url=${CREATIVE_AGENT_URL}" >> "$GITHUB_OUTPUT"
    fi
}

cmd_up() {
    if _healthy && _tls_healthy; then
        echo "[creative-agent] already healthy on :9999 and https (reuse)"
        _emit_url_output
        return 0
    fi

    cmd_build

    docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET"

    if ! docker ps --format '{{.Names}}' | grep -qx "$PG"; then
        docker rm -f "$PG" >/dev/null 2>&1 || true
        docker run -d --network "$NET" --name "$PG" \
            -e POSTGRES_DB=adcp_registry -e POSTGRES_USER=adcp -e POSTGRES_PASSWORD=localdev \
            postgres:17-alpine >/dev/null
        sleep 5
    fi

    if ! docker ps --format '{{.Names}}' | grep -qx "$AGENT"; then
        docker rm -f "$AGENT" >/dev/null 2>&1 || true
        # NODE_ENV must NOT be production: since adcp v3.1.1 the agent refuses
        # to start with the DEV_USER_* auth bypass (which the harness needs) in
        # a production-shaped env. Mirrored in docker-compose.e2e.yml.
        docker run -d --network "$NET" --name "$AGENT" -p 9999:8080 \
            -e NODE_ENV=development -e PORT=8080 \
            -e DATABASE_URL=postgresql://adcp:localdev@${PG}:5432/adcp_registry \
            -e RUN_MIGRATIONS=true -e ALLOW_INSECURE_COOKIES=true \
            -e DEV_USER_EMAIL=ci@test.com -e DEV_USER_ID=ci-user \
            -e AGENT_TOKEN_ENCRYPTION_SECRET=local-ci-encryption-key-32chars!! \
            -e WORKOS_API_KEY=sk_test_dummy -e WORKOS_CLIENT_ID=client_dummy \
            "$IMAGE" >/dev/null
    fi

    echo "[creative-agent] waiting for health..."
    _agent_ok=false
    for _ in $(seq 1 60); do
        if _healthy; then _agent_ok=true; echo "[creative-agent] healthy on :9999"; break; fi
        sleep 2
    done
    if [ "$_agent_ok" != true ]; then
        echo "[creative-agent] FAILED to become healthy" >&2
        docker logs "$AGENT" 2>&1 | tail -30 >&2
        return 1
    fi

    # TLS front (salesagent-40qh): same image+template as the shared stack's
    # tls-proxy service (salesagent-amht.2), applied to this standalone stack —
    # this SNI name matches nothing in that template's map, so it falls back to
    # the TLS_UPSTREAM set below. The generator writes/refreshes .test-tls/
    # before nginx needs it.
    scripts/dev/ensure-test-tls.sh
    if ! docker ps --format '{{.Names}}' | grep -qx "$TLS_PROXY"; then
        docker rm -f "$TLS_PROXY" >/dev/null 2>&1 || true
        docker run -d --network "$NET" --name "$TLS_PROXY" -p "${TLS_PORT}:8443" \
            -e TLS_UPSTREAM="${AGENT}:8080" \
            -v "$(pwd)/config/nginx/nginx-tls-test.conf.template:/etc/nginx/templates/tls-test.conf.template:ro" \
            -v "$(pwd)/.test-tls:/app/.test-tls:ro" \
            nginx:stable >/dev/null
    fi

    echo "[creative-agent] waiting for TLS handshake..."
    for _ in $(seq 1 60); do
        if _tls_healthy; then
            echo "[creative-agent] https ready on agent.localhost:${TLS_PORT}"
            _emit_url_output
            return 0
        fi
        sleep 2
    done
    echo "[creative-agent] TLS front FAILED to complete a verified handshake at $HEALTH_TLS" >&2
    docker logs "$TLS_PROXY" 2>&1 | tail -30 >&2
    return 1
}

cmd_down() {
    docker rm -f "$TLS_PROXY" >/dev/null 2>&1 || true
    docker rm -f "$AGENT" >/dev/null 2>&1 || true
    docker rm -f "$PG" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "[creative-agent] torn down (image + ${SRC} cache kept for fast reuse)"
}

case "${1:-}" in
    up) cmd_up ;;
    build) cmd_build ;;
    down) cmd_down ;;
    url) cmd_url ;;
    origin) cmd_origin ;;
    env) cmd_env ;;
    publish) cmd_publish ;;
    *) echo "usage: $0 {up|build|down|url|origin|env|publish}" >&2; exit 2 ;;
esac
