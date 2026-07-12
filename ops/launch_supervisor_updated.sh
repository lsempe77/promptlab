#!/bin/sh
# Supervisor launch script — parallelism=4 on performance-2x (2 dedicated CPUs, 4GB RAM)
cd /app
nohup python -m backend.scripts.supervisor \
  --project dep-extraction \
  --loop \
  --max-cycles 12 \
  --interval 10800 \
  --parallelism 4 \
  >> /data/supervisor.log 2>&1 &
echo "LAUNCHED pid $!"
