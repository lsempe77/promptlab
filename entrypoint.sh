#!/bin/sh
# Entrypoint for Fly.io — starts the API server, supervisor, and worker.
# All three are long-running processes; the API server is the primary
# (Fly health checks target it), the supervisor and worker are background.
# A machine restart (deploy, crash, Fly maintenance) kills all three, so
# they MUST be started here (not manually via fly ssh console) to survive.

set -e
cd /app

# Start the worker in the background (processes extraction/judge/optimize tasks)
nohup python -m backend.scripts.worker --loop >> /data/worker.log 2>&1 &
echo "worker started (pid $!)"

# Start the supervisor in the background (enqueues tasks, manages the cycle)
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

# Start the API server in the foreground (Fly health checks target port 8080)
exec uvicorn backend.app.api:app --host 0.0.0.0 --port 8080
