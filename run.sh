#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

cleanup() { docker compose down >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker compose build
docker compose up -d bio-backend bio-device
# The evaluation waits for both services (the first start downloads the face models).
docker compose --profile jobs run --rm bio-eval

echo
echo "Done. Metrics file: results/biometrics/metrics.json"
