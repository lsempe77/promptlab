#!/bin/sh
# List all python processes and kill stray subprocesses from old supervisor run
echo "=== Python processes ==="
for p in /proc/[0-9]*/cmdline; do
    pid=$(echo $p | cut -d/ -f3)
    cmd=$(cat $p 2>/dev/null | strings | head -5 | tr '\n' ' ')
    case "$cmd" in
        *python*) echo "pid=$pid: $cmd" ;;
    esac
done
echo "=== Load ==="
cat /proc/loadavg
