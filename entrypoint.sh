#!/bin/sh
# Entrypoint for Fly.io.
#
# This image is a READ-ONLY observability API (serves the SQLite DB at
# DEP_DB_PATH). The background agents (worker + supervisor) are DISABLED by
# default: they need Postgres (DATABASE_URL) + an OpenRouter key and cost money,
# and a dead/quota-exhausted Postgres previously made the worker busy-loop
# forever and pin the always-on machine. Enable them only with DEP_ENABLE_AGENTS=1.

set -e
cd /app

if [ "$DEP_ENABLE_AGENTS" = "1" ]; then
  echo "DEP_ENABLE_AGENTS=1 -> starting worker + supervisor"
  # Worker: processes extraction/judge/optimize tasks (needs DATABASE_URL).
  nohup python -m backend.scripts.worker --loop >> /data/worker.log 2>&1 &
  echo "worker started (pid $!)"
  # Supervisor: enqueues tasks, manages the cycle.
  nohup python -m backend.scripts.supervisor \
    --project dep-extraction \
    --loop \
    --max-cycles 12 \
    --interval 60 \
    --parallelism 2 \
    --tiers cheap \
    --reflector-model "~anthropic/claude-sonnet-latest" \
    >> /data/supervisor.log 2>&1 &
  echo "supervisor started (pid $!)"
else
  echo "agents disabled (read-only API only); set DEP_ENABLE_AGENTS=1 to enable worker+supervisor"
fi

# Start the API server in the foreground (Fly health checks target port 8080)
exec uvicorn backend.app.api:app --host 0.0.0.0 --port 8080
