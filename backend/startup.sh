#!/usr/bin/env bash
# Azure App Service (Linux, Python): bind to PORT injected by the platform.
set -euo pipefail
cd "$(dirname "$0")"
# When deploying without Oryx, CI can `pip install -r requirements.txt --target .python_packages`;
# prepend so imports resolve before the app tree.
if [ -d ".python_packages" ]; then
  export PYTHONPATH="$(pwd)/.python_packages:$(pwd):${PYTHONPATH:-}"
else
  export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
fi
exec gunicorn \
  -w 1 \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
