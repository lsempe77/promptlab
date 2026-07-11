#!/bin/sh
# Kill supervisor + workers, then relaunch (uses launch_all_new.sh with 300s interval)
cd /app
for f in /proc/*/cmdline; do
  pid=$(echo $f | cut -d/ -f3)
  cmd=$(cat "$f" 2>/dev/null | tr '\0' ' ')
  case "$cmd" in
    *supervisor*|*worker*)
      kill "$pid" 2>/dev/null && echo "Killed $pid"
      ;;
  esac
done
sleep 2
sh /data/launch_all_new.sh
