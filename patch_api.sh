#!/bin/sh
cp /data/api.py.new /app/backend/app/api.py
echo "api.py patched"
# Uvicorn needs to reload — restart it
kill $(cat /tmp/uvicorn.pid 2>/dev/null) 2>/dev/null
cd /app && nohup uvicorn backend.app.api:app --host 0.0.0.0 --port 8080 >> /tmp/uvicorn.log 2>&1 &
echo $! > /tmp/uvicorn.pid
echo "uvicorn restarted pid $!"
