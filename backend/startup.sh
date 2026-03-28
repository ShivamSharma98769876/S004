#!/usr/bin/env bash
# Azure App Service (Linux, Python): bind to PORT injected by the platform.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-$(pwd)}"
exec gunicorn \
  -w 1 \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
