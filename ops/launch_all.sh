#!/bin/sh
# Launch supervisor + N workers (Phase 2: Postgres task-queue mode)
# Usage: sh /data/launch_all.sh [num_workers]
# Called after every fly deploy to restart the daemon processes.
N=${1:-4}
cd /app

# Supervisor — decides, enqueues
nohup python -m backend.scripts.supervisor \
  --project dep-extraction \
  --loop \
  --max-cycles 12 \
  --interval 300 \
  --parallelism 4 \
  >> /data/supervisor.log 2>&1 &
echo "LAUNCHED supervisor pid $!"

# Workers — claim & execute
i=0
while [ $i -lt $N ]; do
  nohup python -m backend.scripts.worker --loop >> /tmp/worker_${i}.log 2>&1 &
  echo "LAUNCHED worker $i pid $!"
  i=$((i+1))
done
