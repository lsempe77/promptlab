#!/bin/sh
# Launch N worker processes that poll the Postgres task queue
N=${1:-4}
i=0
while [ $i -lt $N ]; do
  nohup python -m backend.scripts.worker --loop >> /tmp/worker_${i}.log 2>&1 &
  echo "LAUNCHED worker $i pid $!"
  i=$((i+1))
done
