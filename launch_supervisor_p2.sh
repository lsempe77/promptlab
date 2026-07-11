#!/bin/sh
cd /app
nohup python -m backend.scripts.supervisor \
  --project dep-extraction \
  --loop \
  --max-cycles 12 \
  --interval 10800 \
  --parallelism 2 \
  >> /data/supervisor.log 2>&1 &
echo "LAUNCHED pid $!"
