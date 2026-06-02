#!/usr/bin/env bash
# One-command macbox demo for friends, recordings, or sanity checks.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-/Applications/Amphetamine.app}"
IMAGE="${MACBOX_IMAGE:-macos-sequoia-clean}"

if [[ -f "${ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.venv/bin/activate"
fi

exec macbox demo \
  --app "${APP}" \
  --image "${IMAGE}" \
  --json
