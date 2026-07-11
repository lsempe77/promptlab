#!/bin/sh
# Hot-patch: copy updated files from /data/ to /app/, then restart daemons
set -e
cp /data/prompts.py.new /app/backend/app/prompts.py
cp /data/optimizer.py.new /app/backend/app/optimizer.py
cp /data/run_extraction.py.new /app/backend/scripts/run_extraction.py
echo "Files patched."
# Restart daemons so they load the new code
sh /data/restart2.sh
