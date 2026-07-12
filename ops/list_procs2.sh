#!/bin/sh
# List all python processes (supervisor + workers)
for f in /proc/*/cmdline; do
  pid=$(echo $f | cut -d/ -f3)
  cmd=$(cat "$f" 2>/dev/null | tr '\0' ' ')
  case "$cmd" in
    *python*) echo "$pid: $cmd" ;;
  esac
done
