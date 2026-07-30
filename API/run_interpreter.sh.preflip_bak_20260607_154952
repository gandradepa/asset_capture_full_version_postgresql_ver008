#!/usr/bin/env bash
set -Eeuo pipefail
export PYTHONUNBUFFERED=1

API_ROOT="/home/developer/API"
VENV_PY="/home/developer/asset_capture_app_dev/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "[Wrapper] ERROR: venv Python not found at ${VENV_PY}" >&2
  exit 1
fi

echo "[Wrapper] Using Python: ${VENV_PY}"
echo "[Wrapper] Args: $*"

# run whatever was passed (script path or -c "code")
exec "${VENV_PY}" "$@"
