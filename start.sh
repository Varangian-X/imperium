#!/bin/bash
set -e

# Port conflict guard — active in Workshop sandbox, skipped elsewhere
APP_PORT=${APP_PORT:-3001}
if [ -f /usr/local/lib/workshop-devguard.sh ]; then
    source /usr/local/lib/workshop-devguard.sh
    devguard_acquire "$APP_PORT"
fi

# Startup timing
T0=$(date +%s%3N 2>/dev/null || python3 -c "import time;print(int(time.time()*1000))")
elapsed() { echo $(( $(date +%s%3N 2>/dev/null || python3 -c "import time;print(int(time.time()*1000))") - T0 )); }

# Skip sync if lockfile unchanged (fast restart)
UV_HASH=$(md5sum uv.lock 2>/dev/null | cut -d' ' -f1)
if [ ! -f ".venv/.uv-hash-$UV_HASH" ]; then
  echo "[+$(elapsed)ms] uv sync starting..."
  uv sync --compile-bytecode --frozen || uv sync --compile-bytecode
  rm -f .venv/.uv-hash-* 2>/dev/null
  touch ".venv/.uv-hash-$UV_HASH"
  echo "[+$(elapsed)ms] uv sync done"
else
  echo "[+$(elapsed)ms] uv sync skipped (lockfile unchanged)"
fi

echo "[+$(elapsed)ms] Starting dev server on http://0.0.0.0:${APP_PORT}"
exec uv run uvicorn app:asgi --host 0.0.0.0 --port ${APP_PORT} --reload \
  --reload-exclude ".venv" --reload-exclude ".git" --reload-exclude "__pycache__" --reload-exclude "*.pyc"
