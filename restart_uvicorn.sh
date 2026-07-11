#!/bin/sh
# Kill ALL uvicorn processes (including fly's init-managed one at pid 662)
# Fly's init supervisor will restart it automatically, loading the patched api.py
for f in /proc/*/cmdline; do
  pid=$(echo $f | cut -d/ -f3)
  cmd=$(cat "$f" 2>/dev/null | tr '\0' ' ')
  case "$cmd" in
    *uvicorn*)
      echo "Killing uvicorn pid $pid"
      kill "$pid" 2>/dev/null
      ;;
  esac
done
echo "Done. Fly init will restart uvicorn with the patched code."
