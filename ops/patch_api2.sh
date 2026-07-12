#!/bin/sh
cp /data/api.py.new2 /app/backend/app/api.py
echo "api.py patched (v2)"
kill $(cat /tmp/uvicorn.pid 2>/dev/null) 2>/dev/null
cd /app && nohup uvicorn backend.app.api:app --host 0.0.0.0 --port 8080 >> /tmp/uvicorn.log 2>&1 &
echo $! > /tmp/uvicorn.pid
echo "uvicorn restarted pid $!"
