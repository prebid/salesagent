#!/usr/bin/env bash
set -euo pipefail

image_ref="${1:?usage: scripts/ci/trivy_image_gate.sh <image-ref>}"

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:latest \
  image \
  --scanners vuln \
  --severity HIGH,CRITICAL \
  --ignore-unfixed \
  --exit-code 1 \
  "${image_ref}"
